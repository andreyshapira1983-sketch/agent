"""Deliberately buggy arithmetic helpers used by the self-repair drill.

Two functions are intentionally wrong:
    * average() divides by len(numbers) - 1 instead of len(numbers).
    * is_even() returns the WRONG branch.
"""


def average(numbers: list[float]) -> float:
    """Mean of a non-empty list. BUG: off-by-one in the denominator."""
    total = sum(numbers)
    # BUG: should be len(numbers), not len(numbers) - 1
    return total / (len(numbers) - 1)


def is_even(n: int) -> bool:
    """True iff n is even. BUG: condition flipped."""
    # BUG: should be `n % 2 == 0`, not `n % 2 == 1`
    return n % 2 == 1


def safe_divide(a: float, b: float) -> float:
    """a / b with a guard for b == 0. This one is CORRECT."""
    if b == 0:
        raise ValueError("division by zero")
    return a / b
