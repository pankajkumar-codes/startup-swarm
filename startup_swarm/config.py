"""Configuration loading and role -> model resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {"name": "my-project", "target_reference_repo": "", "default_idea": ""},
    "provider": {"name": "anthropic", "region": "us-east-1", "project_id": "", "base_url": ""},
    "stages": {"beta": {"backend": "local"}, "prod": {"backend": "local"}},
    "models": {
        "reasoner": {
            "model_ids": {
                "bedrock": "global.anthropic.claude-sonnet-5-5",
                "anthropic": "claude-sonnet-5-5",
                "vertex": "claude-sonnet-5-5",
                "foundry": "claude-sonnet-5-5",
            },
            "max_tokens": 8000,
            "temperature": 0.2,
        },
        "workhorse": {
            "model_ids": {
                "bedrock": "global.anthropic.claude-haiku-5-5",
                "anthropic": "claude-haiku-5-5",
                "vertex": "claude-haiku-5-5",
                "foundry": "claude-haiku-5-5",
            },
            "max_tokens": 6000,
            "temperature": 0.1,
        },
        "evaluator": {
            "model_ids": {
                "bedrock": "global.anthropic.claude-haiku-5-5",
                "anthropic": "claude-haiku-5-5",
                "vertex": "claude-haiku-5-5",
                "foundry": "claude-haiku-5-5",
            },
            "max_tokens": 2000,
            "temperature": 0.0,
        },
    },
    "roles": {},
    "process": {
        "max_review_rounds": 1,
        "rereview_after_revision": False,
        "max_ceo_gate_attempts": 2,
        "founder_approval_required": False,
        "max_tickets_per_run": 5,
        "reviewers": {},
    },
    "token_controls": {
        "enable_prompt_caching": True,
        "cache_ttl": "5m",
        "review_tier": "workhorse",
        "max_review_output_tokens": 800,
        "budget_cap_tokens": 600000,
        "fetch_reference_repo": True,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    max_tokens: int
    temperature: float


class Settings:
    """Typed accessors over the merged YAML config. Environment variables win."""

    def __init__(self, raw: Dict[str, Any], stage: str, provider_override: Optional[str] = None):
        self.raw = raw
        self.stage = stage
        self._provider_override = provider_override

    @classmethod
    def load(cls, path: Optional[str], stage: str, provider: Optional[str] = None) -> "Settings":
        data: Dict[str, Any] = {}
        if path:
            if not os.path.exists(path):
                raise FileNotFoundError(f"Config file not found: {path}")
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        if not stage or not stage.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Invalid stage name '{stage}'.")
        merged = _deep_merge(DEFAULT_CONFIG, data)
        if stage not in merged["stages"]:
            known = ", ".join(sorted(merged["stages"]))
            raise ValueError(f"Unknown stage '{stage}'. Known stages: {known}")
        return cls(merged, stage, provider)

    # ---- sections ------------------------------------------------------
    @property
    def project(self) -> Dict[str, Any]:
        return self.raw["project"]

    @property
    def process(self) -> Dict[str, Any]:
        return self.raw["process"]

    @property
    def tokens(self) -> Dict[str, Any]:
        return self.raw["token_controls"]

    @property
    def stage_cfg(self) -> Dict[str, Any]:
        return self.raw["stages"].get(self.stage, {}) or {}

    def stage_option(self, key: str, env: Optional[str] = None, default: Any = "") -> Any:
        """Stage-level option with an optional environment-variable override."""
        if env and os.environ.get(env):
            return os.environ[env]
        return self.stage_cfg.get(key, default)

    # ---- provider / backend ---------------------------------------------
    @property
    def provider_name(self) -> str:
        return (
            self._provider_override
            or os.environ.get("SWARM_PROVIDER")
            or self.stage_cfg.get("provider")
            or self.raw["provider"].get("name", "anthropic")
        )

    @property
    def provider_cfg(self) -> Dict[str, Any]:
        return self.raw["provider"]

    @property
    def region(self) -> str:
        return (
            os.environ.get("SWARM_REGION")
            or self.stage_cfg.get("region")
            or self.stage_cfg.get("aws_region")
            or self.provider_cfg.get("region", "us-east-1")
        )

    @property
    def backend_name(self) -> str:
        return os.environ.get("SWARM_BACKEND") or self.stage_cfg.get("backend", "local")

    # ---- models --------------------------------------------------------
    def tier(self, name: str) -> ModelSpec:
        spec = self.raw["models"].get(name)
        if not spec:
            raise KeyError(f"Model tier '{name}' is not defined in config.models")
        ids = spec.get("model_ids") or {}
        model_id = ids.get(self.provider_name) or spec.get("model_id")
        if not model_id:
            if self.provider_name == "mock":
                model_id = "mock-model"
            else:
                raise KeyError(
                    f"No model id for provider '{self.provider_name}' in models.{name}. "
                    f"Add models.{name}.model_ids.{self.provider_name} to config.yaml."
                )
        return ModelSpec(
            model_id=model_id,
            max_tokens=int(spec.get("max_tokens", 4000)),
            temperature=float(spec.get("temperature", 0.2)),
        )

    def model_for_role(self, role_key: str) -> ModelSpec:
        return self.tier(self.raw["roles"].get(role_key, "workhorse"))

    def review_model(self) -> ModelSpec:
        return self.tier(self.tokens.get("review_tier", "workhorse"))

    def reviewers_for(self, doc_name: str, default: List[str]) -> List[str]:
        return list(self.process.get("reviewers", {}).get(doc_name, default))
