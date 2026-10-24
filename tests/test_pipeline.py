"""End-to-end pipeline behaviour in mock mode: gates, resume, budget, revisions."""

import json
import os
import unittest

from orchestrator.models import Verdict
from tests.helpers import TempWorkspaceTest, blocker, ceo_json, kinds, review_json

DOCS = [
    "00_CEO_VISION.md",
    "01a_RESEARCH_QUESTIONS.md",
    "01_MARKET_RESEARCH.md",
    "01_BRD.md",
    "02_MOCKS.md",
    "02_CEO_DECISION.md",
    "03_HLD.md",
    "04_LLD.md",
    "05_SPRINT_BACKLOG.md",
    "07_BUILD_REPORT.md",
]


class TestFullRun(TempWorkspaceTest):
    def test_complete_run_produces_docs_and_code(self):
        p, responder = self.pipeline()
        outcome = p.run("Test idea")
        self.assertEqual(outcome.status, "COMPLETED", outcome.message)
        for doc in DOCS:
            self.assertTrue(p.bus.exists(doc), doc)
        gen = os.path.join(self.ws, "generated_project")
        self.assertTrue(os.path.exists(os.path.join(gen, "src", "app", "core.py")))
        self.assertTrue(os.path.exists(os.path.join(gen, "tests", "test_core.py")))
        self.assertTrue(p.state.gate_approved("ceo"))
        # every author doc goes through scoped reviews
        self.assertTrue(p.bus.exists("reviews/03_HLD.md.json"))
        reviewers = [r["reviewer_role"] for r in json.loads(p.bus.read_artifact("reviews/03_HLD.md.json"))]
        self.assertEqual(reviewers, ["staff_em", "lead_swe", "staff_qa", "staff_po"])

    def test_rerun_resumes_without_new_model_calls(self):
        p, _ = self.pipeline()
        self.assertEqual(p.run("Test idea").status, "COMPLETED")
        p2, responder2 = self.pipeline()
        self.assertEqual(p2.run().status, "COMPLETED")
        self.assertEqual(responder2.calls, [])

    def test_different_idea_for_same_project_is_refused(self):
        p, _ = self.pipeline()
        p.run("Idea A")
        p2, _ = self.pipeline()
        outcome = p2.run("Idea B")
        self.assertEqual(outcome.status, "FAILED")
        self.assertIn("different idea", outcome.message)


class TestCEOGate(TempWorkspaceTest):
    def test_rejection_blocks_architecture(self):
        p, responder = self.pipeline({"ceo_gate": ceo_json("REVISION_REQUIRED", ["Narrow the scope"])})
        outcome = p.run("Test idea")
        self.assertEqual(outcome.status, "BLOCKED_ON_CEO_APPROVAL")
        self.assertFalse(p.bus.exists("03_HLD.md"))
        self.assertFalse(p.state.gate_approved("ceo"))
        k = kinds(responder)
        self.assertEqual(k.count("ceo_gate"), 2)  # max_ceo_gate_attempts
        self.assertIn("revise:brd", k)  # PO addressed CEO changes between attempts
        self.assertIn("revise:mocks", k)
        self.assertNotIn("author:hld", k)

    def test_second_attempt_can_approve(self):
        p, _ = self.pipeline({"ceo_gate": [ceo_json("REVISION_REQUIRED", ["x"]), ceo_json("APPROVED")]})
        self.assertEqual(p.run("Test idea").status, "COMPLETED")

    def test_founder_override_then_resume(self):
        p, _ = self.pipeline({"ceo_gate": ceo_json("REVISION_REQUIRED")})
        self.assertEqual(p.run("Test idea").status, "BLOCKED_ON_CEO_APPROVAL")
        p.approve_gate("ceo", "ship it")
        p2, responder = self.pipeline()
        self.assertEqual(p2.run().status, "COMPLETED")
        self.assertNotIn("author:vision", kinds(responder))  # earlier phases not repeated
        self.assertEqual(p2.state.gate("ceo")["by"], "founder")

    def test_founder_approval_required_pauses(self):
        cfg = self.write_config("process:\n  founder_approval_required: true\n")
        p, _ = self.pipeline(config=cfg)
        self.assertEqual(p.run("Test idea").status, "AWAITING_FOUNDER_APPROVAL")
        self.assertFalse(p.bus.exists("03_HLD.md"))
        p.approve_gate("founder")
        p2, _ = self.pipeline(config=cfg)
        self.assertEqual(p2.run().status, "COMPLETED")


