"""Exact rational polynomial operations for root counting and multiplicities."""

import ast
from fractions import Fraction
from math import inf


ONE = (Fraction(1),)
ZERO = (Fraction(0),)


def trim(values):
    while len(values) > 1 and not values[-1]:
        values = values[:-1]
    return tuple(values)


def add(left, right, sign=1):
    n = max(len(left), len(right))
    left += ZERO * (n - len(left))
    right += ZERO * (n - len(right))
    return trim([a + sign*b for a, b in zip(left, right)])


def multiply(left, right):
    if len(left) + len(right) > 34:
        raise ValueError("degree")
    values = [Fraction(0)] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            values[i+j] += a*b
    return trim(values)


def from_ast(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return (Fraction(str(node.value)),)
    if isinstance(node, ast.Name) and node.id == "x":
        return (Fraction(0), Fraction(1))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        vals = from_ast(node.operand)
        return tuple(-v for v in vals) if isinstance(node.op, ast.USub) else vals
    if not isinstance(node, ast.BinOp):
        raise ValueError("non-rational polynomial")
    a, b = from_ast(node.left), from_ast(node.right)
    if isinstance(node.op, ast.Add):
        result = add(a, b)
    elif isinstance(node.op, ast.Sub):
        result = add(a, b, -1)
    elif isinstance(node.op, ast.Mult):
        result = multiply(a, b)
    elif isinstance(node.op, ast.Div) and len(b) == 1 and b[0]:
        result = tuple(v/b[0] for v in a)
    elif isinstance(node.op, ast.Pow) and len(b) == 1 and b[0].denominator == 1 and 0 <= b[0] <= 32:
        result = ONE
        for _ in range(int(b[0])):
            result = multiply(result, a)
            if any(max(v.numerator.bit_length(), v.denominator.bit_length()) > 2048 for v in result):
                raise ValueError("coefficient growth")
    else:
        raise ValueError("non-rational polynomial")
    if any(max(v.numerator.bit_length(), v.denominator.bit_length()) > 2048 for v in result):
        raise ValueError("coefficient growth")
    return result


def derivative(poly):
    return tuple(i*v for i, v in enumerate(poly) if i) or ZERO


def divide(a, b):
    q = [Fraction(0)] * max(1, len(a) - len(b) + 1)
    while a != ZERO and len(a) >= len(b):
        order = len(a) - len(b)
        q[order] = a[-1] / b[-1]
        a = add(a, ZERO*order + tuple(q[order]*v for v in b), -1)
    return trim(q), a


def monic(poly):
    return tuple(c/poly[-1] for c in poly) if poly != ZERO else ZERO


def gcd(p, q):
    while q != ZERO:
        p, q = q, divide(p, q)[1]
    return monic(p)


def square_free(poly):
    repeated = gcd(poly, derivative(poly))
    distinct = monic(divide(poly, repeated)[0])
    factors = []
    remaining = distinct
    multiplicity = 1
    while len(remaining) > 1:
        shared = gcd(remaining, repeated)
        factor = monic(divide(remaining, shared)[0])
        if len(factor) > 1:
            factors.append((factor, multiplicity))
        remaining = shared
        repeated = divide(repeated, shared)[0]
        multiplicity += 1
    return distinct, factors


def written_factors(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub) and from_ast(node.right) == ZERO:
        yield from written_factors(node.left)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        yield from written_factors(node.left)
        yield from written_factors(node.right)
    else:
        poly = from_ast(node)
        if len(poly) > 1:
            yield poly


def evaluate(poly, x):
    value = Fraction(0)
    for c in reversed(poly):
        value = value*x + c
    return value


def sturm(poly):
    a, b = poly, derivative(poly)
    sequence = [a, b]
    while b != ZERO:
        remainder = divide(a, b)[1]
        if remainder == ZERO:
            break
        # Positive rescaling keeps all sign variations intact.
        c = tuple(-v/abs(remainder[-1]) for v in remainder)
        sequence.append(c)
        a, b = b, c
    return sequence


def variations(sequence, endpoint):
    signs = []
    for poly in sequence:
        if endpoint == inf:
            value = poly[-1]
        elif endpoint == -inf:
            value = poly[-1]*(-1)**(len(poly)-1)
        else:
            value = evaluate(poly, endpoint)
        if value:
            signs.append(value > 0)
    return sum(a != b for a, b in zip(signs, signs[1:]))


def restricted_count(poly, target, interval):
    sequence = sturm(poly)
    low, high = (-inf, inf) if not interval else tuple(Fraction(str(x)) for x in interval)
    if target in {"positive", "nonnegative"}:
        low = max(low, Fraction(0))
    if target in {"negative", "nonpositive"}:
        high = min(high, Fraction(0))
    if low > high:
        return 0
    count = variations(sequence, low) - variations(sequence, high)
    if low != -inf and evaluate(poly, low) == 0 and (low != 0 or target not in {"positive", "negative"}):
        count += 1
    if target == "negative" and high == 0 and low < high and evaluate(poly, high) == 0:
        count -= 1
    return count


def isolate_real(poly):
    sequence = sturm(poly)
    bound = 1 + max(abs(c/poly[-1]) for c in poly[:-1])
    pending = [(-bound, bound, variations(sequence, -bound), variations(sequence, bound))]
    roots = []
    while pending:
        low, high, left, right = pending.pop()
        if left == right:
            continue
        middle = (low + high)/2
        if left-right == 1 and (evaluate(poly, middle) == 0 or
                               high-low <= max(abs(middle)/2**59, Fraction(1, 2**1078))):
            roots.append(middle)
            continue
        center = variations(sequence, middle)
        pending.append((middle, high, center, right))
        pending.append((low, middle, left, center))
    return roots


def analyze(tree, target, interval, resolve_real=False):
    try:
        poly = from_ast(tree)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    if len(poly) < 2:
        return poly, (), 0
    distinct, factors = square_free(poly)
    declared = list(written_factors(tree))
    if len(declared) > 1 and all(gcd(first, second) == ONE for i, first in enumerate(declared) for second in declared[i+1:]):
        # Keeping explicit coprime factors avoids cancellation in a single companion matrix.
        factors = [factor for part in declared for factor in square_free(part)[1]]
    count = restricted_count(distinct, target, interval)
    if resolve_real:
        resolved = []
        for factor, multiplicity in factors:
            if len(factor) > 2 and restricted_count(factor, "", ()) == len(factor)-1:
                resolved.extend(((Fraction(1)*-point, Fraction(1)), multiplicity) for point in isolate_real(factor))
            else:
                resolved.append((factor, multiplicity))
        factors = resolved
    try:
        numeric = tuple((tuple(float(c) for c in reversed(factor)), n) for factor, n in factors)
    except OverflowError:
        return None
    return poly, numeric, count


def denominators(tree):
    """Yield explicit divisors and bases raised to a negative rational power."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if isinstance(node.op, ast.Div):
            denominator = node.right
        elif isinstance(node.op, ast.Pow):
            try:
                exponent = from_ast(node.right)
            except (ValueError, OverflowError, ZeroDivisionError):
                continue
            if len(exponent) != 1 or exponent[0] >= 0:
                continue
            denominator = node.left
        else:
            continue
        yield denominator
