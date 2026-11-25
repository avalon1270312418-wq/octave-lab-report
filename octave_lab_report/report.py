"""Read local instructions and fill the corresponding sections in a Word copy."""

from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.part import XmlPart
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.section import Section
from docx.shared import Inches, Pt, RGBColor, Twips
from docx.text.paragraph import Paragraph
from lxml import etree
from PIL import Image

from .spec import ExtraTask, LabError, METHODS, OUTPUT_HEADING, PLOT_NAMES, REQUIREMENT_HEADING, ROOT_NAMES, display_vector


MAIN_WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.{}.main+xml"
REPORT_SECTIONS = ("实验目的", "实验原理", "实验要求", "实验内容", "实验任务", "实验步骤", "实验步骤与实验程序",
                   "实验结果", "实验结果与截图", "实验分析与结论", "实验结论")
SECTION_BOOKMARKS = {f"OctaveSection{i}": title for i, title in enumerate(REPORT_SECTIONS)}


PRINCIPLES = {
    "bisection": "对于连续函数，选择端点异号的区间，每次在中点处将其减半并保留含根的一侧。返回中点时，区间半长给出绝对误差上界。",
    "newton": "以切线的横轴交点更新近似值，迭代式为 x(k+1) = x(k) − f(x(k))/f′(x(k))。程序检查迭代步长及归一化残差。",
    "secant": "用最近两个点的函数差商代替导数，更新到割线与横轴的交点。两个初值须不同，更新过程中要求差商的分母非零。",
    "false_position": "取两端点所连割线的横轴交点，再用试位点替换与它同号的端点。这样每次更新后仍保留异号区间。",
    "fixed_point": "把方程改写为 x = g(x)，从初值反复计算 x(k+1) = g(x(k))。程序同时检查原方程残差和 |g(x) − x|。",
    "steffensen": "计算 y = g(x)、z = g(y)，然后用 x − (y − x)²/(z − 2y + x) 更新近似值。程序同时检查原方程残差和不动点残差。",
    "fzero": "调用 Octave 的 fzero，在给定的异号区间内结合区间收缩和插值寻找单个实零点。",
    "roots": "按降幂顺序构造多项式。对能精确分解的多项式先分离重根因子，再用 Octave 的 roots 求解，并按题目筛选和核对所得根。",
    "gaussian": "先选当前列绝对值最大的主元，交换对应行，再消去主元下方的元素。最后通过回代解上三角方程组，并计算 Ax − b。",
    "fsolve": "从指定的初值向量出发，用 Octave 的 fsolve 迭代求解 F(x)=0，并检查各方程的残差。",
    "lagrange": "从互异节点构造拉格朗日基函数，加权求和得到通过各节点的插值多项式。",
    "newton_interp": "由节点值逐阶计算均差，构造牛顿插值多项式，并用嵌套乘法求值。",
    "trapezoid": "在均匀网格的每个子区间中，对两端函数值取平均并乘以子区间长度，最后累加。",
    "simpson": "将子区间分为相邻两段一组，用三点二次插值的积分近似各组面积。两端点权重为 1，内部奇序号和偶序号的权重分别为 4 和 2。",
    "euler": "用左端点斜率从当前近似值走一步，即 y(k+1) = y(k) + h f(x(k), y(k))。",
    "rk4": "在每步的起点、两次中点和终点计算斜率，以 1、2、2、1 加权更新近似解。",
    "spline": "构造分段低次多项式，从四个节点起使用非扭结三次样条，使插值在内部节点的函数值及一、二阶导数连续。",
    "least_squares": "在缩放后的坐标中构造多项式设计矩阵，用 QR 分解求使各点平方残差之和最小的系数。",
    "brent": "在异号区间内尝试割线插值或逆二次插值，并在步长或位置不合适时退回二分。",
    "jacobi": "每轮用上一轮的分量更新同一轮的全部分量。程序以归一化残差检查迭代终止。",
    "gauss_seidel": "按行更新分量，当前轮已更新的分量立即参与后续更新。程序以归一化残差检查终止。",
    "adaptive_integral": "调用 Octave 的 Gauss–Kronrod 自适应积分，并将返回的绝对误差估计与容差对照。",
}


def xml_text(node):
    if node is None or not isinstance(node.tag, str):
        return ""
    tag = node.tag.rsplit("}", 1)[-1]
    if tag == "t":
        return node.text or ""
    if tag in ("br", "cr"):
        return "\n"
    if tag == "tab":
        return " "
    if node.tag == qn("w:r"):
        value = "".join(xml_text(child) for child in node)
        align = node.find(f"{qn('w:rPr')}/{qn('w:vertAlign')}")
        if align is not None and value:
            if align.get(qn("w:val")) == "superscript":
                return f"^({value})"
            if align.get(qn("w:val")) == "subscript":
                return f"_{value}"
        return value
    if tag == "sSup":
        return f"{xml_text(node.find(qn('m:e')))}^({xml_text(node.find(qn('m:sup')))})"
    if tag == "sSub":
        return f"{xml_text(node.find(qn('m:e')))}_{xml_text(node.find(qn('m:sub')))}"
    if tag == "f" and node.tag == qn("m:f"):
        return f"({xml_text(node.find(qn('m:num')))})/({xml_text(node.find(qn('m:den')))})"
    if tag == "rad":
        expression = xml_text(node.find(qn("m:e")))
        degree = node.find(qn("m:deg"))
        degree = xml_text(degree).strip() if degree is not None else ""
        return f"({expression})^(1/({degree}))" if degree else f"sqrt({expression})"
    if tag == "d" and node.tag == qn("m:d"):
        return "(" + ",".join(xml_text(e) for e in node.findall(qn("m:e"))) + ")"
    return "".join(xml_text(child) for child in node)


