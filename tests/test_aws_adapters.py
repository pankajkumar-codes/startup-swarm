"""Bedrock request shape, caching, usage and budget; S3 mirror; DynamoDB state.

AWS clients are replaced by small fakes so these run offline.
"""

import io
import os
import tempfile
import unittest

from orchestrator.artifact_bus import ArtifactBus, S3Mirror
from orchestrator.bedrock_client import BedrockClient, BudgetExceededError, Prompt
from orchestrator.config import ModelSpec
from orchestrator.state_store import DynamoDBStateStore, LocalStateStore

SONNET = ModelSpec("global.anthropic.claude-sonnet-5-5", 1000, 0.2)
NOVA = ModelSpec("global.amazon.nova-2-lite-v1:0", 500, 0.0)


class FakeBedrock:
    def __init__(self, usage=None, text="hello"):
        self.requests = []
        self.usage = usage or {"inputTokens": 100, "outputTokens": 20}
        self.text = text

    def converse(self, **request):
        self.requests.append(request)
        return {
            "output": {"message": {"content": [{"text": self.text}]}},
            "usage": self.usage,
            "stopReason": "end_turn",
        }


def prompt(**kw):
    base = dict(agent="staff_swe", kind="author:hld", system=["charter", "persona"], instruction="do it")
    base.update(kw)
    return Prompt(**base)


class TestBedrockClient(unittest.TestCase):
    def test_cache_points_follow_system_and_context(self):
        fake = FakeBedrock()
        client = BedrockClient(mock_mode=False, boto_client=fake)
        client.complete(SONNET, prompt(context=["# BRD\n..."]))
        req = fake.requests[0]
        self.assertEqual(req["modelId"], SONNET.model_id)
        self.assertEqual(req["system"][-1], {"cachePoint": {"type": "default"}})
        content = req["messages"][0]["content"]
        self.assertEqual(content[0], {"text": "# BRD\n..."})
        self.assertIn("cachePoint", content[1])
        self.assertEqual(content[-1], {"text": "do it"})

    def test_one_hour_ttl_only_for_claude(self):
        client = BedrockClient(mock_mode=False, boto_client=FakeBedrock(), cache_ttl="1h")
        claude = client.build_request(SONNET, prompt())
        nova = client.build_request(NOVA, prompt())
        self.assertEqual(claude["system"][-1]["cachePoint"].get("ttl"), "1h")
        self.assertNotIn("ttl", nova["system"][-1]["cachePoint"])

    def test_caching_can_be_disabled(self):
        client = BedrockClient(mock_mode=False, boto_client=FakeBedrock(), enable_caching=False)
        req = client.build_request(SONNET, prompt(context=["doc"]))
        self.assertFalse(any("cachePoint" in b for b in req["system"]))
        self.assertFalse(any("cachePoint" in b for b in req["messages"][0]["content"]))

    def test_usage_counts_cache_reads_and_writes(self):
        fake = FakeBedrock(
            usage={"inputTokens": 50, "outputTokens": 10, "cacheReadInputTokens": 900, "cacheWriteInputTokens": 0}
        )
        client = BedrockClient(mock_mode=False, boto_client=fake)
        self.assertEqual(client.complete(SONNET, prompt()), "hello")
        u = client.usage.as_dict()
        self.assertEqual(u["total_tokens"], 960)
        self.assertAlmostEqual(u["cache_hit_rate"], 900 / 950, places=3)
        self.assertFalse(u["estimated"])
        self.assertEqual(client.usage_by_agent["staff_swe"].calls, 1)

    def test_budget_stops_further_calls(self):
        client = BedrockClient(mock_mode=False, boto_client=FakeBedrock(), budget_cap_tokens=100)
        client.complete(SONNET, prompt())  # uses 120
        with self.assertRaises(BudgetExceededError):
            client.complete(SONNET, prompt())


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)], "IsTruncated": False}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


class TestArtifactBus(unittest.TestCase):
    def test_versions_diffs_and_unsafe_names(self):
        with tempfile.TemporaryDirectory() as d:
            bus = ArtifactBus(d)
            bus.write_artifact("01_BRD.md", "v1", "staff_po")
            bus.write_artifact("01_BRD.md", "v1\nmore", "staff_po")
            meta = bus.list_artifacts()["01_BRD.md"]
            self.assertEqual(meta["version"], 2)
            self.assertTrue(meta["has_diff"])
            self.assertTrue(os.path.exists(os.path.join(d, ".history", "01_BRD.md.v2.diff")))
            with self.assertRaises(ValueError):
                bus.write_artifact("../escape.md", "x", "a")

    def test_s3_mirror_round_trip(self):
        s3 = FakeS3()
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            ArtifactBus(d1, mirror=S3Mirror("b", "proj", client=s3)).write_artifact("03_HLD.md", "# HLD", "staff_swe")
            self.assertIn("proj/03_HLD.md", s3.objects)
            fresh = ArtifactBus(d2, mirror=S3Mirror("b", "proj", client=s3))
            self.assertGreaterEqual(fresh.pull(), 2)
            self.assertEqual(fresh.read_artifact("03_HLD.md"), "# HLD")
            self.assertEqual(fresh.list_artifacts()["03_HLD.md"]["author"], "staff_swe")


class FakeDynamo:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[(Item["PK"]["S"], Item["SK"]["S"])] = Item

    def get_item(self, TableName, Key, **kw):
        item = self.items.get((Key["PK"]["S"], Key["SK"]["S"]))
        return {"Item": item} if item else {}

    def query(self, TableName, ExpressionAttributeValues, **kw):
        pk = ExpressionAttributeValues[":pk"]["S"]
        return {"Items": [v for (p, _), v in self.items.items() if p == pk]}


class TestStateStores(unittest.TestCase):
    def _exercise(self, store):
        self.assertFalse(store.gate_approved("ceo"))
        store.set_gate("ceo", "APPROVED", "ceo", "ok")
        store.set_phase("1_strategy", "DONE")
        store.set_meta("idea", {"text": "x"})
        self.assertTrue(store.gate_approved("ceo"))
        self.assertTrue(store.phase_done("1_strategy"))
        snap = store.snapshot()
        self.assertEqual(snap["gates"]["ceo"]["decision"], "APPROVED")
        self.assertEqual(snap["meta"]["idea"]["text"], "x")

    def test_local(self):
        with tempfile.TemporaryDirectory() as d:
            self._exercise(LocalStateStore(d))

    def test_dynamodb(self):
        fake = FakeDynamo()
        self._exercise(DynamoDBStateStore("t", "proj", "us-east-1", client=fake))
        self.assertIn(("PROJECT#proj", "GATE#ceo"), fake.items)


if __name__ == "__main__":
    unittest.main()
