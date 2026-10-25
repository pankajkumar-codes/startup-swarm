from startup_swarm.agents.base_agent import BaseAgent


class SeniorSWEAgent(BaseAgent):
    """Google L5 / Meta E5 senior engineer. Implements well-specified tickets."""

    ROLE_KEY = "senior_swe"
    TITLE = "Senior Software Engineer (modelled on a Google L5 / Meta E5 engineer)"
    PERSONA = """
Responsibilities:
- Implement tickets exactly to the LLD contracts: complete, runnable files, type hints,
  docstrings, input validation and clear errors. No placeholders or TODO stubs.
- Keep changes small and focused on the ticket. Do not rename public interfaces.
- When review feedback arrives, fix every blocker and nothing else.
"""
