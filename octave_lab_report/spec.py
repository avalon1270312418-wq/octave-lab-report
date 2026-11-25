"""Parse numerical assignments and compile their math expressions."""

import ast
from dataclasses import dataclass, replace
import math
import re
import unicodedata
from fractions import Fraction

from . import polynomial as rational
from .domain import inspect_domain


class LabError(Exception):
    """An input or runtime problem that can be explained to the user."""


class Cancelled(Exception):
    """The user returned from the run confirmation without starting Octave."""


METHODS = {
    "bisection": ("二分法", r"二分(?:法|迭代)|\bbisection\b", "NA1_alg1.m"),
    "newton": ("牛顿法", r"牛顿(?!插值|插商|均差|差商|差分)(?:迭代)?(?:法)?|\bnewton(?:-raphson)?(?![\w -]*interpolation)\b", "NA1_alg2.m"),
    "secant": ("割线法", r"割线(?:迭代)?(?:法)?|弦截法|\bsecant\b", "NA1_alg3.m"),
    "false_position": ("试位法", r"试位法|假位法|假位置法|\bregula\s+falsi\b|\bfalse\s+position\b", "NA1_alg4.m"),
    "fixed_point": ("不动点迭代法", r"(?:不动|固定)点迭代(?:法)?|\bfixed[- ]point\b", "NA1_alg5.m"),
    "steffensen": ("斯特芬森法", r"斯特(?:芬|弗)森(?:法)?|\bsteffensen\b", "NA1_alg6.m"),
    "fzero": ("fzero", r"\bfzero\b", None),
    "roots": ("roots", r"\broots\b", None),
    "gaussian": ("高斯消元法", r"(?:列主元)?高斯消元(?:法)?|\bgaussian elimination\b", "NA1_alg7.m"),
    "fsolve": ("fsolve", r"\bfsolve\b", None),
    "lagrange": ("拉格朗日插值法", r"拉格朗日(?:多项式)?插值(?:法)?|\blagrange(?: interpolation)?\b", "NA1_alg8.m"),
    "newton_interp": ("牛顿插值法", r"牛顿(?:插值|插商|均差|差商|差分)(?:插值)?(?:法)?|\bnewton interpolation\b", "NA1_alg9.m"),
    "trapezoid": ("复合梯形法", r"复合梯形(?:法|公式)?|\bcomposite trapezoid(?:al)?\b", "NA1_alg10.m"),
    "simpson": ("复合辛普森法", r"复合辛普森(?:法|公式)?|\bcomposite simpson\b", "NA1_alg11.m"),
    "euler": ("显式欧拉法", r"(?<!改进)(?<!隐式)(?:显式)?欧拉法|\beuler\b", "NA1_alg12.m"),
    "rk4": ("经典四阶龙格–库塔法", r"(?:经典)?(?:四阶|4阶)龙格\s*-?\s*库塔(?:法)?|\brk4\b", "NA1_alg13.m"),
    "spline": ("三次样条插值", r"(?:三次)?样条插值(?:法)?|\bcubic spline\b", "NA1_alg14.m"),
    "least_squares": ("多项式最小二乘法", r"(?:多项式)?最小二乘(?:法|拟合)|\bleast squares\b", "NA1_alg15.m"),
    "brent": ("布伦特法", r"布伦特(?:法)?|\bbrent\b", "NA1_alg16.m"),
    "jacobi": ("雅可比迭代法", r"雅可比(?:迭代)?法|\bjacobi\b", "NA1_alg17.m"),
    "gauss_seidel": ("高斯–赛德尔迭代法", r"高斯\s*-?\s*赛德尔(?:迭代)?(?:法)?|\bgauss[- ]seidel\b", "NA1_alg18.m"),
    "adaptive_integral": ("自适应高斯–克朗罗德积分", r"自适应(?:高斯\s*-?\s*克朗罗德)?(?:数值)?积分(?:法)?|\bquadgk\b", "NA1_alg19.m"),
}
FUNCTIONS = {"exp", "log", "log10", "sqrt", "sin", "cos", "tan", "abs"}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
PAIR = rf"\[\s*({NUMBER})\s*[,，;；]\s*({NUMBER})\s*\]"
ALPHABET = r"[A-Za-z0-9_().+*/^\- \t]"
TASK_HEADING = re.compile(r"^\s*(?:任务\s*\d+|第\s*[一二三四五六七八九十\d]+\s*题)\s*[:：]?\s*(.*)$")
OUTPUT_HEADING = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+[.、．]\s*)?(?:实验步骤(?:与实验程序|\s*[:：]?\s*$)|实验结果|结果与运行|实验分析与结论|实验结论)")
REQUIREMENT_HEADING = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+[.、．]\s*)?(?:实验内容|实验要求|实验任务)\s*[:：]?\s*$")
PAGE_MARKER = re.compile(r"^【图片 \d+】(?: .*)?$")
INITIAL_VALUES = rf"(?:{PAIR}|\(\s*({NUMBER})\s*[,，]\s*({NUMBER})\s*\)|({NUMBER}))"
VECTOR = r"\[([^\[\]\n]+)\]"
INTERPOLATION_METHODS = {"lagrange", "newton_interp", "spline"}
INTEGRAL_METHODS = {"trapezoid", "simpson", "adaptive_integral"}
ODE_METHODS = {"euler", "rk4"}
LINEAR_METHODS = {"gaussian","jacobi","gauss_seidel"}
EXTRA_METHODS = LINEAR_METHODS | {"fsolve", "least_squares"} | INTERPOLATION_METHODS | INTEGRAL_METHODS | ODE_METHODS
ROOT_NAMES = {"positive": "正根", "negative": "负根", "nonnegative": "非负实根", "nonpositive": "非正实根",
              "all_real": "全部实根", "all": "全部根"}
PLOT_NAMES = {"residual": "残差收敛图", "step": "相邻迭代差收敛图", "absolute": "绝对误差收敛图",
              "relative": "相对误差收敛图", "interpolation": "插值曲线", "ode": "数值解曲线",
              "regression": "拟合曲线", "integral": "被积函数曲线", "grid_error": "分段数与绝对误差估计"}
UNAVAILABLE = re.compile(r"(?:二阶|三阶|五阶|自适应)龙格\s*-?\s*库塔|(?:改进|隐式)欧拉法|牛顿下山法|改进牛顿法|修正牛顿法|阻尼牛顿法|改进割线法|列主元(?:选取)?高斯[-－]?约当|高斯[-－]?约当|追赶法|共轭梯度法|牛顿迭代法求解方程组|分段线性插值|自然(?:三次)?样条|周期(?:三次)?样条|夹持(?:三次)?样条", re.I)
METHOD_LINE = re.compile(r"^(?:(?:求解|计算|实验|插值|数值)?方法)\s*[:=]\s*(.+)$")


@dataclass(frozen=True)
class Equation:
    label: str
    expression: str
    derivative: str
    coefficients: tuple[float, ...]
    methods: tuple[str, ...]
    interval: tuple[float, ...] = ()
    initial: tuple[float, ...] = ()
    tolerance: float = 1e-12
    digits: int = 6
    max_steps: int = 100
    iteration_function: str = ""
    plot: str = ""
    reference: float | None = None
    root_target: str = ""
    additional_plots: tuple[str, ...] = ()
    factors: tuple = ()
    real_count: int | None = None
    decimals: int | None = None
    fixed_steps: int | None = None
    trace_table: bool = False
    residual_tolerance: float | None = None
    method_initials: tuple = ()
    error_tolerance: float | None = None
    error_mode: str = ""
    open_interval: tuple = ()
    domain_problem: str = ""
    excluded_points: tuple = ()
    capture: tuple = ()
    domain_warning: str = ""

    @property
    def plots(self):
        return (self.plot, *self.additional_plots) if self.plot else ()


