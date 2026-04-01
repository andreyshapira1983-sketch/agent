# reasoning — Temporal Reasoning (Слой 40) + Causal Reasoning (Слой 41)
#             + Logical Reasoning (Cognitive Core, Layer 3 — reasoning engine)
#             + Optimization Engine (DP, scheduling, routing)
#             + Adaptive Reasoning (конфликты / неполные данные / пересмотр решений)
# Рассуждение о времени: последовательности, длительность, отношения.
# Рассуждение о причинах и следствиях: граф причинности, контрфактика.
# Логическое рассуждение: парадоксы, полный перебор, формальные модели стратегии.
# Оптимизация: DP для knapsack, scheduling, routing (строгие методы + эвристики).
from .temporal_reasoning import TemporalReasoningSystem, TemporalEvent, TemporalRelation
from .causal_reasoning import CausalReasoningSystem, CausalLink, CausalGraph
from .logical_reasoning import (
    LogicalReasoningSystem,
    ParadoxDetector, ParadoxType, ParadoxResolution,
    LogicalStatement, ParadoxResult,
    ExhaustiveSearch, SearchResult,
    AStarSolver, GraphPathResult,
    NormalFormGame, NashEquilibrium,
    DecisionTree, DecisionNode,
    MinimaxTree,
)
from .optimization_engine import (
    OptimizationEngine,
    Item, KnapsackSolution, UnboundedKnapsackSolver, BoundedKnapsackSolver,
    SchedulingJob, SchedulingResult, SchedulingSolver,
    RouteResult, VehicleRoutingSolver,
)
from .adaptive_reasoning import (
    GoalConflictResolver, GoalConflict, ConflictType, ConflictResolution,
    IncompletenessDetector, DataGap,
    DecisionRevisor, RevisionTrigger, RevisionDecision,
)

__all__ = [
    # Temporal
    'TemporalReasoningSystem', 'TemporalEvent', 'TemporalRelation',
    # Causal
    'CausalReasoningSystem', 'CausalLink', 'CausalGraph',
    # Logical
    'LogicalReasoningSystem',
    'ParadoxDetector', 'ParadoxType', 'ParadoxResolution',
    'LogicalStatement', 'ParadoxResult',
    'ExhaustiveSearch', 'SearchResult',
    'AStarSolver', 'GraphPathResult',
    'NormalFormGame', 'NashEquilibrium',
    'DecisionTree', 'DecisionNode',
    'MinimaxTree',
    # Optimization
    'OptimizationEngine',
    'Item', 'KnapsackSolution', 'UnboundedKnapsackSolver', 'BoundedKnapsackSolver',
    'SchedulingJob', 'SchedulingResult', 'SchedulingSolver',
    'RouteResult', 'VehicleRoutingSolver',
    # Adaptive Reasoning
    'GoalConflictResolver', 'GoalConflict', 'ConflictType', 'ConflictResolution',
    'IncompletenessDetector', 'DataGap',
    'DecisionRevisor', 'RevisionTrigger', 'RevisionDecision',
]
