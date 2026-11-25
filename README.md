# Octave 实验报告助手

在本地用 GNU Octave 完成数值计算实验，按指定的 Word 模板生成报告。运行前可核对识别到的题目和参数，并选择本次是否附上截图。

## 在 Windows 上启动

1. 安装带 Tk 的 Python 3.10 或更新版本，再安装 GNU Octave。需要处理旧版 Word 模板或导出 PDF 时安装 LibreOffice。
2. 解压源码后运行 `setup_lab_report.cmd` 安装依赖并检查环境。
3. 双击 `start_lab_report.cmd`，选择题目和报告模板，核对清单后运行。

题目支持 Word、PDF、电子表格、文本和图片。内置求根、方程组、插值、拟合、积分和常微分方程算法。结果可导出为 Word、PDF 和 Excel，已完成的计算可以续跑。

```powershell
python -m octave_lab_report --list-methods
python -m octave_lab_report --check
python -m octave_lab_report "题目.txt" --no-screenshots
```

完整的题目语法、模板用法和计算限制见[使用说明](实验报告助手使用说明.md)。

## 运行测试

```powershell
python -m unittest discover -s tests
```

图片中的复杂公式及一般函数的定义域仍需核对。完整的图形和 OCR 环境可在目标电脑上通过 `--check --screenshots` 检查。
