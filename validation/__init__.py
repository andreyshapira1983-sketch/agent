# validation — Data Validation & Contracts Layer (Слой 24)
# Валидация входных данных, схемы, контракты, защита от повреждённых данных.
from .data_validation import DataValidator, ValidationResult, ValidationStatus

__all__ = ['DataValidator', 'ValidationResult', 'ValidationStatus']
