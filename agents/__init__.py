# agents — Agent System (Слой 4)
# Мульти-агентная система: manager, research, coding, debugging, analysis, planning, learning, communication.
# Inter-agent communication: MessageBus, AgentMessage, Blackboard.
from .agent_system import (
    AgentRole,
    BaseAgent,
    AgentMessage,
    MessageBus,
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
    'AgentRole', 'BaseAgent', 'AgentMessage', 'MessageBus',
    'ManagerAgent',
    'ResearchAgent', 'CodingAgent', 'DebuggingAgent',
    'AnalysisAgent', 'PlanningAgent', 'LearningAgent',
    'CommunicationAgent', 'build_agent_system',
]
