"""Amazon Bedrock provider (Converse API). Install with `pip install "startup-swarm[bedrock]"`.

Converse gives one request shape for Claude and Amazon Nova, native ``cachePoint``
blocks, and usage fields for cache reads/writes. Credentials come from the normal AWS
chain (profile, env vars, role).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from startup_swarm.config import ModelSpec
from startup_swarm.llm.base import LLMClient, Prompt, SendResult


class BedrockClient(LLMClient):
    provider_name = "bedrock"

    def __init__(self, region: str = "us-east-1", boto_client: Any = None, **kwargs):
        super().__init__(**kwargs)
        self.region = region
        if boto_client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ImportError('Bedrock provider needs boto3: pip install "startup-swarm[bedrock]"') from exc
            boto_client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=Config(retries={"mode": "adaptive", "max_attempts": 8}, read_timeout=600),
            )
        self._client = boto_client

    def _cache_point(self, model_id: str) -> Dict[str, Any]:
        point: Dict[str, Any] = {"type": "default"}
        if self.cache_ttl == "1h" and "anthropic" in model_id:  # Nova rejects ttl
            point["ttl"] = "1h"
        return {"cachePoint": point}

    def build_request(self, model: ModelSpec, prompt: Prompt) -> Dict[str, Any]:
        system: List[Dict[str, Any]] = [{"text": s} for s in prompt.system if s]
        if self.enable_caching and system:
            system.append(self._cache_point(model.model_id))
        content: List[Dict[str, Any]] = [{"text": c} for c in prompt.context if c]
        if self.enable_caching and content:
            content.append(self._cache_point(model.model_id))
        content.append({"text": prompt.instruction})
        return {
            "modelId": model.model_id,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {
                "maxTokens": self.max_tokens(model, prompt),
                "temperature": self.temperature(model, prompt),
            },
        }

    def _send(self, model: ModelSpec, prompt: Prompt) -> SendResult:
        response = self._client.converse(**self.build_request(model, prompt))
        u: Dict[str, Optional[int]] = response.get("usage", {})
        usage = {
            "input": u.get("inputTokens", 0) or 0,
            "output": u.get("outputTokens", 0) or 0,
            "cache_read": u.get("cacheReadInputTokens", 0) or 0,
            "cache_write": u.get("cacheWriteInputTokens", 0) or 0,
        }
        blocks = response["output"]["message"]["content"]
        text = "".join(b.get("text", "") for b in blocks).strip()
        return text, usage, response.get("stopReason", "")
