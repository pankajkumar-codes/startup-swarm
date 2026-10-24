"""The ten agents. Each can be imported and used on its own."""

from agents.base_agent import BaseAgent, StructuredOutputError
from agents.ceo import CEOAgent
from agents.lead_swe import LeadSWEAgent
from agents.senior_swe import SeniorSWEAgent
from agents.staff_designer import StaffDesignerAgent
from agents.staff_em import StaffEMAgent
from agents.staff_po import StaffPOAgent
from agents.staff_qa import StaffQAAgent
from agents.staff_researcher import StaffResearcherAgent
from agents.staff_swe import StaffSWEAgent
from agents.staff_tpm import StaffTPMAgent

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
