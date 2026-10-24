"""Merge parallel JSON reviews into one deduplicated punchlist."""

from __future__ import annotations

import re
from typing import Dict, List

from orchestrator.models import AggregatedReview, ReviewResult, Verdict


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


class ReviewAggregator:
    @staticmethod
    def aggregate(target_document: str, reviews: List[ReviewResult]) -> AggregatedReview:
        """Deduplicate by (section, issue) while keeping every reviewer who raised it.

        Verdict is REVISE if any blocker remains or the mean score is below 3.
        """
        if not reviews:
            return AggregatedReview(
                target_document=target_document,
                final_verdict=Verdict.APPROVED,
                total_reviews=0,
                blocking_items_count=0,
            )

        def merge(kind: str) -> List[str]:
            merged: Dict[tuple, dict] = {}
            for r in reviews:
                for c in getattr(r, kind):
                    key = (_norm(c.section), _norm(c.issue))
                    entry = merged.setdefault(
                        key, {"section": c.section, "issue": c.issue, "fix": c.suggested_fix, "who": []}
                    )
                    if r.reviewer_role not in entry["who"]:
                        entry["who"].append(r.reviewer_role)
                    if not entry["fix"] and c.suggested_fix:
                        entry["fix"] = c.suggested_fix
            lines = []
            for e in merged.values():
                line = f"[{', '.join(e['who'])}] {e['section']}: {e['issue']}"
                if e["fix"]:
                    line += f" Fix: {e['fix']}"
                lines.append(line)
            return lines

        blockers = merge("blockers")
        suggestions = merge("suggestions")
        avg = sum(r.overall_score for r in reviews) / len(reviews)
        verdict = Verdict.REVISE if blockers or avg < 3.0 else Verdict.APPROVED
        return AggregatedReview(
            target_document=target_document,
            final_verdict=verdict,
            total_reviews=len(reviews),
            blocking_items_count=len(blockers),
            blockers=blockers,
            suggestions=suggestions,
        )
