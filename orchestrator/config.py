"""Configuration loading and role -> model resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {
        "name": "my-project",
        "target_reference_repo": "",
        "default_idea": "",
    },
    "stages": {
        "beta": {"aws_region": "us-east-1"},
        "prod": {"aws_region": "us-east-1"},
    },
    "models": {
        "reasoner": {
            "model_id": "global.anthropic.claude-sonnet-5-5",
            "max_tokens": 8000,
            "temperature": 0.2,
        },
        "workhorse": {
            "model_id": "global.anthropic.claude-haiku-5-5",
            "max_tokens": 6000,
            "temperature": 0.1,
        },
        "evaluator": {
            "model_id": "global.anthropic.claude-haiku-5-5",
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
    """Typed accessors over the merged YAML config."""

    def __init__(self, raw: Dict[str, Any], stage: str):
        self.raw = raw
        self.stage = stage

    # ---- loading -------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str], stage: str) -> "Settings":
        data: Dict[str, Any] = {}
        if path:
            if not os.path.exists(path):
                raise FileNotFoundError(f"Config file not found: {path}")
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        if stage not in ("beta", "prod"):
            raise ValueError(f"Unknown stage '{stage}'. Use beta or prod.")
        return cls(_deep_merge(DEFAULT_CONFIG, data), stage)

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
        return self.raw["stages"].get(self.stage, {})

    @property
    def region(self) -> str:
        return os.environ.get("AWS_REGION") or self.stage_cfg.get("aws_region", "us-east-1")

    @property
    def s3_bucket(self) -> str:
        return os.environ.get("S3_ARTIFACT_BUCKET") or self.stage_cfg.get("s3_artifact_bucket", "")

    @property
    def state_table(self) -> str:
        return os.environ.get("DYNAMODB_STATE_TABLE") or self.stage_cfg.get("dynamodb_state_table", "")

    # ---- models --------------------------------------------------------
    def tier(self, name: str) -> ModelSpec:
        spec = self.raw["models"].get(name)
        if not spec:
            raise KeyError(f"Model tier '{name}' is not defined in config.models")
        return ModelSpec(
            model_id=spec["model_id"],
            max_tokens=int(spec.get("max_tokens", 4000)),
            temperature=float(spec.get("temperature", 0.2)),
        )

    def model_for_role(self, role_key: str) -> ModelSpec:
        tier = self.raw["roles"].get(role_key, "workhorse")
        return self.tier(tier)

    def review_model(self) -> ModelSpec:
        return self.tier(self.tokens.get("review_tier", "workhorse"))

    def reviewers_for(self, doc_name: str, default: List[str]) -> List[str]:
        return list(self.process.get("reviewers", {}).get(doc_name, default))
