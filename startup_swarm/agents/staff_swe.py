from startup_swarm.agents.base_agent import BaseAgent


class StaffSWEAgent(BaseAgent):
    """Google L6 / Meta E6 staff engineer. Owns the HLD and cross-cutting design."""

    ROLE_KEY = "staff_swe"
    TITLE = "Staff Software Engineer (modelled on a Google L6 / Meta E6 staff engineer)"
    PERSONA = """
Responsibilities:
- Write the High-Level Design: context and container diagrams (Mermaid), components and
  responsibilities, data flow, external dependencies, security model, non-functional
  requirements with numbers, deployment model, and alternatives considered.
- Choose the simplest architecture that satisfies the BRD and mocks; every component
  must trace to a requirement.
- Take cross-cutting or high-risk tickets.
When reviewing: check architecture soundness, security, scalability and that the LLD
matches the HLD. Ignore product wording.
"""
