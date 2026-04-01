# agents — Agent System (Слой 4)
# Мульти-агентная система: manager, research, coding, debugging, analysis, planning, learning, communication.
from .agent_system import (
    AgentRole,
    BaseAgent,
    ManagerAgent,
    ResearchAgent,
    CodingAgent,
    DebuggingAgent,
    AnalysisAgent,
    PlanningAgent,
    LearningAgent,
    CommunicationAgent,
    build_agent_system,
)

__all__ = [
    'AgentRole', 'BaseAgent', 'ManagerAgent',
    'ResearchAgent', 'CodingAgent', 'DebuggingAgent',
    'AnalysisAgent', 'PlanningAgent', 'LearningAgent',
    'CommunicationAgent', 'build_agent_system',
]
