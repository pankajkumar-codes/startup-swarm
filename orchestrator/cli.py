"""Command line: `startup-swarm <command>` or `python -m orchestrator <command>`.

Commands
  run            Run (or resume) the full company on an idea.
  task           Give one task straight to the engineering squad.
  status         Show phases, gates, documents, tickets and token use.
  approve-gate   Founder override / approval for the 'ceo' or 'founder' gate.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from typing import Dict, List, Optional

from orchestrator import status as status_mod
from orchestrator.pipeline import StartupPipeline

EXIT_CODES = {
    "COMPLETED": 0,
    "APPROVED": 0,
    "PARTIAL": 0,
    "AWAITING_FOUNDER_APPROVAL": 0,
    "BLOCKED_ON_CEO_APPROVAL": 2,
    "REJECTED_BY_EM": 2,
    "PAUSED_BUDGET": 3,
    "FAILED": 1,
}


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stage", choices=["beta", "prod"], default=os.environ.get("STAGE", "beta"))
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--project", default=None, help="Project name (default: project.name in config)")
    p.add_argument("--workspace", default=None, help="Local folder for artifacts")
    p.add_argument("--mock", action="store_true", help="Offline mode: no AWS calls, no token spend")
    p.add_argument("-v", "--verbose", action="store_true")


def _read_context_files(paths: List[str]) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for path in paths or []:
        with open(path, "r", encoding="utf-8") as fh:
            files[os.path.relpath(path).replace("\\", "/")] = fh.read()
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="startup-swarm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run or resume the full startup lifecycle")
    _common(run)
    run.add_argument("--idea", default=None, help="Project idea (first run only)")
    run.add_argument("--fresh", action="store_true", help="Delete this project's local progress first")

    task = sub.add_parser("task", help="Send one task directly to the engineering squad")
    _common(task)
    task.add_argument("description", help="What to build or fix")
    task.add_argument(
        "--context-file", action="append", default=[], help="Existing file the team should read (repeatable)"
    )
    task.add_argument("--target-dir", default=None, help="Write files here instead of the workspace")

    st = sub.add_parser("status", help="Show project status")
    _common(st)
    st.add_argument("--json", action="store_true")

    ap = sub.add_parser("approve-gate", help="Approve the 'ceo' (override) or 'founder' gate")
    _common(ap)
    ap.add_argument("gate", choices=["ceo", "founder"])
    ap.add_argument("--note", default="")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run" and args.fresh:
        pipeline = StartupPipeline.build(args.stage, args.config, args.mock, args.project, args.workspace)
        if pipeline.bus.mirror or not hasattr(pipeline.state, "path"):
            print("--fresh only clears local progress. For AWS state use a new --project name.")
            return 1
        shutil.rmtree(pipeline.bus.workspace_dir, ignore_errors=True)

    pipeline = StartupPipeline.build(args.stage, args.config, args.mock, args.project, args.workspace)

    if args.command == "status":
        data = status_mod.collect(pipeline)
        print(status_mod.to_json(data) if args.json else status_mod.render(data))
        return 0

    if args.command == "approve-gate":
        outcome = pipeline.approve_gate(args.gate, args.note)
    elif args.command == "task":
        outcome = pipeline.run_task(args.description, _read_context_files(args.context_file), args.target_dir)
    else:
        outcome = pipeline.run(args.idea)

    print(f"\n{outcome.status}: {outcome.message}")
    usage = pipeline.client.usage.as_dict()
    if usage["calls"]:
        est = " (estimated)" if usage["estimated"] else ""
        print(
            f"Tokens this run{est}: {usage['total_tokens']:,} in {usage['calls']} calls; "
            f"cache hit rate {usage['cache_hit_rate']:.0%}"
        )
    print(f"Workspace: {pipeline.bus.workspace_dir}")
    return EXIT_CODES.get(outcome.status, 1)


if __name__ == "__main__":
    sys.exit(main())