@dataclass(frozen=True)
class ExtraTask:
    kind: str
    label: str
    methods: tuple[str, ...]
    tolerance: float
    digits: int
    max_steps: int
    plot: str = ""
    initial: tuple[float, ...] = ()
    expressions: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    matrix: tuple[tuple[float, ...], ...] = ()
    rhs: tuple[float, ...] = ()
    nodes: tuple[float, ...] = ()
    values: tuple[float, ...] = ()
    query: tuple[float, ...] = ()
    decimals: int | None = None
    segments: int = 0
    degree: int = 0
    trace_table: bool = False
    accuracy_tolerance: float | None = None
    domain_problem: str = ""
    capture: tuple = ()
    domain_warning: str = ""

    @property
    def plots(self):
        return (self.plot,) if self.plot else ()


@dataclass(frozen=True)
class Assignment:
    equations: tuple[Equation | ExtraTask, ...]
    help_commands: tuple[str, ...]
    screenshot_note: str = ""
    report_template_name: str = ""
    original_requirements: str = ""
    screenshot_kind: str = "result"
    layout_note: str = ""
    pending: tuple = ()
    exports: tuple = ()


def normalize(text):
    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ˣ", "0123456789-+x")
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ˣ]+", lambda m: "^(" + m[0].translate(superscripts) + ")", text)
    text = re.sub(r"[①-⑳]", "；", text)
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("−–×÷′’", "--*/''")).replace("π", "pi")
    return re.sub(r"\^\{([^{}]+)\}", r"^(\1)", text)


def math_tree(expression, variables=("x",)):
    text = re.sub(r"\.(?=[*/^])", "", normalize(expression).lower()).replace("^", "**")
    text = re.sub(r"\bln(?=\s|\()", "log", text)
    text = re.sub(r"\b(log|sin|cos|tan)\s+(x|[0-9]+(?:\.[0-9]+)?)\b", r"\1(\2)", text)
    text = text.replace("xexp(", "x*exp(").replace("xe**", "x*e**")
    if re.search(r"\d[ \t]+[\d.]|\.\s+\d", text):
        raise LabError(f"表达式“{expression}”的数字中有空格，请核对原式。")
    if len(text) > 250 or not re.fullmatch(r"[a-z0-9.+*/()\-\s]+", text):
        raise LabError(f"无法识别数学表达式“{expression}”。")
    tokens = re.findall(r"(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|[a-z][a-z0-9]*|\*\*|\S", text)
    parts = []
    for token in tokens:
        previous = parts[-1] if parts else ""
        closed = previous == ")" or previous in {*variables, "e", "pi"} or bool(re.fullmatch(NUMBER, previous))
        opened = token == "(" or token in FUNCTIONS | {*variables, "e", "pi"} or bool(re.fullmatch(NUMBER, token))
        if token not in variables and len(variables) > 1 and len(token) > 1 and all(char in variables for char in token):
            token = "*".join(token)
            opened = True
        if closed and opened:
            parts.append("*")
        parts.append(token)
    try:
        return ast.parse("".join(parts), mode="eval").body
    except (SyntaxError, ValueError) as exc:
        raise LabError(f"无法识别数学表达式“{expression}”。") from exc


def compile_math(node, derivative=False, variables=None):
    if variables and isinstance(node, ast.Name) and node.id in variables:
        return variables[node.id]
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        if not math.isfinite(node.value) or abs(node.value) > 1e100:
            raise LabError("数学表达式中的数值超出支持范围。")
        return "0" if derivative else repr(node.value)
    if isinstance(node, ast.Name) and node.id in {"x", "e", "pi"}:
        return str(int(node.id == "x")) if derivative else {"x": "x", "e": "exp(1)", "pi": "pi"}[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = "-" if isinstance(node.op, ast.USub) else "+"
        return f"({sign}{compile_math(node.operand, derivative, variables)})"
    if isinstance(node, ast.BinOp):
        symbols = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}
        symbol = symbols.get(type(node.op))
        if symbol:
            left, right = compile_math(node.left, variables=variables), compile_math(node.right, variables=variables)
            if not derivative:
                if symbol == "^" and isinstance(node.left, ast.Name) and node.left.id == "e":
                    return f"exp({right})"
                return f"({left}{symbol}{right})"
            dl, dr = compile_math(node.left, True), compile_math(node.right, True)
            if symbol in "+-":
                return f"({dl}{symbol}{dr})"
            if symbol == "*":
                return f"({dl}*{right}+{left}*{dr})"
            if symbol == "/":
                return f"(({dl}*{right}-{left}*{dr})/{right}^2)"
            if not any(isinstance(n, ast.Name) and n.id == "x" for n in ast.walk(node.right)):
                try:
                    exponent = rational.from_ast(node.right)
                except (ValueError, OverflowError, ZeroDivisionError):
                    exponent = ()
                if exponent == rational.ZERO:
                    return "0"
                if exponent == rational.ONE:
                    return dl
                return f"({right}*{left}^({right}-1)*{dl})"
            if dl == "0":
                return f"({left}^{right}*log({left})*{dr})"
            return f"({left}^{right}*({dr}*log({left})+{right}*{dl}/{left}))"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords:
        name, arg = node.func.id, compile_math(node.args[0], variables=variables)
        if not derivative:
            return f"{name}({arg})"
        outer = {
            "sin": f"cos({arg})", "cos": f"-sin({arg})", "tan": f"1/cos({arg})^2", "exp": f"exp({arg})",
            "sqrt": f"1/(2*sqrt({arg}))", "log": f"1/({arg})", "log10": f"1/(({arg})*log(10))",
        }
        if name not in outer:
            raise LabError("牛顿法处理 abs 时需要提供导数，例如 f'(x) = …。")
        return f"(({outer[name]})*{compile_math(node.args[0], True)})"
    raise LabError("方程仅支持变量 x、常数、四则运算、幂和常见初等函数。")


