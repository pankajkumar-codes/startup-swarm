"""Entry-point discovery for provider and backend plugins."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Dict

logger = logging.getLogger(__name__)


def load_entry_points(group: str) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    for ep in entry_points(group=group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:  # a broken plugin must not break the core
            logger.warning("Could not load %s plugin '%s': %s", group, ep.name, exc)
    return found
