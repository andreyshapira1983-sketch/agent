# core — Cognitive Core (Слой 3) + Model Management (Слой 32)
#        Goal Manager (Слой 37) + Long-Horizon Planning (Слой 38)
# Мозг агента: рассуждение, планирование, стратегия, гипотезы, диалог, генерация кода.
# Управление моделями: выбор, переключение, учёт стоимости.
# Цели и долгосрочные дорожные карты.
from .cognitive_core import CognitiveCore
from .model_manager import ModelManager, ModelProfile, ModelTier
from .goal_manager import GoalManager, Goal, GoalStatus, GoalPriority
from .long_horizon_planning import LongHorizonPlanning, Roadmap, Milestone, HorizonScale
from .identity import IdentityCore, AgentCapabilityStatus
from .proactive_mind import ProactiveMind

__all__ = [
    'CognitiveCore',
    'ModelManager', 'ModelProfile', 'ModelTier',
    'GoalManager', 'Goal', 'GoalStatus', 'GoalPriority',
    'LongHorizonPlanning', 'Roadmap', 'Milestone', 'HorizonScale',
    'IdentityCore', 'AgentCapabilityStatus',
    'ProactiveMind',
]
