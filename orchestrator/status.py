"""Project status report: what anyone (founder, CEO, EM) sees with `startup-swarm status`."""

from __future__ import annotations

import json
from typing import Any, Dict

from orchestrator.pipeline import PHASES, StartupPipeline

ICONS = {"DONE": "[x]", "IN_PROGRESS": "[~]", "BLOCKED": "[!]", "WAITING": "[?]"}


def collect(p: StartupPipeline) -> Dict[str, Any]:
    p.bus.pull()
    snap = p.state.snapshot()
    phases = snap["phases"]
    founder_needed = bool(p.settings.process.get("founder_approval_required"))
    rows = []
    for pid, label in PHASES:
        if pid == "gate_founder" and not founder_needed:
            continue
        rec = phases.get(pid, {})
        rows.append(
            {"id": pid, "label": label, "status": rec.get("status", "PENDING"), "detail": rec.get("detail", "")}
        )

    tickets = {k.split(":", 1)[1]: v for k, v in phases.items() if k.startswith("ticket:")}
    open_issues = p.bus.exists("99_OPEN_ISSUES.md")
    nxt = next((r for r in rows if r["status"] != "DONE"), None)
    if nxt is None:
        hint = "All phases done. Read 07_BUILD_REPORT.md and run the generated tests."
    elif nxt["id"] == "gate_ceo" and nxt["status"] == "BLOCKED":
        hint = "Read 02_CEO_DECISION.md; re-run, or override with: startup-swarm approve-gate ceo"
    elif nxt["id"] == "gate_founder":
        hint = "Review BRD + mocks, then: startup-swarm approve-gate founder"
    else:
        hint = f"Next: {nxt['label']}. Run: startup-swarm run"

    done = sum(1 for r in rows if r["status"] == "DONE")
    return {
        "project": p.project,
        "stage": p.settings.stage,
        "idea": (snap["meta"].get("idea") or {}).get("text", ""),
        "progress_pct": round(100 * done / len(rows)),
        "phases": rows,
        "gates": snap["gates"],
        "tickets": tickets,
        "artifacts": p.bus.list_artifacts(),
        "open_issues": open_issues,
        "usage_last_run": snap["meta"].get("usage_last_run", {}),
        "usage_total": snap["meta"].get("usage_total", {}),
        "next_action": hint,
    }


def render(data: Dict[str, Any]) -> str:
    out = [
        f"==== {data['project']} [{data['stage'].upper()}] ====",
        f"Idea: {data['idea'][:150]}",
        f"Progress: {data['progress_pct']}%",
        "",
        "Phases:",
    ]
    for r in data["phases"]:
        icon = ICONS.get(r["status"], "[ ]")
        detail = f"  ({r['detail']})" if r["detail"] else ""
        out.append(f"  {icon} {r['label']}: {r['status']}{detail}")
    if data["gates"]:
        out += ["", "Gates:"]
        for gid, g in data["gates"].items():
            out.append(f"  {gid}: {g.get('decision')} by {g.get('by')} at {g.get('updated_at')}")
    if data["tickets"]:
        out += ["", "Tickets built:"]
        for tid, t in sorted(data["tickets"].items()):
            out.append(f"  {tid}: {t.get('status')} ({t.get('detail')})")
    docs = {k: v for k, v in data["artifacts"].items() if "/" not in k and k.endswith(".md")}
    if docs:
        out += ["", "Documents:"]
        for name, meta in sorted(docs.items()):
            out.append(f"  {name:<28} v{meta.get('version')}  by {meta.get('author')}")
    if data["open_issues"]:
        out += ["", "Open issues escalated: see 99_OPEN_ISSUES.md"]
    total = data["usage_total"].get("total_tokens")
    if total is not None:
        est = " (estimated, mock mode)" if data["usage_total"].get("estimated") else ""
        last = data["usage_last_run"]
        out += [
            "",
            f"Tokens: {total:,} total{est}; last run {last.get('total_tokens', 0):,} "
            f"in {last.get('calls', 0)} calls, cache hit rate {last.get('cache_hit_rate', 0):.0%}",
        ]
    out += ["", f"Next action: {data['next_action']}"]
    return "\n".join(out)


def to_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, default=str)
