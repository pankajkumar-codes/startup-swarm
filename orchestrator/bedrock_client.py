"""Bedrock client built on the Converse API.

Why Converse: one request shape for Claude and Nova, native ``cachePoint`` blocks for
prompt caching, and usage fields that report cache reads/writes so savings can be
measured instead of assumed.

Cache layout used by every call (prefix order is tools -> system -> messages):

    system:   [org charter + role persona]           <cachePoint>
    messages: user[ shared context (docs, template) <cachePoint>, instruction ]

Calls that share the same model + system + context (for example several reviewers
reading the same HLD on the review tier) read the cached prefix instead of paying for
it again. Prefixes shorter than the model's minimum (512-4096 tokens depending on
model) are simply not cached; the call still succeeds.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestrator.config import ModelSpec

logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised before a call that would start after the token budget is used up."""


@dataclass
class Prompt:
    agent: str
    kind: str  # e.g. "author:brd", "review", "ceo_gate", "files", "backlog", "scope"
    system: List[str]
    instruction: str
    context: List[str] = field(default_factory=list)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


@dataclass
class Usage:
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes")


class BedrockClient:
    def __init__(
        self,
        region: str = "us-east-1",
        mock_mode: Optional[bool] = None,
        enable_caching: bool = True,
        cache_ttl: str = "5m",
        budget_cap_tokens: Optional[int] = None,
        boto_client: Any = None,
        mock_responder: Any = None,
    ):
        self.region = region
        self.mock_mode = _env_flag("AWS_MOCK_MODE") if mock_mode is None else mock_mode
        self.enable_caching = enable_caching
        self.cache_ttl = cache_ttl
        self.budget_cap_tokens = budget_cap_tokens
        self.usage = Usage(estimated=self.mock_mode)
        self.usage_by_agent: Dict[str, Usage] = {}
        self._client = boto_client
        self._mock = mock_responder

        if self.mock_mode:
            if self._mock is None:
                from orchestrator.mock_responder import MockResponder

                self._mock = MockResponder()
        elif self._client is None:
            # No silent fallback to mock: a live run that cannot reach Bedrock must fail loudly.
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                config=Config(
                    retries={"mode": "adaptive", "max_attempts": 8},
                    read_timeout=600,
                ),
            )

    # ------------------------------------------------------------------
    def _check_budget(self) -> None:
        if self.budget_cap_tokens and self.usage.total >= self.budget_cap_tokens:
            raise BudgetExceededError(
                f"Token budget of {self.budget_cap_tokens:,} reached "
                f"({self.usage.total:,} used). Raise token_controls.budget_cap_tokens "
                f"or resume later; completed phases are not re-run."
            )

    def _cache_point(self, model_id: str) -> Dict[str, Any]:
        point: Dict[str, Any] = {"type": "default"}
        # 1h TTL is Claude-only; Nova rejects the ttl field.
        if self.cache_ttl == "1h" and "anthropic" in model_id:
            point["ttl"] = "1h"
        return {"cachePoint": point}

    def build_request(self, model: ModelSpec, prompt: Prompt) -> Dict[str, Any]:
        system_blocks: List[Dict[str, Any]] = [{"text": s} for s in prompt.system if s]
        if self.enable_caching and system_blocks:
            system_blocks.append(self._cache_point(model.model_id))

        content: List[Dict[str, Any]] = [{"text": c} for c in prompt.context if c]
        if self.enable_caching and content:
            content.append(self._cache_point(model.model_id))
        content.append({"text": prompt.instruction})

        return {
            "modelId": model.model_id,
            "system": system_blocks,
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {
                "maxTokens": prompt.max_tokens or model.max_tokens,
                "temperature": (model.temperature if prompt.temperature is None else prompt.temperature),
            },
        }

    def _record(self, agent: str, usage: Dict[str, int]) -> None:
        per_agent = self.usage_by_agent.setdefault(agent, Usage(estimated=self.mock_mode))
        for bucket in (self.usage, per_agent):
            bucket.calls += 1
            bucket.input_tokens += int(usage.get("inputTokens", 0))
            bucket.output_tokens += int(usage.get("outputTokens", 0))
            bucket.cache_read_tokens += int(usage.get("cacheReadInputTokens", 0))
            bucket.cache_write_tokens += int(usage.get("cacheWriteInputTokens", 0))

    # ------------------------------------------------------------------
    def complete(self, model: ModelSpec, prompt: Prompt) -> str:
        self._check_budget()
        request = self.build_request(model, prompt)

        if self.mock_mode:
            text = self._mock.respond(prompt)
            chars_in = sum(len(s) for s in prompt.system + prompt.context) + len(prompt.instruction)
            self._record(
                prompt.agent,
                {"inputTokens": chars_in // 4, "outputTokens": len(text) // 4},
            )
            return text

        logger.debug("Bedrock converse: agent=%s kind=%s model=%s", prompt.agent, prompt.kind, model.model_id)
        response = self._client.converse(**request)
        self._record(prompt.agent, response.get("usage", {}))

        stop = response.get("stopReason")
        if stop == "max_tokens":
            logger.warning(
                "%s hit max_tokens on %s; output may be truncated. " "Raise models.<tier>.max_tokens if this repeats.",
                prompt.agent,
                prompt.kind,
            )
        blocks = response["output"]["message"]["content"]
        return "".join(b.get("text", "") for b in blocks).strip()