def polynomial(node):
    """Return ascending real coefficients for the polynomial subset of the grammar."""
    if isinstance(node, ast.Constant):
        return [float(node.value)]
    if isinstance(node, ast.Name):
        return {"x": [0., 1.], "pi": [math.pi], "e": [math.e]}[node.id]
    if isinstance(node, ast.UnaryOp):
        values = polynomial(node.operand)
        return [-v for v in values] if isinstance(node.op, ast.USub) else values
    if isinstance(node, ast.BinOp):
        left, right = polynomial(node.left), polynomial(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            size = max(len(left), len(right))
            left += [0.] * (size - len(left))
            right += [0.] * (size - len(right))
            return [a - b if isinstance(node.op, ast.Sub) else a + b for a, b in zip(left, right)]
        if isinstance(node.op, ast.Div) and len(right) == 1 and right[0] != 0:
            return [v / right[0] for v in left]
        if isinstance(node.op, ast.Mult):
            return multiply(left, right)
        if isinstance(node.op, ast.Pow) and len(right) == 1 and right[0].is_integer() and 0 <= right[0] <= 32:
            result = [1.]
            for _ in range(int(right[0])):
                result = multiply(result, left)
            return result
    raise LabError("roots 需要次数不超过 32 的实系数多项式。")


def multiply(left, right):
    if len(left) + len(right) > 34:
        raise LabError("roots 需要次数不超过 32 的实系数多项式。")
    result = [0.] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            result[i + j] += a * b
    return result


def without_negations(text):
    return re.sub(r"(?:不要|无需|不用|禁止|不得|不要求|不必|不需|未采用)(?:再|使用|采用|用|执行|绘制)?[^，,。；;\n]*", "", text)


def selected_methods(text):
    positive = without_negations(text)
    explicit = [match[1] for line in positive.splitlines() if (match := METHOD_LINE.fullmatch(line))]
    if explicit:
        positive = "\n".join(explicit)
    else:
        positive = re.sub(r"(?i)\bhelp\b[^\n。；;]*", "", positive)
    return tuple(name for name, (_, pattern, _) in METHODS.items() if re.search(pattern, positive, re.I))


def requested_settings(label, context, defaults):
    tol = scoped_setting(context, defaults, r"(?:容差|\btol(?:erance)?\b|(?<!绝对)(?<!相对)(?<!真实)误差(?:上限|限)?|精度)", "容差", numeric_setting, 1e-12)
    figures = display_precision(context, defaults, "位有效数字", 6)
    steps = scoped_setting(context, defaults, r"(?:最[大多]迭代(?:次数|步数)?|max_iter)", "迭代上限", iteration_count, 100)
    if not 1e-15 <= tol <= 0.1 or not 1 <= figures <= 15 or not 1 <= steps <= 1000:
        raise LabError(f"“{label}”的有效数字、容差或最大迭代次数超出范围。")
    return tol, figures, steps


def scoped_setting(context, defaults, field, label, convert, default=None):
    for content in (context, defaults):
        pattern = rf"(?:{field})[ \t]*(?:不超过|小于|为|<=|≤|<|[:=])[ \t]*([^\n,，;；。]*)"
        occurrences = re.findall(pattern, content, re.I)
        if occurrences:
            parsed = []
            for value in occurrences:
                try:
                    parsed.append(convert(value.strip()))
                except (ValueError, OverflowError, ZeroDivisionError) as exc:
                    raise LabError(f"无法读取{label}“{value.strip()}”。请核对数字和格式。") from exc
            if len(set(parsed)) != 1:
                raise LabError(f"同一任务有互相冲突的{label}。请明确使用哪一个值。")
            return parsed[0]
    return default


def numeric_setting(value):
    if "位小数" in value or "位有效数字" in value or re.search(r"\d[ \t]+\d", value):
        raise ValueError(value)
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(NUMBER, value):
        number = float(value)
    elif match := re.fullmatch(rf"({NUMBER})/({NUMBER})", value):
        number = float(Fraction(match[1])/Fraction(match[2]))
    elif match := re.fullmatch(rf"(?:({NUMBER})\*)?10\^\(?([+-]?\d{{1,3}})\)?", value):
        number = float(match[1] or 1)*10.**int(match[2])
    else:
        raise ValueError(value)
    if not math.isfinite(number):
        raise ValueError(value)
    return number


def iteration_count(value):
    match = re.fullmatch(r"([+-]?\d+)\s*(?:次|步)?", value)
    if not match or not 1 <= int(match[1]) <= 10000:
        raise ValueError(value)
    return int(match[1])


def display_precision(context, defaults, suffix, default=None):
    for content in (context, defaults):
        figures = {int(value) for value in re.findall(rf"(\d+)\s*{suffix}", content)}
        if figures:
            if len(figures) != 1 or not 0 <= next(iter(figures)) <= 15:
                raise LabError(f"显示精度有冲突或超出范围，请核对{suffix}设置。")
            return figures.pop()
    return default


def plot_requests(text, interpolation=False):
    text = without_negations(text)
    requests_plot = re.search(r"绘(?:制|图)|画(?:出|图)|作图|生成.{0,10}(?:图|曲线)|收敛(?:曲线|图)|插值(?:曲线|图)|\bplot\b", text, re.I)
    if not requests_plot:
        return ()
    if interpolation and re.search(r"插值(?:多项式)?(?:的)?(?:曲线|图)|绘(?:制|图)|画(?:出|图)|作图", text) and not re.search(r"收敛|误差|误差棒", text):
        return ("interpolation",)
    if not re.search(r"(?:收敛|(?:残差|迭代误差).{0,6}(?:图|曲线))", text):
        raise LabError("存在无法执行的绘图要求。请明确写出所需的残差收敛图或插值曲线。")
    plots = tuple(key for key, pattern in (
        ("residual", r"残差\s*(?:收敛)?\s*(?:图|曲线)"),
        ("relative", r"相对误差\s*(?:收敛)?\s*(?:图|曲线)"),
        ("absolute", r"(?:绝对误差|真实误差)\s*(?:收敛)?\s*(?:图|曲线)"),
        ("step", r"相邻迭代差|(?<!绝对)(?<!相对)(?<!真实)误差收敛"),
    ) if re.search(pattern, text))
    return plots or ("residual",)


def plot_request(text, interpolation=False):
    plots = plot_requests(text, interpolation)
    if len(plots) > 1:
        raise LabError("此类型的多个图形请分别设置在独立任务中。")
    return plots[0] if plots else ""


def root_request(text):
    text = without_negations(text)
    if any(float(value) != 0 for value in re.findall(rf"\b[xt]\s*(?:>=|<=|>|<|≥|≤)\s*({NUMBER})",text)):
        raise LabError("请用开区间或闭区间表达非零的根范围。")
    if re.search(r"最[大小](?:的)?(?:实|正|负)?根|第\s*\d+\s*(?:个|个实)根|较[大小](?:的)?根|正负(?:实)?根", text):
        raise LabError("请用明确的区间指定要找的根。排序或带多个符号条件的根请拆成单独任务。")
    targets = [name for name, pattern in (
        ("positive", r"(?<!非)正(?:实)?根|\b[xt]\s*>\s*0\b"), ("negative", r"(?<!非)负(?:实)?根|\b[xt]\s*<\s*0\b"),
        ("nonnegative", r"非负(?:实)?根|\b[xt]\s*(?:≥|>=)\s*0\b"),
        ("nonpositive", r"非正(?:实)?根|\b[xt]\s*(?:≤|<=)\s*0\b"),
        ("all_real", r"(?:全部|所有|各个|两个|三个|四个)实根"),
        ("all", r"(?:全部|所有|各个)(?:复数)?根"),
    ) if re.search(pattern, text)]
    if len(targets) > 1:
        raise LabError("一题同时指定了不同的目标根。请为每个目标根使用单独的任务标题。")
    return targets[0] if targets else ""


def make_equation(label, left, right, methods, context="", defaults=""):
    def setting(pattern, flags=0):
        return re.search(pattern, context, flags) or re.search(pattern, defaults, flags)

    lhs, rhs = math_tree(left), math_tree(right)
    tree = ast.BinOp(left=lhs, op=ast.Sub(), right=rhs)
    expression = compile_math(tree)
    if not any(isinstance(n, ast.Name) and n.id == "x" for n in ast.walk(tree)):
        raise LabError(f"方程“{label}”缺少变量 x。")
    derivative = ""
    if "newton" in methods:
        match = setting(rf"(?:f\s*['′]\(x\)|导数)\s*[:=]\s*({ALPHABET}+)", re.I)
        derivative = compile_math(math_tree(match[1].strip())) if match else compile_math(tree, True)
    iteration = ""
    if {"fixed_point", "steffensen"}.intersection(methods):
        match = setting(rf"(?:g\s*\(x\)|迭代函数)\s*[:=]\s*({ALPHABET}+)", re.I)
        if match:
            iteration = compile_math(math_tree(match[1].strip().rstrip(".")))
        elif isinstance(lhs, ast.Name) and lhs.id == "x":
            iteration = compile_math(rhs)
        else:
            raise LabError(f"方程“{label}”需要指定迭代函数 g(x)，也可以直接写成 x = g(x) 的形式。")
    coefficients = []
    if "roots" in methods:
        coefficients = polynomial(tree)
        while len(coefficients) > 1 and coefficients[-1] == 0:
            coefficients.pop()
        if len(coefficients) < 2 or not all(math.isfinite(n) for n in coefficients):
            raise LabError(f"方程“{label}”需要非恒定的有限多项式系数。")

    bounds = rf"([\[(])\s*({NUMBER})\s*[,;]\s*({NUMBER})\s*([\])])"

    def interval_match(text):
        explicit = list(re.finditer(rf"(?:区间|范围|\binterval\b)\s*(?:为|[:=])?\s*{bounds}", text, re.I))
        inline = list(re.finditer(rf"{bounds}\s*(?:内|上|中的)", text))
        choices = explicit + inline
        if len({item.groups() for item in choices}) > 1:
            raise LabError("同一任务指定了不同的区间。请将各区间拆成独立任务。")
        return choices[0] if choices else None

    def initial_match(text):
        choices = list(re.finditer(rf"(?:初始点|初始值|初值)\s*(?:为|[:=])?\s*{INITIAL_VALUES}", text))
        numbered = re.findall(rf"\bx\s*_?([01])\s*=\s*({NUMBER})", text, re.I)
        if len({tuple(float(n) for n in item.groups() if n is not None) for item in choices}) > 1 or choices and numbered:
            raise LabError("同一任务指定了不同形式的初值。请给出一组明确的初值。")
        return (choices[0] if choices else None), numbered

    bracket = interval_match(context) or interval_match(defaults)
    method_initials = []
    general_context = []
    for line in context.splitlines():
        field = re.match(r"^(.+?)\s*(?:初始点|初始值|初值)\s*[:=]", line)
        key = next((key for key, (_, pattern, _) in METHODS.items() if field and re.fullmatch(pattern, field[1].strip(), re.I)), None)
        if key is None:
            general_context.append(line)
            continue
        chosen, _ = initial_match(line)
        if key not in methods or chosen is None or any(method == key for method, _ in method_initials):
            raise LabError(f"无法确定单独初值设置“{line}”。")
        values = tuple(float(n) for n in chosen.groups() if n is not None)
        if len(values) != (2 if key == "secant" else 1) or not all(map(math.isfinite, values)) or (key == "secant" and values[0] == values[1]):
            raise LabError(f"指定的{METHODS[key][0]}初值无效。")
        method_initials.append((key, values))
    starts, numbered = initial_match("\n".join(general_context))
    if not starts and not numbered:
        starts, numbered = initial_match(defaults)
    if len({k for k, _ in numbered}) != len(numbered):
        raise LabError(f"方程“{label}”有重复的初值设置。")
    interval = (float(bracket[2]), float(bracket[3])) if bracket else ()
    open_interval = (bracket[1] == "(", bracket[4] == ")") if bracket else ()
    initial = tuple(float(n) for n in starts.groups() if n is not None) if starts else tuple(float(n) for _, n in sorted(numbered))
    if any(not math.isfinite(n) for n in interval + initial) or interval and interval[0] >= interval[1]:
        raise LabError(f"方程“{label}”的区间须递增，区间和初值须为有限数。")
    if len(initial) == 2 and "secant" in methods and initial[0] == initial[1]:
        raise LabError(f"方程“{label}”的两个割线初值须不同。")
    tol, figures, steps = requested_settings(label, context, defaults)
    plots = plot_requests(context) or plot_requests(defaults)
    ref = scoped_setting(context, defaults, r"(?:参考根|精确根|真实根)", "参考根", numeric_setting)
    if {"absolute", "relative"}.intersection(plots) and (ref is None or "relative" in plots and ref == 0):
        raise LabError("误差收敛图需要有限的参考根，例如“参考根：1.414213562373095”。相对误差的参考根须非零。")
    if plots and methods == ("roots",):
        raise LabError("roots 没有可用于收敛图的迭代序列，请指定可记录迭代过程的方法。")
    target = root_request(context) or root_request(defaults)
    if target == "all" and interval:
        raise LabError("“全部根”可能包含复根，与实数区间限制冲突。请改成“区间内的全部实根”或移除区间。")
    if target in {"all", "all_real"} and methods != ("roots",):
        raise LabError("查找所有根需要指定 roots。若还需用其他方法验证某个根，请另设一个任务与求根区间。")
    info = rational.analyze(tree, target, interval, resolve_real="roots" in methods)
    factors, count = (), None
    if info is not None:
        poly, factors, count = info
        if len(poly) < 2:
            raise LabError("化简后方程为常数，不能报告一个独立的近似根。")
        if "roots" in methods:
            coefficients = list(map(float, poly))
        if any(open_interval):
            distinct, _ = rational.square_free(poly)
            for edge, opened in zip(interval, open_interval):
                sign_allowed = {"positive": edge > 0, "negative": edge < 0,
                                "nonnegative": edge >= 0, "nonpositive": edge <= 0}.get(target, True)
                if opened and sign_allowed and rational.evaluate(distinct, Fraction(str(edge))) == 0:
                    count -= 1
    decimals = display_precision(context, defaults, "位小数")
    fixed_pattern = r"(?:只|恰好|固定|仅)\s*迭代\s*([一二两三四五六七八九十\d]+)\s*(?:步|次)"
    exact = re.search(fixed_pattern, context) or re.search(fixed_pattern, defaults)
    fixed_steps = None
    if exact:
        numeral = dict(zip("一二两三四五六七八九十", (1,2,2,3,4,5,6,7,8,9,10)))
        fixed_steps = int(exact[1]) if exact[1].isdigit() else numeral.get(exact[1])
        if not fixed_steps or fixed_steps > steps or set(methods) & {"roots", "fzero", "brent"}:
            raise LabError("固定步数仅支持自编迭代法，且步数须在迭代上限内。")
    trace_table = bool(re.search(r"(?:每轮|逐轮|每步|逐步|各步).{0,20}(?:迭代|上下界|误差界|残差|结果)|迭代表", context + "\n" + defaults))
    if trace_table and "roots" in methods:
        raise LabError("roots 没有可填入逐步迭代表的记录，请另列非迭代结果。")
    residual_tol = scoped_setting(context, defaults, r"(?:绝对)?残差(?:上限)?", "残差容差", numeric_setting)
    if residual_tol is not None and residual_tol <= 0:
        raise LabError("残差容差须为正数。")
    relative = scoped_setting(context, defaults, r"相对误差(?:上限|限)?", "相对误差", numeric_setting)
    absolute = scoped_setting(context, defaults, r"(?:绝对|真实)误差(?:上限|限)?", "绝对误差", numeric_setting)
    if relative is not None and absolute is not None:
        raise LabError("请将绝对误差与相对误差要求分别核对。")
    error_mode = "relative" if relative is not None else "absolute" if absolute is not None else ""
    error_tolerance = relative if relative is not None else absolute
    if error_tolerance is not None:
        if not 1e-15 <= error_tolerance <= .1 or relative is not None and ref == 0:
            raise LabError("误差限超出支持范围，或相对误差使用了零参考根。")
        if ref is None and set(methods) - {"bisection"}:
            raise LabError("该误差要求需要可核验的参考根。请提供参考根，或使用可给出区间误差界的二分法。")
        tol = min(tol, error_tolerance if relative is None else error_tolerance * (abs(ref) if ref else 1))
    domain_problem, domain_warning, excluded = inspect_domain(tree, interval)
    return Equation(label, expression, derivative, tuple(reversed(coefficients)), methods, interval, initial,
                    tol, figures, steps, iteration, plots[0] if plots else "", ref, target,
                    plots[1:], factors, count, decimals, fixed_steps, trace_table, residual_tol,
                    tuple(method_initials), error_tolerance, error_mode, open_interval, domain_problem, excluded,
                    domain_warning=domain_warning)


def equations_on(line):
    line = re.sub(r"[fFgG]\s*['′]\s*\(x\)\s*=[^,，;；。]+", "", line)
    for chunk in re.split(r"[^\x00-\x7f]|[,;:]", line):
        chunk = chunk.strip().rstrip(".")
        if re.match(r"^(?:[fg]\s*['′]|g\s*\(x\))", chunk, re.I):
            continue
        if re.match(r"^f\s*\([xt]\)\s*=", chunk, re.I):
            left = re.split(r"=", chunk, maxsplit=1)[1].strip()
            left = re.sub(r"\s*=\s*0\s*$", "", left)
            left = re.sub(r"\bt\b", "x", left)
            yield left + " = 0", left, "0"
            continue
        match = re.fullmatch(rf"(?:\d+\s*\)\s*)?({ALPHABET}+)\s*=\s*({ALPHABET}+)", chunk)
        if match and re.search(r"(?:\b[xt]|(?<=\d)[xt])(?![a-z0-9_])|\bx(?:e\^|exp\()", match[1] + " " + match[2], re.I):
            left, right = (part.strip() for part in match.groups())
            yield f"{left} = {right}", re.sub(r"\bt\b", "x", left), re.sub(r"\bt\b", "x", right)


def requirements(lines, stop_at_output=True):
    result = []
    output = False
    example = False
    for line in lines:
        if line.strip() in {"{{示例开始}}", "{{示例结束}}"}:
            example = line.strip() == "{{示例开始}}"
            continue
        if example:
            continue
        if stop_at_output and OUTPUT_HEADING.match(line):
            if re.search(r"[:：].+", line) and re.search(r"截图|录屏|绘图|绘制|保留|逐轮|给出", line):
                result.append(normalize(line.strip()))
            output = True
            continue
        if output:
            if TASK_HEADING.match(normalize(line)) or re.match(r"\s*方程\s*\d*\s*[:：]", line):
                output = False
            elif not re.search(r"截图|录屏|绘图|绘制|必须|请|逐轮|导出|收敛阶|保留\s*\d+\s*位|"
                               r"(?:参考根|精确根|真实根|容差|最大迭代)\s*[:：=]|(?:只|恰好|固定|仅)\s*迭代|"
                               r"(?:残差|误差)(?:上限|限)?\s*(?:不超过|小于|≤|<=|<|[:：=])", line):
                continue
        if line.strip():
            result.append(line.strip() if PAGE_MARKER.fullmatch(line.strip()) else normalize(line.strip()))
    return result


def after_requirements_heading(lines):
    start = next((i for i, line in enumerate(lines) if REQUIREMENT_HEADING.match(line)), -1)
    common = [line for line in lines[:max(0, start)] if METHOD_LINE.match(line) or re.match(r"(?:容差|误差|最大迭代|初值|区间)\s*[:=]", line)]
    return common + lines[start + 1:]


def validate_requirements(text):
    text = without_negations(text)
    if match := UNAVAILABLE.search(text):
        raise LabError(f"识别到当前无法执行的方法“{match[0]}”。请确认老师的要求后修改题目。")
    for line in text.splitlines():
        field = METHOD_LINE.fullmatch(line)
        natural = re.search(r"(?:请|分别)?(?:用|采用|使用)\s*([^\n。；;，,]+?)\s*(?:求解|分别求|求出|求根|求|计算|进行插值)", line)
        declaration = field[1] if field else natural[1] if natural else ""
        for name in re.split(r"\s*(?:以及|并用|还有|或|和|与|及|、|[,;])\s*", declaration.rstrip("。")):
            name = re.sub(r"^(?:分别|直接)?(?:使用|采用|用|调用|编写)\s*", "", name.strip()).strip()
            if not name or re.match(r"^(?:绘制|作图|画出|输出|记录|使用区间|求近似)", name):
                continue
            pure_name = re.sub(r"\s*\([^()]*\)\s*$", "", name) if field else name
            if field and not any(re.fullmatch(pattern, pure_name, re.I) for _, pattern, _ in METHODS.values()):
                # A method line may also contain an explicit calculation instruction.
                if not (selected_methods(name) and re.search(r"求解|求根|计算|迭代至|^\w+\s*\[", name)):
                    raise LabError(f"方法“{name}”无法识别或尚未实现。请逐个写出要使用的方法。")
            elif not field and not selected_methods(name) and (re.search(r"(?:法|插值)$", name) or selected_methods(declaration) and re.fullmatch(r"[\u3400-\u9fff]{2,20}|[\w -]{3,30}", name)):
                raise LabError(f"方法“{name}”无法识别或尚未实现。请核对实验要求。")


def vector_values(text, name):
    if re.search(r"^\s*[,;]|[,;]\s*[,;]|[,;]\s*$", text):
        raise LabError(f"{name}含空白数据项，请核对原数据。")
    parts = re.split(r"[\s,;]+", text.strip())
    if not parts or not all(re.fullmatch(NUMBER, value) for value in parts):
        raise LabError(f"{name}须为有限数字组成的向量。")
    values = tuple(float(value) for value in parts)
    if not all(math.isfinite(value) for value in values):
        raise LabError(f"{name}须为有限数字组成的向量。")
    return values


def read_vector(text, names, title, scalar=False):
    candidates = []
    for field in re.findall(rf"(?im)^[ \t]*(?:{names})[ \t]*[:=][ \t]*(.*)$", text):
        field = field.strip().rstrip("。")
        if match := re.fullmatch(VECTOR, field):
            candidates.append(vector_values(match[1], title))
        elif scalar and re.fullmatch(NUMBER, field):
            candidates.append(vector_values(field, title))
        else:
            raise LabError(f"无法读取{title}“{field}”。请核对原数据。")
    if len(set(candidates)) > 1:
        raise LabError(f"同一任务有互相冲突的{title}。")
    return candidates[0] if candidates else ()


def parse_extra(lines, methods, defaults):
    text = "\n".join(lines)
    if not EXTRA_METHODS.intersection(methods) and not re.search(r"方程组|被积函数|微分方程|拟合|(?:节点|数据点).{0,5}插值|插值(?:任务|实验|曲线|多项式)", text):
        return None
    if not methods:
        if re.search(r"非线性方程组", text):
            methods = ("fsolve",)
        elif re.search(r"线性方程组", text):
            methods = ("gaussian",)
    if not methods or set(methods) - EXTRA_METHODS:
        raise LabError("请将不同类型的计算分为独立任务，并为每题注明方法。")
    tol, digits, steps = requested_settings("本题", text, defaults)
    decimals = display_precision(text, defaults, "位小数")
    trace_table = bool(re.search(r"迭代(?:记录|表|过程)|逐步(?:记录|表|结果)|每步(?:结果|数值)", without_negations(text)))

    if set(methods) <= INTEGRAL_METHODS or set(methods) <= ODE_METHODS:
        if any(next(equations_on(line), None) for line in lines if not re.match(r"^(?:被积函数|微分方程)", line)):
            raise LabError("这段同时给出了求根方程。请为积分、微分方程和求根分别设置任务标题。")
        ode = set(methods) <= ODE_METHODS
        kind = "ode" if ode else "integral"
        bounds = read_vector(text, r"(?:积分|求解|计算)?区间", "计算区间")
        if len(bounds) != 2 or bounds[0] == bounds[1] or (ode and bounds[0] > bounds[1]):
            raise LabError("请用“区间：[a,b]”给出两个不同端点。微分方程须从左端点向右计算。")
        segments = scoped_setting(text, defaults, r"分段数|子区间数|步数", "分段数", iteration_count)
        step = scoped_setting(text, defaults, r"步长", "步长", numeric_setting)
        if step is not None:
            ratio = abs(bounds[1]-bounds[0]) / step if step > 0 else 0
            if not 1 <= ratio <= 10000 or not math.isclose(ratio, round(ratio), rel_tol=1e-12):
                raise LabError("步长应为正数，且能将区间等分为 1 至 10000 段。")
            if segments is not None and segments != round(ratio):
                raise LabError("步长与分段数不一致。")
            segments = round(ratio)
        if segments is None and set(methods) != {"adaptive_integral"} or ("simpson" in methods and segments % 2):
            raise LabError("请给出分段数或步长。复合辛普森法需要偶数个子区间。")
        initial = read_vector(text, r"初值|初始值|y0", "初值", scalar=True) if ode else ()
        variables = {"x": "x", "t": "x", "y": "y"} if ode else {"x": "x", "t": "x"}
        if ode and 1 < len(initial) <= 8:
            names = tuple(f"y{i}" for i in range(1,len(initial)+1))
            variables.update({name: f"y({j})" for j,name in enumerate(names,1)})
            fields = [(name, re.findall(rf"^[ \t]*(?:微分方程\s*[:=]\s*)?{name}\s*'\s*=\s*(.+)$", text, re.M)) for name in names]
            if any(len(matches) != 1 for _,matches in fields):
                raise LabError("微分方程组请逐行写 y1'=…、y2'=…，且与初值向量等长。")
            matches = [values[0] for _,values in fields]
        else:
            names = ("y",) if ode else ()
            field = r"(?:微分方程\s*[:=]\s*)?y\s*'\s*=" if ode else r"被积函数\s*[:=]"
            matches = re.findall(rf"^[ \t]*{field}\s*(.+)$", text, re.M)
        if (ode and not 1 <= len(initial) <= 8) or len(matches) != (len(initial) if ode else 1):
            example = "微分方程：y'=x+y 及初值：1" if ode else "被积函数：x^2"
            raise LabError(f"请单独写一行方程，例如“{example}”。")
        formulas = tuple(re.sub(r"^f\s*\(\s*[xt]\s*\)\s*=\s*", "", match.rstrip("。")) for match in matches)
        expressions = tuple(compile_math(math_tree(formula, tuple(variables)), variables=variables) for formula in formulas)
        label = "; ".join(f"{name}' = {formula}" for name, formula in zip(names,formulas)) if ode else "积分函数 " + formulas[0]
        graph_text = "\n".join(line for line in without_negations(text).splitlines()
                               if re.search(r"绘|画|图|曲线",line))
        plot = kind if graph_text else ""
        if plot and re.search(r"误差|收敛",graph_text):
            if not re.search(r"分段|网格",graph_text) or "adaptive_integral" in methods:
                raise LabError("本任务可绘制固定网格分段数与绝对误差估计的关系图。请明确图形类型。")
            plot = "grid_error"
        if trace_table and segments and segments > 1000:
            raise LabError("逐步表格最多输出 1000 段。请减少分段或取消逐步表格要求。")
        if trace_table and "adaptive_integral" in methods:
            raise LabError("quadgk 不返回逐步网格表。请为自适应和固定网格方法分别设置任务。")
        accuracy = scoped_setting(text, defaults, r"容差|\btol\b|(?<!相对)(?:绝对)?误差(?:上限|限)?|精度", "绝对误差限", numeric_setting)
        if scoped_setting(text, defaults, r"相对误差(?:上限|限)?", "相对误差", numeric_setting) is not None:
            raise LabError("积分与常微分方程仅核对绝对误差估计，请给出绝对误差限，或提供参考解另行比较。")
        domain = warning = ""
        if not ode:
            integrand = math_tree(re.sub(r"\bt\b", "x", formulas[0]))
            domain, warning, _ = inspect_domain(integrand, tuple(sorted(bounds)))
            if domain:
                domain += "积分前需分析奇点与反常积分收敛性。"
        return ExtraTask(kind, label, methods, tol, digits, steps, plot, initial=initial, expressions=expressions,
                         variables=names, nodes=bounds, decimals=decimals, segments=segments or 0, trace_table=trace_table,
                         accuracy_tolerance=accuracy, domain_problem=domain, domain_warning=warning)
    if set(methods) <= LINEAR_METHODS:
        matrix_matches = []
        for field in re.findall(r"(?im)^[ \t]*(?:A|系数矩阵)[ \t]*[:=][ \t]*(.*)$",text):
            if not (match := re.fullmatch(VECTOR,field.strip().rstrip("。"))):
                raise LabError("无法读取矩阵 A。请用方括号包住矩阵，各行用分号分开。")
            matrix_matches.append(tuple(vector_values(row,"矩阵 A") for row in match[1].split(";")))
        if len(set(matrix_matches)) > 1:
            raise LabError("同一任务有互相冲突的系数矩阵 A。")
        rhs = read_vector(text, r"b|右端向量", "右端向量")
        if not matrix_matches or not rhs:
            raise LabError("高斯消元请提供 A = [2,1;1,3] 和 b = [1,2]，两项各占一行。")
        matrix = matrix_matches[0]
        if not 2 <= len(matrix) <= 100 or len(rhs) != len(matrix) or any(len(row) != len(matrix) for row in matrix):
            raise LabError("矩阵 A 须为 2 至 100 阶方阵，b 的长度须与行数相同。")
        plot = plot_request(text) or plot_request(defaults)
        if plot and (methods == ("gaussian",) or plot != "residual"):
            raise LabError("高斯消元是直接法，当前没有相应的收敛图。")
        initial = read_vector(text, r"初值|初始值|x0", "线性迭代初值") or (0.,)*len(rhs)
        if len(initial) != len(rhs):
            raise LabError("线性迭代的初值长度须与未知量数相同。")
        return ExtraTask("linear", "A*x = b", methods, tol, digits, steps, plot, initial=initial if set(methods) - {"gaussian"} else (),
                         matrix=matrix, rhs=rhs, decimals=decimals, trace_table=trace_table)
    if methods == ("fsolve",):
        if re.search(r"(?:只|恰好|固定|仅)\s*迭代", text):
            raise LabError("fsolve 支持指定迭代上限。固定输出第 N 次迭代需要另行选用能控制每步的算法。")
        initial = read_vector(text, r"初值|初始值|x0", "方程组初值")
        entries = []
        for line in lines:
            equation = re.fullmatch(rf"(?:方程\s*\d*\s*[:=]\s*)?({ALPHABET}+)\s*=\s*({ALPHABET}+)\s*。?", line)
            if equation and not re.match(r"^\s*(?:x0|tol|max_iter)\s*=" , line, re.I):
                entries.append(tuple(p.strip() for p in equation.groups()))
            elif re.match(r"^\s*方程\s*\d*\s*[:=]", line):
                raise LabError(f"方程未能完整读取：“{line}”。请将说明放到方程下面的一行。")
        if not 2 <= len(entries) <= 8 or len(initial) != len(entries):
            raise LabError("fsolve 需要 2 至 8 个方程，每行一个，并给出同样长度的初值，例如“初值：[1,2]”。")
        names = tuple(f"x{i}" for i in range(1, len(entries)+1))
        if len(entries) <= 3 and not any(re.search(r"\bx[1-9]\d*\b", " ".join(pair), re.I) for pair in entries):
            names = ("x", "y", "z")[:len(entries)]
        mapping = {name: f"v({index})" for index, name in enumerate(names, 1)}
        expressions = []
        for left, right in entries:
            tree = ast.BinOp(left=math_tree(left, names), op=ast.Sub(), right=math_tree(right, names))
            expressions.append(compile_math(tree, variables=mapping))
        if not all(any(variable in expression for expression in expressions) for variable in mapping.values()):
            raise LabError("方程组中应出现全部未知量。请统一使用 x、y、z 或 x1、x2 等变量名。")
        plot = plot_request(text) or plot_request(defaults)
        if plot not in {"", "residual"}:
            raise LabError("方程组目前支持绘制残差收敛图，请写明“绘制残差收敛图”。")
        return ExtraTask("system", "; ".join(f"{l} = {r}" for l, r in entries), methods, tol, digits, steps, plot,
                         initial=initial, expressions=tuple(expressions), variables=names, decimals=decimals,
                         trace_table=trace_table)
    if set(methods) <= INTERPOLATION_METHODS or methods == ("least_squares",):
        if any(next(equations_on(line), None) for line in lines):
            raise LabError("同一任务中还有求根方程。请把插值和求根分成两个任务。")
        nodes = read_vector(text, r"节点\s*x|x\s*节点|节点|x", "插值节点")
        values = read_vector(text, r"节点值|函数值|y|y\s*值", "节点值")
        query = read_vector(text, r"求值点|待求点|插值点|查询点", "求值点", scalar=True)
        max_nodes = 24 if set(methods) & {"lagrange","newton_interp"} else 500
        if not 2 <= len(nodes) <= max_nodes or len(nodes) != len(values) or len(set(nodes)) != len(nodes):
            raise LabError(f"需要 2 至 {max_nodes} 个互异节点及等长节点值，例如“节点：[0,1,2]”和“节点值：[1,2,5]”。")
        if len(query) > 30:
            raise LabError("一次最多处理 30 个插值求值点。")
        if not query and re.search(r"(?:求值点|待求点|插值点|查询点)\s*[:=]", text):
            raise LabError("无法读取插值求值点。请写作“求值点：[0.5,1.5]”。")
        if methods == ("least_squares",):
            degree = scoped_setting(text, defaults, r"(?:多项式)?次数|拟合阶数", "拟合次数", numeric_setting)
            if degree is None or not degree.is_integer() or not 0 <= degree <= min(10, len(nodes)-1):
                raise LabError("请用“次数：2”指定拟合次数。允许 0 至 10 次，且小于数据点数。")
            graph = "regression" if re.search(r"绘(?:制|图)|画(?:出|图)|作图|拟合曲线", without_negations(text)) else ""
            return ExtraTask("regression", f"{int(degree)} 次最小二乘拟合", methods, tol, digits, steps, graph,
                             nodes=nodes, values=values, query=query, decimals=decimals, degree=int(degree))
        plot = plot_request(text, interpolation=True) or plot_request(defaults, interpolation=True)
        if plot not in {"", "interpolation"}:
            raise LabError("插值任务目前支持绘制插值曲线，请写明“绘制插值曲线”。")
        return ExtraTask("interpolation", f"{len(nodes)} nodes", methods, tol, digits, steps, plot,
                         nodes=nodes, values=values, query=query, decimals=decimals)
    raise LabError("请用任务标题分开方程组与插值方法。")


def task_blocks(lines):
    lines = tuple(line for line in lines if not PAGE_MARKER.fullmatch(line))
    starts = [i for i, line in enumerate(lines) if TASK_HEADING.match(line)]
    if not starts:
        markers = [i for i, line in enumerate(lines) if METHOD_LINE.match(line)]
        if len(markers) > 1 and all(
                any(next(equations_on(line), None) or re.match(r"^(?:被积函数|微分方程|节点|A)\s*[:=]", line)
                    for line in lines[a:b]) for a, b in zip(markers, [*markers[1:], len(lines)])):
            starts = markers
        if not starts:
            examples = [i for i,line in enumerate(lines) if re.search(r"^(?:教材|课本)\s*P?\d+.*实(?:习|验)题",line,re.I)]
            if examples and any(next(equations_on(line),None) for line in lines[:examples[0]]):
                return (), tuple(lines[a:b] for a,b in zip([0,*examples],[*examples,len(lines)]))
    if not starts:
        return (), (tuple(line for line in lines if not PAGE_MARKER.fullmatch(line)),)
    return lines[:starts[0]], tuple(tuple(line for line in lines[a:b] if not PAGE_MARKER.fullmatch(line))
                                   for a, b in zip(starts, [*starts[1:], len(lines)]))


def display_vector(values):
    return "[" + ", ".join(format(value, ".15g") for value in values) + "]"


def format_plan(assignment):
    layout = assignment.report_template_name or "通用 Word 版式"
    lines = [f"Word 报告模板  {layout}", ""]
    for i, item in enumerate(assignment.equations, 1):
        lines.append(f"第 {i} 题  {item.label}")
        lines.append("方法  " + "、".join(METHODS[method][0] for method in item.methods))
        if isinstance(item, ExtraTask):
            if item.kind == "linear":
                lines.append("A  " + "; ".join(display_vector(row) for row in item.matrix))
                lines.append("b  " + display_vector(item.rhs))
            if item.kind in {"interpolation", "regression"}:
                lines.append(f"节点  {display_vector(item.nodes)}\n节点值  {display_vector(item.values)}")
                if item.query:
                    lines.append("求值点  " + display_vector(item.query))
            elif item.kind in {"integral", "ode"}:
                lines.append(f"区间  {display_vector(item.nodes)}    分段数  {item.segments}")
                if item.trace_table:
                    lines.append("附上逐步计算表")
                if item.accuracy_tolerance is not None:
                    lines.append(f"绝对误差限  {item.accuracy_tolerance:g}（用网格加密估计）")
        else:
            if item.interval:
                lines.append("区间  " + display_vector(item.interval))
            elif any(method in {"bisection", "fzero", "false_position"} or method != "roots" and len(item.initial) < (2 if method == "secant" else 1) for method in item.methods):
                lines.append("区间  只在能证明有唯一候选实根时自动选择，否则要求补充区间")
            if item.root_target:
                lines.append("目标  " + ROOT_NAMES[item.root_target])
            if item.iteration_function:
                lines.append("迭代函数  g(x) = " + item.iteration_function)
            if item.derivative:
                lines.append("牛顿法导数  f'(x) = " + item.derivative)
            if item.reference is not None and {"absolute", "relative"}.intersection(item.plots):
                lines.append(f"参考根  {item.reference:.15g}")
            if item.fixed_steps is not None:
                lines.append(f"固定迭代  {item.fixed_steps} 步，输出该步结果")
            if item.trace_table:
                lines.append("附上逐步迭代表")
        if item.initial:
            lines.append("初值  " + display_vector(item.initial))
        if item.domain_problem or item.domain_warning:
            lines.append("定义域  " + (item.domain_problem or item.domain_warning))
        for method, values in getattr(item, "method_initials", ()):
            lines.append(METHODS[method][0] + "初值  " + display_vector(values))
        if isinstance(item, Equation) or item.kind in {"linear", "system", "interpolation"}:
            lines.append(f"容差  {item.tolerance:g}    有效数字  {item.digits}    迭代上限  {item.max_steps}")
        else:
            lines.append(f"有效数字  {item.digits}")
        if item.decimals is not None:
            lines.append(f"显示  小数点后 {item.decimals} 位")
        lines.append("图形  " + ("、".join(PLOT_NAMES[p] for p in item.plots) if item.plots else "无计算图形要求"))
        if item.capture:
            lines.append("要求截图  " + "、".join({"command":"命令窗口", "editor":"编辑器", "result":"结果图窗"}[kind] for kind in item.capture))
        lines.append("")
    if assignment.screenshot_note:
        lines.append(assignment.screenshot_note)
    if assignment.layout_note:
        lines.append(assignment.layout_note)
    if assignment.pending:
        lines.extend(("下列原文要求待人工核对", *assignment.pending))
    if assignment.original_requirements:
        lines.extend(("\n供复核的题目原文", assignment.original_requirements))
    return "\n".join(lines).strip()


def capture_requests(text):
    text = without_negations(text)
    kinds = tuple(kind for kind, caption in (("command", r"命令(?:窗口|行)"), ("editor", r"编辑器"),
                                             ("result", r"结果(?:图窗|窗口)"))
                  if re.search(rf"(?:{caption}.{{0,12}}截图|截图.{{0,12}}{caption})", text))
    if kinds:
        return kinds
    return ("result",) if re.search(r"(?:需要|必须|请|提供|给出|附上|提交|要求).{0,12}截图", text) else ()


def select_exercise(lines, number):
    candidates = [(line, candidate) for line in after_requirements_heading(requirements(lines))
                  for candidate in equations_on(line)]
    if not 1 <= number <= len(candidates):
        raise LabError(f"原文检出 {len(candidates)} 道可单选的一元方程。请核对题号，只有教材题号时请补充实际题目。")
    line, (label, _, _) = candidates[number-1]
    methods = selected_methods(line)
    if not methods:
        raise LabError(f"这道题未在同一行注明方法。请用题目录入框补齐方法、区间或初值。方程为{label}。")
    conditions = [line for line in (line,) if re.search(PAIR, line)]
    return ["方法：" + "、".join(METHODS[key][0] for key in methods), f"方程：{label}",
            *[match[0] for row in conditions for match in re.finditer(rf"(?:区间\s*{PAIR}|{PAIR}\s*(?:内|上))", row)]]


def parse_assignment(lines, problem_lines=None):
    source_lines = requirements(lines)
    source_text = "\n".join(source_lines)
    supplement = problem_lines is not None
    requirement_lines = requirements(problem_lines, stop_at_output=False) if supplement else after_requirements_heading(source_lines)
    text = "\n".join(line for line in requirement_lines if not PAGE_MARKER.fullmatch(line))
    combined = source_text + "\n" + text if supplement else source_text
    raw_source = "\n".join(line.strip() for line in [*lines, *(problem_lines or ())] if line.strip())
    validate_requirements(combined)
    help_commands = tuple(name for name in ("fzero", "roots") if re.search(rf"\bhelp\b[^\n]{{0,45}}\b{name}\b", combined, re.I))
    preamble, blocks = task_blocks(requirement_lines)
    if any(next(equations_on(line), None) for line in preamble):
        raise LabError("第一个任务标题前还有方程。请为每道题添加任务标题，再确认各题参数。")
    defaults = "\n".join(preamble)
    if supplement:
        source_common, source_blocks = task_blocks(source_lines)
        if len(source_blocks) == 1 and not any(TASK_HEADING.match(line) or next(equations_on(line), None) for line in after_requirements_heading(source_lines)):
            source_common = source_lines
        defaults += "\n" + "\n".join(source_common)
    shared_methods = selected_methods(defaults)
    equations = []
    if supplement and any(next(equations_on(line), None) or re.match(
            r"^(?:节点|节点值|被积函数|微分方程|系数矩阵|A)\s*[:=]", line)
            for line in after_requirements_heading(source_lines)):
        equations.extend(parse_assignment(lines).equations)
    for block in blocks:
        block_text = "\n".join(block)
        methods = selected_methods(block_text) or shared_methods
        extra = parse_extra(block, methods, defaults)
        if extra:
            equations.append(replace(extra, capture=capture_requests(block_text) or capture_requests(defaults)))
            continue
        found = [(index, equation) for index, line in enumerate(block) for equation in equations_on(line)]
        equation_rows = {index for index, _ in found}
        for index, line in enumerate(block):
            if re.match(r"^(?:方程(?:\s*\d+)?|f\s*\([xt]\))\s*[:=]", line, re.I) and index not in equation_rows:
                raise LabError(f"无法识别方程“{line}”。请按“方程：x^2 - 2 = 0”的格式核对。")
            for label, expected in ((r"(?:计算|求解|初始)?区间", rf"[\[(]\s*{NUMBER}\s*[,;]\s*{NUMBER}\s*[\])]"), (r"初(?:始点|始值|值)", INITIAL_VALUES)):
                parameter = re.match(rf"^{label}\s*[:=]\s*(.+)$", line)
                if parameter and not re.fullmatch(expected, parameter[1].strip().rstrip("。")):
                    raise LabError(f"无法读取参数“{line}”。请检查小数点和区间括号。")
        if not found and (methods or any(TASK_HEADING.match(line) for line in block) or len(blocks) == 1):
            raise LabError("有任务缺少可计算的方程。请填写实际方程或插值数据，也可以添加题目图片。")
        for index, (line_no, (label, left, right)) in enumerate(found):
            end = found[index + 1][0] if index + 1 < len(found) else len(block)
            context = block_text if len(found) == 1 else "\n".join(block[line_no:max(line_no + 1, end)])
            per_task = selected_methods(context) or methods
            if len(found) == 1 and not any(METHOD_LINE.match(line) for line in block):
                per_task = selected_methods(block[line_no]) or per_task
            if not per_task:
                raise LabError(f"请为方程“{label}”注明要使用的方法。")
            local_defaults = "\n".join(block[:found[0][0]]) + "\n" + defaults if len(found) > 1 else defaults
            try:
                eq = make_equation(label, left, right, per_task, context, local_defaults)
                equations.append(replace(eq, capture=capture_requests(context) or capture_requests(local_defaults)))
            except LabError as exc:
                raise LabError(f"请核对方程“{label}”。{exc}") from exc
    if not equations:
        raise LabError("找不到可执行的实验任务。请在要求中写出方程或插值数据。")
    if len(equations) > 100:
        raise LabError("一次最多处理 100 个实验任务，请拆分报告。")
    kinds = {kind for equation in equations for kind in equation.capture}
    screenshot_kind = "command_editor" if {"command","editor"} <= kinds else next(iter(kinds & {"command","editor"}), "result")
    screenshot_note = "原文指定的截图列在对应题目下。跳过时会逐题标明待补项。" if kinds else ""
    positive = without_negations(combined)
    exports = tuple(kind for kind, pattern in (("pdf", r"\bPDF\b"), ("xlsx", r"\bExcel\b|\bXLSX\b"))
                    if re.search(rf"(?:导出|输出|保存|提交|整理成|生成).{{0,30}}(?:{pattern})", positive, re.I))
    pending = tuple(dict.fromkeys(line for line in positive.splitlines() if re.search(
        r"收敛阶|收敛速率(?:分析|比较)|误差来源|若.{0,10}未收敛|自行编写.{0,12}算法|"
        r"(?:导出|整理).{0,15}误差(?:表|到)|绝对误差.{0,10}(?:Excel|表格)|"
        r"手工推导|手算|理论证明|证明(?:算法|收敛|唯一|存在)|误差棒|制作动画|录屏|比较实际误差|"
        r"\bhelp\s+(?!fzero\b|roots\b)\w+", line, re.I)))
    return Assignment(tuple(equations), help_commands, screenshot_note=screenshot_note,
                      original_requirements=raw_source, screenshot_kind=screenshot_kind, pending=pending, exports=exports)
