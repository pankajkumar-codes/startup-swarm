"""Pydantic schemas for every structured hand-off between agents."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Stage(str, Enum):
    BETA = "beta"
    PROD = "prod"


class Priority(str, Enum):
    BLOCKER = "BLOCKER"
    SUGGESTION = "SUGGESTION"
    NIT = "NIT"


class Verdict(str, Enum):
    APPROVED = "APPROVED"
    REVISE = "REVISE"
    BLOCKED = "BLOCKED"


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------
class ReviewComment(BaseModel):
    section: str = Field(..., max_length=200)
    priority: Priority
    issue: str = Field(..., max_length=600)
    suggested_fix: Optional[str] = Field(None, max_length=600)


class ReviewResult(BaseModel):
    reviewer_role: str
    target_document: str
    verdict: Verdict
    overall_score: int = Field(..., ge=1, le=5)
    blockers: List[ReviewComment] = Field(default_factory=list)
    suggestions: List[ReviewComment] = Field(default_factory=list)
    parse_failed: bool = False


class AggregatedReview(BaseModel):
    target_document: str
    final_verdict: Verdict
    total_reviews: int
    blocking_items_count: int
    blockers: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)

    def punchlist_markdown(self) -> str:
        lines: List[str] = []
        if self.blockers:
            lines.append("### Blocking issues (must fix)")
            lines += [f"{i}. {b}" for i, b in enumerate(self.blockers, 1)]
        if self.suggestions:
            lines.append("")
            lines.append("### Suggestions (fix if cheap)")
            lines += [f"- {s}" for s in self.suggestions]
        return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# CEO gate
# --------------------------------------------------------------------------
class CEODecisionValue(str, Enum):
    APPROVED = "APPROVED"
    REVISION_REQUIRED = "REVISION_REQUIRED"


class CEODecision(BaseModel):
    decision: CEODecisionValue
    rationale: str = Field(..., max_length=1500)
    required_changes: List[str] = Field(default_factory=list)
    strategic_guardrails: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Planning and engineering
# --------------------------------------------------------------------------
class Assignee(str, Enum):
    STAFF_SWE = "staff_swe"
    LEAD_SWE = "lead_swe"
    SENIOR_SWE = "senior_swe"


class TaskTicket(BaseModel):
    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,32}$")
    title: str = Field(..., max_length=200)
    description: str = Field("", max_length=2000)
    assignee: Assignee
    priority: str = Field(..., pattern=r"^P[0-2]$")
    complexity: str = Field("M", pattern=r"^(S|M|L)$")
    dependencies: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)

    @field_validator("assignee", mode="before")
    @classmethod
    def _normalise_assignee(cls, value):
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_").replace("-", "_")
        return value


class SprintBacklog(BaseModel):
    milestone: str
    tickets: List[TaskTicket] = Field(default_factory=list)

    def ordered(self) -> List[TaskTicket]:
        """Priority order, then dependency order (dependencies first)."""
        by_id = {t.id: t for t in self.tickets}
        ordered: List[TaskTicket] = []
        seen: set = set()

        def visit(ticket: TaskTicket, stack: set) -> None:
            if ticket.id in seen or ticket.id in stack:
                return
            stack.add(ticket.id)
            for dep in ticket.dependencies:
                if dep in by_id:
                    visit(by_id[dep], stack)
            stack.discard(ticket.id)
            seen.add(ticket.id)
            ordered.append(ticket)

        for t in sorted(self.tickets, key=lambda t: (t.priority, t.id)):
            visit(t, set())
        return ordered

    def to_markdown(self) -> str:
        lines = [f"# Sprint Backlog: {self.milestone}", ""]
        lines.append("| ID | Priority | Size | Owner | Title | Depends on |")
        lines.append("|---|---|---|---|---|---|")
        for t in self.ordered():
            deps = ", ".join(t.dependencies) or "-"
            lines.append(f"| {t.id} | {t.priority} | {t.complexity} | {t.assignee.value} | {t.title} | {deps} |")
        for t in self.ordered():
            lines += ["", f"## {t.id}: {t.title}", t.description or ""]
            if t.acceptance_criteria:
                lines.append("")
                lines.append("Acceptance criteria:")
                lines += [f"- {c}" for c in t.acceptance_criteria]
        return "\n".join(lines).strip() + "\n"


class EMScope(BaseModel):
    """Staff EM's scoping of a direct engineering task."""

    in_scope: bool
    summary: str = Field(..., max_length=1000)
    assignee: Assignee
    complexity: str = Field("M", pattern=r"^(S|M|L)$")
    risks: List[str] = Field(default_factory=list)
    out_of_scope_reason: Optional[str] = None

    @field_validator("assignee", mode="before")
    @classmethod
    def _normalise_assignee(cls, value):
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_").replace("-", "_")
        return value
