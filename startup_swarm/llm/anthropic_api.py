"""Anthropic Messages API providers: the Claude API directly, Claude on Google Vertex AI,
or Claude on Microsoft Foundry (Azure). All three share one request format.

Install with `pip install "startup-swarm[anthropic]"` (Claude API, needs ANTHROPIC_API_KEY),
`pip install "startup-swarm[vertex]"` (Google Cloud credentials + project) or
`pip install "startup-swarm[foundry]"` (Azure: API key or Entra ID).

Caching uses ``cache_control`` breakpoints on the last system block and the last
context block, matching the layout in ``llm/base.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from startup_swarm.config import ModelSpec
from startup_swarm.llm.base import LLMClient, Prompt, SendResult


class AnthropicClient(LLMClient):
    provider_name = "anthropic"

    def __init__(self, sdk_client: Any = None, max_retries: int = 6, **kwargs):
        super().__init__(**kwargs)
        if sdk_client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ImportError('Anthropic provider needs the SDK: pip install "startup-swarm[anthropic]"') from exc
            sdk_client = anthropic.Anthropic(max_retries=max_retries, timeout=600)
        self._client = sdk_client

    def _cache_control(self) -> Dict[str, str]:
        cc = {"type": "ephemeral"}
        if self.cache_ttl == "1h":
            cc["ttl"] = "1h"
        return cc

    def _blocks(self, texts: List[str], cache: bool) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = [{"type": "text", "text": t} for t in texts if t]
        if cache and self.enable_caching and blocks:
            blocks[-1]["cache_control"] = self._cache_control()
        return blocks

    def build_request(self, model: ModelSpec, prompt: Prompt) -> Dict[str, Any]:
        content = self._blocks(prompt.context, cache=True)
        content.append({"type": "text", "text": prompt.instruction})
        return {
            "model": model.model_id,
            "max_tokens": self.max_tokens(model, prompt),
            "temperature": self.temperature(model, prompt),
            "system": self._blocks(prompt.system, cache=True),
            "messages": [{"role": "user", "content": content}],
        }

    def _send(self, model: ModelSpec, prompt: Prompt) -> SendResult:
        msg = self._client.messages.create(**self.build_request(model, prompt))
        u = msg.usage

        def val(name: str) -> int:
            v: Optional[int] = getattr(u, name, 0)
            return int(v or 0)

        usage = {
            "input": val("input_tokens"),
            "output": val("output_tokens"),
            "cache_read": val("cache_read_input_tokens"),
            "cache_write": val("cache_creation_input_tokens"),
        }
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()
        return text, usage, getattr(msg, "stop_reason", "") or ""


class VertexClient(AnthropicClient):
    """Claude on Google Vertex AI through the Anthropic SDK's Vertex client."""

    provider_name = "vertex"

    def __init__(self, region: str = "us-east5", project_id: str = "", sdk_client: Any = None, **kwargs):
        if sdk_client is None:
            try:
                from anthropic import AnthropicVertex
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ImportError('Vertex provider needs: pip install "startup-swarm[vertex]"') from exc
            sdk_client = AnthropicVertex(region=region, project_id=project_id or None, max_retries=6, timeout=600)
        super().__init__(sdk_client=sdk_client, **kwargs)


class FoundryClient(AnthropicClient):
    """Claude on Microsoft Foundry (Azure) through the Anthropic SDK's Foundry client.

    ``base_url`` is ``https://<resource-name>.services.ai.azure.com/anthropic``. Model ids in
    config are your Foundry *deployment names*. Auth: ``AZURE_FOUNDRY_API_KEY`` if set,
    otherwise Microsoft Entra ID via ``azure-identity`` (DefaultAzureCredential).
    """

    provider_name = "foundry"
    ENTRA_SCOPE = "https://ai.azure.com/.default"

    def __init__(self, base_url: str = "", api_key: str = "", sdk_client: Any = None, **kwargs):
        if sdk_client is None:
            if not base_url:
                raise ValueError(
                    "Foundry provider needs provider.base_url in config.yaml or AZURE_FOUNDRY_BASE_URL "
                    "(https://<resource-name>.services.ai.azure.com/anthropic)."
                )
            try:
                from anthropic import AnthropicFoundry
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ImportError('Foundry provider needs: pip install "startup-swarm[foundry]"') from exc
            if api_key:
                sdk_client = AnthropicFoundry(api_key=api_key, base_url=base_url, max_retries=6, timeout=600)
            else:
                try:
                    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                except ImportError as exc:  # pragma: no cover - depends on install
                    raise ImportError(
                        'Set AZURE_FOUNDRY_API_KEY, or install Entra ID support: pip install "startup-swarm[foundry]"'
                    ) from exc
                token_provider = get_bearer_token_provider(DefaultAzureCredential(), self.ENTRA_SCOPE)
                sdk_client = AnthropicFoundry(
                    azure_ad_token_provider=token_provider, base_url=base_url, max_retries=6, timeout=600
                )
        super().__init__(sdk_client=sdk_client, **kwargs)
