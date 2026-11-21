import ast
import math
import operator

import pytest

from tzara.tools.calculator import Calculator, ExpressionEvaluator


@pytest.fixture(autouse=True)
def fail_fast_instead_of_hanging(monkeypatch):
    """Turn a missing exponent limit into a test failure rather than a hang.

    ``9 ** 9 ** 9`` runs for minutes and exhausts memory, and C code cannot be
    interrupted by a timeout, so ``pow`` is replaced with a guard that raises
    before doing any real work.
    """

    def guarded_pow(base, exponent):
        if exponent > ExpressionEvaluator.MAX_EXPONENT:
            raise AssertionError("pow called with a huge exponent: this would hang")
        return operator.pow(base, exponent)

    monkeypatch.setitem(ExpressionEvaluator.BINARY_OPERATORS, ast.Pow, guarded_pow)


@pytest.fixture(name="evaluator")
def evaluator_fixture():
    return ExpressionEvaluator()


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3", 5),
        ("(5 + 3) * 2", 16),
        ("7 // 2", 3),
        ("7 % 4", 3),
        ("2 ** 10", 1024),
        ("-4 + +2", -2),
        ("10 / 4", 2.5),
        ("sqrt(144) * 3 + 7", 43.0),
        ("floor(2.7)", 2),
        ("pi", math.pi),
        ("2 * pi", 2 * math.pi),
        ("  1 + 1  ", 2),
    ],
)
def test_evaluates_valid_expressions(evaluator, expression, expected):
    assert evaluator.evaluate(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "'a' * 5",
        "True + 1",
        "x + 1",
        "[1, 2]",
        "lambda: 1",
        "(1).real",
        "sqrt(x=4)",
        "1 << 3",
        "~1",
        "not 1",
        "1 if 1 else 2",
    ],
)
def test_rejects_anything_outside_arithmetic(evaluator, expression):
    with pytest.raises(ValueError):
        evaluator.evaluate(expression)


@pytest.mark.parametrize(
    ("expression", "error"),
    [
        ("1 / 0", ZeroDivisionError),
        ("5 % 0", ZeroDivisionError),
        ("sqrt(-1)", ValueError),
        ("sin(1, 2)", TypeError),
        ("2 +", SyntaxError),
        ("", SyntaxError),
        ("10.0 ** 999", OverflowError),
    ],
)
def test_propagates_arithmetic_and_syntax_errors(evaluator, expression, error):
    with pytest.raises(error):
        evaluator.evaluate(expression)


def test_huge_exponent_is_rejected_instead_of_hanging(evaluator):
    with pytest.raises(ValueError, match="Exponent too large"):
        evaluator.evaluate("9 ** 9 ** 9")


def test_exponent_limit_is_inclusive(evaluator):
    assert evaluator.evaluate(f"1 ** {ExpressionEvaluator.MAX_EXPONENT}") == 1
    with pytest.raises(ValueError, match="Exponent too large"):
        evaluator.evaluate(f"1 ** {ExpressionEvaluator.MAX_EXPONENT + 1}")


def test_oversized_integer_result_is_rejected(evaluator):
    with pytest.raises(ValueError, match="Result too large"):
        evaluator.evaluate("(10 ** 1000) ** 1000")


def test_private_math_members_are_not_callable(monkeypatch):
    monkeypatch.setattr(math, "_internal", lambda x: x, raising=False)
    evaluator = ExpressionEvaluator()  # collects math's members when created

    assert evaluator.evaluate("floor(2.5)") == 2
    with pytest.raises(ValueError, match="Unsupported function"):
        evaluator.evaluate("_internal(1)")


class TestCalculatorTool:
    def test_exposes_name_and_single_string_argument(self):
        tool = Calculator()
        assert tool.name == "calculator"
        assert list(tool.args) == ["expression"]
        assert tool.args["expression"]["type"] == "string"

    def test_returns_result_as_text(self):
        assert Calculator().invoke({"expression": "17 * 23"}) == "391"

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            ("1 / 0", "Error: division by zero"),
            ("2 +", "Error: "),
            ("sqrt(-1)", "Error: math domain error"),
            ("9 ** 9 ** 9", "Error: Exponent too large"),
            ("__import__('os')", "Error: Unsupported function"),
        ],
    )
    def test_reports_failures_as_text_instead_of_raising(self, expression, message):
        assert Calculator().invoke({"expression": expression}).startswith(message)