def read_source(path, *, allow_filled=False):
    if path.suffix.lower() == ".docx":
        doc = Document(path)
        if not allow_filled and any(b.get(qn("w:name")) == "OctaveLabResult" for b in doc.element.body.iter(qn("w:bookmarkStart"))):
            raise LabError("这份文件已经由本程序回填。请使用原始实验报告重新运行。")
        lines = [section_marker(line) or line for p in doc.element.body.iter(qn("w:p")) for line in xml_text(p).splitlines()]
        return doc, lines
    return None, read_text(path).splitlines()


def open_dotx(source, destination):
    """Create a working DOCX package from a macro-free Word template."""
    types_part = "[Content_Types].xml"
    try:
        with ZipFile(source) as template:
            types = etree.fromstring(template.read(types_part))
            main = [node for node in types if node.get("ContentType") == MAIN_WORD_TYPE.format("template")]
            if len(main) != 1:
                raise LabError("请选择有效的 .dotx 模板，或在 Word 中将模板另存为 .docx。")
            main[0].set("ContentType", MAIN_WORD_TYPE.format("document"))
            with ZipFile(destination, "w") as work:
                for entry in template.infolist():
                    payload = etree.tostring(types, encoding="UTF-8", xml_declaration=True) if entry.filename == types_part else template.read(entry)
                    work.writestr(entry, payload)
    except (BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise LabError(f"Word 模板无法读取：{source.name}。请在 Word 中另存为 .docx 后重试。") from exc
    return destination


def read_text(path):
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LabError("文本无法读取，请将 .txt 或 .md 另存为 UTF-8。")


class Writer:
    def __init__(self, doc, after=None):
        self.doc = doc
        self.cursor = after._p if after is not None else None

    def paragraph(self, text="", style=None):
        if self.cursor is None:
            paragraph = self.doc.add_paragraph(text, style)
        else:
            element = OxmlElement("w:p")
            self.cursor.addnext(element)
            paragraph = Paragraph(element, self.doc)
            if style:
                paragraph.style = style
            if text:
                paragraph.add_run(text)
        self.cursor = paragraph._p
        if style is None:
            paragraph.paragraph_format.space_after = Pt(6)
            paragraph.paragraph_format.line_spacing = 1.2
        return paragraph

    def label(self, text):
        p = self.paragraph()
        p.add_run(text).bold = True
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(6)
        return p

    def code(self, text):
        p = self.paragraph()
        run = p.add_run(text.rstrip())
        run.font.name = "Consolas"
        run.font.size = Pt(8.5)
        p.paragraph_format.line_spacing = Pt(10.5)
        p.paragraph_format.widow_control = True

    def table(self, headers, rows, width):
        table = self.doc.add_table(rows=0, cols=len(headers))
        if self.cursor is not None:
            self.cursor.addnext(table._tbl)
        if table._tbl.getparent().tag == qn("w:tc") and table._tbl.getnext() is None:
            table._tbl.addnext(OxmlElement("w:p"))
        self.cursor = table._tbl
        table.autofit = False
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "bottom", "left", "right", "insideH", "insideV"):
            line = OxmlElement(f"w:{edge}")
            line.set(qn("w:val"), "single")
            line.set(qn("w:sz"), "4")
            line.set(qn("w:color"), "B7B7B7")
            borders.append(line)
        table._tbl.tblPr.insert_element_before(borders, "w:shd", "w:tblLayout", "w:tblLook")
        fractions = (0.18, 0.37, 0.15, 0.30) if len(headers) == 4 else [1/len(headers)]*len(headers)
        if len(headers) == 6:
            fractions = (.09, .18, .19, .18, .18, .18)
        widths = [int(width * fraction) for fraction in fractions]
        for column, value in zip(table.columns, widths):
            column.width = value
        for index, values in enumerate([headers, *rows]):
            row = table.add_row()
            trpr = row._tr.get_or_add_trPr()
            trpr.append(OxmlElement("w:cantSplit"))
            if index == 0:
                trpr.append(OxmlElement("w:tblHeader"))
            for cell, value, cell_width in zip(row.cells, values, widths):
                cell.width = cell_width
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
                p.paragraph_format.line_spacing = 1.1
                p.paragraph_format.keep_with_next = index < len(rows) and len(rows) < 10
                run = p.add_run(value)
                run.bold = index == 0
                run.font.size = Pt(9)

    def figure(self, figure, caption, width, drawing_id):
        with Image.open(figure.path) as image:
            aspect = image.height / image.width
            native_width = min(width, Inches(image.width / 135))
        picture = self.paragraph()
        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture.paragraph_format.keep_with_next = True
        picture.paragraph_format.space_before = Pt(8)
        picture.paragraph_format.space_after = Pt(2)
        shape = picture.add_run().add_picture(str(figure.path), width=min(native_width, int(Inches(4.25) / aspect)))
        # Word requires drawing IDs to be unique across body, headers, and footers.
        shape._inline.docPr.id = drawing_id
        shape._inline.docPr.name = f"Picture {drawing_id}"
        label = self.paragraph(caption)
        label.alignment = WD_ALIGN_PARAGRAPH.CENTER
        label.runs[0].font.size = Pt(9)


