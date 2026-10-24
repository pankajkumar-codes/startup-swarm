"""Fetch real material about the reference repository for the Market Researcher.

Without this the researcher can only guess about the repo. We fetch the public README
(and top-level file list) from GitHub, trimmed to keep the prompt small. Any failure
returns an empty string and the researcher is told the material is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_GH = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
MAX_CHARS = 12_000


def _get(url: str, timeout: float = 10.0) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "startup-swarm"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed GitHub hosts
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # network is optional
        logger.info("Reference repo fetch failed for %s: %s", url, exc)
        return None


def fetch_reference_material(repo_url: str) -> str:
    match = _GH.search(repo_url or "")
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    parts = []

    listing = _get(f"https://api.github.com/repos/{owner}/{repo}/contents/")
    if listing:
        try:
            names = [f"{e['type']}: {e['name']}" for e in json.loads(listing)]
            parts.append("Top-level files:\n" + "\n".join(names[:80]))
        except (ValueError, KeyError, TypeError):
            pass

    readme = None
    for branch in ("main", "master"):
        readme = _get(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md")
        if readme:
            break
    if readme:
        parts.append("README.md:\n" + readme)

    text = "\n\n".join(parts)
    return text[:MAX_CHARS]