class TestReviewsAndLimits(TempWorkspaceTest):
    def test_blockers_trigger_exactly_one_revision(self):
        p, responder = self.pipeline({"review": review_json("REVISE", [blocker()], score=2)})
        p.run("Test idea")
        k = kinds(responder)
        for doc in ("brd", "mocks", "hld", "lld"):
            self.assertEqual(k.count(f"revise:{doc}"), 1, doc)
        self.assertEqual(p.bus.list_artifacts()["01_BRD.md"]["version"], 2)

    def test_rereview_escalates_unresolved_blockers(self):
        cfg = self.write_config("process:\n  rereview_after_revision: true\n")
        p, _ = self.pipeline({"review": review_json("REVISE", [blocker()], score=2)}, config=cfg)
        p.run("Test idea")
        self.assertIn("01_BRD.md", p.bus.read_artifact("99_OPEN_ISSUES.md"))

    def test_budget_pauses_and_resume_continues(self):
        cfg = self.write_config("token_controls:\n  budget_cap_tokens: 3000\n")
        p, _ = self.pipeline(config=cfg)
        outcome = p.run("Test idea")
        self.assertEqual(outcome.status, "PAUSED_BUDGET")
        p2, responder = self.pipeline()  # default budget
        self.assertEqual(p2.run().status, "COMPLETED")
        self.assertNotIn("author:vision", kinds(responder))

    def test_ticket_limit_gives_partial_then_completes(self):
        cfg = self.write_config("process:\n  max_tickets_per_run: 1\n")
        p, _ = self.pipeline(config=cfg)
        outcome = p.run("Test idea")
        self.assertEqual(outcome.status, "PARTIAL")
        self.assertTrue(p.state.phase_done("ticket:T1"))
        self.assertFalse(p.state.phase_done("ticket:T2"))
        p2, _ = self.pipeline(config=cfg)
        self.assertEqual(p2.run().status, "COMPLETED")

    def test_code_review_blocker_triggers_fix_pass(self):
        p, responder = self.pipeline({"code_review": review_json("REVISE", [blocker("core.py", "No input check")])})
        self.assertEqual(p.run("Test idea").status, "COMPLETED")
        self.assertEqual(kinds(responder).count("files:impl"), 4)  # 2 tickets x (impl + fix)
        report = p.bus.read_artifact("tickets/T1.md")
        self.assertIn("FIXED_NOT_REREVIEWED", report)


class TestTaskMode(TempWorkspaceTest):
    def test_direct_task_skips_front_office(self):
        p, responder = self.pipeline()
        target = os.path.join(self.tmp, "repo")
        outcome = p.run_task("Add retry with backoff", {"src/app/core.py": "x = 1\n"}, target_dir=target)
        self.assertEqual(outcome.status, "COMPLETED", outcome.message)
        k = kinds(responder)
        self.assertEqual(k[:2], ["scope", "contract"])
        self.assertNotIn("author:vision", k)
        self.assertNotIn("ceo_gate", k)
        self.assertTrue(os.path.exists(os.path.join(target, "src", "app", "core.py")))

    def test_em_can_reject(self):
        reject = json.dumps(
            {
                "in_scope": False,
                "summary": "no",
                "assignee": "senior_swe",
                "complexity": "L",
                "out_of_scope_reason": "Needs a product decision first",
            }
        )
        p, responder = self.pipeline({"scope": reject})
        outcome = p.run_task("Rewrite everything in Rust")
        self.assertEqual(outcome.status, "REJECTED_BY_EM")
        self.assertEqual(kinds(responder), ["scope"])


