"""The engineering squad: implement one ticket end to end.

Flow per ticket:
  (Lead SWE contract, only when there is no LLD) -> owner implements -> QA writes tests
  -> Lead SWE code review (JSON) -> at most one fix pass -> files written to disk.

Used by Phase 5 of the full pipeline and by direct task mode (`startup-swarm task`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from orchestrator.file_blocks import (
    FILE_FORMAT_INSTRUCTIONS,
    parse_file_blocks,
    render_files_for_prompt,
    write_files,
)
from orchestrator.models import ReviewResult, Verdict

logger = logging.getLogger(__name__)

MAX_EXISTING_CONTEXT_CHARS = 40_000


@dataclass
class TicketSpec:
    id: str
    title: str
    description: str
    assignee: str
    acceptance_criteria: List[str] = field(default_factory=list)


@dataclass
class TicketResult:
    ticket_id: str
    files: List[str]
    review_verdict: str
    review_blockers: List[str]
    fixed_after_review: bool
    notes: str = ""

    def as_dict(self) -> Dict:
        return self.__dict__.copy()


def _ticket_text(t: TicketSpec) -> str:
    lines = [f"Ticket {t.id}: {t.title}", "", t.description or ""]
    if t.acceptance_criteria:
        lines += ["", "Acceptance criteria:"] + [f"- {c}" for c in t.acceptance_criteria]
    return "\n".join(lines).strip()


def _existing_context(existing: Dict[str, str]) -> str:
    if not existing:
        return ""
    rendered = render_files_for_prompt(existing)
    if len(rendered) <= MAX_EXISTING_CONTEXT_CHARS:
        return "# Existing repository files\n" + rendered
    listing = "\n".join(f"- {p}" for p in sorted(existing))
    return "# Existing repository files (too large to include, paths only)\n" + listing


def _notes(text: str) -> str:
    first = (text or "").strip().splitlines()[:1]
    return first[0][6:].strip() if first and first[0].startswith("NOTES:") else ""


class EngineeringTeam:
    def __init__(self, agents: Dict[str, object], rereview: bool = False):
        self.agents = agents
        self.rereview = rereview

    def implement(
        self,
        ticket: TicketSpec,
        output_dir: str,
        design_context: List[str],
        existing_files: Optional[Dict[str, str]] = None,
        need_contract: bool = False,
    ) -> TicketResult:
        lead = self.agents["lead_swe"]
        qa = self.agents["staff_qa"]
        owner = self.agents.get(ticket.assignee) or self.agents["senior_swe"]
        existing = _existing_context(existing_files or {})
        ticket_text = _ticket_text(ticket)

        context = [c for c in design_context if c]
        if existing:
            context.append(existing)

        if need_contract:
            contract = lead.author(
                "contract",
                "Write the implementation contract for this ticket: files to add or change, "
                "public signatures with types, error cases, and dependencies. Be brief and exact.\n\n" + ticket_text,
                context,
            )
            context = context + [f"# Contract from Lead SWE\n{contract}"]

        impl_text = owner.author(
            "files:impl",
            f"Implement this ticket.\n\n{ticket_text}\n\n{FILE_FORMAT_INSTRUCTIONS}",
            context,
        )
        impl_files = parse_file_blocks(impl_text)
        if not impl_files:
            raise ValueError(f"{owner.ROLE_KEY} returned no files for ticket {ticket.id}")

        test_text = qa.author(
            "files:tests",
            "Write pytest tests for this ticket's acceptance criteria against the implementation "
            f"below. Mock all network/cloud calls.\n\n{ticket_text}\n\n{FILE_FORMAT_INSTRUCTIONS}",
            context + ["# Implementation under test\n" + render_files_for_prompt(impl_files)],
        )
        files = {**impl_files, **parse_file_blocks(test_text)}

        review = self._code_review(lead, ticket, files)
        fixed = False
        if review.verdict != Verdict.APPROVED and review.blockers:
            punch = "\n".join(f"- {b.section}: {b.issue} Fix: {b.suggested_fix or ''}" for b in review.blockers)
            fix_text = owner.author(
                "files:impl",
                f"Code review requested changes for ticket {ticket.id}. Fix every blocker below and "
                f"nothing else. Return all changed files.\n\n{punch}\n\n{FILE_FORMAT_INSTRUCTIONS}",
                context + ["# Current change\n" + render_files_for_prompt(files)],
            )
            files.update(parse_file_blocks(fix_text))
            fixed = True
            if self.rereview:
                review = self._code_review(lead, ticket, files)

        written = write_files(output_dir, files)
        return TicketResult(
            ticket_id=ticket.id,
            files=written,
            review_verdict=review.verdict.value if not fixed or self.rereview else "FIXED_NOT_REREVIEWED",
            review_blockers=[f"{b.section}: {b.issue}" for b in review.blockers],
            fixed_after_review=fixed,
            notes=_notes(impl_text),
        )

    @staticmethod
    def _code_review(lead, ticket: TicketSpec, files: Dict[str, str]) -> ReviewResult:
        content = _ticket_text(ticket) + "\n\n# Change\n" + render_files_for_prompt(files)
        return lead.review(f"change:{ticket.id}", content, review_model=lead.model, max_tokens=1500, kind="code_review")


def report_markdown(ticket: TicketSpec, result: TicketResult) -> str:
    return (
        f"# {ticket.id}: {ticket.title}\n\n"
        f"- Owner: {ticket.assignee}\n"
        f"- Review: {result.review_verdict}\n"
        f"- Fixed after review: {result.fixed_after_review}\n"
        f"- Notes: {result.notes or '-'}\n\n"
        "## Files\n" + "\n".join(f"- `{f}`" for f in result.files) + "\n\n"
        "## Review blockers\n" + ("\n".join(f"- {b}" for b in result.review_blockers) or "- none") + "\n\n"
        "```json\n" + json.dumps(result.as_dict(), indent=2) + "\n```\n"
    )
