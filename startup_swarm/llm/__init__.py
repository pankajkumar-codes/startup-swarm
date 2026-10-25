"""Model providers.

Built in: ``mock`` (offline), ``bedrock`` (Amazon Bedrock), ``anthropic`` (Claude API),
``vertex`` (Claude on Google Vertex AI), ``foundry`` (Claude on Microsoft Foundry / Azure).
Other packages can add providers through the ``startup_swarm.providers``
entry-point group; each entry point is a callable
``factory(settings, **common) -> LLMClient``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from startup_swarm.llm.base import BudgetExceededError, LLMClient, Prompt, Usage
from startup_swarm.plugins import load_entry_points

ProviderFactory = Callable[..., LLMClient]


def _mock(settings, responder=None, **common) -> LLMClient:
    from startup_swarm.llm.mock import MockClient

    return MockClient(responder=responder, **common)


def _bedrock(settings, **common) -> LLMClient:
    from startup_swarm.llm.bedrock import BedrockClient

    return BedrockClient(region=settings.region, **common)


def _anthropic(settings, **common) -> LLMClient:
    from startup_swarm.llm.anthropic_api import AnthropicClient

    return AnthropicClient(**common)


def _vertex(settings, **common) -> LLMClient:
    from startup_swarm.llm.anthropic_api import VertexClient

    cfg = settings.provider_cfg
    return VertexClient(region=settings.region, project_id=cfg.get("project_id", ""), **common)


def _foundry(settings, **common) -> LLMClient:
    import os

    from startup_swarm.llm.anthropic_api import FoundryClient

    cfg = settings.provider_cfg
    return FoundryClient(
        base_url=os.environ.get("AZURE_FOUNDRY_BASE_URL") or cfg.get("base_url", ""),
        api_key=os.environ.get("AZURE_FOUNDRY_API_KEY", ""),
        **common,
    )


BUILTIN: Dict[str, ProviderFactory] = {
    "mock": _mock,
    "bedrock": _bedrock,
    "anthropic": _anthropic,
    "vertex": _vertex,
    "foundry": _foundry,
}


def available_providers() -> Dict[str, ProviderFactory]:
    providers = dict(BUILTIN)
    providers.update(load_entry_points("startup_swarm.providers"))
    return providers


def make_client(settings, responder: Any = None) -> LLMClient:
    """Build the configured provider with the config's caching and budget settings."""
    tokens = settings.tokens
    common = {
        "enable_caching": bool(tokens.get("enable_prompt_caching", True)),
        "cache_ttl": str(tokens.get("cache_ttl", "5m")),
        "budget_cap_tokens": int(tokens.get("budget_cap_tokens") or 0) or None,
    }
    name = settings.provider_name
    providers = available_providers()
    if name not in providers:
        raise ValueError(f"Unknown provider '{name}'. Available: {', '.join(sorted(providers))}")
    if name == "mock":
        return providers[name](settings, responder=responder, **common)
    return providers[name](settings, **common)


__all__ = ["BudgetExceededError", "LLMClient", "Prompt", "Usage", "make_client", "available_providers"]