def section_title(text):
    return re.sub(r"^[\s（(一二三四五六七八九十0-9.．、)）]+", "", text)


def find_heading(doc, *prefixes):
    def key(value):
        return re.sub(r"\s+", "", section_title(value)).casefold().replace("matlab", "octave")

    names = tuple(key(value) for value in prefixes)
    for p in doc.element.body.iter(qn("w:p")):
        for bookmark in p.findall(qn("w:bookmarkStart")):
            title = SECTION_BOOKMARKS.get(bookmark.get(qn("w:name")), "")
            if title and key(title).startswith(names):
                return Paragraph(p, doc)
    for element in doc.element.body.iter(qn("w:p")):
        paragraph = Paragraph(element, doc)
        if key(paragraph.text).startswith(names):
            return paragraph
    return None


def ensure_heading(doc, title, *aliases, before=()):
    found = find_heading(doc, title, *aliases)
    if found is not None:
        return found
    style = "Heading 1" if "Heading 1" in doc.styles else None
    anchor = find_heading(doc, *before) if before else None
    paragraph = anchor.insert_paragraph_before(title, style) if anchor is not None else doc.add_paragraph(title, style)
    if style is None:
        paragraph.runs[0].bold = True
        paragraph.paragraph_format.keep_with_next = True
    return paragraph


def section(doc, title, matches, parent):
    anchor = find_heading(doc, *matches)
    if anchor is not None:
        return Writer(doc, anchor)
    return Writer(doc, parent.label(title))


def section_marker(text):
    marker = re.fullmatch(r"\{\{\s*([^{}]+?)\s*\}\}", text.strip())
    return marker[1] if marker and marker[1] in REPORT_SECTIONS else None


def expand_section_markers(doc):
    ordinary = {section_title(paragraph.text.strip()) for paragraph in doc.paragraphs if not section_marker(paragraph.text)}
    for element in doc.element.body.iter(qn("w:p")):
        paragraph = Paragraph(element, doc)
        title = section_marker(paragraph.text)
        if title and paragraph.runs:
            paragraph.runs[0].text = "" if any(line.startswith(title) for line in ordinary) else title
            for run in paragraph.runs[1:]:
                run.text = ""
            mark(doc, paragraph, f"OctaveSection{REPORT_SECTIONS.index(title)}")


def validate_layout(doc):
    seen = set()
    aliases = {"实验内容": "实验要求", "实验任务": "实验要求", "实验步骤与实验程序": "实验步骤",
               "实验结果与截图": "实验结果", "实验结论": "实验分析与结论"}
    for node in doc.element.body.iter(qn("w:p")):
        paragraph = Paragraph(node, doc)
        if title := section_marker(paragraph.text):
            key = aliases.get(title, title)
            if key in seen:
                raise LabError(f"Word 模板有多处“{key}”占位符。请留下一处指定的回填位置。")
            seen.add(key)
    return any("示例" in line.text and line.text.strip() not in {"{{示例开始}}", "{{示例结束}}"} for line in doc.paragraphs)


def clear_old_results(doc, questions=False):
    """Clear prior content in the explicit results and conclusion slots of a layout."""
    targets = (find_heading(doc, "实验结果", "结果与运行"),
               find_heading(doc, "实验分析与结论", "实验结论", "分析与结论"))
    if questions:
        targets += (find_heading(doc,"实验内容","实验要求","实验任务"),)
    for heading in filter(None, targets):
        for node in list(heading._p.itersiblings()):
            if node.tag == qn("w:sectPr") or node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None:
                break
            if node.tag == qn("w:p"):
                paragraph = Paragraph(node, doc)
                label = section_title(paragraph.text)
                if label.startswith((*REPORT_SECTIONS, "结果与运行", "附录")) or section_marker(paragraph.text):
                    break
            if node.tag in {qn("w:tbl"), qn("w:p")}:
                node.getparent().remove(node)
            else:
                break


def has_old_results(doc):
    heading = find_heading(doc,"实验结果","结果与运行")
    if heading is None:
        return False
    for node in heading._p.itersiblings():
        if node.tag == qn("w:tbl") or next(node.iter(qn("w:drawing")),None) is not None:
            return True
        if node.tag != qn("w:p"):
            return False
        label=section_title(Paragraph(node,doc).text)
        if label.startswith(REPORT_SECTIONS):
            return False
        if label.strip():
            return True
    return False


