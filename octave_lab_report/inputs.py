"""Read PDF and spreadsheet task sources without executing embedded content."""

import csv
from io import StringIO
import re

from .spec import Cancelled, LabError, TASK_HEADING, normalize


SOURCE_SUFFIXES = {".docx", ".doc", ".txt", ".md", ".pdf", ".xlsx", ".xls", ".csv", ".tsv"}


def pdf_pages(path, cancel=None):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError, DependencyError
    except ImportError as exc:
        raise LabError("PDF 导入需要 pypdf。请安装 requirements.txt 中的依赖。") from exc
    try:
        with path.open("rb") as source:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise LabError("该 PDF 已加密。请先提供可读取的副本。")
            pages = []
            for page in reader.pages:
                if cancel and cancel.is_set():
                    raise Cancelled()
                pages.append((page.extract_text() or "").strip())
            if not pages:
                raise LabError("PDF 不含页面。")
            return pages
    except (PdfReadError, DependencyError, ValueError) as exc:
        raise LabError(f"无法读取 PDF。{exc}") from exc


def pdf_images(path, folder, selected=None, cancel=None):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise LabError("扫描 PDF 需要 pypdfium2。请安装 requirements.txt 中的依赖。") from exc
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        with pdfium.PdfDocument(path) as document:
            for index, page in enumerate(document, 1):
                try:
                    if cancel and cancel.is_set():
                        raise Cancelled()
                    if selected is not None and index not in selected:
                        continue
                    output = folder / f"{path.stem[:65]}-第{index:03}页.png"
                    scale = min(3, 3600/max(page.get_width(), page.get_height()))
                    bitmap = page.render(scale=scale)
                    try:
                        bitmap.to_pil().save(output)
                    finally:
                        bitmap.close()
                    paths.append(output)
                finally:
                    page.close()
    except (pdfium.PdfiumError, OSError, ValueError) as exc:
        raise LabError(f"无法渲染扫描 PDF。{exc}") from exc
    return paths


def read_pdf(path, use_review=False, cancel=None):
    pages = pdf_pages(path,cancel)
    if not all(pages):
        if not use_review:
            raise LabError("PDF 含扫描页或无文字页面。请在桌面核对扫描识别结果，或通过 --ocr-only 提取后提交 --reviewed-text。")
    return [line for page in pages for line in page.splitlines()]


def merge_pdf_review(path, review, cancel=None):
    """Restore the original page order for scanned pages and retain existing text."""
    pages = pdf_pages(path,cancel)
    if all(pages) and not review:
        return [line for page in pages for line in page.splitlines()]
    chunks = re.split(r"(?m)^【图片 \d+】(?: ([^\n]*))?\s*\n", review or "")
    extras = [chunks[0]]
    edited_pages = set()
    for name, content in zip(chunks[1::2], chunks[2::2]):
        match = re.fullmatch(rf"{re.escape(path.stem[:65])}-第(\d+)页\.png", name or "")
        if not match or not 1 <= int(match[1]) <= len(pages):
            extras.append(content)
            continue
        index = int(match[1])-1
        if index in edited_pages:
            raise LabError("PDF 核对文字中有重复页码。")
        pages[index] = content.strip() or "【核对为空白页】"
        edited_pages.add(index)
    if any(not page for page in pages):
        if not edited_pages and review and not any(pages):
            return review.splitlines()
        raise LabError("扫描 PDF 仍有未核对的页面。请保留核对文本里的页码标记。")
    return [line for page in [*pages,*extras] for line in page.splitlines() if line.strip() != "【核对为空白页】"]


def matrix_row(row):
    return bool(row) and all(isinstance(cell, (int,float)) and not isinstance(cell, bool) for cell in row)


def table_lines(rows):
    def cell_text(value):
        return format(value, ".16g") if isinstance(value, float) else str(value)

    normalized = []
    for row in rows:
        row = list(row)
        while row and (row[-1] is None or row[-1] == ""):
            row.pop()
        if row and any(cell is not None and cell != "" for cell in row):
            normalized.append(row)
    index = 0
    while index < len(normalized):
        row = normalized[index]
        index += 1
        first = str(row[0] or "").strip()
        label = normalize(first).rstrip(":=").strip()
        rest = row[1:]
        if label in {"A", "系数矩阵"} and not rest:
            values = []
            while index < len(normalized) and matrix_row(normalized[index]):
                values.append(",".join(cell_text(value) for value in normalized[index]))
                index += 1
            if values:
                yield "A: [" + "; ".join(values) + "]"
                continue
        if label in {"节点", "节点值", "函数值", "求值点", "待求点", "区间", "初值", "初始值", "b", "右端向量", "x", "y"} and rest:
            yield label + ": [" + ",".join("" if cell is None else cell_text(cell) for cell in rest) + "]"
        elif label in {"方法", "方程", "容差", "精度", "最大迭代", "分段数", "步长", "次数", "被积函数", "微分方程"} and rest:
            yield label + ": " + " ".join(cell_text(cell) for cell in rest if cell is not None)
        else:
            yield " ".join(cell_text(cell) for cell in row if cell is not None)


