"""Bounded arithmetic interpreter; no eval, names, calls, attributes or code execution."""
import ast
import math
import operator
from app.core.errors import DomainError

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}


def calculate(expression: str) -> float | int:
    try:
        tree = ast.parse(expression, mode="eval")
        if len(list(ast.walk(tree))) > 64:
            raise ValueError()

        def visit(node, depth=0):
            if depth > 16:
                raise ValueError()
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = node.value
            elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
                value = visit(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and type(node.op) in OPS:
                left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
                if isinstance(node.op, ast.Pow) and abs(right) > 100:
                    raise ValueError()
                value = OPS[type(node.op)](left, right)
            else:
                raise ValueError()
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
                raise ValueError()
            return value
        return visit(tree.body)
    except (ValueError, TypeError, SyntaxError, ArithmeticError, RecursionError):
        raise DomainError(422, "CALCULATOR_INVALID", "仅支持有限范围的四则运算、取余和幂；请检查表达式或除零问题") from None
