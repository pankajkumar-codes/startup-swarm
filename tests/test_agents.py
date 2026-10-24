"""Agent behaviour: fail-closed reviews and structured-output retries."""

import unittest

from agents import AGENT_CLASSES, StaffQAAgent, StructuredOutputError
from agents.charter import ORG_CHARTER
from orchestrator.bedrock_client import BedrockClient
from orchestrator.config import ModelSpec
from orchestrator.mock_responder import MockResponder
from orchestrator.models import CEODecision, Verdict
from tests.helpers import blocker, ceo_json, review_json

MODEL = ModelSpec("global.anthropic.claude-haiku-5-5", 1000, 0.0)


def agent(cls, overrides):
    responder = MockResponder(overrides)
    return cls(MODEL, BedrockClient(mock_mode=True, mock_responder=responder)), responder


class TestAgents(unittest.TestCase):
    def test_ten_roles_share_the_charter_prefix(self):
        self.assertEqual(len(AGENT_CLASSES), 10)
        for cls in AGENT_CLASSES.values():
            a = cls(MODEL, BedrockClient(mock_mode=True))
            self.assertEqual(a.system_blocks()[0], ORG_CHARTER)

    def test_review_fails_closed_on_garbage(self):
        qa, responder = agent(StaffQAAgent, {"review": "I think it looks great!"})
        result = qa.review("03_HLD.md", "# HLD")
        self.assertEqual(result.verdict, Verdict.REVISE)
        self.assertTrue(result.parse_failed)
        self.assertEqual(len(result.blockers), 1)
        self.assertEqual(len(responder.calls), 2)  # one retry

    def test_review_with_blockers_cannot_be_approved(self):
        qa, _ = agent(StaffQAAgent, {"review": review_json("APPROVED", [blocker()])})
        self.assertEqual(qa.review("x.md", "x").verdict, Verdict.REVISE)

    def test_review_shares_document_prefix_across_reviewers(self):
        qa, r1 = agent(StaffQAAgent, {})
        from agents import StaffEMAgent

        em = StaffEMAgent(MODEL, qa.client)
        qa.review("03_HLD.md", "# HLD body")
        em.review("03_HLD.md", "# HLD body")
        a, b = r1.calls
        self.assertEqual(a.system, b.system)
        self.assertEqual(a.context, b.context)
        self.assertNotEqual(a.instruction, b.instruction)

    def test_structured_output_retries_then_raises(self):
        from agents import CEOAgent

        ceo, responder = agent(CEOAgent, {"ceo_gate": ["not json", ceo_json("APPROVED")]})
        decision = ceo.ask_structured("ceo_gate", "decide", CEODecision)
        self.assertEqual(decision.decision.value, "APPROVED")
        self.assertEqual(len(responder.calls), 2)

        ceo2, _ = agent(CEOAgent, {"ceo_gate": '{"decision": "MAYBE"}'})
        with self.assertRaises(StructuredOutputError):
            ceo2.ask_structured("ceo_gate", "decide", CEODecision)


if __name__ == "__main__":
    unittest.main()
