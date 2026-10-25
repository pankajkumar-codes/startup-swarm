"""Unit tests: JSON extraction, review aggregation, file blocks, models, config."""

import os
import tempfile
import unittest

from startup_swarm.config import Settings
from startup_swarm.file_blocks import UnsafePathError, parse_file_blocks, safe_relpath, write_files
from startup_swarm.json_utils import JSONExtractionError, extract_json_object
from startup_swarm.models import Priority, ReviewComment, ReviewResult, SprintBacklog, Verdict
from startup_swarm.review_aggregator import ReviewAggregator
from tests.helpers import CONFIG


class TestJSONExtraction(unittest.TestCase):
    def test_plain_fenced_and_prose(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json_object('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(extract_json_object('Sure! {"a": {"b": "}"}} thanks'), {"a": {"b": "}"}})

    def test_failures_raise(self):
        for bad in ("", "no json here", '{"a": 1', "[1, 2]"):
            with self.assertRaises(JSONExtractionError):
                extract_json_object(bad)


def _review(role, blockers=(), score=4, verdict=Verdict.APPROVED):
    return ReviewResult(
        reviewer_role=role,
        target_document="HLD.md",
        verdict=verdict,
        overall_score=score,
        blockers=[ReviewComment(section=s, priority=Priority.BLOCKER, issue=i) for s, i in blockers],
    )


class TestReviewAggregator(unittest.TestCase):
    def test_duplicates_merge_and_keep_every_reviewer(self):
        agg = ReviewAggregator.aggregate(
            "LLD.md",
            [
                _review("staff_qa", [("Sec 3", "Missing test mocks")], 2, Verdict.REVISE),
                _review("staff_swe", [("sec 3", "Missing test mocks.")], 2, Verdict.REVISE),
            ],
        )
        self.assertEqual(agg.final_verdict, Verdict.REVISE)
        self.assertEqual(agg.blocking_items_count, 1)
        self.assertIn("staff_qa, staff_swe", agg.blockers[0])

    def test_clean_approval(self):
        agg = ReviewAggregator.aggregate("HLD.md", [_review("staff_em"), _review("lead_swe", score=5)])
        self.assertEqual(agg.final_verdict, Verdict.APPROVED)

    def test_low_scores_force_revision_even_without_blockers(self):
        agg = ReviewAggregator.aggregate("HLD.md", [_review("a", score=2), _review("b", score=2)])
        self.assertEqual(agg.final_verdict, Verdict.REVISE)

    def test_no_reviews_is_approved(self):
        self.assertEqual(ReviewAggregator.aggregate("x", []).final_verdict, Verdict.APPROVED)


class TestFileBlocks(unittest.TestCase):
    def test_parse_and_write(self):
        text = "NOTES: hi\n<<<FILE src/a.py>>>\nprint('a')\n<<<END FILE>>>\n<<<FILE b.md>>>\n# B\n<<<END FILE>>>"
        files = parse_file_blocks(text)
        self.assertEqual(set(files), {"src/a.py", "b.md"})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sorted(write_files(d, files)), ["b.md", "src/a.py"])
            self.assertTrue(os.path.exists(os.path.join(d, "src", "a.py")))

    def test_unsafe_paths_rejected(self):
        for bad in ("/etc/passwd", "../x.py", "a/../../x", "C:/x", ".git/config", ""):
            with self.assertRaises(UnsafePathError):
                safe_relpath(bad)
        with self.assertRaises(UnsafePathError):
            parse_file_blocks("<<<FILE ../../evil.py>>>\nx\n<<<END FILE>>>")


class TestBacklogOrdering(unittest.TestCase):
    def test_dependencies_come_first(self):
        backlog = SprintBacklog(
            milestone="m",
            tickets=[
                {"id": "T2", "title": "b", "assignee": "senior_swe", "priority": "P0", "dependencies": ["T1"]},
                {"id": "T1", "title": "a", "assignee": "Lead SWE", "priority": "P1"},
                {"id": "T3", "title": "c", "assignee": "staff-swe", "priority": "P2"},
            ],
        )
        self.assertEqual([t.id for t in backlog.ordered()], ["T1", "T2", "T3"])
        self.assertEqual(backlog.tickets[1].assignee.value, "lead_swe")
        self.assertIn("| T1 |", backlog.to_markdown())


class TestSettings(unittest.TestCase):
    def test_roles_resolve_to_tiers(self):
        s = Settings.load(CONFIG, "beta")
        self.assertIn("sonnet", s.model_for_role("staff_swe").model_id)
        self.assertIn("haiku", s.model_for_role("senior_swe").model_id)
        self.assertEqual(s.reviewers_for("03_HLD.md", []), ["staff_em", "lead_swe", "staff_qa", "staff_po"])

    def test_env_overrides_provider_and_backend(self):
        os.environ["SWARM_PROVIDER"] = "bedrock"
        os.environ["SWARM_BACKEND"] = "aws"
        try:
            s = Settings.load(CONFIG, "prod")
            self.assertEqual(s.provider_name, "bedrock")
            self.assertEqual(s.backend_name, "aws")
            self.assertEqual(s.model_for_role("staff_swe").model_id, "global.anthropic.claude-sonnet-5-5")
        finally:
            del os.environ["SWARM_PROVIDER"], os.environ["SWARM_BACKEND"]

    def test_model_ids_follow_provider(self):
        self.assertEqual(
            Settings.load(CONFIG, "beta", provider="anthropic").tier("workhorse").model_id, "claude-haiku-5-5"
        )
        with self.assertRaises(KeyError):
            Settings.load(CONFIG, "beta", provider="azure").tier("workhorse")
        self.assertEqual(Settings.load(None, "beta", provider="mock").tier("reasoner").model_id, "mock-model")

    def test_bad_stage_and_missing_file(self):
        with self.assertRaises(ValueError):
            Settings.load(CONFIG, "dev")
        with self.assertRaises(FileNotFoundError):
            Settings.load("/nope/config.yaml", "beta")


if __name__ == "__main__":
    unittest.main()
