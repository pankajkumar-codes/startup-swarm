"""Persistent project state: phase progress, approval gates, token usage.

This is where the process rules are recorded. The pipeline consults it before every
phase, so a phase that needs an approval simply does not start until the gate record
says APPROVED — no matter who or what invokes the pipeline.

The core ships ``LocalStateStore`` (``state.json`` in the project workspace). Backend
plugins provide cloud stores by subclassing ``StateStore`` and implementing ``_get``,
``_put`` and ``_all`` (keys look like "PHASE#<id>", "GATE#<id>", "META#<key>"), for
example the DynamoDB store in startup-swarm-aws.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def _get(self, sk: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def _put(self, sk: str, data: Dict[str, Any]) -> None:
        raise NotImplementedError

    def _all(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    # ---- phases --------------------------------------------------------
    def phase(self, phase_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"PHASE#{phase_id}")

    def phase_done(self, phase_id: str) -> bool:
        rec = self.phase(phase_id)
        return bool(rec and rec.get("status") == "DONE")

    def set_phase(self, phase_id: str, status: str, detail: str = "") -> None:
        self._put(f"PHASE#{phase_id}", {"status": status, "detail": detail, "updated_at": _now()})

    # ---- gates ---------------------------------------------------------
    def gate(self, gate_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"GATE#{gate_id}")

    def gate_approved(self, gate_id: str) -> bool:
        rec = self.gate(gate_id)
        return bool(rec and rec.get("decision") == "APPROVED")

    def set_gate(self, gate_id: str, decision: str, by: str, note: str = "") -> None:
        self._put(
            f"GATE#{gate_id}",
            {"decision": decision, "by": by, "note": note[:2000], "updated_at": _now()},
        )

    # ---- meta ----------------------------------------------------------
    def meta(self, key: str) -> Optional[Dict[str, Any]]:
        return self._get(f"META#{key}")

    def set_meta(self, key: str, data: Dict[str, Any]) -> None:
        self._put(f"META#{key}", {**data, "updated_at": _now()})

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {"phases": {}, "gates": {}, "meta": {}}
        for sk, data in self._all().items():
            kind, _, ident = sk.partition("#")
            bucket = {"PHASE": "phases", "GATE": "gates", "META": "meta"}.get(kind)
            if bucket:
                out[bucket][ident] = data
        return out


class LocalStateStore(StateStore):
    FILE = "state.json"

    def __init__(self, workspace_dir: str):
        os.makedirs(workspace_dir, exist_ok=True)
        self.path = os.path.join(workspace_dir, self.FILE)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _get(self, sk: str) -> Optional[Dict[str, Any]]:
        return self._load().get(sk)

    def _put(self, sk: str, data: Dict[str, Any]) -> None:
        state = self._load()
        state[sk] = data
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def _all(self) -> Dict[str, Dict[str, Any]]:
        return self._load()
