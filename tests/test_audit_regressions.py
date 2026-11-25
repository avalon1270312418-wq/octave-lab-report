from pathlib import Path
from tempfile import TemporaryDirectory
from json import dumps, loads
import unittest
from unittest.mock import patch

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter

from octave_lab_report.__main__ import file_digest, import_checkpoints
from octave_lab_report.engine import prepare_run
from octave_lab_report.inputs import merge_pdf_review, pdf_images, read_pdf, read_spreadsheet
from octave_lab_report.report import clear_old_results, find_heading, read_text, remove_marked_examples, validate_layout
from octave_lab_report.spec import LabError, parse_assignment, select_exercise


WORKSPACE = Path(__file__).resolve().parents[1]


def parse(text):
    return parse_assignment(text.strip().splitlines())


class AuditRegressions(unittest.TestCase):
    def test_root_scope_and_exact_multiplicity(self):
        equation, = parse("方法：roots\n方程：(x-2)^5*(x+1)=0\nx>0").equations
        self.assertEqual(equation.real_count, 1)
        self.assertIn(((1, -2), 5), equation.factors)
        nearby, = parse("方法：二分法\n方程：(x-1)*(x-1.000000001)=0").equations
        self.assertEqual(nearby.real_count, 2)
        self.assertEqual(nearby.factors, (((1, -1), 1), ((1, -1.000000001), 1)))

    def test_unicode_derivative_and_computed_exponent(self):
        explicit, = parse("方法：牛顿法\n方程：x^2-2=0\nf′(x)=2*x\n初值：1").equations
        self.assertEqual(explicit.derivative, "(2*x)")
        computed, = parse("方法：牛顿法\n方程：x^(1+1)-2=0\n初值：-1").equations
        self.assertNotIn("log(", computed.derivative)

    def test_rejects_conflicting_and_corrupted_settings(self):
        base = "方法：牛顿法\n方程：x^2-2=0\n初值：1\n"
        for rule in ("初值：2", "容差：1e-8\n容差：1e-9", "容差：1 0e-8", "区间：[1,2]\n区间：[3,4]"):
            with self.subTest(rule=rule), self.assertRaises(LabError):
                parse(base + rule)

    def test_report_requirements_and_native_screenshot_type(self):
        assignment = parse("""方法：二分法
方程：x^2-2=0
区间：[1,2]
只迭代 5 次
实验结果
保留 6 位小数
逐轮给出迭代结果
绘制残差收敛图及绝对误差收敛图
参考根：1.414213562373095
请提供命令窗口截图和编辑器截图""")
        equation, = assignment.equations
        self.assertEqual(equation.fixed_steps, 5)
        self.assertEqual(equation.decimals, 6)
        self.assertEqual(equation.reference, 1.414213562373095)
        self.assertEqual(equation.plots, ("residual", "absolute"))
        self.assertEqual(assignment.screenshot_kind, "command_editor")

    def test_text_and_reviewed_images_can_each_supply_a_task(self):
        first = ["方法：二分法", "方程：x^2-2=0", "区间：[1,2]"]
        second = ["【图片 1】 题目.png", "方法：牛顿法", "方程：x^2-3=0", "初值：1"]
        assignment = parse_assignment(first, second)
        self.assertEqual([item.methods for item in assignment.equations], [("bisection",), ("newton",)])

    def test_scan_pdf_requires_review_and_renders_to_image(self):
        with TemporaryDirectory(dir=WORKSPACE) as tmp:
            folder = Path(tmp)
            source = folder / "scan.pdf"
            pdf = PdfWriter()
            pdf.add_blank_page(width=320, height=480)
            with source.open("wb") as stream:
                pdf.write(stream)
            with self.assertRaisesRegex(LabError, "扫描"):
                read_pdf(source)
            self.assertEqual(read_pdf(source, use_review=True), [])
            self.assertTrue(pdf_images(source, folder / "pages")[0].is_file())

    def test_excel_import_rejects_uncached_formulas(self):
        with TemporaryDirectory(dir=WORKSPACE) as tmp:
            source = Path(tmp) / "nodes.xlsx"
            book = Workbook()
            sheet = book.active
            for row in (("方法", "拉格朗日插值法"), ("节点", 0, 1), ("节点值", 1, 2), ("求值点", .5)):
                sheet.append(row)
            book.save(source)
            equation, = parse_assignment(read_spreadsheet(source, read_text)).equations
            self.assertEqual(equation.query, (.5,))
            sheet["B3"] = "=1+0"
            book.save(source)
            with self.assertRaisesRegex(LabError, "未计算"):
                read_spreadsheet(source, read_text)
            book.close()

    def test_template_duplicate_locations_and_marked_examples(self):
        doc = Document()
        doc.add_paragraph("{{实验结果}}")
        cell = doc.add_table(rows=1, cols=1).cell(0, 0)
        cell.text = "{{实验结果与截图}}"
        with self.assertRaisesRegex(LabError, "占位符"):
            validate_layout(doc)
        cell.text = "{{示例开始}}"
        cell.add_paragraph("示例答案")
        cell.add_paragraph("{{示例结束}}")
        remove_marked_examples(doc)
        self.assertEqual(cell.text, "")
        self.assertFalse(validate_layout(doc))

    def test_modified_method_source_is_rejected_before_execution(self):
        assignment = parse("方法：二分法\n方程：x^2-2=0\n区间：[1,2]")
        with TemporaryDirectory(dir=WORKSPACE) as tmp:
            parent = Path(tmp)
            (parent / "NA1_alg1.m").write_text("% modified", encoding="ascii")
            with self.assertRaisesRegex(LabError, "随附版本不一致"):
                prepare_run(assignment, parent / "source.txt", parent / "run", parent)

    def test_expanded_near_roots_are_isolated(self):
        equation, = parse("方法：roots\n方程：x^2-2.000000001*x+1.000000001=0\nx≥0").equations
        self.assertEqual(equation.root_target, "nonnegative")
        self.assertEqual(sorted(-c[1] for c, _ in equation.factors), [1, 1.000000001])

    def test_rational_domain_and_error_criteria(self):
        hole, = parse("方法：二分法\n方程：x^2/x=0\n区间：[-1,2]").equations
        self.assertIn("分母为零",hole.domain_problem)
        relative, = parse("方法：二分法\n方程：t^2-2=0\n区间：(1,2)\n相对误差小于1e-6").equations
        self.assertEqual(relative.open_interval,(True,True))
        self.assertEqual((relative.error_mode,relative.error_tolerance),("relative",1e-6))

    def test_trigonometric_denominators_and_poles(self):
        for formula in ("x^2/sin(x)", "(x-1)^2/cos(2*x)", "tan(x)"):
            with self.subTest(formula=formula):
                task, = parse(f"方法：二分法\n方程：{formula}=0\n区间：[-1,2]").equations
                self.assertTrue(task.domain_problem)
        integral, = parse("方法：复合梯形法\n被积函数：1/sin(t)\n区间：[-1,2]\n分段数：4").equations
        self.assertIn("反常积分", integral.domain_problem)

    def test_nonzero_and_unverified_denominators_have_distinct_status(self):
        valid, = parse("方法：二分法\n方程：(x-1.5)/sin(x)=0\n区间：[1,2]").equations
        pending, = parse("方法：二分法\n方程：x^2/(exp(x)-1)=0\n区间：[-1,2]").equations
        self.assertFalse(valid.domain_problem or valid.domain_warning)
        self.assertFalse(pending.domain_problem)
        self.assertIn("结合原式定义域核对", pending.domain_warning)

    def test_task_locals_follow_their_method_declarations(self):
        assignment = parse("""方法：二分法
方程：x^2-2=0
区间：[1,2]
方法：牛顿法、割线法
方程：f(x)=x^2-3=0
牛顿法初值：2
割线法初值：[1,2]""")
        self.assertEqual([item.methods for item in assignment.equations],
                         [("bisection",),("newton","secant")])
        self.assertEqual(assignment.equations[1].method_initials,(("newton",(2.,)),("secant",(1.,2.))))

    def test_no_silent_drop_of_duplicate_or_mixed_data(self):
        with self.assertRaisesRegex(LabError,"求根方程"):
            parse("方法：复合梯形法\n被积函数：x^2\n区间：[0,1]\n分段数：4\n方程：x^2-2=0")
        with self.assertRaisesRegex(LabError,"冲突"):
            parse("方法：拉格朗日插值法\n节点：[0,1]\n节点：[0,2]\n节点值：[0,1]")

    def test_screenshot_status_and_continued_pages_are_per_task(self):
        one = ["【图片 1】 a.png","任务 1","方法：二分法","方程：x^2-2=0","【图片 2】 b.png","区间：[1,2]"]
        two = ["任务 2","方法：二分法","方程：x^2-3=0","区间：[1,2]","请截取编辑器截图"]
        first,second = parse_assignment([],one+two).equations
        self.assertEqual(first.interval,(1.,2.))
        self.assertEqual(first.capture,())
        self.assertEqual(second.capture,("editor",))

    def test_requirements_and_selectable_exercise(self):
        lines = ["实验内容","在区间 [1,2] 内用二分法计算 x^3 - x - 1 = 0",
                 "请另外比较收敛阶", "实验结果", "绝对残差小于 1e-7"]
        full = parse_assignment(lines)
        self.assertEqual(full.equations[0].residual_tolerance,1e-7)
        self.assertTrue(full.pending)
        only = parse_assignment(select_exercise(lines,1))
        self.assertEqual(only.equations[0].interval,(1.,2.))

    def test_merge_pdf_review_keeps_text_at_its_original_positions(self):
        with patch("octave_lab_report.inputs.pdf_pages",return_value=["方法：二分法","","区间：[1,2]"]):
            merged = merge_pdf_review(Path("paper.pdf"),"【图片 1】 paper-第002页.png\n方程：x^2-2=0")
        self.assertEqual(merged,["方法：二分法","方程：x^2-2=0","区间：[1,2]"])

    def test_choose_excel_sheet_and_region(self):
        with TemporaryDirectory(dir=WORKSPACE) as tmp:
            source = Path(tmp)/"tasks.xlsx"
            book = Workbook()
            book.active["A1"] = "忽略本表"
            tab=book.create_sheet("题目")
            tab["B2"],tab["B3"],tab["B4"] = "方法：二分法","方程：x^2-2=0","区间：[1,2]"
            book.save(source)
            self.assertEqual(len(parse_assignment(read_spreadsheet(source,read_text,sheet="题目",data_range="B2:B4")).equations),1)

    def test_normalized_template_headings_and_clear_stale_results(self):
        document=Document()
        document.add_paragraph("封面")
        command=document.add_paragraph("（3） 在Octave命令窗口输入指令")
        document.add_paragraph("实验结果")
        document.add_paragraph("旧答案")
        document.add_paragraph("实验分析与结论")
        document.add_paragraph("旧结论")
        self.assertEqual(find_heading(document,"在 Octave 命令窗口输入指令").text,command.text)
        clear_old_results(document)
        self.assertEqual([p.text for p in document.paragraphs],["封面",command.text,"实验结果","实验分析与结论"])

    def test_resume_reorders_good_results_and_rejects_changed_files(self):
        with TemporaryDirectory(dir=WORKSPACE) as tmp:
            root=Path(tmp)
            old,new=root/"old",root/"new"
            old.mkdir()
            new.mkdir()
            state={"runtime":{},"octave":{"version":"ok"},"tasks":["a","b"],"files":{}}
            for i in (1,2):
                item=old/f"task-{i:02}.json"
                item.write_text(dumps({"results":[{"re":i}],"pages":[[f"Equation {i}"]]}),encoding="utf-8")
                state["files"][item.name]=file_digest(item)
            (old/"run-state.json").write_text(dumps(state),encoding="utf-8")
            (old/"task-01.json").write_text(dumps({"results":[{"re":3}]}),encoding="utf-8")
            import_checkpoints(old,new,{**state,"tasks":["b","a"],"screenshots":True})
            self.assertEqual(loads((new/"task-01.json").read_text(encoding="utf-8"))["pages"],[["Equation 1"]])
            self.assertFalse((new/"task-02.json").exists())
