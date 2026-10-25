"""Command line: `startup-swarm <command>` or `python -m startup_swarm <command>`.

Commands
  run            Run (or resume) the full company on an idea.
  task           Give one task straight to the engineering squad.
  status         Show phases, gates, documents, tickets and token use.
  approve-gate   Founder override / approval for the 'ceo' or 'founder' gate.
  init           Write a starter config.yaml in the current folder.
  info           Show installed providers and backends.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from typing import Dict, List, Optional

from startup_swarm import status as status_mod
from startup_swarm.pipeline import StartupPipeline

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


EXAMPLE_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.yaml")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stage", default=os.environ.get("STAGE", "beta"), help="Stage name from config (default beta)")
    p.add_argument("--config", default=None, help="Path to config.yaml (default: ./config.yaml if present)")
    p.add_argument("--project", default=None, help="Project name (default: project.name in config)")
    p.add_argument("--workspace", default=None, help="Local folder for artifacts")
    p.add_argument(
        "--provider", default=None, help="Model provider: bedrock, anthropic, vertex, ... (overrides config)"
    )
    p.add_argument("--backend", default=None, help="Storage backend: local, or one from a plugin such as aws")
    p.add_argument("--mock", action="store_true", help="Offline mode: no cloud calls, no token spend")
    p.add_argument("-v", "--verbose", action="store_true")


def _config_path(arg: Optional[str]) -> Optional[str]:
    if arg:
        return arg
    return "config.yaml" if os.path.exists("config.yaml") else None


def _build(args) -> StartupPipeline:
    return StartupPipeline.build(
        stage=args.stage,
        config_path=_config_path(args.config),
        mock=args.mock,
        project=args.project,
        workspace=args.workspace,
        provider=args.provider,
        backend=args.backend,
    )


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

    init = sub.add_parser("init", help="Write a starter config.yaml here")
    init.add_argument("--force", action="store_true", help="Overwrite an existing config.yaml")

    sub.add_parser("info", help="List installed providers and backends")
    return parser


def _init(force: bool) -> int:
    if os.path.exists("config.yaml") and not force:
        print("config.yaml already exists (use --force to overwrite).")
        return 1
    shutil.copy(EXAMPLE_CONFIG, "config.yaml")
    print("Wrote config.yaml. Set provider.name and your idea, then: startup-swarm run --mock")
    return 0


def _info() -> int:
    from startup_swarm import __version__
    from startup_swarm.backends import available_backends
    from startup_swarm.llm import available_providers

    print(f"startup-swarm {__version__}")
    print("Providers:", ", ".join(sorted(available_providers())))
    print("Backends: ", ", ".join(sorted(available_backends())))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "init":
        return _init(args.force)
    if args.command == "info":
        return _info()

    try:
        return _dispatch(args)
    except (ImportError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.command == "run" and args.fresh:
        pipeline = _build(args)
        if pipeline.bus.mirror or not hasattr(pipeline.state, "path"):
            print("--fresh only clears local progress. For cloud state use a new --project name.")
            return 1
        shutil.rmtree(pipeline.bus.workspace_dir, ignore_errors=True)

    pipeline = _build(args)

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
