"""The ten agents. Each can be imported and used on its own."""

from startup_swarm.agents.base_agent import BaseAgent, StructuredOutputError
from startup_swarm.agents.ceo import CEOAgent
from startup_swarm.agents.lead_swe import LeadSWEAgent
from startup_swarm.agents.senior_swe import SeniorSWEAgent
from startup_swarm.agents.staff_designer import StaffDesignerAgent
from startup_swarm.agents.staff_em import StaffEMAgent
from startup_swarm.agents.staff_po import StaffPOAgent
from startup_swarm.agents.staff_qa import StaffQAAgent
from startup_swarm.agents.staff_researcher import StaffResearcherAgent
from startup_swarm.agents.staff_swe import StaffSWEAgent
from startup_swarm.agents.staff_tpm import StaffTPMAgent

AGENT_CLASSES = {
    cls.ROLE_KEY: cls
    for cls in (
        CEOAgent,
        StaffEMAgent,
        StaffPOAgent,
        StaffTPMAgent,
        StaffResearcherAgent,
        StaffSWEAgent,
        LeadSWEAgent,
        SeniorSWEAgent,
        StaffQAAgent,
        StaffDesignerAgent,
    )
}

__all__ = [
    "AGENT_CLASSES",
    "BaseAgent",
    "StructuredOutputError",
    "CEOAgent",
    "StaffEMAgent",
    "StaffPOAgent",
    "StaffTPMAgent",
    "StaffResearcherAgent",
    "StaffSWEAgent",
    "LeadSWEAgent",
    "SeniorSWEAgent",
    "StaffQAAgent",
    "StaffDesignerAgent",
]
