"""Recognize domain exclusions before accepting a numerical result."""

import ast
from fractions import Fraction
import math

from . import polynomial as rational


def rational_polynomial(node):
    try:
        return rational.from_ast(node)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def contains_zero(node, interval):
    """Return True, False, or None when this check cannot determine the answer."""
    exact = rational.analyze(node, "", interval)
    if exact is not None:
        return exact[0] == rational.ZERO or exact[2] > 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return contains_zero(node.operand, interval)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mult):
            factors = (contains_zero(node.left, interval), contains_zero(node.right, interval))
            return True if True in factors else None if None in factors else False
        if isinstance(node.op, ast.Pow):
            power = rational_polynomial(node.right)
            if power is not None and len(power) == 1:
                return contains_zero(node.left, interval) if power[0] > 0 else False
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or len(node.args) != 1:
        return None
    name = node.func.id
    argument = node.args[0]
    if name == "exp":
        return False
    if name in {"abs", "sqrt"}:
        return contains_zero(argument, interval)
    if name in {"log", "log10"}:
        return contains_zero(ast.BinOp(left=argument, op=ast.Sub(), right=ast.Constant(1)), interval)
    if name not in {"sin", "cos", "tan"} or not interval:
        return None
    affine = rational_polynomial(argument)
    if affine is None or len(affine) > 2:
        return None
    try:
        low, high = sorted(float(rational.evaluate(affine, Fraction(str(x)))) for x in interval)
    except OverflowError:
        return None
    if not math.isfinite(low) or not math.isfinite(high):
        return None
    offset = 0.5 if name == "cos" else 0
    low, high = low/math.pi, high/math.pi
    # Allow for floating-point uncertainty at the endpoints of the phase interval.
    margin = 4 * max(math.ulp(low), math.ulp(high))
    return math.ceil(low-offset-margin) <= math.floor(high-offset+margin)


def inspect_domain(tree, interval):
    """Return a known exclusion, a verification flag, and isolated forbidden points."""
    checks = [(node, "分母为零") for node in rational.denominators(tree)]
    checks.extend((ast.Call(func=ast.Name(id="cos"), args=node.args), "正切函数无定义")
                  for node in ast.walk(tree) if isinstance(node, ast.Call) and
                  isinstance(node.func, ast.Name) and node.func.id == "tan")
    unresolved = []
    excluded = set()
    for node, label in checks:
        zero = contains_zero(node, interval)
        if zero is False:
            continue
        if zero is True and interval:
            return f"计算区间内有{label}的点。请核对原式的定义域和区间。", "", ()
        if not interval:
            exact = rational.analyze(node, "", (), resolve_real=True)
            if exact is not None:
                poly, factors, real_count = exact
                if poly == rational.ZERO:
                    return "式中有恒等于零的分母，无法计算。", "", ()
                points = {-coefficients[1]/coefficients[0] for coefficients, _ in factors if len(coefficients) == 2}
                if len(points) == real_count:
                    excluded.update(points)
                    continue
        unresolved.append(label)
    warning = ""
    if unresolved:
        kinds = "、".join(dict.fromkeys(unresolved))
        warning = f"尚未排除{kinds}的点。当前近似值需结合原式定义域核对。"
    return "", warning, tuple(sorted(excluded))
