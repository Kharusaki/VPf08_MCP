"""Инструмент «Калькулятор» без eval().

Разбираем выражение через ast и разрешаем только арифметику,
математические функции и константы. Никакого выполнения кода.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable, Dict

MAX_EXPRESSION_LEN = 500
MAX_POWER = 10_000

BIN_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

UNARY_OPS: Dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "factorial": math.factorial,
    "degrees": math.degrees,
    "radians": math.radians,
}

CONSTANTS: Dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


def _eval(node: ast.AST) -> Any:
    """Рекурсивно вычислить разрешённый узел AST."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("Разрешены только числа.")
        return node.value

    if isinstance(node, ast.BinOp):
        op = BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("Такая операция не разрешена.")
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER:
            raise ValueError(f"Слишком большая степень (максимум {MAX_POWER}).")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("Такая унарная операция не разрешена.")
        return op(_eval(node.operand))

    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise ValueError(f"Неизвестная переменная: {node.id}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ValueError("Разрешены только известные математические функции.")
        if node.keywords:
            raise ValueError("Именованные аргументы не поддерживаются.")
        args = [_eval(arg) for arg in node.args]
        return FUNCTIONS[node.func.id](*args)

    raise ValueError("Выражение содержит недопустимую конструкцию.")


def calculate(expression: str) -> float:
    """Вычислить математическое выражение."""
    expression = (expression or "").strip()
    if not expression:
        raise ValueError("Пустое выражение.")
    if len(expression) > MAX_EXPRESSION_LEN:
        raise ValueError(f"Слишком длинное выражение (максимум {MAX_EXPRESSION_LEN} символов).")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree)
    except SyntaxError as exc:
        raise ValueError(f"Синтаксическая ошибка: {exc.msg}") from exc

    if isinstance(result, complex):
        raise ValueError("Результат не является конечным числом.")
    try:
        value = float(result)
    except (OverflowError, TypeError) as exc:
        raise ValueError("Результат слишком большой.") from exc
    if not math.isfinite(value):
        raise ValueError("Результат не является конечным числом.")
    return value
