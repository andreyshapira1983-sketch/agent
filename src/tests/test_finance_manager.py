from __future__ import annotations

from src.finance.finance_manager import FinanceManager


def test_balance_updates_after_income_and_expense() -> None:
    fm = FinanceManager()
    fm.add_income(250)
    fm.add_expense(40)
    assert fm.get_balance() == 210


def test_financial_report_contains_current_values() -> None:
    fm = FinanceManager()
    fm.add_income(100)
    fm.add_income(50)
    fm.add_expense(30)

    report = fm.get_financial_report()
    assert report == {"income": 150, "expenses": 30, "balance": 120}


def test_negative_balance_when_expenses_exceed_income() -> None:
    fm = FinanceManager()
    fm.add_income(10)
    fm.add_expense(25)
    assert fm.get_balance() == -15