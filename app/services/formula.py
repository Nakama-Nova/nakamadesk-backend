"""
Safe arithmetic for part sizes.

A part's size is a small formula of the piece's overall size, e.g. a leg's
length is `H - 30`, a side rail's is `W - 100`. Staff type these, so they are
parsed with Python's `ast` and walked by hand: only numbers, the names L W H
(any case), + - * / and parentheses are understood. Nothing is ever passed to
eval(), so there are no calls, attributes, indexing, powers or other names to
abuse, and a hostile or mistyped formula can only produce a clear error.
"""

import ast
import math
from typing import Dict

MAX_LEN = 100
MAX_NODES = 30
LIMIT = 1_000_000.0  # no number (constant or intermediate result) may exceed this
NAMES = ("L", "W", "H")


class FormulaError(ValueError):
    """A formula that cannot be used. The message is safe to show to staff."""


def _parse(expr: str) -> ast.expr:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("formula is empty")
    if len(expr) > MAX_LEN:
        raise FormulaError(f"formula is longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        raise FormulaError("formula is not valid arithmetic") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise FormulaError("formula is too long or too complicated")
    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            continue
        if isinstance(node, ast.operator):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.unaryop):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError("only numbers are allowed")
            if abs(node.value) > LIMIT:
                raise FormulaError("number is too large")
            continue
        if isinstance(node, ast.Name):
            if node.id.upper() not in NAMES:
                raise FormulaError(f"unknown name '{node.id}'. Use L, W and H")
            continue
        raise FormulaError("only + - * / ( ) numbers and L W H are allowed")
    return tree.body


def validate(expr: str) -> None:
    """Raise FormulaError if the text is not an allowed formula."""
    _parse(expr)


def _eval(node: ast.expr, env: Dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env[node.id.upper()]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Add):
            out = a + b
        elif isinstance(node.op, ast.Sub):
            out = a - b
        elif isinstance(node.op, ast.Mult):
            out = a * b
        else:
            if b == 0:
                raise FormulaError("divides by zero")
            out = a / b
        if not math.isfinite(out) or abs(out) > LIMIT * LIMIT:
            raise FormulaError("result is out of range")
        return out
    raise FormulaError("unsupported formula")  # unreachable after _parse


def evaluate(expr: str, length: float, width: float, height: float) -> float:
    """Value of the formula for the overall size L=length, W=width, H=height (mm)."""
    value = _eval(_parse(expr), {"L": length, "W": width, "H": height})
    if not math.isfinite(value) or abs(value) > LIMIT:
        raise FormulaError("result is out of range")
    return value
