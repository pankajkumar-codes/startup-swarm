"""Provider-neutral LLM client: prompts, usage accounting, budget, caching layout.

Every provider receives the same ``Prompt`` and lays it out the same way so prompt
caching works everywhere:

    system:   [org charter + role persona]                  <cache breakpoint>
    user:     [shared context: documents, template]          <cache breakpoint>
              [instruction for this call]                    (not cached)

Calls that share model + system + context (for example all reviewers of one HLD on the
review tier) reuse the cached prefix. Prefixes below a model's minimum size are simply
not cached; the call still succeeds.

Providers subclass ``LLMClient`` and implement ``_send``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from startup_swarm.config import ModelSpec

logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised before a call that would start after the token budget is used up."""


@dataclass
class Prompt:
    agent: str
    kind: str  # e.g. "author:brd", "review", "ceo_gate", "files:impl", "backlog", "scope"
    system: List[str]
    instruction: str
    context: List[str] = field(default_factory=list)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


@dataclass
class Usage:
    """Token counts normalised across providers."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = False

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def cache_hit_rate(self) -> float:
        prompt_side = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return (self.cache_read_tokens / prompt_side) if prompt_side else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "estimated": self.estimated,
        }


# (text, {"input": n, "output": n, "cache_read": n, "cache_write": n}, stop_reason)
SendResult = Tuple[str, Dict[str, int], str]


class LLMClient:
    """Base class. Handles budget, usage per agent and logging; providers send."""

    provider_name = "base"
    estimated_usage = False

    def __init__(self, enable_caching: bool = True, cache_ttl: str = "5m", budget_cap_tokens: Optional[int] = None):
        self.enable_caching = enable_caching
        self.cache_ttl = cache_ttl
        self.budget_cap_tokens = budget_cap_tokens
        self.usage = Usage(estimated=self.estimated_usage)
        self.usage_by_agent: Dict[str, Usage] = {}

    # ---- to implement ---------------------------------------------------
    def _send(self, model: ModelSpec, prompt: Prompt) -> SendResult:
        raise NotImplementedError

    # ---- shared ---------------------------------------------------------
    @property
    def mock_mode(self) -> bool:
        return False

    def _check_budget(self) -> None:
        if self.budget_cap_tokens and self.usage.total >= self.budget_cap_tokens:
            raise BudgetExceededError(
                f"Token budget of {self.budget_cap_tokens:,} reached ({self.usage.total:,} used). "
                "Raise token_controls.budget_cap_tokens or resume later; completed phases are not re-run."
            )

    def _record(self, agent: str, usage: Dict[str, int]) -> None:
        per_agent = self.usage_by_agent.setdefault(agent, Usage(estimated=self.estimated_usage))
        for bucket in (self.usage, per_agent):
            bucket.calls += 1
            bucket.input_tokens += int(usage.get("input", 0))
            bucket.output_tokens += int(usage.get("output", 0))
            bucket.cache_read_tokens += int(usage.get("cache_read", 0))
            bucket.cache_write_tokens += int(usage.get("cache_write", 0))

    def complete(self, model: ModelSpec, prompt: Prompt) -> str:
        self._check_budget()
        logger.debug(
            "%s call: agent=%s kind=%s model=%s", self.provider_name, prompt.agent, prompt.kind, model.model_id
        )
        text, usage, stop = self._send(model, prompt)
        self._record(prompt.agent, usage)
        if stop in ("max_tokens", "length"):
            logger.warning(
                "%s hit max_tokens on %s; output may be truncated. Raise models.<tier>.max_tokens if this repeats.",
                prompt.agent,
                prompt.kind,
            )
        return text

    def max_tokens(self, model: ModelSpec, prompt: Prompt) -> int:
        return prompt.max_tokens or model.max_tokens

    def temperature(self, model: ModelSpec, prompt: Prompt) -> float:
        return model.temperature if prompt.temperature is None else prompt.temperature
