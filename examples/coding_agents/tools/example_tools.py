"""
Example tools for the Thenvoi Claude SDK agent runner.

Tool Response Format:
    Tools must return a dict with:
    - "content": list of content blocks, e.g., [{"type": "text", "text": "result"}]
    - "is_error": (optional) set to True if the tool encountered an error

Copy the tools you need and register them in __init__.py.
"""

from __future__ import annotations

import ast
import operator
from datetime import datetime
from random import randint
from typing import Any

from claude_agent_sdk import tool

# Safe operators for calculator
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Safe functions for calculator
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
}

# Maximum recursion depth for expression evaluation
_MAX_DEPTH = 50

# Maximum allowed values for pow operations to prevent resource exhaustion
_MAX_POW_BASE = 10000
_MAX_POW_EXPONENT = 100

# Maximum expression length to prevent DoS via very long strings
_MAX_EXPRESSION_LENGTH = 1000

def _safe_eval(node: ast.AST, depth: int = 0) -> float | int:
    """Safely evaluate an AST node containing only math operations."""
    if depth > _MAX_DEPTH:
        raise ValueError(f"Expression too deeply nested (max depth: {_MAX_DEPTH})")
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, depth + 1)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval(node.left, depth + 1)
        right = _safe_eval(node.right, depth + 1)
        # Bounds check for pow to prevent resource exhaustion
        if op_type is ast.Pow:
            if abs(left) > _MAX_POW_BASE or abs(right) > _MAX_POW_EXPONENT:
                raise ValueError(
                    f"Pow operands too large (max base: {_MAX_POW_BASE}, "
                    f"max exponent: {_MAX_POW_EXPONENT})"
                )
        return _OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        operand = _safe_eval(node.operand, depth + 1)
        return _OPERATORS[op_type](operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are supported")
        func_name = node.func.id
        if func_name not in _FUNCTIONS:
            raise ValueError(f"Unsupported function: {func_name}")
        eval_args = [_safe_eval(arg, depth + 1) for arg in node.args]
        # Bounds check for pow() function to prevent resource exhaustion
        if func_name == "pow" and len(eval_args) >= 2:
            if (
                abs(eval_args[0]) > _MAX_POW_BASE
                or abs(eval_args[1]) > _MAX_POW_EXPONENT
            ):
                raise ValueError(
                    f"Pow operands too large (max base: {_MAX_POW_BASE}, "
                    f"max exponent: {_MAX_POW_EXPONENT})"
                )
        return _FUNCTIONS[func_name](*eval_args)
    else:
        raise ValueError(f"Unsupported expression type: {type(node).__name__}")

def safe_math_eval(expression: str) -> float | int:
    """Safely evaluate a mathematical expression string."""
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError(
            f"Expression too long (max {_MAX_EXPRESSION_LENGTH} characters)"
        )
    tree = ast.parse(expression, mode="eval")
    return _safe_eval(tree)

@tool("calculator", "Evaluate math expressions", {"expression": str})
async def calculator(args: dict[str, Any]) -> dict[str, Any]:
    """Example: calculator("2 + 2") → "4" """
    try:
        result = safe_math_eval(args["expression"])
        return {"content": [{"type": "text", "text": str(result)}]}
    except (ValueError, SyntaxError, TypeError, KeyError, ZeroDivisionError) as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}

@tool("get_time", "Get current date/time", {})
async def get_time(_args: dict[str, Any]) -> dict[str, Any]:
    """Returns current time in ISO format."""
    return {"content": [{"type": "text", "text": datetime.now().isoformat()}]}

@tool("random_number", "Generate random number", {"min": int, "max": int})
async def random_number(args: dict[str, Any]) -> dict[str, Any]:
    """Example: random_number(1, 100) → "42" """
    try:
        min_val = int(args["min"])
        max_val = int(args["max"])
        if min_val > max_val:
            return {
                "content": [{"type": "text", "text": "Error: min must be <= max"}],
                "is_error": True,
            }
        result = randint(min_val, max_val)
        return {"content": [{"type": "text", "text": str(result)}]}
    except (ValueError, TypeError, KeyError) as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}
