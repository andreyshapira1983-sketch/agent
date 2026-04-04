# evaluation — Evaluation & Benchmarking Layer (Слой 25)
# Оценка качества, бенчмарки, KPI, A/B сравнение стратегий.
from .evaluation import EvaluationSystem, EvalResult, EvalStatus, BenchmarkSuite
from .audit_journal import AuditJournal, AuditEntry, get_journal
from .trace_context import (
    TraceContext, StructuredAuditEvent, Actor,
    build_event, generate_trace_id, generate_event_id,
)

__all__ = [
    'EvaluationSystem', 'EvalResult', 'EvalStatus', 'BenchmarkSuite',
    'AuditJournal', 'AuditEntry', 'get_journal',
    'TraceContext', 'StructuredAuditEvent', 'Actor',
    'build_event', 'generate_trace_id', 'generate_event_id',
]
