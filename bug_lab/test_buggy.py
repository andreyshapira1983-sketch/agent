"""Truth-table tests for bug_lab.buggy — DESIGNED to fail.

The failures are the evidence the live agent must surface.
"""
import pytest

from bug_lab.buggy import average, is_even, safe_divide


class TestAverage:
    def test_simple(self):
        # mean([2, 4, 6]) == 4.0
        assert average([2.0, 4.0, 6.0]) == 4.0  # WILL FAIL due to off-by-one

    def test_single_value(self):
        # mean([10]) == 10.0
        assert average([10.0]) == 10.0  # WILL CRASH: division by zero


class TestIsEven:
    @pytest.mark.parametrize("n", [0, 2, 4, 10, 100])
    def test_even_numbers(self, n: int):
        assert is_even(n) is True  # WILL FAIL — function returns the opposite

    @pytest.mark.parametrize("n", [1, 3, 5, 11, 99])
    def test_odd_numbers(self, n: int):
        assert is_even(n) is False  # WILL FAIL — function returns the opposite


class TestSafeDivide:
    """These pass — safe_divide is the only correct function in the file.
    Their presence proves the test runner DID work, the failures above
    are real bugs not a discovery infrastructure problem."""

    def test_basic(self):
        assert safe_divide(10, 2) == 5.0

    def test_zero_denominator_raises(self):
        with pytest.raises(ValueError, match="division by zero"):
            safe_divide(1, 0)
