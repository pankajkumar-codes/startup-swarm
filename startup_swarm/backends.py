"""Storage backends: where documents and project state live.

The core ships ``local`` (files under the workspace). Cloud packages register more
through the ``startup_swarm.backends`` entry-point group; each entry point is a
zero-argument callable returning a ``Backend``. Example: startup-swarm-aws registers
``aws`` (S3 + DynamoDB).

Select one per stage in config.yaml (``stages.<stage>.backend``) or with SWARM_BACKEND.
"""

from __future__ import annotations

from typing import Dict, Optional

from startup_swarm.artifact_bus import RemoteMirror
from startup_swarm.plugins import load_entry_points
from startup_swarm.state_store import LocalStateStore, StateStore


class Backend:
    name = "base"

    def make_state(self, settings, project: str, workspace: str) -> StateStore:
        raise NotImplementedError

    def make_mirror(self, settings, project: str) -> Optional[RemoteMirror]:
        return None


class LocalBackend(Backend):
    name = "local"

    def make_state(self, settings, project: str, workspace: str) -> StateStore:
        return LocalStateStore(workspace)


def available_backends() -> Dict[str, object]:
    found: Dict[str, object] = {"local": LocalBackend}
    found.update(load_entry_points("startup_swarm.backends"))
    return found


def get_backend(name: str) -> Backend:
    backends = available_backends()
    if name not in backends:
        hint = " Install startup-swarm-aws for 'aws'." if name == "aws" else ""
        raise ValueError(f"Unknown backend '{name}'. Available: {', '.join(sorted(backends))}.{hint}")
    return backends[name]()