def remove_marked_examples(doc):
    for container in [doc.element.body, *doc.element.body.iter(qn("w:tc"))]:
        if container.getparent() is None or not any(node is doc.element.body for node in container.iterancestors()) and container.tag != qn("w:body"):
            continue
        block = []
        for node in list(container):
            text = Paragraph(node, doc).text.strip() if node.tag == qn("w:p") else ""
            if text == "{{示例开始}}":
                if block:
                    raise LabError("模板的示例块不能嵌套。")
                block.append(node)
            elif text == "{{示例结束}}":
                if not block:
                    raise LabError("模板示例的起止标记须位于同一正文或表格单元格。")
                for item in [*block, node]:
                    container.remove(item)
                block.clear()
            elif block:
                if node.tag == qn("w:sectPr") or node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None:
                    raise LabError("模板示例块不能跨 Word 分节符。")
                block.append(node)
        if block:
            raise LabError("模板有示例开始标记，请在同一位置补上示例结束标记。")
        if container.tag == qn("w:tc") and (len(container) == 0 or container[-1].tag != qn("w:p")):
            container.append(OxmlElement("w:p"))


def section_body_width(doc, anchor):
    node = anchor._p
    cell = None
    while node.getparent() is not doc.element.body:
        node = node.getparent()
        if node.tag == qn("w:tc") and cell is None:
            cell = node.find(f"{qn('w:tcPr')}/{qn('w:tcW')}")
    section_properties = None
    for following in [node, *node.itersiblings()]:
        section_properties = following if following.tag == qn("w:sectPr") else following.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
        if section_properties is not None:
            break
    page = Section(section_properties, doc.part) if section_properties is not None else doc.sections[-1]
    usable = (page.page_width or Inches(8.27)) - (page.left_margin or 0) - (page.right_margin or 0)
    width = min(Inches(6.1), usable)
    columns = page._sectPr.find(qn("w:cols"))
    if columns is not None and int(columns.get(qn("w:num"), "1")) > 1:
        count = int(columns.get(qn("w:num")))
        spacing = Twips(int(columns.get(qn("w:space"),"720")))
        width = min(width, (usable-(count-1)*spacing)/count)
    if cell is not None:
        if cell.get(qn("w:type")) == "dxa":
            width = min(width, Twips(int(cell.get(qn("w:w")))) - Inches(.16))
        elif cell.get(qn("w:type")) == "pct":
            width = min(width, usable*int(cell.get(qn("w:w")))/5000 - Inches(.16))
    if width < Inches(.8):
        raise LabError("实验结果位置的版心太窄。请移到较宽的段落或表格单元格。")
    return width


def empty_section(heading, document):
    for sibling in heading._p.itersiblings():
        if sibling.tag != qn("w:p"):
            return sibling.tag == qn("w:sectPr")
        text = Paragraph(sibling, document).text.strip()
        if text:
            return section_title(text).startswith(REPORT_SECTIONS)
    return True


def insert_problem_text(doc, lines, image_questions, custom_layout):
    source = []
    for line in lines:
        if OUTPUT_HEADING.match(line):
            break
        if line.strip():
            source.append(line.strip())
    heading = next((i for i, line in enumerate(source) if REQUIREMENT_HEADING.match(line)), -1)
    source = source[heading + 1:]
    if not source and not image_questions:
        return
    brief = ensure_heading(doc, "实验要求", "实验内容", "实验任务", before=("实验步骤", "实验结果", "实验分析与结论"))
    writer = Writer(doc, brief)
    if source and custom_layout:
        writer.label("本次实验题目")
    for line in source:
        writer.paragraph(line).paragraph_format.space_after = Pt(3)
    if image_questions:
        writer.label("核对后的图片题目")
        for line in image_questions:
            if line.strip():
                writer.paragraph(line.strip()).paragraph_format.space_after = Pt(3)


def items(value):
    return value if isinstance(value, list) else [value]


def diagnostic(message):
    messages = (
        ("Multiple candidate roots", "请为一个目标根缩小区间，或改为列出该区间内全部实根。"),
        ("opposite endpoint signs", "请检查区间端点的函数值是否异号。"),
        ("Interval endpoints must bracket", "请给出端点异号的区间，或检查端点根。"),
        ("outside the specified interval", "请调整初值，使迭代收敛到题目指定范围。"),
        ("finite real domain", "请检查根号、分母、对数和迭代点的定义域。"),
        ("numerically rank deficient", "请检查矩阵是否奇异或接近奇异。"),
        ("did not converge", "请核对方法适用条件、初值及迭代上限。"),
        ("Could not open", "请检查输出文件是否被锁定及目录是否可写。"),
    )
    hints = [hint for fragment,hint in messages if fragment.casefold() in message.casefold()]
    return " ".join([*hints,message])


