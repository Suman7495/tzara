"""Calculator tool: safe evaluation of arithmetic expressions."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

Number = int | float


class ExpressionEvaluator:
    """Evaluates arithmetic expressions by walking their syntax tree.

    Nothing is passed to ``eval``. Only numeric literals, arithmetic operators,
    a few constants and the callable functions of :mod:`math` are accepted.
    Exponents and integer sizes are bounded so a short expression such as
    ``9**9**9`` cannot exhaust CPU or memory.
    """

    BINARY_OPERATORS: ClassVar[dict[type[ast.operator], Callable[[Any, Any], Number]]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
    }
    UNARY_OPERATORS: ClassVar[dict[type[ast.unaryop], Callable[[Any], Number]]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    CONSTANT_NAMES: ClassVar[tuple[str, ...]] = ("pi", "e", "tau")
    MAX_EXPONENT: ClassVar[int] = 1000
    MAX_INTEGER_BITS: ClassVar[int] = 100_000

    def __init__(self) -> None:
        """Collect the whitelisted functions and constants from :mod:`math`."""
        self._functions: dict[str, Callable[..., Number]] = {
            name: member
            for name in dir(math)
            if not name.startswith("_") and callable(member := getattr(math, name))
        }
        self._constants: dict[str, Number] = {
            name: getattr(math, name) for name in self.CONSTANT_NAMES
        }

    def evaluate(self, expression: str) -> Number:
        """Evaluate an arithmetic expression.

        Args:
            expression: Python-syntax arithmetic, e.g. ``(5 + 3) * 2``.

        Returns:
            The numeric result.

        Raises:
            SyntaxError: If the expression cannot be parsed.
            ValueError: If it uses anything outside the supported subset, or a
                function rejects its arguments.
            ArithmeticError: On division by zero or numeric overflow.
            TypeError: If a function is called with the wrong arguments.
        """
        return self._visit(ast.parse(expression.strip(), mode="eval").body)

    def _visit(self, node: ast.expr) -> Number:
        """Evaluate one syntax-tree node."""
        if isinstance(node, ast.Constant):
            return self._literal(node)
        if isinstance(node, ast.Name):
            return self._constant(node)
        if isinstance(node, ast.BinOp):
            return self._binary(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        raise ValueError(f"Unsupported syntax: {type(node).__name__}")

    @staticmethod
    def _literal(node: ast.Constant) -> Number:
        """Return a numeric literal, rejecting strings, booleans and complex."""
        value = node.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"Unsupported literal: {value!r}")
        return value

    def _constant(self, node: ast.Name) -> Number:
        """Return a named constant such as ``pi``."""
        try:
            return self._constants[node.id]
        except KeyError:
            raise ValueError(f"Unknown name: {node.id}") from None

    def _binary(self, node: ast.BinOp) -> Number:
        """Apply a binary operator, bounding exponents and integer size."""
        apply = self.BINARY_OPERATORS.get(type(node.op))
        if apply is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left, right = self._visit(node.left), self._visit(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > self.MAX_EXPONENT:
            raise ValueError(f"Exponent too large (limit {self.MAX_EXPONENT})")
        return self._bounded(apply(left, right))

    def _unary(self, node: ast.UnaryOp) -> Number:
        """Apply a unary plus or minus."""
        apply = self.UNARY_OPERATORS.get(type(node.op))
        if apply is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return apply(self._visit(node.operand))

    def _call(self, node: ast.Call) -> Number:
        """Call a whitelisted :mod:`math` function with positional arguments."""
        if not isinstance(node.func, ast.Name) or node.func.id not in self._functions:
            raise ValueError("Unsupported function")
        if node.keywords:
            raise ValueError("Keyword arguments are not supported")
        arguments = [self._visit(argument) for argument in node.args]
        return self._bounded(self._functions[node.func.id](*arguments))

    def _bounded(self, value: Number) -> Number:
        """Reject integers too large to compute with safely."""
        if isinstance(value, int) and value.bit_length() > self.MAX_INTEGER_BITS:
            raise ValueError("Result too large")
        return value


class CalculatorInput(BaseModel):
    """Arguments accepted by the calculator tool."""

    expression: str = Field(
        description="A valid Python math expression, e.g. '(5 + 3) * 2' or 'sqrt(16)'."
    )


class Calculator(BaseTool):
    """Tool that evaluates a mathematical expression for the agent."""

    name: str = "calculator"
    description: str = (
        "Evaluate a mathematical expression.\n\n"
        "Use ONLY if the user asks to calculate, compute, add, subtract, multiply, "
        "divide, or evaluate an expression.\n\n"
        'Examples: "what is 12 * 7", "calculate (5 + 3) * 2", "compute sqrt(16)".\n\n'
        "Do NOT use for explanations, word problems, or general conversation."
    )
    args_schema: type[BaseModel] = CalculatorInput
    _evaluator: ExpressionEvaluator = PrivateAttr(default_factory=ExpressionEvaluator)

    def _run(self, expression: str) -> str:  # pylint: disable=arguments-differ
        """Evaluate ``expression``.

        Args:
            expression: Python-syntax arithmetic.

        Returns:
            The result as text, or ``Error: ...`` describing why it failed. Errors
            are returned rather than raised so the model can read and correct them.
        """
        try:
            return str(self._evaluator.evaluate(expression))
        except (ArithmeticError, SyntaxError, TypeError, ValueError) as error:
            return f"Error: {error}"