def read_spreadsheet(path, read_text, *, sheet=None, data_range=None, trust_formula_cache=False,cancel=None):
    suffix = path.suffix.lower()
    limits = None
    if data_range:
        from openpyxl.utils.cell import range_boundaries
        try:
            limits = range_boundaries(data_range)
            if any(value is None or value < 1 for value in limits) or limits[0] > limits[2] or limits[1] > limits[3]:
                raise ValueError(data_range)
        except ValueError as exc:
            raise LabError("数据区域请填写完整矩形，例如 A1:D12。") from exc

    def choose(titles):
        if sheet is None:
            return range(len(titles))
        if sheet not in titles:
            raise LabError(f"找不到工作表“{sheet}”。")
        return (titles.index(sheet),)

    if suffix in {".csv", ".tsv"}:
        if sheet or limits:
            raise LabError("工作表和单元格区域仅用于 Excel。")
        rows = csv.reader(StringIO(read_text(path)), delimiter="\t" if suffix == ".tsv" else ",", strict=True)
        try:
            return list(table_lines([[float(value) if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", value.strip()) else value or None
                                      for value in row] for row in rows]))
        except csv.Error as exc:
            raise LabError(f"分隔表格第 {rows.line_num} 行格式错误。{exc}") from exc
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise LabError("旧版 .xls 导入需要 xlrd，或请将文件另存为 .xlsx。") from exc
        try:
            with xlrd.open_workbook(str(path)) as book:
                result = []
                for index in choose(book.sheet_names()):
                    tab = book.sheet_by_index(index)
                    a,b,c,d = limits or (1,1,tab.ncols,tab.nrows)
                    result.append(list(table_lines(tab.row_values(i,a-1,c) for i in range(b-1,min(d,tab.nrows)))))
                return sheets_to_lines(result)
        except (xlrd.XLRDError, OSError) as exc:
            raise LabError(f"无法读取旧版 Excel。{exc}") from exc
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:
        raise LabError("Excel 导入需要 openpyxl。请安装 requirements.txt 中的依赖。") from exc
    formula_book = result_book = None
    try:
        formula_book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        result_book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        sheets = []
        for index in choose(formula_book.sheetnames):
            source, cached = formula_book.worksheets[index], result_book.worksheets[index]
            a,b,c,d = limits or (None,None,None,None)
            region = dict(min_col=a,min_row=b,max_col=c,max_row=d)
            rows = []
            for i, (formulas, values) in enumerate(zip(source.iter_rows(**region), cached.iter_rows(**region, values_only=True), strict=True), b or 1):
                if cancel and cancel.is_set():
                    raise Cancelled()
                for formula, value in zip(formulas, values, strict=True):
                    if formula.data_type == "f" and value is None:
                        raise LabError(f"工作表 {source.title} 第 {i} 行有未计算的公式。请先在 Excel 中重新计算并保存。")
                    if formula.data_type == "f" and not trust_formula_cache:
                        raise LabError(f"工作表 {source.title} 第 {i} 行含公式。请确认已重新计算并保存，再启用读取公式缓存。")
                rows.append(values)
            sheets.append(list(table_lines(rows)))
        return sheets_to_lines(sheets)
    except (InvalidFileException, OSError, ValueError) as exc:
        raise LabError(f"无法读取 Excel。{exc}") from exc
    finally:
        for book in (formula_book, result_book):
            if book is not None:
                book.close()


def sheets_to_lines(sheets):
    populated = [lines for lines in sheets if lines]
    result = []
    for i, lines in enumerate(populated, 1):
        if len(populated) > 1 and not any(TASK_HEADING.match(normalize(line)) for line in lines):
            result.append(f"任务 {i}")
        result.extend(lines)
    return result
