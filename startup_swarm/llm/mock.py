"""Deterministic offline responses, keyed by the call's ``kind``.

Used with --mock or SWARM_MOCK=1 (provider "mock"). Lets you run the whole company,
the CLI and the test suite with no cloud account and no token spend. Tests can script specific answers
(for example a CEO rejection) with ``MockResponder(overrides={...})``.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Union

from startup_swarm.config import ModelSpec
from startup_swarm.llm.base import LLMClient, Prompt, SendResult

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
            "title": "Core feature",
            "description": "Implement run_core() per LLD section 3.",
            "assignee": "senior_swe",
            "priority": "P1",
            "complexity": "M",
            "dependencies": ["T1"],
            "acceptance_criteria": ["Returns three result lines for a non-empty input"],
        },
    ],
}

_DOCS = {
    "author:vision": (
        "# CEO Vision\n\nTarget user: the founder who gave the idea.\n\n"
        "## Guardrails\n1. Ship small.\n2. Offline tests.\n3. No secrets in code.\n"
    ),
    "author:research_questions": (
        "# Key questions for research\n1. Who uses the reference repo today?\n2. What is missing?\n"
    ),
    "author:research": (
        "# Market Research\n\n## Unique selling proposition\nPlaceholder positioning.\n\n"
        "## Unknowns\n- Usage numbers.\n"
    ),
    "author:brd": (
        "# BRD\n\n## Press release\nPlaceholder press release.\n\n## User stories\n"
        "Given an input, when I run the tool, then I get three result lines.\n"
    ),
    "author:mocks": "# DX Mocks\n\n```text\n$ tool run --input 'example'\nResult 1: ...\n```\n",
    "author:hld": "# HLD\n\n```mermaid\ngraph TD\n  CLI --> Core\n```\n",
    "author:lld": "# LLD\n\n## 2. Models\n`class Request(BaseModel): text: str`\n",
    "contract": "# Contract\n\n`def run_core(text: str) -> list[str]`\n",
}

_IMPL = """NOTES: mock implementation
<<<FILE src/app/core.py>>>
\"\"\"Core module (mock output).\"\"\"


def run_core(text: str) -> list[str]:
    \"\"\"Return three placeholder result lines.\"\"\"
    if not text.strip():
        raise ValueError("text must not be empty")
    return [f"{text}: result {i}" for i in range(1, 4)]
<<<END FILE>>>
"""

_TESTS = """<<<FILE tests/test_core.py>>>
import pytest

from src.app.core import run_core


def test_three_results():
    assert len(run_core("AI")) == 3


def test_empty_input_rejected():
    with pytest.raises(ValueError):
        run_core("  ")
<<<END FILE>>>
"""


_IDEA_RE = re.compile(r"Founder's idea:\n(.+?)(?:\n\n|$)", re.S)


def _banner(prompt: Prompt) -> str:
    """Make it obvious that offline output is placeholder text, and echo the founder's idea."""
    text = "\n".join(list(prompt.context) + [prompt.instruction])
    match = _IDEA_RE.search(text)
    idea = f" Idea: {match.group(1).strip()[:200]}" if match else ""
    return f"> MOCK OUTPUT: placeholder text, no model was called.{idea}\n\n"


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
        doc = _DOCS.get(kind, f"# Mock output for {kind}\n")
        return _banner(prompt) + doc if kind.startswith("author:") else doc


class MockClient(LLMClient):
    """Offline provider. Token counts are estimates (characters / 4)."""

    provider_name = "mock"
    estimated_usage = True

    def __init__(self, responder: "MockResponder" = None, **kwargs):
        super().__init__(**kwargs)
        self.responder = responder or MockResponder()

    @property
    def mock_mode(self) -> bool:
        return True

    def _send(self, model: ModelSpec, prompt: Prompt) -> SendResult:
        text = self.responder.respond(prompt)
        chars_in = sum(len(s) for s in prompt.system + prompt.context) + len(prompt.instruction)
        return text, {"input": chars_in // 4, "output": len(text) // 4}, "end_turn"
