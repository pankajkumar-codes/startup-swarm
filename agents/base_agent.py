"""Base agent: persona, document authoring, JSON reviews and structured outputs."""

from __future__ import annotations

import json
import logging
from typing import List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from agents.charter import ORG_CHARTER
from orchestrator.bedrock_client import BedrockClient, Prompt
from orchestrator.config import ModelSpec
from orchestrator.json_utils import JSONExtractionError, extract_json_object
from orchestrator.models import Priority, ReviewComment, ReviewResult, Verdict

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """The model did not return valid JSON for a schema after a retry."""


REVIEW_SCHEMA = """\
Return ONLY this JSON object:
{
  "reviewer_role": "<your role key>",
  "target_document": "<document name>",
  "verdict": "APPROVED" | "REVISE",
  "overall_score": 1-5,
  "blockers": [{"section": "...", "priority": "BLOCKER", "issue": "...", "suggested_fix": "..."}],
  "suggestions": [{"section": "...", "priority": "SUGGESTION", "issue": "...", "suggested_fix": "..."}]
}
Rules: at most 5 blockers and 5 suggestions; each issue under 300 characters;
verdict is REVISE only if there is at least one blocker."""


class BaseAgent:
    """One member of the startup.

    Subclasses set ``ROLE_KEY``, ``TITLE`` and ``PERSONA``. The system prompt is the
    shared charter followed by the persona, so each tier caches the charter once.
    """

    ROLE_KEY = "base"
    TITLE = "Team member"
    PERSONA = ""

    def __init__(self, model: ModelSpec, client: BedrockClient):
        self.model = model
        self.client = client

    # ------------------------------------------------------------------
    @property
    def role_name(self) -> str:
        return self.ROLE_KEY

    def system_blocks(self) -> List[str]:
        return [ORG_CHARTER, f"# Your role: {self.TITLE}\n{self.PERSONA.strip()}"]

    def _call(
        self,
        kind: str,
        instruction: str,
        context: Optional[List[str]] = None,
        model: Optional[ModelSpec] = None,
        system: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        prompt = Prompt(
            agent=self.ROLE_KEY,
            kind=kind,
            system=system if system is not None else self.system_blocks(),
            context=context or [],
            instruction=instruction,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self.client.complete(model or self.model, prompt)

    # ------------------------------------------------------------------
    def author(self, kind: str, instruction: str, context: Optional[List[str]] = None) -> str:
        """Write or revise a document. Returns markdown."""
        return self._call(kind, instruction, context)

    def ask_structured(
        self,
        kind: str,
        instruction: str,
        schema: Type[T],
        context: Optional[List[str]] = None,
        model: Optional[ModelSpec] = None,
    ) -> T:
        """Ask for JSON matching ``schema``. One corrective retry, then raise."""
        text = self._call(kind, instruction, context, model=model)
        try:
            return schema(**extract_json_object(text))
        except (JSONExtractionError, ValidationError, TypeError) as first_err:
            logger.warning("%s returned invalid %s JSON, retrying once: %s", self.ROLE_KEY, kind, first_err)
            retry = (
                f"{instruction}\n\nYour previous answer was not valid JSON for the schema "
                f"({str(first_err)[:300]}). Return ONLY the corrected JSON object."
            )
            text = self._call(kind, retry, context, model=model, temperature=0.0)
            try:
                return schema(**extract_json_object(text))
            except (JSONExtractionError, ValidationError, TypeError) as err:
                raise StructuredOutputError(
                    f"{self.ROLE_KEY} could not produce valid {schema.__name__}: {err}"
                ) from err

    # ------------------------------------------------------------------
    def review(
        self,
        doc_name: str,
        content: str,
        review_model: Optional[ModelSpec] = None,
        max_tokens: int = 800,
        kind: str = "review",
    ) -> ReviewResult:
        """Strict JSON review. Fails CLOSED: unparseable output becomes a blocker.

        Layout for caching: the shared review system prompt and the document come first
        and are identical for every reviewer of this document; only the final
        instruction (this role's persona and focus) differs.
        """
        system = [ORG_CHARTER, "# Task: document review\n" + REVIEW_SCHEMA]
        context = [f"# Document under review: {doc_name}\n\n{content}"]
        instruction = (
            f"Your role: {self.TITLE} (role key '{self.ROLE_KEY}').\n"
            f"{self.PERSONA.strip()}\n\n"
            f"Review '{doc_name}' strictly from your role's point of view. Return only the JSON."
        )
        model = review_model or self.model
        for attempt in (1, 2):
            raw = self._call(
                kind,
                instruction,
                context,
                model=model,
                system=system,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            try:
                data = extract_json_object(raw)
                data["reviewer_role"] = self.ROLE_KEY
                data["target_document"] = doc_name
                result = ReviewResult(**data)
                if result.blockers and result.verdict == Verdict.APPROVED:
                    result.verdict = Verdict.REVISE
                return result
            except (JSONExtractionError, ValidationError, TypeError) as err:
                logger.warning("%s review of %s unparseable (attempt %d): %s", self.ROLE_KEY, doc_name, attempt, err)
                instruction += "\n\nReturn ONLY a JSON object. No markdown, no prose."

        return ReviewResult(
            reviewer_role=self.ROLE_KEY,
            target_document=doc_name,
            verdict=Verdict.REVISE,
            overall_score=1,
            blockers=[
                ReviewComment(
                    section="(review process)",
                    priority=Priority.BLOCKER,
                    issue=f"{self.ROLE_KEY} returned an unparseable review twice; treat as not approved.",
                    suggested_fix="Re-run the review or inspect the reviewer output in the logs.",
                )
            ],
            parse_failed=True,
        )

    def describe(self) -> str:
        return json.dumps({"role": self.ROLE_KEY, "title": self.TITLE, "model": self.model.model_id})
