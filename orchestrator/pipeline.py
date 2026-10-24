"""The startup lifecycle: phases, review cycles and approval gates.

Process rules enforced here (not just described in prompts):
- Phases run in order and are checkpointed in the state store; re-running resumes
  after the last completed phase instead of paying for it again.
- Every document gets one round of domain-scoped JSON reviews, an aggregated
  punchlist, and at most ``process.max_review_rounds`` author revisions.
- Architecture (Phase 3) cannot start unless the CEO gate record is APPROVED. If
  ``founder_approval_required`` is set, it also needs the founder gate.
- A token budget stops the run cleanly; the next run resumes where it stopped.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from agents import AGENT_CLASSES, StructuredOutputError
from orchestrator.artifact_bus import ArtifactBus, S3Mirror
from orchestrator.bedrock_client import BedrockClient, BudgetExceededError
from orchestrator.config import Settings
from orchestrator.engineering import EngineeringTeam, TicketSpec, report_markdown
from orchestrator.models import (
    AggregatedReview,
    CEODecision,
    CEODecisionValue,
    EMScope,
    SprintBacklog,
    Verdict,
)
from orchestrator.reference_repo import fetch_reference_material
from orchestrator.review_aggregator import ReviewAggregator
from orchestrator.state_store import DynamoDBStateStore, LocalStateStore, StateStore

logger = logging.getLogger("startup_swarm")

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
GENERATED_DIR = "generated_project"

PHASES = [
    ("1_strategy", "Strategy & market research"),
    ("2a_brd", "BRD (Product Owner)"),
    ("2b_mocks", "DX mocks (Product Designer)"),
    ("gate_ceo", "CEO gate"),
    ("gate_founder", "Founder approval"),
    ("3a_hld", "HLD (Staff SWE)"),
    ("3b_lld", "LLD (Lead SWE)"),
    ("4_backlog", "Sprint backlog (TPM + EM + Staff SWE)"),
    ("5_build", "Build & verify (engineers + QA)"),
]


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "project"


def _template(name: str) -> str:
    path = os.path.join(TEMPLATES_DIR, name)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return f"# Required structure (template {name})\n" + fh.read()


@dataclass
class RunOutcome:
    status: str
    message: str
    phase: Optional[str] = None

    def as_dict(self) -> Dict[str, str]:
        return {"status": self.status, "message": self.message, "phase": self.phase or ""}


class StartupPipeline:
    def __init__(
        self,
        settings: Settings,
        client: BedrockClient,
        bus: ArtifactBus,
        state: StateStore,
        project: str,
        reference_fetcher: Callable[[str], str] = fetch_reference_material,
    ):
        self.settings = settings
        self.client = client
        self.bus = bus
        self.state = state
        self.project = project
        self.fetch_reference = reference_fetcher
        self.agents = {key: cls(settings.model_for_role(key), client) for key, cls in AGENT_CLASSES.items()}
        self.engineering = EngineeringTeam(self.agents, rereview=bool(settings.process.get("rereview_after_revision")))

    # ------------------------------------------------------------------
    # Construction from CLI / runtime arguments
    # ------------------------------------------------------------------
    @classmethod
    def build(
        cls,
        stage: str = "beta",
        config_path: Optional[str] = "config.yaml",
        mock: bool = False,
        project: Optional[str] = None,
        workspace: Optional[str] = None,
        mock_responder=None,
        boto_session=None,
    ) -> "StartupPipeline":
        settings = Settings.load(config_path, stage)
        project = slugify(project or settings.project.get("name", "project"))
        workspace = workspace or os.path.join("artifacts", stage, project)

        tokens = settings.tokens
        client = BedrockClient(
            region=settings.region,
            mock_mode=True if mock else None,
            enable_caching=bool(tokens.get("enable_prompt_caching", True)),
            cache_ttl=str(tokens.get("cache_ttl", "5m")),
            budget_cap_tokens=int(tokens.get("budget_cap_tokens") or 0) or None,
            mock_responder=mock_responder,
            boto_client=boto_session.client("bedrock-runtime") if boto_session else None,
        )

        mirror = None
        state: StateStore = LocalStateStore(workspace)
        if not client.mock_mode:
            if settings.s3_bucket:
                s3 = boto_session.client("s3") if boto_session else None
                mirror = S3Mirror(settings.s3_bucket, project, client=s3)
            if settings.state_table:
                ddb = boto_session.client("dynamodb") if boto_session else None
                state = DynamoDBStateStore(settings.state_table, project, settings.region, client=ddb)
        bus = ArtifactBus(workspace, stage=stage, mirror=mirror)
        return cls(settings, client, bus, state, project)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _read(self, name: str) -> str:
        return self.bus.read_artifact(name) if self.bus.exists(name) else ""

    def _phase_skippable(self, phase_id: str, outputs: List[str]) -> bool:
        return self.state.phase_done(phase_id) and all(self.bus.exists(o) for o in outputs)

    def _save_usage(self) -> None:
        prev = (self.state.meta("usage_total") or {}).get("total_tokens", 0)
        run = self.client.usage.as_dict()
        self.state.set_meta("usage_last_run", run)
        self.state.set_meta(
            "usage_total",
            {"total_tokens": prev + run["total_tokens"], "estimated": run["estimated"]},
        )
        by_agent = {k: v.as_dict() for k, v in self.client.usage_by_agent.items()}
        self.bus.write_artifact(
            "usage_last_run.json", json.dumps({"run": run, "by_agent": by_agent}, indent=2), "orchestrator"
        )

    def _reviewers(self, doc: str, default: List[str]) -> List[object]:
        keys = self.settings.reviewers_for(doc, default)
        return [self.agents[k] for k in keys if k in self.agents]

    def _review(self, doc: str, content: str, reviewers: List[object]) -> AggregatedReview:
        model = self.settings.review_model()
        max_out = int(self.settings.tokens.get("max_review_output_tokens", 800))
        results = [r.review(doc, content, review_model=model, max_tokens=max_out) for r in reviewers]
        agg = ReviewAggregator.aggregate(doc, results)
        self.bus.write_artifact(
            f"reviews/{doc}.json",
            json.dumps([r.model_dump(mode="json") for r in results], indent=2),
            "review_aggregator",
        )
        self.bus.write_artifact(
            f"reviews/{doc}.punchlist.md",
            f"# Review punchlist: {doc}\n\nVerdict: {agg.final_verdict.value}\n\n{agg.punchlist_markdown()}\n",
            "review_aggregator",
        )
        logger.info("%s review: %s (%d blockers)", doc, agg.final_verdict.value, agg.blocking_items_count)
        return agg

    def _document_cycle(
        self,
        doc: str,
        author,
        kind: str,
        instruction: str,
        context: List[str],
        reviewers: List[object],
    ) -> str:
        """Author -> scoped JSON reviews -> bounded revisions. Returns final content."""
        content = author.author(f"author:{kind}", instruction, context)
        self.bus.write_artifact(doc, content, author.ROLE_KEY, note="draft")
        agg = self._review(doc, content, reviewers)

        rounds = int(self.settings.process.get("max_review_rounds", 1))
        rereview = bool(self.settings.process.get("rereview_after_revision", False))
        for round_no in range(1, rounds + 1):
            if agg.final_verdict == Verdict.APPROVED:
                break
            content = author.author(
                f"revise:{kind}",
                f"Revise {doc} to resolve every blocking issue below. Keep what reviewers did not "
                f"flag. End with a 'Changes in this revision' section.\n\n{agg.punchlist_markdown()}",
                context + [f"# Current {doc}\n{content}"],
            )
            self.bus.write_artifact(doc, content, author.ROLE_KEY, note=f"revision {round_no}")
            if not rereview:
                agg = None
                break
            agg = self._review(doc, content, reviewers)

        if agg is not None and agg.final_verdict != Verdict.APPROVED:
            self._append_open_issues(doc, agg)
        return content

    def _append_open_issues(self, doc: str, agg: AggregatedReview) -> None:
        existing = self._read("99_OPEN_ISSUES.md") or "# Open issues escalated to the CEO\n"
        block = f"\n## {doc}\n" + "\n".join(f"- {b}" for b in agg.blockers) + "\n"
        self.bus.write_artifact("99_OPEN_ISSUES.md", existing + block, "orchestrator")

    def _require_gate(self, gate_id: str) -> bool:
        return self.state.gate_approved(gate_id)

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------
    def _phase_strategy(self, idea: str) -> None:
        outputs = ["00_CEO_VISION.md", "01_MARKET_RESEARCH.md"]
        if self._phase_skippable("1_strategy", outputs):
            return
        ref_repo = self.settings.project.get("target_reference_repo", "")
        ceo, po, researcher = self.agents["ceo"], self.agents["staff_po"], self.agents["staff_researcher"]

        vision = ceo.author(
            "author:vision",
            f"Founder's idea:\n{idea}\n\nReference repository to benchmark: {ref_repo or 'none'}\n\n"
            "Write the project vision: target user, problem, why now, 90-day success measures, "
            "and exactly 3 strategic guardrails.",
        )
        self.bus.write_artifact("00_CEO_VISION.md", vision, "ceo")

        questions = po.author(
            "author:research_questions",
            "Write 5-8 key questions for the Market Researcher that will decide how to position "
            "this product as a top idea (users, alternatives, gap vs the reference repo, adoption "
            "friction, risks). Questions only.",
            [f"# CEO vision\n{vision}"],
        )
        self.bus.write_artifact("01a_RESEARCH_QUESTIONS.md", questions, "staff_po")

        material = ""
        if ref_repo and self.settings.tokens.get("fetch_reference_repo", True) and not self.client.mock_mode:
            material = self.fetch_reference(ref_repo)
        if material:
            self.bus.write_artifact("01b_REFERENCE_MATERIAL.md", material, "orchestrator")
        research = researcher.author(
            "author:research",
            "Answer the Product Owner's questions and write the market research report: users, "
            "alternatives, benchmark of the reference repository, the gap, a one-sentence unique "
            "selling proposition, top 3 positioning risks, and Unknowns.",
            [
                f"# CEO vision\n{vision}",
                f"# Questions from the Product Owner\n{questions}",
                (
                    f"# Reference repository material ({ref_repo})\n{material}"
                    if material
                    else "# Reference repository material\nNot available. Do not guess its contents; "
                    "list them as Unknowns."
                ),
            ],
        )
        self.bus.write_artifact("01_MARKET_RESEARCH.md", research, "staff_researcher")
        self.state.set_phase("1_strategy", "DONE")

    def _phase_brd(self) -> None:
        if self._phase_skippable("2a_brd", ["01_BRD.md"]):
            return
        self._document_cycle(
            "01_BRD.md",
            self.agents["staff_po"],
            "brd",
            "Write the BRD following the required structure.",
            [
                _template("01_BRD_TEMPLATE.md"),
                f"# CEO vision\n{self._read('00_CEO_VISION.md')}",
                f"# Market research\n{self._read('01_MARKET_RESEARCH.md')}",
            ],
            self._reviewers("01_BRD.md", ["staff_em", "staff_swe", "staff_researcher", "staff_designer"]),
        )
        self.state.set_phase("2a_brd", "DONE")

    def _phase_mocks(self) -> None:
        if self._phase_skippable("2b_mocks", ["02_MOCKS.md"]):
            return
        self._document_cycle(
            "02_MOCKS.md",
            self.agents["staff_designer"],
            "mocks",
            "Write the developer-experience mocks for the approved BRD, following the required structure.",
            [_template("02_MOCKS_TEMPLATE.md"), f"# BRD\n{self._read('01_BRD.md')}"],
            self._reviewers("02_MOCKS.md", ["staff_em", "staff_swe", "staff_researcher", "staff_po"]),
        )
        self.state.set_phase("2b_mocks", "DONE")

    def _phase_ceo_gate(self) -> Optional[RunOutcome]:
        if self._require_gate("ceo"):
            return None
        ceo = self.agents["ceo"]
        attempts = int(self.settings.process.get("max_ceo_gate_attempts", 2))
        decision: Optional[CEODecision] = None
        for attempt in range(1, attempts + 1):
            context = [
                f"# CEO vision\n{self._read('00_CEO_VISION.md')}",
                f"# BRD\n{self._read('01_BRD.md')}",
                f"# DX mocks\n{self._read('02_MOCKS.md')}",
            ]
            if self.bus.exists("99_OPEN_ISSUES.md"):
                context.append(self._read("99_OPEN_ISSUES.md"))
            decision = ceo.ask_structured(
                "ceo_gate",
                "Decide whether engineering may start on this BRD + mocks. Return ONLY JSON: "
                '{"decision": "APPROVED" | "REVISION_REQUIRED", "rationale": "...", '
                '"required_changes": ["..."], "strategic_guardrails": ["...", "...", "..."]}',
                CEODecision,
                context,
            )
            self.bus.write_artifact(
                "02_CEO_DECISION.md",
                f"# CEO decision (attempt {attempt})\n\n**{decision.decision.value}**\n\n{decision.rationale}\n\n"
                "## Required changes\n" + ("\n".join(f"- {c}" for c in decision.required_changes) or "- none") + "\n\n"
                "## Strategic guardrails\n" + "\n".join(f"- {g}" for g in decision.strategic_guardrails) + "\n",
                "ceo",
            )
            if decision.decision == CEODecisionValue.APPROVED:
                self.state.set_gate("ceo", "APPROVED", "ceo", decision.rationale)
                self.state.set_phase("gate_ceo", "DONE")
                return None
            if attempt < attempts:
                changes = "\n".join(f"- {c}" for c in decision.required_changes)
                for doc, agent_key, kind in (
                    ("01_BRD.md", "staff_po", "brd"),
                    ("02_MOCKS.md", "staff_designer", "mocks"),
                ):
                    agent = self.agents[agent_key]
                    revised = agent.author(
                        f"revise:{kind}",
                        f"The CEO requires these changes before approval. Update {doc} accordingly "
                        f"(only the parts your role owns):\n{changes}",
                        [f"# Current {doc}\n{self._read(doc)}", f"# CEO rationale\n{decision.rationale}"],
                    )
                    self.bus.write_artifact(doc, revised, agent_key, note="CEO-requested revision")

        note = decision.rationale if decision else ""
        self.state.set_gate("ceo", "REVISION_REQUIRED", "ceo", note)
        self.state.set_phase("gate_ceo", "BLOCKED", "CEO did not approve")
        return RunOutcome(
            "BLOCKED_ON_CEO_APPROVAL",
            "The CEO did not approve the BRD + mocks. Read 02_CEO_DECISION.md, edit the documents "
            "if you want, then re-run (the gate is re-evaluated) or override with "
            "`startup-swarm approve-gate ceo`.",
            "gate_ceo",
        )

    def _phase_founder_gate(self) -> Optional[RunOutcome]:
        if not self.settings.process.get("founder_approval_required"):
            return None
        if self._require_gate("founder"):
            self.state.set_phase("gate_founder", "DONE")
            return None
        self.state.set_phase("gate_founder", "WAITING", "Waiting for founder approval")
        return RunOutcome(
            "AWAITING_FOUNDER_APPROVAL",
            "The CEO approved. Review 01_BRD.md and 02_MOCKS.md, then run "
            "`startup-swarm approve-gate founder` and re-run to start engineering.",
            "gate_founder",
        )

    def _phase_hld(self) -> None:
        if self._phase_skippable("3a_hld", ["03_HLD.md"]):
            return
        self._document_cycle(
            "03_HLD.md",
            self.agents["staff_swe"],
            "hld",
            "Write the High-Level Design following the required structure.",
            [
                _template("03_HLD_TEMPLATE.md"),
                f"# BRD\n{self._read('01_BRD.md')}",
                f"# DX mocks\n{self._read('02_MOCKS.md')}",
                f"# CEO decision and guardrails\n{self._read('02_CEO_DECISION.md')}",
            ],
            self._reviewers("03_HLD.md", ["staff_em", "lead_swe", "staff_qa", "staff_po"]),
        )
        self.state.set_phase("3a_hld", "DONE")

    def _phase_lld(self) -> None:
        if self._phase_skippable("3b_lld", ["04_LLD.md"]):
            return
        self._document_cycle(
            "04_LLD.md",
            self.agents["lead_swe"],
            "lld",
            "Write the Low-Level Design following the required structure.",
            [
                _template("04_LLD_TEMPLATE.md"),
                f"# HLD\n{self._read('03_HLD.md')}",
                f"# BRD (user stories and acceptance criteria)\n{self._read('01_BRD.md')}",
            ],
            self._reviewers("04_LLD.md", ["staff_em", "staff_swe", "staff_qa", "staff_po"]),
        )
        self.state.set_phase("3b_lld", "DONE")

    def _phase_backlog(self) -> SprintBacklog:
        if self._phase_skippable("4_backlog", ["05_SPRINT_BACKLOG.md", "05_sprint_backlog.json"]):
            return SprintBacklog(**json.loads(self._read("05_sprint_backlog.json")))
        tpm = self.agents["staff_tpm"]
        context = [
            _template("05_SPRINT_BACKLOG_TEMPLATE.md"),
            f"# LLD\n{self._read('04_LLD.md')}",
            f"# BRD\n{self._read('01_BRD.md')}",
        ]
        ask = (
            "Break the LLD and BRD user stories into a prioritised backlog. Assign each ticket to "
            "staff_swe (cross-cutting/high risk), lead_swe (core modules/contracts) or senior_swe "
            "(well-specified implementation). Return ONLY JSON: "
            '{"milestone": "...", "tickets": [{"id": "T1", "title": "...", "description": "...", '
            '"assignee": "senior_swe", "priority": "P0", "complexity": "S|M|L", '
            '"dependencies": [], "acceptance_criteria": ["..."]}]}'
        )
        backlog = tpm.ask_structured("backlog", ask, SprintBacklog, context)
        draft = backlog.to_markdown()
        agg = self._review("05_SPRINT_BACKLOG.md", draft, [self.agents["staff_em"], self.agents["staff_swe"]])
        if agg.final_verdict != Verdict.APPROVED:
            backlog = tpm.ask_structured(
                "backlog",
                ask + "\n\nFix these review blockers from the EM and Staff SWE:\n" + agg.punchlist_markdown(),
                SprintBacklog,
                context + [f"# Previous draft\n{draft}"],
            )
        self.bus.write_artifact("05_SPRINT_BACKLOG.md", backlog.to_markdown(), "staff_tpm")
        self.bus.write_artifact("05_sprint_backlog.json", backlog.model_dump_json(indent=2), "staff_tpm")
        self.state.set_phase("4_backlog", "DONE")
        return backlog

    def _generated_files(self, root: str) -> Dict[str, str]:
        files: Dict[str, str] = {}
        if not os.path.isdir(root):
            return files
        for dirpath, _, names in os.walk(root):
            for name in names:
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace("\\", "/")
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        files[rel] = fh.read()
                except UnicodeDecodeError:
                    continue
        return files

    def _phase_build(self, backlog: SprintBacklog) -> Optional[RunOutcome]:
        if self.state.phase_done("5_build"):
            return None
        out_dir = self.bus.path(GENERATED_DIR)
        limit = int(self.settings.process.get("max_tickets_per_run", 5))
        design = [f"# LLD (the contract)\n{self._read('04_LLD.md')}"]
        done_now = 0
        remaining = 0
        for ticket in backlog.ordered():
            if self.state.phase_done(f"ticket:{ticket.id}"):
                continue
            if done_now >= limit:
                remaining += 1
                continue
            spec = TicketSpec(
                id=ticket.id,
                title=ticket.title,
                description=ticket.description,
                assignee=ticket.assignee.value,
                acceptance_criteria=ticket.acceptance_criteria,
            )
            logger.info("Building %s (%s) owner=%s", ticket.id, ticket.title, spec.assignee)
            result = self.engineering.implement(spec, out_dir, design, self._generated_files(out_dir))
            self.bus.write_artifact(f"tickets/{ticket.id}.md", report_markdown(spec, result), "lead_swe")
            for rel in result.files:  # mirror generated code to S3 too
                with open(os.path.join(out_dir, rel), "r", encoding="utf-8") as fh:
                    self.bus.write_artifact(f"{GENERATED_DIR}/{rel}", fh.read(), spec.assignee)
            self.state.set_phase(f"ticket:{ticket.id}", "DONE", result.review_verdict)
            done_now += 1

        self._write_build_report(backlog)
        if remaining:
            self.state.set_phase("5_build", "IN_PROGRESS", f"{remaining} tickets left")
            return RunOutcome(
                "PARTIAL",
                f"Built {done_now} tickets this run; {remaining} remain "
                "(process.max_tickets_per_run). Run again to continue.",
                "5_build",
            )
        self.state.set_phase("5_build", "DONE")
        return None

    def _write_build_report(self, backlog: SprintBacklog) -> None:
        lines = ["# Build report", "", "| Ticket | Status | Review |", "|---|---|---|"]
        for t in backlog.ordered():
            rec = self.state.phase(f"ticket:{t.id}") or {}
            lines.append(f"| {t.id} {t.title} | {rec.get('status', 'TODO')} | {rec.get('detail', '-')} |")
        lines += [
            "",
            f"Generated code is in `{GENERATED_DIR}/`. Run its tests before publishing:",
            "",
            "```bash",
            f"cd {GENERATED_DIR} && python -m pytest -q",
            "```",
            "",
            "Code was written by models and reviewed by the Lead SWE agent. Read it before you",
            "run it or publish it.",
        ]
        self.bus.write_artifact("07_BUILD_REPORT.md", "\n".join(lines) + "\n", "orchestrator")

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def _resolve_idea(self, idea: Optional[str]) -> str:
        stored = (self.state.meta("idea") or {}).get("text")
        if idea and stored and idea.strip() != stored.strip():
            raise ValueError(
                f"Project '{self.project}' was started with a different idea. Use --project <new-name> "
                "for a new project, or --fresh to discard this one's progress."
            )
        chosen = idea or stored or self.settings.project.get("default_idea") or ""
        if not chosen.strip():
            raise ValueError("No idea given. Pass --idea or set project.default_idea in config.yaml.")
        if not stored:
            self.state.set_meta("idea", {"text": chosen})
        return chosen

    def run(self, idea: Optional[str] = None) -> RunOutcome:
        self.bus.pull()
        try:
            idea = self._resolve_idea(idea)
            logger.info("Project %s | stage %s | idea: %s", self.project, self.settings.stage, idea[:120])
            self._phase_strategy(idea)
            self._phase_brd()
            self._phase_mocks()
            outcome = self._phase_ceo_gate() or self._phase_founder_gate()
            if outcome:
                return outcome
            if not self._require_gate("ceo"):  # defensive: never design without approval
                return RunOutcome("BLOCKED_ON_CEO_APPROVAL", "CEO gate is not approved.", "gate_ceo")
            self._phase_hld()
            self._phase_lld()
            backlog = self._phase_backlog()
            outcome = self._phase_build(backlog)
            if outcome:
                return outcome
            return RunOutcome("COMPLETED", f"All phases done. Code is in {self.bus.path(GENERATED_DIR)}")
        except BudgetExceededError as exc:
            return RunOutcome("PAUSED_BUDGET", str(exc))
        except (StructuredOutputError, ValueError) as exc:
            logger.error("Run stopped: %s", exc)
            return RunOutcome("FAILED", str(exc))
        finally:
            self._save_usage()

    def run_task(
        self,
        task: str,
        context_files: Optional[Dict[str, str]] = None,
        target_dir: Optional[str] = None,
    ) -> RunOutcome:
        """Direct engineering mode: skip strategy/product phases and build one task."""
        self.bus.pull()
        task_id = "task-" + slugify(task, 24)
        try:
            em = self.agents["staff_em"]
            context = [f"# Task from the founder\n{task}"]
            if self.bus.exists("04_LLD.md"):
                context.append(f"# Existing LLD\n{self._read('04_LLD.md')}")
            if context_files:
                context.append(
                    "# Relevant existing files\n"
                    + "\n".join(f"<<<FILE {p}>>>\n{b}<<<END FILE>>>" for p, b in context_files.items())
                )
            scope = em.ask_structured(
                "scope",
                "Scope this task for the engineering team. Return ONLY JSON: "
                '{"in_scope": true|false, "summary": "...", "assignee": "staff_swe|lead_swe|senior_swe", '
                '"complexity": "S|M|L", "risks": ["..."], "out_of_scope_reason": null}',
                EMScope,
                context,
            )
            self.bus.write_artifact(f"tasks/{task_id}.scope.json", scope.model_dump_json(indent=2), "staff_em")
            if not scope.in_scope:
                return RunOutcome("REJECTED_BY_EM", scope.out_of_scope_reason or scope.summary, task_id)

            spec = TicketSpec(
                id=task_id[:32],
                title=task[:200],
                description=f"{task}\n\nEM scoping: {scope.summary}",
                assignee=scope.assignee.value,
            )
            out_dir = target_dir or self.bus.path(GENERATED_DIR)
            existing = dict(context_files or {})
            result = self.engineering.implement(
                spec,
                out_dir,
                design_context=[c for c in context[1:2] if c.startswith("# Existing LLD")],
                existing_files=existing,
                need_contract=True,
            )
            self.bus.write_artifact(f"tasks/{task_id}.md", report_markdown(spec, result), "lead_swe")
            self.state.set_phase(f"task:{task_id}", "DONE", result.review_verdict)
            return RunOutcome("COMPLETED", f"Wrote {len(result.files)} files to {out_dir}", task_id)
        except BudgetExceededError as exc:
            return RunOutcome("PAUSED_BUDGET", str(exc), task_id)
        except (StructuredOutputError, ValueError) as exc:
            return RunOutcome("FAILED", str(exc), task_id)
        finally:
            self._save_usage()

    def approve_gate(self, gate_id: str, note: str = "") -> RunOutcome:
        if gate_id not in ("ceo", "founder"):
            raise ValueError("gate must be 'ceo' or 'founder'")
        self.state.set_gate(gate_id, "APPROVED", "founder", note or "Approved from the CLI")
        self.state.set_phase(f"gate_{gate_id}", "DONE", "approved by founder")
        return RunOutcome("APPROVED", f"Gate '{gate_id}' approved. Re-run to continue.", f"gate_{gate_id}")
