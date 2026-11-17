"""On-demand delegation: the CEO or Staff EM decide which specialists to call.

Specialists are never part of the fixed phase sequence. At each scope (a document, a ticket,
the docs pass) the pipeline asks the responsible leader for a small JSON plan and then
validates it in code:

- only known specialists that are eligible for that scope can be called;
- at most ``process.delegation.max_specialists_per_scope`` are kept;
- an unparseable answer means nobody is called (the cheap, safe default; the core review
  gates still run);
- the decision is stored in the state store, so a resumed run does not ask (or pay) again.

Every specialist call goes through the same LLM client, so the token budget cap applies.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from startup_swarm.agents.base_agent import BaseAgent, StructuredOutputError
from startup_swarm.agents.specialists import SPECIALIST_CLASSES
from startup_swarm.models import DelegationPlan

logger = logging.getLogger(__name__)

MAX_SUBJECT_CHARS = 4000


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:80] or "scope"


class Delegator:
    def __init__(self, settings, agents: Dict[str, BaseAgent], state, bus):
        self.settings = settings
        self.agents = agents
        self.state = state
        self.bus = bus

    @property
    def enabled(self) -> bool:
        return bool(self.settings.delegation.get("enabled", True))

    @property
    def max_per_scope(self) -> int:
        return max(0, int(self.settings.delegation.get("max_specialists_per_scope", 2)))

    def eligible(self, scope: str) -> List[str]:
        allowed = self.settings.delegation.get("allowed") or []
        return [
            key
            for key, cls in SPECIALIST_CLASSES.items()
            if scope in cls.SCOPES and key in self.agents and (not allowed or key in allowed)
        ]

    def decide(self, scope: str, scope_id: str, leader_key: str, subject: str) -> List[str]:
        """Return the specialist role keys to call for this scope (often an empty list)."""
        if not self.enabled or self.max_per_scope == 0:
            return []
        eligible = self.eligible(scope)
        if not eligible:
            return []
        state_key = f"delegation:{scope_id}"
        cached = self.state.meta(state_key)
        if cached is not None:
            return [r for r in cached.get("roles", []) if r in eligible]

        roster = "\n".join(f"- {k}: call only if {SPECIALIST_CLASSES[k].WHEN}" for k in eligible)
        instruction = (
            f"Decide which on-call specialists, if any, to call in for '{scope_id}'.\n"
            f"Available specialists:\n{roster}\n\n"
            f"Rules: call a specialist only when the material below clearly needs their expertise. "
            f"Calling nobody is a normal and often correct answer; every call costs tokens. "
            f"Call at most {self.max_per_scope}. Do not call a specialist to repeat a review your "
            "own team already does. Return ONLY JSON: "
            '{"specialists": [{"role": "<role key>", "reason": "<one sentence>"}]}'
        )
        leader = self.agents[leader_key]
        try:
            plan = leader.ask_structured(
                "delegate", instruction, DelegationPlan, [f"# Material for '{scope_id}'\n{subject[:MAX_SUBJECT_CHARS]}"]
            )
        except StructuredOutputError as exc:
            # Fail safe: call nobody for this run, but do not cache the failure, so a re-run asks again.
            logger.warning("Delegation for %s unparseable, calling no specialists: %s", scope_id, exc)
            return []

        chosen: List[str] = []
        reasons: Dict[str, str] = {}
        for call in plan.specialists:
            if call.role in eligible and call.role not in chosen and len(chosen) < self.max_per_scope:
                chosen.append(call.role)
                reasons[call.role] = call.reason
            else:
                logger.info(
                    "Ignoring delegation to %r for %s (unknown, ineligible, duplicate or over cap)", call.role, scope_id
                )

        record = {"scope": scope, "leader": leader_key, "roles": chosen, "reasons": reasons}
        self.state.set_meta(state_key, record)
        self.bus.write_artifact(f"delegation/{_slug(scope_id)}.json", json.dumps(record, indent=2), leader_key)
        logger.info("%s delegated %s to: %s", leader_key, scope_id, ", ".join(chosen) or "nobody")
        return chosen