def format_root(re_value, im_value, digits, decimals=None):
    if decimals is not None:
        real = f"{re_value:.{decimals}f}"
        return real if im_value == 0 else f"{real} {'+' if im_value >= 0 else '-'} {abs(im_value):.{decimals}f}i"
    real = f"{re_value:#.{digits}g}".replace(".e", "e").rstrip(".")
    if im_value == 0:
        return real
    imag = (f"{im_value:+.{decimals}f}" if decimals is not None else
            f"{im_value:+#.{digits}g}".replace(".e", "e").rstrip("."))
    return f"{real} {imag[0]} {imag[1:]}i"


def parameters(writer, equation, case):
    if case.get("error"):
        writer.paragraph("本任务未算完。运行清单保留了已确认的输入参数。")
        return
    if isinstance(equation, ExtraTask):
        if equation.kind == "linear":
            writer.paragraph("系数矩阵 A 的各行依次为 " + "、".join(display_vector(row) for row in equation.matrix) + "。")
            writer.paragraph("右端向量 b = " + display_vector(equation.rhs) + "。")
        elif equation.kind == "system":
            writer.paragraph(f"未知量为{'、'.join(equation.variables)}。初值向量为 {display_vector(equation.initial)}。迭代上限 {equation.max_steps}。")
        elif equation.kind in {"ode", "integral"}:
            grid = f"，固定网格等分 {equation.segments} 段" if equation.segments else "，使用自适应积分"
            writer.paragraph(f"区间 {display_vector(equation.nodes)}{grid}。")
            if equation.initial:
                writer.paragraph(f"左端点初值 y({equation.nodes[0]:g}) = {display_vector(equation.initial)}。")
        else:
            writer.paragraph(f"数据节点 {display_vector(equation.nodes)}。节点值 {display_vector(equation.values)}。")
            if equation.query:
                writer.paragraph("求值点 " + display_vector(equation.query) + "。")
            outside = tuple(x for x in equation.query if not min(equation.nodes) <= x <= max(equation.nodes))
            if outside:
                writer.paragraph("超出原数据区间的求值点属于外推。" + display_vector(outside))
        if equation.kind in {"linear", "system", "interpolation"}:
            writer.paragraph(f"残差检查容差为 {equation.tolerance:g}。")
        writer.paragraph(f"结果保留 {equation.decimals} 位小数。" if equation.decimals is not None
                         else f"结果显示 {equation.digits} 位有效数字。")
        return
    interval = case["interval"]
    fields = []
    if interval:
        label = "自动选择的实根搜索区间" if case["automatic"] else "计算区间"
        fields.append(f"{label} [{interval[0]:g}, {interval[1]:g}]")
    for result in case["results"]:
        if result["starts"] != []:
            numbers = "、".join(f"{v:g}" for v in items(result["starts"]))
            fields.append(f"{METHODS[result['method']][0]}初值 {numbers}")
    if equation.coefficients:
        vector = ", ".join(f"{v:g}" for v in equation.coefficients)
        fields.append(f"降幂系数 [{vector}]")
    if equation.iteration_function:
        fields.append(f"g(x) = {equation.iteration_function}")
    if equation.root_target:
        fields.append(f"目标为{ROOT_NAMES[equation.root_target]}")
    if equation.reference is not None and {"absolute", "relative"}.intersection(equation.plots):
        fields.append(f"参考根 {equation.reference:.15g}")
    precision = f"保留 {equation.decimals} 位小数" if equation.decimals is not None else f"显示 {equation.digits} 位有效数字"
    fields.extend((f"容差 {equation.tolerance:g}", f"迭代上限 {equation.max_steps}", precision))
    writer.paragraph("。".join(fields) + "。")


