"""Export computed task results and traces as a spreadsheet."""

from .spec import LabError, METHODS


def spreadsheet(data, assignment, destination):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise LabError("导出 Excel 需要 openpyxl。请运行安装脚本。") from exc

    book = Workbook()
    overview = book.active
    overview.title = "运行状态"
    overview.append(("题号", "题目", "状态", "待处理事项"))
    for i, (case, job) in enumerate(zip(data["cases"], assignment.equations, strict=True), 1):
        issues = " ".join(case[key] for key in ("error", "issue", "screenshot_error") if case.get(key))
        overview.append((i, job.label, "待处理" if issues else "已计算", issues))
        sheet = book.create_sheet(f"题 {i}")
        sheet.append(("方法", "结果字段", "值"))
        for result in case["results"]:
            method = METHODS[result["method"]][0]
            for key, values in result.items():
                if key == "method":
                    continue
                if isinstance(values, list):
                    for index, value in enumerate(values, 1):
                        if isinstance(value, list):
                            sheet.append((method, f"{key} {index}", *value))
                        else:
                            sheet.append((method, f"{key} {index}", value))
                else:
                    sheet.append((method, key, values))
    for line in assignment.pending:
        overview.append((None, None, "待核对", line))
    for sheet in book:
        sheet.freeze_panes = "C2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for column in ("A", "B", "C", "D"):
            sheet.column_dimensions[column].width = 24
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
    book.save(destination)
