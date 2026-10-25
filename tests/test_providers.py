"""Providers (request shape, caching, usage, budget), registries and the storage contracts.

SDK clients are replaced by small fakes so these run offline.
"""

import tempfile
import unittest
from types import SimpleNamespace

from startup_swarm.artifact_bus import ArtifactBus
from startup_swarm.backends import Backend, LocalBackend, get_backend
from startup_swarm.config import ModelSpec, Settings
from startup_swarm.llm import available_providers, make_client
from startup_swarm.llm.anthropic_api import AnthropicClient, FoundryClient, VertexClient
from startup_swarm.llm.base import BudgetExceededError, Prompt
from startup_swarm.llm.bedrock import BedrockClient
from startup_swarm.llm.mock import MockClient
from startup_swarm.state_store import LocalStateStore
from tests.helpers import CONFIG

SONNET_BR = ModelSpec("global.anthropic.claude-sonnet-5-5", 1000, 0.2)
NOVA = ModelSpec("global.amazon.nova-2-lite-v1:0", 500, 0.0)
SONNET_API = ModelSpec("claude-sonnet-5-5", 1000, 0.2)


def prompt(**kw):
    base = dict(agent="staff_swe", kind="author:hld", system=["charter", "persona"], instruction="do it")
    base.update(kw)
    return Prompt(**base)


class FakeBedrock:
    def __init__(self, usage=None):
        self.requests = []
        self.usage = usage or {"inputTokens": 100, "outputTokens": 20}

    def converse(self, **request):
        self.requests.append(request)
        return {"output": {"message": {"content": [{"text": "hello"}]}}, "usage": self.usage, "stopReason": "end_turn"}


class FakeAnthropic:
    def __init__(self, usage=None):
        self.requests = []
        self.usage = usage or {"input_tokens": 40, "output_tokens": 10}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi there")],
            usage=SimpleNamespace(**self.usage),
            stop_reason="end_turn",
        )


class TestBedrock(unittest.TestCase):
    def test_cache_points_follow_system_and_context(self):
        fake = FakeBedrock()
        BedrockClient(boto_client=fake).complete(SONNET_BR, prompt(context=["# BRD"]))
        req = fake.requests[0]
        self.assertEqual(req["system"][-1], {"cachePoint": {"type": "default"}})
        content = req["messages"][0]["content"]
        self.assertEqual(content[0], {"text": "# BRD"})
        self.assertIn("cachePoint", content[1])
        self.assertEqual(content[-1], {"text": "do it"})

    def test_one_hour_ttl_only_for_claude(self):
        client = BedrockClient(boto_client=FakeBedrock(), cache_ttl="1h")
        self.assertEqual(client.build_request(SONNET_BR, prompt())["system"][-1]["cachePoint"].get("ttl"), "1h")
        self.assertNotIn("ttl", client.build_request(NOVA, prompt())["system"][-1]["cachePoint"])

    def test_caching_can_be_disabled(self):
        req = BedrockClient(boto_client=FakeBedrock(), enable_caching=False).build_request(
            SONNET_BR, prompt(context=["doc"])
        )
        self.assertFalse(any("cachePoint" in b for b in req["system"] + req["messages"][0]["content"]))

    def test_usage_includes_cache_tokens(self):
        fake = FakeBedrock(
            usage={"inputTokens": 50, "outputTokens": 10, "cacheReadInputTokens": 900, "cacheWriteInputTokens": 0}
        )
        client = BedrockClient(boto_client=fake)
        self.assertEqual(client.complete(SONNET_BR, prompt()), "hello")
        self.assertEqual(client.usage.total, 960)
        self.assertAlmostEqual(client.usage.cache_hit_rate, 900 / 950, places=3)