def write_results(writer, cases, equations, figures, width):
    max_drawing_id = max((int(node.get("id")) for part in writer.doc.part.package.parts if isinstance(part, XmlPart)
                          for node in part.element.iter(qn("wp:docPr"))), default=0)
    figure_number = 0
    for index, (case, equation, pictures) in enumerate(zip(cases, equations, figures, strict=True), 1):
        writer.label(f"第 {index} 题  {equation.label if case.get('kind') != 'interpolation' else '插值计算'}")
        if case.get("error"):
            writer.paragraph("未完成。" + diagnostic(case["error"]))
            continue
        for field in ("issue", "screenshot_error"):
            if issue := case.get(field):
                writer.paragraph("本题有未完成项。" + diagnostic(issue))
        rows = []
        if case["kind"] == "scalar":
            for result in case["results"]:
                name = METHODS[result["method"]][0]
                steps = str(result["steps"]) if result["steps"] != [] else "—"
                mult = items(result.get("multiplicity", []))
                if result.get("fixed"):
                    if result.get("requested_steps", result["steps"]) == result["steps"]:
                        writer.paragraph(f"{name}按指定步数停止，以下为当步近似值。")
                    else:
                        writer.paragraph(f"{name}实际运行 {result['steps']} 次。原题要求 {result['requested_steps']} 次，以下为提前停止时的值。")
                if (estimate := result.get("error_estimate")) not in (None, []):
                    kind = "相对" if equation.error_mode == "relative" else "绝对"
                    criteria = "参考根差值" if equation.reference is not None else "区间误差上界"
                    writer.paragraph(f"{name}的{kind}误差核对采用{criteria}，值为 {max(items(estimate)):.3e}。")
                for i, (real, imag, residual) in enumerate(zip(*(items(result[k]) for k in ("re", "im", "residual")), strict=True)):
                    value = format_root(real, imag, equation.digits, equation.decimals)
                    if mult and mult[i] > 1:
                        value += f"（重数 {mult[i]}）"
                    rows.append((name, value, steps, f"{residual:.3e}"))
            writer.table(("方法", "近似根", "迭代次数", "残差 |f(x)|"), rows, width)
            if equation.trace_table:
                for result in case["results"]:
                    trace = result.get("trace", [])
                    if not trace:
                        continue
                    if not isinstance(trace[0], list):
                        trace = [trace]
                    labels = ("迭代", "近似值", "f(x)", "下界", "上界", "误差界")[:len(trace[0])]
                    writer.label(METHODS[result["method"]][0] + "迭代记录")
                    trace_rows = []
                    for row in trace:
                        formatted = [str(int(row[0]))]
                        for column, value in enumerate(row[1:], 1):
                            if column in {2, 5}:
                                formatted.append(f"{value:.3e}" if value is not None else "—")
                            else:
                                precision = equation.decimals
                                formatted.append(format_root(value, 0, equation.digits, precision) if value is not None else "—")
                        trace_rows.append(tuple(formatted))
                    writer.table(labels, trace_rows, width)
        elif case["kind"] in {"interpolation", "regression"}:
            for result in case["results"]:
                name = METHODS[result["method"]][0]
                coefficients = items(result["coefficients"])
                quality = f"{result['residual']:.3e}"
                for x, value in zip(items(result["query"]), items(result["values"]), strict=True):
                    rows.append((name, format_root(x, 0, equation.digits),
                                 format_root(value, 0, equation.digits, equation.decimals), quality))
                if result["method"] == "spline":
                    knots = items(result["knots"])
                    if not isinstance(coefficients[0], list):
                        coefficients = [coefficients]
                    writer.paragraph(f"{name}采用非扭结边界。节点最大残差为 {quality}。各段系数按 (x−左端点) 的降幂排列。")
                    writer.table(("区间", "降幂系数"), [
                        (f"[{a:g}, {b:g}]", display_vector(row))
                        for a, b, row in zip(knots[:-1], knots[1:], coefficients, strict=True)], width)
                elif result["method"] == "least_squares":
                    writer.paragraph(f"{name}令 z = (x − {coefficients[0]:.15g}) / {coefficients[1]:.15g}，"
                                     f"得到关于 z 的降幂系数 {display_vector(coefficients[2:])}。"
                                     f"最大节点残差为 {quality}，均方根残差为 {result['rmse']:.3e}。")
                else:
                    writer.paragraph(f"{name}所得多项式的降幂系数为 {display_vector(coefficients)}。节点处的最大残差为 {quality}。")
            if rows:
                writer.table(("方法", "求值点", "计算结果", "最大节点残差"), rows, width)
        elif case["kind"] in {"ode", "integral"}:
            ode = case["kind"] == "ode"
            for result in case["results"]:
                name = METHODS[result["method"]][0]
                terminal = items(result.get("terminal", result.get("values", [None])[-1])) if ode else [result["integral"]]
                estimate = result.get("error_estimate")
                formatted = ", ".join(format_root(value,0,equation.digits,equation.decimals) for value in terminal)
                rows.append((name, str(result["steps"]) if result["steps"] != [] else "自适应",
                             f"[{formatted}]" if len(terminal)>1 else formatted,
                             f"{estimate:.3e}" if estimate not in (None, []) else "待核对"))
                if result.get("order_estimate") is not None:
                    writer.paragraph(f"{name}的网格加密差值给出的观察收敛阶约为 {result['order_estimate']:.3g}。")
            writer.table(("方法", "分段数", "终点数值解" if ode else "积分近似值", "绝对误差估计"), rows, width)
            writer.paragraph("固定步长结果用网格加密估计误差，自适应结果使用积分器的误差估计。")
            for result in case["results"] if equation.trace_table else ():
                writer.label(METHODS[result["method"]][0] + "网格记录")
                values = result["values"]
                columns = zip(*values,strict=True) if isinstance(values[0], list) else ((value,) for value in values)
                writer.table(("x", *(equation.variables if ode else ("f(x)",))), [
                    (f"{x:.10g}", *(format_root(value, 0, equation.digits, equation.decimals) for value in column))
                    for x, column in zip(result["grid"], columns, strict=True)], width)
        else:
            for result in case["results"]:
                values = items(result["values"])
                names = equation.variables or tuple(f"x{i}" for i in range(1, len(values) + 1))
                steps = str(result["steps"]) if result["steps"] != [] else "—"
                writer.label(METHODS[result["method"]][0])
                rows = [(name, format_root(value, 0, equation.digits, equation.decimals), steps, f"{residual:.3e}")
                        for name,value,residual in zip(names,values,items(result["residual"]),strict=True)]
                writer.table(("未知量", "近似解", "迭代次数", "对应方程残差"), rows, width)
                if equation.trace_table and result.get("trace"):
                    trace = result["trace"]
                    if not isinstance(trace[0], list):
                        trace = [trace]
                    dimension = len(names)
                    writer.table(("迭代", "迭代向量", "残差向量"), [
                        (str(int(row[0])), "[" + ", ".join(format_root(x, 0, equation.digits, equation.decimals)
                                                            for x in row[3:3+dimension]) + "]",
                         "[" + ", ".join(f"{r:.3e}" for r in row[3+dimension:]) + "]")
                        for row in trace], width)
        evidence_count = sum(not picture.chart for picture in pictures)
        page = 0
        for picture in pictures:
            figure_number += 1
            if picture.chart:
                evidence, suffix = PLOT_NAMES[equation.plots[picture.plot_index]], ""
            else:
                page += 1
                evidence = {"command": "Octave 命令窗口中的实际运行日志", "editor": "Octave 编辑器中的运行脚本"}.get(
                    picture.kind, "Octave 结果窗口截图" if picture.screenshot else "由 Octave 运行日志生成的图像")
                if picture.kind == "editor" and picture.source:
                    evidence += " " + picture.source
                suffix = f"（{page}/{evidence_count}）" if evidence_count > 1 else ""
            writer.figure(picture, f"图 {figure_number}  第 {index} 题 {evidence}{suffix}", width, max_drawing_id + figure_number)


