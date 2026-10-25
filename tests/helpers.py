import json
import os
import shutil
import tempfile
import unittest

from startup_swarm.llm.mock import MockResponder
from startup_swarm.pipeline import StartupPipeline

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "startup_swarm", "config.example.yaml")


def review_json(verdict="APPROVED", blockers=None, score=4):
    return json.dumps(
        {
            "reviewer_role": "x",
            "target_document": "x",
            "verdict": verdict,
            "overall_score": score,
            "blockers": blockers or [],
            "suggestions": [],
        }
    )


def blocker(section="Scope", issue="Missing offline mode"):
    return {"section": section, "priority": "BLOCKER", "issue": issue, "suggested_fix": "Add it"}


def ceo_json(decision="APPROVED", changes=None):
    return json.dumps(
        {
            "decision": decision,
            "rationale": "because",
            "required_changes": changes or [],
            "strategic_guardrails": ["a", "b", "c"],
        }
    )


class TempWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="swarm-test-")
        self.ws = os.path.join(self.tmp, "ws")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pipeline(self, overrides=None, config=CONFIG, project="demo"):
        responder = MockResponder(overrides or {})
        p = StartupPipeline.build(
            stage="beta",
            config_path=config,
            mock=True,
            project=project,
            workspace=self.ws,
            mock_responder=responder,
        )
        return p, responder

    def write_config(self, extra_yaml: str) -> str:
        with open(CONFIG, "r", encoding="utf-8") as fh:
            base = fh.read()
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(base + "\n" + extra_yaml)
        return path


def kinds(responder):
    return [c.kind for c in responder.calls]