class TestStatusAndEntrypoints(TempWorkspaceTest):
    def test_status_render(self):
        from orchestrator import status

        p, _ = self.pipeline({"ceo_gate": ceo_json("REVISION_REQUIRED")})
        p.run("Test idea")
        data = status.collect(p)
        text = status.render(data)
        self.assertIn("[!] CEO gate: BLOCKED", text)
        self.assertIn("approve-gate ceo", text)
        self.assertLess(data["progress_pct"], 100)

    def test_cli_round_trip(self):
        from orchestrator.cli import main

        args = ["--mock", "--workspace", self.ws]
        self.assertEqual(main(["run", *args, "--idea", "CLI idea"]), 0)
        self.assertEqual(main(["status", *args, "--json"]), 0)
        self.assertEqual(main(["task", "Add a flag", *args]), 0)

    def test_agentcore_handler(self):
        import importlib.util

        from tests.helpers import REPO

        spec = importlib.util.spec_from_file_location("ac_main", os.path.join(REPO, "agentcore_app", "main.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        os.environ["AWS_MOCK_MODE"] = "1"
        try:
            res = mod.handle({"action": "run", "project": "ac-test", "idea": "x"})
            self.assertEqual(res["status"], "COMPLETED")
            st = mod.handle({"action": "status", "project": "ac-test"})
            self.assertEqual(st["progress_pct"], 100)
            self.assertEqual(mod.handle({"action": "nope"})["status"], "FAILED")
        finally:
            del os.environ["AWS_MOCK_MODE"]
            import shutil
            import tempfile

            shutil.rmtree(os.path.join(tempfile.gettempdir(), "swarm"), ignore_errors=True)

    def test_verdict_enum_values(self):
        self.assertEqual(Verdict.REVISE.value, "REVISE")


class TestPrepareAgentCore(TempWorkspaceTest):
    def test_upsert_runtime_entry(self):
        import subprocess
        import sys

        from tests.helpers import REPO

        proj = os.path.join(self.tmp, "SwarmRuntime")
        os.makedirs(os.path.join(proj, "agentcore"))
        with open(os.path.join(proj, "agentcore", "agentcore.json"), "w") as fh:
            json.dump({"name": "SwarmRuntime", "runtimes": [{"name": "agent", "build": "CodeZip"}]}, fh)
        outputs = os.path.join(self.tmp, "out.json")
        with open(outputs, "w") as fh:
            json.dump(
                {
                    "StartupOrchestratorStack-Beta": {
                        "ArtifactBucketName": "b",
                        "StateTableName": "t",
                        "AgentRuntimeRoleArn": "arn:aws:iam::1:role/r",
                    }
                },
                fh,
            )
        cmd = [sys.executable, os.path.join(REPO, "scripts", "prepare_agentcore.py")]
        cmd += ["--stage", "beta", "--project-dir", proj, "--outputs", outputs]
        for _ in range(2):  # idempotent
            subprocess.run(cmd, check=True, capture_output=True)
        spec = json.load(open(os.path.join(proj, "agentcore", "agentcore.json")))
        names = [r["name"] for r in spec["runtimes"]]
        self.assertEqual(names, ["agent", "startup_swarm_beta"])
        rt = spec["runtimes"][1]
        self.assertEqual(rt["executionRoleArn"], "arn:aws:iam::1:role/r")
        self.assertIn({"name": "S3_ARTIFACT_BUCKET", "value": "b"}, rt["envVars"])
        code = os.path.join(proj, "app", "startup_swarm_beta")
        for f in ("main.py", "pyproject.toml", "config.yaml", "orchestrator/pipeline.py", "agents/ceo.py"):
            self.assertTrue(os.path.exists(os.path.join(code, f)), f)
        self.assertTrue(os.path.exists(os.path.join(code, "orchestrator", "templates", "03_HLD_TEMPLATE.md")))


if __name__ == "__main__":
    unittest.main()