def conclusions(writer, cases):
    for index, case in enumerate(cases, 1):
        if case.get("error"):
            writer.paragraph(f"第 {index} 题未完成。{case['error']}")
            continue
        results = {record["method"]: record for record in case["results"]}
        if case["kind"] in {"ode", "integral"}:
            values = [items(r["terminal"]) if case["kind"] == "ode" else [r["integral"]] for r in results.values()]
            note = f"第 {index} 题得到数值近似，并记录可获得的绝对误差估计。"
            if len(values) == 2:
                difference = max(abs(a-b) for a,b in zip(*values,strict=True))
                note += f"两种方法的结果差的无穷范数为 {difference:.3e}。"
            if case.get("issue"):
                note += f"待处理事项。{case['issue']}"
            writer.paragraph(note)
            continue
        maximum = max(r for row in results.values() for r in items(row["residual"]))
        if case["kind"] == "interpolation":
            writer.paragraph(f"第 {index} 题所得插值多项式的最大节点残差为 {maximum:.3e}。")
            continue
        observations = [f"第 {index} 题的计算结果中，最大绝对残差为 {maximum:.3e}。"]
        if "bisection" in results:
            width = results["bisection"]["half_width"]
            observations.append(f"二分法结束时区间半长为 {width:.3e}。异号区间内函数连续时，该半长给出中点近似根的绝对误差上界。")
            if case.get("unique_interval"):
                observations.append("按有理多项式的精确实根计数，该计算区间内恰有一个符合条件的实根。")
            observations.append("表格中的小数位数表示显示精度，误差上界用于判断结果精度。")
        if {"newton", "secant"} <= results.keys():
            observations.append(f"在本题使用的初值下，牛顿法更新 {results['newton']['steps']} 次，割线法更新 {results['secant']['steps']} 次。")
        if {"fixed_point", "steffensen"} <= results.keys():
            observations.append(f"在本题使用的初值下，不动点迭代更新 {results['fixed_point']['steps']} 次，斯特芬森法更新 {results['steffensen']['steps']} 次。")
        if "fzero" in results and len(results) > 1 and not case.get("issue"):
            reference = results["fzero"]["re"]
            distances = [abs(row["re"] - reference) for key, row in results.items() if key not in {"fzero", "roots"}]
            if distances:
                observations.append(f"其余一元实根算法与 fzero 所得近似值的最大差值为 {max(distances):.3e}。")
        if "roots" in results:
            imaginary = items(results["roots"]["im"])
            observations.append(f"roots 返回 {len(imaginary)} 个根，其中 {sum(v != 0 for v in imaginary)} 个为非实复根。")
        if case.get("issue"):
            observations.append(f"本题待处理事项。{case['issue']}")
        writer.paragraph("".join(observations))


def mark(document, paragraph, name="OctaveLabResult"):
    used = [int(b.get(qn("w:id"))) for b in document.element.body.iter(qn("w:bookmarkStart"))]
    identifier = str(max(used, default=0) + 1)
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), identifier)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), identifier)
    paragraph._p.append(start)
    paragraph._p.append(end)


def save_document(doc, source_docx, target):
    if source_docx is None:
        doc.save(target)
        return
    buffer = BytesIO()
    doc.save(buffer)
    editable = {"word/document.xml", "word/_rels/document.xml.rels"}
    with ZipFile(source_docx) as original, ZipFile(buffer) as modified, ZipFile(target, "w") as output:
        names = set(original.namelist())
        generated = set(modified.namelist())
        for part in original.infolist():
            use_modified = part.filename in editable and part.filename in generated
            content = (modified if use_modified else original).read(part.filename)
            if part.filename == "[Content_Types].xml":
                current = etree.fromstring(content)
                known = {node.get("PartName") or node.get("Extension") for node in current}
                updates = [node for node in etree.fromstring(modified.read(part.filename))
                           if (node.get("PartName") or node.get("Extension")) not in known]
                if updates:
                    current.extend(updates)
                    content = etree.tostring(current, encoding="UTF-8", xml_declaration=True, standalone=True)
            output.writestr(part, content)
        for part in modified.infolist():
            if part.filename not in names:
                output.writestr(part, modified.read(part.filename))