class TestAnthropic(unittest.TestCase):
    def test_request_marks_cache_breakpoints(self):
        fake = FakeAnthropic()
        AnthropicClient(sdk_client=fake).complete(SONNET_API, prompt(context=["doc A", "doc B"]))
        req = fake.requests[0]
        self.assertEqual(req["model"], "claude-sonnet-5-5")
        self.assertEqual(req["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", req["system"][0])
        content = req["messages"][0]["content"]
        self.assertEqual(content[1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", content[-1])
        self.assertEqual(content[-1]["text"], "do it")

    def test_ttl_and_disable(self):
        req = AnthropicClient(sdk_client=FakeAnthropic(), cache_ttl="1h").build_request(SONNET_API, prompt())
        self.assertEqual(req["system"][-1]["cache_control"]["ttl"], "1h")
        req = AnthropicClient(sdk_client=FakeAnthropic(), enable_caching=False).build_request(SONNET_API, prompt())
        self.assertNotIn("cache_control", req["system"][-1])

    def test_usage_mapping(self):
        fake = FakeAnthropic(
            {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 700, "cache_creation_input_tokens": 30}
        )
        client = AnthropicClient(sdk_client=fake)
        self.assertEqual(client.complete(SONNET_API, prompt()), "hi there")
        u = client.usage.as_dict()
        self.assertEqual((u["cache_read_tokens"], u["cache_write_tokens"], u["total_tokens"]), (700, 30, 745))


class TestVertexAndFoundry(unittest.TestCase):
    def test_same_messages_format_with_provider_name(self):
        for cls in (VertexClient, FoundryClient):
            fake = FakeAnthropic()
            client = cls(sdk_client=fake)
            self.assertEqual(client.complete(SONNET_API, prompt(context=["doc"])), "hi there")
            self.assertEqual(fake.requests[0]["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})
            self.assertIn(client.provider_name, ("vertex", "foundry"))

    def test_foundry_requires_base_url(self):
        with self.assertRaisesRegex(ValueError, "base_url"):
            FoundryClient()

    def test_foundry_model_id_from_config(self):
        settings = Settings.load(CONFIG, "beta", provider="foundry")
        self.assertEqual(settings.model_for_role("staff_swe").model_id, "claude-sonnet-5-5")


class TestBudgetAndRegistry(unittest.TestCase):
    def test_budget_stops_further_calls(self):
        client = BedrockClient(boto_client=FakeBedrock(), budget_cap_tokens=100)
        client.complete(SONNET_BR, prompt())
        with self.assertRaises(BudgetExceededError):
            client.complete(SONNET_BR, prompt())

    def test_builtin_providers_and_unknown(self):
        self.assertTrue({"mock", "bedrock", "anthropic", "vertex", "foundry"} <= set(available_providers()))
        client = make_client(Settings.load(CONFIG, "beta", provider="mock"))
        self.assertIsInstance(client, MockClient)
        self.assertEqual(client.budget_cap_tokens, 600000)
        with self.assertRaises(ValueError):
            make_client(Settings.load(CONFIG, "beta", provider="nope"))

    def test_backends(self):
        self.assertIsInstance(get_backend("local"), LocalBackend)
        with self.assertRaisesRegex(ValueError, "startup-swarm-aws"):
            get_backend("aws")


class FakeMirror:
    """In-memory RemoteMirror: the contract a cloud backend must satisfy."""

    def __init__(self):
        self.objects = {}

    def put(self, name, body):
        self.objects[name] = body

    def pull_all(self, dest_dir):
        import os

        for name, body in self.objects.items():
            path = os.path.join(dest_dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(body)
        return len(self.objects)

    def describe(self):
        return "memory://"


class TestStorageContracts(unittest.TestCase):
    def test_bus_versions_and_mirror_round_trip(self):
        mirror = FakeMirror()
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            bus = ArtifactBus(d1, mirror=mirror)
            bus.write_artifact("01_BRD.md", "v1", "staff_po")
            bus.write_artifact("01_BRD.md", "v1\nmore", "staff_po")
            self.assertEqual(bus.list_artifacts()["01_BRD.md"]["version"], 2)
            with self.assertRaises(ValueError):
                bus.write_artifact("../escape.md", "x", "a")
            fresh = ArtifactBus(d2, mirror=mirror)
            self.assertGreater(fresh.pull(), 0)
            self.assertEqual(fresh.read_artifact("01_BRD.md"), "v1\nmore")
            self.assertEqual(fresh.list_artifacts()["01_BRD.md"]["version"], 2)

    def test_local_state_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = LocalStateStore(d)
            self.assertFalse(store.gate_approved("ceo"))
            store.set_gate("ceo", "APPROVED", "ceo", "ok")
            store.set_phase("1_strategy", "DONE")
            store.set_meta("idea", {"text": "x"})
            snap = store.snapshot()
            self.assertEqual(snap["gates"]["ceo"]["decision"], "APPROVED")
            self.assertTrue(store.phase_done("1_strategy"))

    def test_custom_backend_plugs_into_pipeline(self):
        from startup_swarm.pipeline import StartupPipeline

        mirror = FakeMirror()

        class MemoryBackend(Backend):
            name = "memory"

            def make_state(self, settings, project, workspace):
                return LocalStateStore(workspace)

            def make_mirror(self, settings, project):
                return mirror

        with tempfile.TemporaryDirectory() as d:
            p = StartupPipeline.build(
                config_path=CONFIG, workspace=d, provider="mock", backend_impl=MemoryBackend(), project="x"
            )
            self.assertEqual(p.run("idea").status, "COMPLETED")
            self.assertIn("03_HLD.md", mirror.objects)


if __name__ == "__main__":
    unittest.main()
