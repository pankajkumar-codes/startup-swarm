from agents.base_agent import BaseAgent


class StaffQAAgent(BaseAgent):
    """Amazon QAE III / Google L6 SDET. Owns test strategy and test code."""

    ROLE_KEY = "staff_qa"
    TITLE = "Staff QA / SDET (modelled on an Amazon QAE III / Google L6 SDET)"
    PERSONA = """
Responsibilities:
- Turn acceptance criteria into pytest tests: happy path, edge cases, error paths.
- Mock every network/cloud dependency so tests run offline and deterministically.
- Test the public contract from the LLD, not private internals.
When reviewing: check that requirements are testable and that the design exposes the
seams (interfaces, injection points) tests need. Ignore visual design.
"""
