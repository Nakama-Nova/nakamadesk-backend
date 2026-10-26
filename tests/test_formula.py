"""The formula engine is the one place staff-typed text is evaluated: it must be safe."""

import pytest

from app.services import formula
from app.services.formula import FormulaError, evaluate, validate


def test_arithmetic_and_names():
    assert evaluate("H - 30", 450, 400, 900) == 870
    assert evaluate("L * W / 2", 450, 400, 900) == 90000
    assert evaluate("(L - 80) / 2", 450, 400, 900) == 185
    assert evaluate("-L + 500", 450, 400, 900) == 50
    assert evaluate("l + w + h", 1, 2, 3) == 6  # any case
    assert evaluate("2 + 3 * 4", 0, 0, 0) == 14  # normal precedence
    assert evaluate("10.5", 0, 0, 0) == 10.5


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('x')",
        "L.real",
        "L ** 2",
        "2 ** 999",
        "abs(L)",
        "max(L, W)",
        "[1, 2]",
        "'text'",
        "True",
        "None",
        "X + 1",
        "lambda: 1",
        "L if W else H",
        "L; W",
        "L // 2",
        "L % 2",
        "L < W",
        "(L, W)",
        "1e9",
        "",
        "   ",
        "L +",
        "+" * 40 + "1",
        "L" + " + L" * 30,
        "L" * 101,
    ],
)
def test_rejects_anything_that_is_not_plain_arithmetic(expr):
    with pytest.raises(FormulaError):
        validate(expr)
    with pytest.raises(FormulaError):
        evaluate(expr, 450, 400, 900)


def test_division_by_zero_is_a_clear_error():
    with pytest.raises(FormulaError, match="zero"):
        evaluate("L / (W - W)", 450, 400, 900)


def test_results_are_bounded():
    with pytest.raises(FormulaError, match="range"):
        evaluate("L * L * L", 100000, 1, 1)  # 1e15


def test_non_string_input_is_rejected():
    with pytest.raises(FormulaError):
        validate(None)  # type: ignore[arg-type]
    with pytest.raises(FormulaError):
        validate(12)  # type: ignore[arg-type]


def test_formula_module_never_calls_eval_exec_or_compile():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(formula))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__", "getattr"}