def write_report(doc, lines, layout_docx, target, assignment, data, figures, templates, work, image_questions=(), *, screenshot_requested=True):
    general = any(isinstance(item, ExtraTask) for item in assignment.equations)
    if doc is None:
        doc = Document()
        for name in ("Title", "Heading 1"):
            style = doc.styles[name]
            style.font.color.rgb = RGBColor(0, 0, 0)
            for border in style.element.iter(qn("w:pBdr")):
                border.getparent().remove(border)
        doc.add_paragraph("数值计算实验报告" if general else "一元方程求根实验报告", style="Title")
        doc.add_heading("实验要求", level=1)
    else:
        remove_marked_examples(doc)
        expand_section_markers(doc)
    purpose = ensure_heading(doc, "实验目的", before=("实验原理", "实验内容", "实验步骤", "实验结果", "实验分析与结论"))
    if empty_section(purpose, doc):
        Writer(doc, purpose).paragraph("用 GNU Octave 完成题目指定的数值计算，记录所用方法、参数、计算结果和误差检查。" if general else
                                       "用 GNU Octave 求一元方程的近似根，记录求解参数、迭代次数和残差，比较各方法的实际结果。")
    used_methods = {key for equation in assignment.equations for key in equation.methods}
    principles = Writer(doc, ensure_heading(doc, "实验原理", before=("实验内容", "实验步骤", "实验结果", "实验分析与结论")))
    for key in METHODS:
        if key in used_methods:
            principles.paragraph(f"{METHODS[key][0]}。{PRINCIPLES[key]}")

    steps = Writer(doc, ensure_heading(doc, "实验步骤与实验程序", "实验步骤", before=("实验结果", "结果与运行", "实验分析与结论")))
    cases = data["cases"]
    algorithm_headings = ("算法的MATLAB编程", "算法的 Octave 编程", "算法的Octave编程")
    inline_code = find_heading(doc, *algorithm_headings) is not None
    code = section(doc, "方法程序", algorithm_headings, steps)
    first = code.paragraph(f"本实验在 GNU Octave {data['version']} 中运行。入口脚本 run_experiment.m 包含方程和方法设置。")
    mark(doc, first)
    for key, (filename, reused) in templates.items():
        note = "使用已有方法模板" if reused else "已生成方法模板"
        code.paragraph(f"{METHODS[key][0]}使用 {filename}，{note}。")
        if inline_code:
            code.code((work / filename).read_text(encoding="utf-8-sig"))
    if find_heading(doc, "函数定义") is None:
        steps = code
    definitions = section(doc, "函数定义与初值", ("函数定义",), steps)
    for index, (equation, case) in enumerate(zip(assignment.equations, cases, strict=True), 1):
        definitions.label(f"第 {index} 题  {equation.label if case.get('kind') != 'interpolation' else '插值数据与求值点'}")
        parameters(definitions, equation, case)
    command = section(doc, "Octave 运行指令", ("在MATLAB命令窗口输入指令", "在 Octave 命令窗口输入指令"), definitions)
    command.paragraph("将随附的 .m 文件放在 Octave 当前目录中，然后运行以下命令。")
    invocation = "run_experiment(true)" if screenshot_requested else "run_experiment(false)"
    command.code("\n".join([*(f"help {name}" for name in assignment.help_commands), invocation]))
    title = "实验结果与截图" if screenshot_requested else "实验结果"
    result_heading = ensure_heading(doc, title, "结果与运行", "实验结果", before=("实验分析与结论", "分析与结论", "实验结论"))
    results = Writer(doc, result_heading)
    if not screenshot_requested and "截图" in result_heading.text:
        results.paragraph("本次选择不截取运行窗口。")
    write_results(results, cases, assignment.equations, figures, section_body_width(doc, result_heading))
    conclusion_heading = ensure_heading(doc, "实验分析与结论", "分析与结论", "实验结论")
    conclusion = Writer(doc, conclusion_heading)
    conclusions(conclusion, cases)
    if assignment.pending:
        conclusion.label("待核对的实验要求")
        for requirement in assignment.pending:
            conclusion.paragraph(requirement)
    if templates and not inline_code:
        appendix = Writer(doc, ensure_heading(doc, "附录 Octave 方法源码"))
        for key, (filename, _) in templates.items():
            appendix.label(f"{METHODS[key][0]}  {filename}")
            appendix.code((work / filename).read_text(encoding="utf-8-sig"))
    insert_problem_text(doc, lines, image_questions, layout_docx is not None)
    save_document(doc, layout_docx, target)
