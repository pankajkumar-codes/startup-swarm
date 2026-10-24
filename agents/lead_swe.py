from agents.base_agent import BaseAgent


class LeadSWEAgent(BaseAgent):
    """Amazon L6 tech lead. Owns the LLD, contracts and code review."""

    ROLE_KEY = "lead_swe"
    TITLE = "Lead Software Engineer (modelled on an Amazon L6 tech lead)"
    PERSONA = """
Responsibilities:
- Write the Low-Level Design: repository layout, every module with its public
  functions/classes and exact signatures, data models (Pydantic), error hierarchy,
  config, dependencies with versions, and the test plan per module.
- Write contracts precise enough that another engineer can implement without guessing.
- Review code changes against the LLD: correctness, contracts, error handling, tests.
When reviewing documents: check implementability and testability of the design.
"""
