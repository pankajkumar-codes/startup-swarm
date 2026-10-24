"""Deterministic offline responses, keyed by the call's ``kind``.

Used when AWS_MOCK_MODE=1 or --mock. Lets you run the whole company, the CLI and the
test suite with no AWS account and no token spend. Tests can script specific answers
(for example a CEO rejection) with ``MockResponder(overrides={...})``.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Union

from orchestrator.bedrock_client import Prompt

Scripted = Union[str, List[str], Callable[[Prompt], str]]

_APPROVED_REVIEW = {
    "reviewer_role": "x",
    "target_document": "x",
    "verdict": "APPROVED",
    "overall_score": 4,
    "blockers": [],
    "suggestions": [
        {
            "section": "General",
            "priority": "SUGGESTION",
            "issue": "Add one concrete example per requirement.",
            "suggested_fix": "Append an example under each story.",
        }
    ],
}

_BACKLOG = {
    "milestone": "MVP",
    "tickets": [
        {
            "id": "T1",
            "title": "Core data models",
            "description": "Implement the Pydantic models defined in LLD section 2.",
            "assignee": "lead_swe",
            "priority": "P0",
            "complexity": "S",
            "dependencies": [],
            "acceptance_criteria": ["Models validate the documented fields"],
        },
        {
            "id": "T2",
            "title": "Caption generator",
            "description": "Implement generate_caption() per LLD section 3.",
            "assignee": "senior_swe",
            "priority": "P1",
            "complexity": "M",
            "dependencies": ["T1"],
            "acceptance_criteria": ["Returns 3 hooks and up to 15 hashtags"],
        },
    ],
}

_DOCS = {
    "author:vision": (
        "# CEO Vision\n\nTarget user: solo creators.\n\n"
        "## Guardrails\n1. Ship small.\n2. Offline tests.\n3. No secrets in code.\n"
    ),
    "author:research_questions": (
        "# Key questions for research\n1. Who uses the reference repo today?\n2. What is missing?\n"
    ),
    "author:research": (
        "# Market Research\n\n## Unique selling proposition\nOne-command deploy to your own AWS account.\n\n"
        "## Unknowns\n- Usage numbers.\n"
    ),
    "author:brd": (
        "# BRD\n\n## Press release\n...\n\n## User stories\n"
        "Given a topic, when I run `caption`, then I get 3 hooks.\n"
    ),
    "author:mocks": "# DX Mocks\n\n```text\n$ skill caption --topic 'AI'\nHook 1: ...\n```\n",
    "author:hld": "# HLD\n\n```mermaid\ngraph TD\n  CLI --> Core\n```\n",
    "author:lld": "# LLD\n\n## 2. Models\n`class CaptionRequest(BaseModel): topic: str`\n",
    "contract": "# Contract\n\n`def generate_caption(topic: str) -> list[str]`\n",
}

_IMPL = """NOTES: mock implementation
<<<FILE src/app/core.py>>>
\"\"\"Core module (mock output).\"\"\"


def generate_caption(topic: str) -> list[str]:
    \"\"\"Return three hook lines for a topic.\"\"\"
    if not topic.strip():
        raise ValueError("topic must not be empty")
    return [f"{topic}: hook {i}" for i in range(1, 4)]
<<<END FILE>>>
"""

_TESTS = """<<<FILE tests/test_core.py>>>
import pytest

from src.app.core import generate_caption


def test_three_hooks():
    assert len(generate_caption("AI")) == 3


def test_empty_topic_rejected():
    with pytest.raises(ValueError):
        generate_caption("  ")
<<<END FILE>>>
"""


class MockResponder:
    def __init__(self, overrides: Dict[str, Scripted] = None):
        self.overrides: Dict[str, Scripted] = dict(overrides or {})
        self.calls: List[Prompt] = []

    def respond(self, prompt: Prompt) -> str:
        self.calls.append(prompt)
        scripted = self.overrides.get(prompt.kind)
        if scripted is not None:
            if callable(scripted):
                return scripted(prompt)
            if isinstance(scripted, list):
                return scripted.pop(0) if len(scripted) > 1 else scripted[0]
            return scripted

        kind = prompt.kind
        if kind in ("review", "code_review"):
            return json.dumps(_APPROVED_REVIEW)
        if kind == "ceo_gate":
            return json.dumps(
                {
                    "decision": "APPROVED",
                    "rationale": "Clear user, clear gap, small enough to ship.",
                    "required_changes": [],
                    "strategic_guardrails": ["Ship the MVP first", "Offline tests", "No secrets"],
                }
            )
        if kind == "backlog":
            return json.dumps(_BACKLOG)
        if kind == "scope":
            return json.dumps(
                {
                    "in_scope": True,
                    "summary": "Small, well-specified change.",
                    "assignee": "senior_swe",
                    "complexity": "S",
                    "risks": [],
                }
            )
        if kind == "files:impl":
            return _IMPL
        if kind == "files:tests":
            return _TESTS
        if kind.startswith("revise:"):
            return (
                _DOCS.get("author:" + kind.split(":", 1)[1], "# Revised\n")
                + "\n## Changes in this revision\n- Addressed review.\n"
            )
        return _DOCS.get(kind, f"# Mock output for {kind}\n")
