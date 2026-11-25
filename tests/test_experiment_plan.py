import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from octave_lab_report.engine import execute
from octave_lab_report.spec import LabError, format_plan, parse_assignment


class AssignmentTests(unittest.TestCase):
    def parse(self, text):
        return parse_assignment(text.strip().splitlines())

    def test_unavailable_method_cannot_be_left_out(self):
        for method in ("共轭梯度法", "示例消去法"):
            with self.subTest(method=method), self.assertRaisesRegex(LabError, method):
                self.parse(f"方法：二分法和{method}\n方程：x^2-2=0")

    def test_local_tolerance_does_not_leak(self):
        assignment = self.parse("""
任务 1
方法：牛顿法
容差：1e-6
方程：x^2-2=0
初值：1.5
任务 2
方法：牛顿法
方程：x^2-3=0
初值：1.7
""")
        self.assertEqual([task.tolerance for task in assignment.equations], [1e-6, 1e-12])

    def test_common_settings_can_be_explicitly_shared(self):
        assignment = self.parse("""
容差：1e-8
方法：牛顿法
任务 1
容差：1e-6
方程：x^2-2=0
初值：1.5
任务 2
方程：x^2-3=0
初值：1.7
""")
        self.assertEqual([task.tolerance for task in assignment.equations], [1e-6, 1e-8])

    def test_image_metadata_is_checked_and_shown(self):
        with self.assertRaisesRegex(LabError, "小数点"):
            parse_assignment([], ["方法：牛顿法", "方程：x^2-2=0", "初值：1 · 5"])
        assignment = parse_assignment(["方法：牛顿法"], ["方程：x^2-2=0", "初值：1.6", "求正根"])
        self.assertIn("初值  [1.6]", format_plan(assignment))
        self.assertEqual(assignment.equations[0].root_target, "positive")

    def test_convergence_chart_is_part_of_the_plan(self):
        assignment = self.parse("方法：牛顿法\n方程：x^2-2=0\n初值：1.5\n绘制误差收敛图")
        self.assertEqual(assignment.equations[0].plot, "step")
        self.assertIn("相邻迭代差收敛图", format_plan(assignment))

    def test_extra_tasks_use_distinct_methods(self):
        assignment = self.parse("""
任务 1 非线性方程组
方法：fsolve
方程 1：x^2+y^2=5
方程 2：x+y=3
初值：[1.1,1.9]
任务 2 插值
方法：拉格朗日插值法和牛顿插值法
节点：[0,1,2]
节点值：[1,2,5]
求值点：0.5
绘制插值曲线
""")
        self.assertEqual([task.kind for task in assignment.equations], ["system", "interpolation"])
        self.assertEqual(assignment.equations[1].methods, ("lagrange", "newton_interp"))


class ScreenshotChoiceTests(unittest.TestCase):
    def test_no_screenshots_does_not_create_log_images(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            work = Path(temp)
            (work / "results.json").write_text(json.dumps({"cases": [{"pages": [["Octave output"]]}]}), encoding="utf-8")
            with patch("octave_lab_report.engine.octave_command", return_value=(["octave"], {})), \
                    patch("octave_lab_report.engine.run_process", return_value=SimpleNamespace(returncode=0, stdout="Octave output")) as process, \
                    patch("octave_lab_report.engine.render_output") as render:
                _, evidence = execute(Path("octave"), work, screenshots=False)
            self.assertEqual(evidence, [[]])
            self.assertIn("run_experiment(false)", process.call_args.args[0])
            self.assertTrue((work / "octave.log").is_file())
            render.assert_not_called()
