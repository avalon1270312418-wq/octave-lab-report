from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from docx.shared import Inches

from octave_lab_report.__main__ import complete
from octave_lab_report.report import MAIN_WORD_TYPE
from octave_lab_report.spec import LabError, parse_assignment


class ReportTemplateTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        case = {"kind": "scalar", "interval": [1, 2], "automatic": False,
                "results": [{"method": "fzero", "re": 1.414213562373095, "im": 0, "steps": 7,
                             "residual": 4e-16, "starts": []}]}
        octave = patch("octave_lab_report.__main__.find_octave", return_value=Path("octave"))
        run = patch("octave_lab_report.__main__.execute", return_value=({"version": "11.3.0", "cases": [case]}, [[]]))
        for mock in (octave, run):
            mock.start()
            self.addCleanup(mock.stop)

    def make_word(self, name, header, paragraphs):
        document = Document()
        document.sections[0].header.paragraphs[0].text = header
        document.sections[0].left_margin = Inches(1.45)
        for text in paragraphs:
            document.add_paragraph(text)
        path = self.root / name
        document.save(path)
        return path

    def generate(self, source=None, **options):
        filed = complete(source, output=self.root, screenshots=False, progress=lambda _: None, **options)
        return filed, Document(filed.document)

    def test_question_document_does_not_select_layout(self):
        path = self.make_word("题目.docx", "题目文档页眉", ("方法：fzero", "方程：x^2-2=0", "区间：[1,2]"))
        result, report = self.generate(path)
        self.assertNotEqual(report.sections[0].header.paragraphs[0].text, "题目文档页眉")
        self.assertIn("通用 Word 版式", (result.document.parent / "实验运行清单.txt").read_text(encoding="utf-8"))
        self.assertIn("方程：x^2-2=0", [p.text for p in report.paragraphs])

    def test_explicit_layout_preserves_template_and_inserts_source_questions(self):
        path = self.make_word("题目.docx", "题目文档页眉", ("方法：fzero", "方程：x^2-2=0", "区间：[1,2]"))
        layout = self.make_word("指定模板.docx", "版式专属页眉", ("课程封面", "{{实验目的}}", "{{实验要求}}", "{{实验结果}}"))
        before = sha256(layout.read_bytes()).digest()
        result, report = self.generate(path, report_template=layout)
        self.assertEqual(report.sections[0].header.paragraphs[0].text, "版式专属页眉")
        self.assertAlmostEqual(report.sections[0].left_margin.inches, 1.45, places=3)
        text = [p.text for p in report.paragraphs]
        self.assertIn("方程：x^2-2=0", text)
        self.assertIn("课程封面", text)
        self.assertNotIn("{{实验结果}}", text)
        self.assertTrue(report.tables)
        self.assertEqual(sha256(layout.read_bytes()).digest(), before)
        self.assertIn(layout.name, (result.document.parent / "实验运行清单.txt").read_text(encoding="utf-8"))

    def test_dotx_can_provide_both_questions_and_layout(self):
        path = self.make_word("底稿.docx", "模板页眉", ("方法：fzero", "方程：x^2-2=0", "区间：[1,2]"))
        layout = self.root / "实验.dotx"
        with ZipFile(path) as original, ZipFile(layout, "w") as output:
            for name in original.namelist():
                data = original.read(name)
                if name == "[Content_Types].xml":
                    data = data.replace(MAIN_WORD_TYPE.format("document").encode(), MAIN_WORD_TYPE.format("template").encode())
                output.writestr(name, data)
        _, report = self.generate(report_template=layout)
        self.assertEqual(report.sections[0].header.paragraphs[0].text, "模板页眉")
        self.assertEqual([p.text for p in report.paragraphs].count("方程：x^2-2=0"), 1)

    def test_textbook_reference_alone_requires_actual_questions(self):
        with self.assertRaisesRegex(LabError, "题目图片"):
            parse_assignment(["实验 1 方程求根", "NA1_alg1.m NA1_alg2.m", "教材 P240 实习题 2"])
