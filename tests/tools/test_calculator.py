"""tests/tools/test_calculator.py — CalculatorTool"""

import pytest
from tools.builtins.calculator import CalculatorTool


class TestCalculatorTool:
    def setup_method(self):
        self.tool = CalculatorTool()

    # spec
    def test_spec_name(self):
        assert self.tool.spec.name == "calculator"

    def test_spec_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_spec_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    # basic arithmetic
    def test_addition(self):
        r = self.tool.execute(expression="2 + 3")
        assert r.success is True
        assert r.output == 5.0

    def test_subtraction(self):
        r = self.tool.execute(expression="10 - 4")
        assert r.output == 6.0

    def test_multiplication(self):
        r = self.tool.execute(expression="6 * 7")
        assert r.output == 42.0

    def test_division(self):
        r = self.tool.execute(expression="10 / 4")
        assert r.output == 2.5

    def test_floor_division(self):
        r = self.tool.execute(expression="10 // 3")
        assert r.output == 3.0

    def test_modulo(self):
        r = self.tool.execute(expression="10 % 3")
        assert r.output == 1.0

    def test_power(self):
        r = self.tool.execute(expression="2 ** 10")
        assert r.output == 1024.0

    def test_unary_minus(self):
        r = self.tool.execute(expression="-5")
        assert r.output == -5.0

    def test_nested_expression(self):
        r = self.tool.execute(expression="(3 + 7) * 2 - 1")
        assert r.output == 19.0

    def test_float_literal(self):
        r = self.tool.execute(expression="3.14 * 2")
        assert abs(r.output - 6.28) < 1e-9

    # errors
    def test_division_by_zero(self):
        r = self.tool.execute(expression="1 / 0")
        assert r.success is False
        assert "zero" in r.error.lower()

    def test_syntax_error(self):
        r = self.tool.execute(expression="2 +* 3")
        assert r.success is False

    def test_empty_expression(self):
        r = self.tool.execute(expression="")
        assert r.success is False

    def test_missing_expression_param(self):
        r = self.tool.execute()
        assert r.success is False

    def test_expression_too_long(self):
        r = self.tool.execute(expression="1 + " * 200)
        assert r.success is False
        assert "long" in r.error.lower()

    def test_power_bomb_rejected(self):
        r = self.tool.execute(expression="2 ** 9999")
        assert r.success is False

    def test_string_literal_rejected(self):
        r = self.tool.execute(expression="'hello'")
        assert r.success is False

    # metadata
    def test_metadata_has_duration(self):
        r = self.tool.execute(expression="1 + 1")
        assert "duration_ms" in r.metadata
