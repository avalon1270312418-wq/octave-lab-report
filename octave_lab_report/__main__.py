"""Command-line and desktop entry point for the local lab report assistant."""

import argparse
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import platform

from docx.opc.exceptions import PackageNotFoundError

from .engine import ASSETS, WINDOWS, convert_doc, execute, find_libreoffice, find_octave, octave_command, prepare_run, render_report, run_process
from .inputs import SOURCE_SUFFIXES, merge_pdf_review, pdf_images, pdf_pages, read_pdf, read_spreadsheet
from .ocr import MISSING_TEXT, editable_text, image_paths, question_lines, recognize
from .report import clear_old_results, has_old_results, open_dotx, read_source, read_text, remove_marked_examples, validate_layout, write_report
from .spec import Cancelled, LabError, METHODS, format_plan, parse_assignment, select_exercise


@dataclass(frozen=True)
class Completed:
    document: Path
    equations: int
    screenshots: int
    log_images: int
    charts: int = 0
    incomplete: int = 0


def publish(work, parent, stem):
    index = 1
    while True:
        name = stem if index == 1 else f"{stem}_{index}"
        destination = parent / name
        if not destination.exists():
            try:
                work.rename(destination)
                return destination
            except OSError:
                if not destination.exists():
                    raise
        index += 1


def prepare_word_file(path, scratch, libreoffice, cancel=None):
    if path.suffix.lower() not in {".doc", ".dotx"}:
        return path
    scratch.mkdir()
    if path.suffix.lower() == ".dotx":
        return open_dotx(path, scratch / "template.docx")
    return convert_doc(path, scratch, libreoffice, cancel)


def read_questions(path, problem_text, sheet=None, data_range=None, trust_formula_cache=False,cancel=None):
    if path is None:
        return None, []
    if path.suffix.lower() == ".pdf":
        return None, read_pdf(path, use_review=problem_text is not None,cancel=cancel)
    if path.suffix.lower() in {".xls", ".xlsx", ".csv", ".tsv"}:
        return None, read_spreadsheet(path, read_text, sheet=sheet, data_range=data_range, trust_formula_cache=trust_formula_cache,cancel=cancel)
    return read_source(path)


def file_digest(path):
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_signature(assignment, screenshots, identity, screenshots_kind, executable, work, cancel):
    binary = {}
    if executable.is_file():
        command, env = octave_command(executable, graphics=False)
        run = run_process([*command, "--version"], work, "Octave 版本检查", 15, env, cancel=cancel)
        version = next((line for line in run.stdout.splitlines() if "GNU Octave" in line), "")
        binary = {"version": version, "platform": platform.system(), "arch": platform.machine(),
                  "binary": file_digest(Path(command[0]))}
    return {"inputs": identity, "tasks": [sha256(repr(replace(job, capture=())).encode()).hexdigest() for job in assignment.equations],
            "screenshots": screenshots, "screenshot_kind": screenshots_kind,
            "octave": binary, "files": {},
            "runtime": {p.name: file_digest(p) for p in sorted(ASSETS.glob("*.m"))}}


def record_checkpoint(work, state, index):
    names = (f"task-{index:02}.json", f"plot-{index:02}.png")
    paths = [*(work / name for name in names), *work.glob(f"plot-{index:02}-*.png"), *work.glob(f"case-{index:02}-*.png")]
    state["files"].update({path.name: file_digest(path) for path in paths if path.is_file()})
    meta = work / "run-state.json"
    staged = meta.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    staged.replace(meta)


def import_checkpoints(previous, work, state):
    original = next((folder for folder in (previous / "Octave", previous / "成果" / "Octave", previous) if
                     (folder / "run-state.json").is_file()), previous)
    metadata = original / "run-state.json"
    try:
        saved = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LabError("续跑目录没有可读取的运行状态。请选择之前的报告输出目录。") from exc
    if any(saved.get(k) != state[k] for k in ("runtime", "octave")):
        return
    previous_tasks = saved.get("tasks", [])
    files = saved.get("files", {})
    reused = False
    for i, task in enumerate(state["tasks"], 1):
        if task not in previous_tasks:
            continue
        old_index = i if i <= len(previous_tasks) and previous_tasks[i-1] == task else previous_tasks.index(task)+1
        checkpoint = original / f"task-{old_index:02}.json"
        if not checkpoint.is_file() or file_digest(checkpoint) != files.get(checkpoint.name):
            continue
        try:
            record = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(record.get(key) for key in ("error", "issue")) or not record.get("results"):
            continue
        charts = tuple(filter(None, record.get("plot_files") or [record.get("plot_file")]))
        if any(old_index != i or not re.fullmatch(rf"plot-{i:02}(?:-\d{{2}})?\.png", name) or
               not (path := original / name).is_file() or file_digest(path) != files.get(name) for name in charts):
            continue
        record.pop("screenshot_error", None)
        record["pages"] = [[line.replace("complete trace in octave.log","complete trace in task JSON")
                            for line in page] for page in record.get("pages",[]) ]
        if old_index != i:
            record["pages"] = [[re.sub(rf"(Task|Equation) {old_index}(?=\D|$)", rf"\g<1> {i}", line)
                                for line in page] for page in record.get("pages", [])]
        (work / f"task-{i:02}.json").write_text(json.dumps(record, ensure_ascii=False),encoding="utf-8")
        reused = True
        pictures = [*(original / name for name in charts)]
        if old_index == i:
            pictures.extend(original.glob(f"case-{i:02}-*.png"))
        for picture in pictures:
            if file_digest(picture) == files.get(picture.name):
                shutil.copyfile(picture, work / picture.name)
    if reused and (previous_log := original / "octave.log").is_file():
        shutil.copyfile(previous_log, work / "octave-reused.log")


def complete(source=None, *, report_template=None, images=(), problem_text=None, output=None, octave=None, template_dir=None,
             libreoffice=None, require_screenshot=False, screenshots=True, confirm=None, progress=print,
             cancel=None, timeout=300, resume=None, screenshot_kind="auto", render=False, exports=(),
             exercise=None, clear_layout=False, sheet=None, data_range=None, trust_formula_cache=False, replace_questions=False):
    images = image_paths(images)
    source = Path(source).resolve() if source else None
    report_template = Path(report_template).resolve() if report_template else None
    if source and (source.suffix.lower() not in SOURCE_SUFFIXES or not source.is_file()):
        raise LabError("请选择已存在的 Word、PDF、Excel、CSV、TSV 或文本题目。")
    if report_template and (report_template.suffix.lower() not in {".docx", ".dotx", ".doc"} or not report_template.is_file()):
        raise LabError("请选择本机已有的 .docx、.dotx 或 .doc Word 报告模板。")
    if not source and not images and not report_template and not problem_text:
        raise LabError("请选择题目文档、题目图片，或包含题目的 Word 报告模板。")
    if images and not problem_text:
        raise LabError("请先核对或录入图片中的题目文字，再生成报告。")
    if problem_text and MISSING_TEXT in problem_text:
        raise LabError("还有未识别的图片。请补录题目文字，或删除与题目无关图片的补录提示。")
    questions = question_lines(problem_text) if problem_text is not None else []
    problem_file = source or (report_template if not images and problem_text is None else None)
    base_file = problem_file or (images[0] if images else report_template or Path.cwd() / "手工录入.txt")
    parent = Path(output).resolve() if output else base_file.parent
    parent.mkdir(parents=True, exist_ok=True)
    stem = problem_file.stem[:110] if problem_file else "图片实验报告" if images else "手工题目实验报告"
    template_dir = Path(template_dir).resolve() if template_dir else None
    with tempfile.TemporaryDirectory(prefix=".octave-lab-", dir=parent) as temp:
        scratch = Path(temp)
        input_path = prepare_word_file(problem_file, scratch / "input", libreoffice, cancel) if problem_file else None
        question_doc, lines = (None, []) if replace_questions and problem_text else read_questions(
            input_path, problem_text, sheet, data_range, trust_formula_cache,cancel)
        layout_path, doc = None, None
        if report_template:
            if report_template == problem_file:
                layout_path, doc = input_path, question_doc
                if doc is None:
                    doc, _ = read_source(layout_path,allow_filled=True)
            else:
                layout_path = prepare_word_file(report_template, scratch / "layout", libreoffice, cancel)
                doc, _ = read_source(layout_path, allow_filled=True)
        if exercise is not None:
            lines = select_exercise(lines, int(exercise))
        if cancel and cancel.is_set():
            raise Cancelled()
        if input_path and input_path.suffix.lower() == ".pdf" and problem_text is not None and not replace_questions:
            lines = merge_pdf_review(input_path, problem_text,cancel)
            questions = []
            assignment = parse_assignment(lines)
        else:
            assignment = parse_assignment(lines, problem_text.splitlines() if problem_text is not None else None)
        if report_template:
            remove_marked_examples(doc)
            note = "请核对模板保留的页眉、提交信息和旧内容。"
            if validate_layout(doc):
                note += " 模板有未标记的示例。"
            if clear_layout:
                clear_old_results(doc,questions=exercise is not None)
            elif has_old_results(doc):
                assignment = replace(assignment,pending=(*assignment.pending,"模板残留旧结果，请勾选清理旧结果或在 Word 中核对。"))
            assignment = replace(assignment, report_template_name=report_template.name, layout_note=note)
        while confirm is not None:
            choice = confirm(assignment)
            if choice is None:
                raise Cancelled()
            if isinstance(choice, dict):
                problem_text = choice["text"]
                if MISSING_TEXT in problem_text:
                    raise LabError("还有待补录的图片公式。")
                lines = []
                questions = question_lines(problem_text)
                edited = parse_assignment(questions)
                assignment = replace(edited, report_template_name=assignment.report_template_name,layout_note=assignment.layout_note)
                continue
            if isinstance(choice, tuple):
                screenshots, screenshot_kind = choice
            else:
                screenshots = choice
            require_screenshot = screenshots
            break
        methods = dict.fromkeys(METHODS[key][0] for eq in assignment.equations for key in eq.methods)
        progress(f"已确认 {len(assignment.equations)} 个实验任务，方法包括{'、'.join(methods)}。")
        executable = find_octave(octave)
        work = scratch / "成果"
        work.mkdir()
        override = {"auto": (), "result": ("result",), "command": ("command",), "editor": ("editor",),
                    "command_editor": ("command","editor")}[screenshot_kind]
        capture = [tuple(dict.fromkeys((*job.capture, *override))) or ("result",) if screenshots else ()
                   for job in assignment.equations]
        captions = {"command": "命令窗口", "editor": "编辑器", "result":"结果图窗"}
        choice_text = "；".join(f"第 {i} 题 " + "、".join(captions[kind] for kind in row)
                                for i, row in enumerate(capture, 1) if row) if screenshots else "本次不截图"
        (work / "实验运行清单.txt").write_text(format_plan(assignment) + f"\n\n运行截图  {choice_text}\n", encoding="utf-8")
        if problem_text is not None:
            references = "\n".join(f"  {index}. {path.name}" for index, path in enumerate(images, 1))
            text = f"题目图片\n{references}\n\n核对后的题目文字\n{problem_text.strip()}\n" if images else problem_text.strip() + "\n"
            (work / "题目录入.txt").write_text(text, encoding="utf-8")
        experiment = work / "Octave"
        templates = prepare_run(assignment, base_file, experiment, template_dir)
        identity = {str(path): file_digest(path) for path in dict.fromkeys((problem_file, report_template, *images)) if path}
        state = run_signature(assignment, screenshots, identity, screenshot_kind, executable, experiment, cancel)
        if resume:
            import_checkpoints(Path(resume).resolve(), experiment, state)
        (experiment / "run-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        progress("正在运行 GNU Octave…" + (f"之后{choice_text}。" if screenshots else ""))
        result_capture = tuple("result" in row for row in capture)
        data, figures = execute(executable, experiment, require_screenshot or screenshots, screenshots=result_capture if screenshots else False,
                                charts=any(job.plots for job in assignment.equations), task_count=len(assignment.equations),
                                progress=progress, cancel=cancel, timeout=timeout, resume=bool(resume),
                                checkpoint=lambda index: record_checkpoint(experiment,state,index))
        from .native_screenshot import capture_window
        for index, (entry, task, kinds) in enumerate(zip(data["cases"], assignment.equations, capture, strict=True), 1):
            if task.capture and not screenshots:
                entry["screenshot_error"] = "用户选择不截图，原题指定的截图待补。"
            if entry.get("error"):
                continue
            if cancel and cancel.is_set():
                if any(kind != "result" for kind in kinds):
                    entry["screenshot_error"] = "窗口截图已中止。"
                continue
            for kind in (item for item in kinds if item != "result"):
                try:
                    figures[index-1].extend(capture_window(executable, experiment, kind, index=index,
                                                           task=task, entry=entry, cancel=cancel))
                except LabError as exc:
                    entry["screenshot_error"] = " ".join(filter(None, (entry.get("screenshot_error"), str(exc))))
        screenshot_count = sum(picture.screenshot for row in figures for picture in row)
        log_images = sum(not picture.screenshot and not picture.chart for row in figures for picture in row)
        charts = sum(picture.chart for row in figures for picture in row)
        progress(f"已获得 {screenshot_count} 张截图、{charts} 张计算图。正在回填 Word 报告…")
        requested_exports = set(exports) | set(assignment.exports)
        global_issues = list(assignment.pending)
        (experiment / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        report_name = stem + ".docx"
        displayed_lines = () if report_template and report_template == problem_file and exercise is None else lines
        try:
            write_report(doc, displayed_lines, layout_path, work / report_name,
                         assignment, data, figures, templates, experiment, questions, screenshot_requested=screenshots)
        except (LabError, OSError, ValueError) as exc:
            (work / report_name).unlink(missing_ok=True)
            (work / "报告回填诊断.txt").write_text(str(exc) + "\n", encoding="utf-8")
            recovery = publish(work, parent, stem + "_报告待回填")
            raise LabError(f"Word 回填失败。计算结果、截图和可续跑状态已保存到 {recovery}。{exc}") from exc
        pdf_staged = work / (stem + ".pdf")
        if (render or "pdf" in requested_exports) and not (cancel and cancel.is_set()):
            try:
                page_count = render_report(work / report_name, scratch / "render", libreoffice, cancel,
                                           pdf_staged if "pdf" in requested_exports else None)
                (work / "版式检查.txt").write_text(f"已成功转换并渲染 {page_count} 页。请在 Word 中核对截图可读性、分页和模板栏目。\n", encoding="utf-8")
                progress(f"已渲染 {page_count} 页，可打开 Word 预览版式。")
            except LabError as exc:
                global_issues.append(f"版式渲染待复核。{exc}")
                (work / "版式检查.txt").write_text(str(exc) + "\n", encoding="utf-8")
                progress(f"Word 已保存，渲染验证待复核。{exc}")
        if "xlsx" in requested_exports and not (cancel and cancel.is_set()):
            from .exports import spreadsheet
            try:
                spreadsheet(data, assignment, work / (stem + ".xlsx"))
            except (LabError, OSError, ValueError) as exc:
                global_issues.append(f"Excel 导出待处理。{exc}")
        if cancel and cancel.is_set():
            global_issues.append("用户停止运行，剩余计算或导出待处理。")
        incomplete = sum(bool(item.get("error") or item.get("issue") or item.get("screenshot_error")) for item in data["cases"])
        if global_issues:
            (work / "待处理事项.txt").write_text("\n".join(global_issues),encoding="utf-8")
            incomplete = max(incomplete,1)
        suffix = "_部分完成" if incomplete else "_完成"
        for extension in (".docx", ".pdf", ".xlsx"):
            staged = work / (stem + extension)
            if staged.is_file():
                staged.rename(work / (stem + suffix + extension))
        report_name = stem + suffix + ".docx"
        final = publish(work, parent, stem + suffix)
    return Completed(final / report_name, len(assignment.equations), screenshot_count, log_images, charts, incomplete)


def check_environment(octave, libreoffice, screenshot=False):
    print(f"Python  {sys.version.split()[0]}  {sys.executable}")
    missing = []
    for name in ("docx", "PIL", "tkinter", "pypdf", "pypdfium2", "openpyxl", "xlrd"):
        installed = find_spec(name) is not None
        print(f"{name}  {'可用' if installed else '缺少'}")
        if not installed and name not in {"xlrd", "tkinter"}:
            missing.append(name)
    office = None
    try:
        office = find_libreoffice(libreoffice)
        print(f"LibreOffice  {office}")
    except LabError as exc:
        print(str(exc))
    try:
        executable = find_octave(octave)
        command, env = octave_command(executable)
        print(f"GNU Octave  {command[0]}")
        with tempfile.TemporaryDirectory(prefix="octave-lab-check-") as tmp:
            folder = Path(tmp)
            probe = ("disp(OCTAVE_VERSION); disp(available_graphics_toolkits()); "
                     "assert(abs(fzero(@(x) x.^2-2,[1 2])-sqrt(2)) < 1e-10); "
                     "assert(abs(quadgk(@(x) x.^2,0,1)-1/3) < 1e-10);")
            result = run_process([*command, "--eval", probe], folder, "GNU Octave 环境检查", 20, env)
            print(result.stdout.strip())
            if result.returncode:
                return 1
            if not missing:
                trial = complete(output=folder,octave=executable,libreoffice=office,screenshots=screenshot,
                                 problem_text="方法：二分法\n方程：x^2-2=0\n区间：[1,2]", render=bool(office),
                                 exports=("xlsx",),progress=lambda _:None)
                if trial.incomplete or (screenshot and not trial.screenshots):
                    print("端到端试跑有待处理项。请检查 Octave 图形工具包和 LibreOffice。")
                    return 1
                print("端到端试跑通过。已验证 Octave 计算、Word 写入和 Excel 导出。" + (" 已验证结果图窗截图。" if screenshot else ""))
            if WINDOWS:
                from PIL import Image, ImageDraw, ImageFont
                target = folder / "ocr.png"
                with Image.new("RGB",(600,180),"white") as image:
                    ImageDraw.Draw(image).text((35,35),"12345",fill="black",font=ImageFont.load_default(size=52))
                    image.save(target)
                page, = recognize([target])
                print("Windows OCR  " + ("已识别试验图片" if "12345" in page.text else "读图待检查。请核对 PowerShell 和系统 OCR 语言。"))
    except (LabError, OSError, ValueError) as exc:
        print(str(exc))
        return 1
    if not WINDOWS:
        print("系统 OCR  桌面 OCR 使用 Windows。")
    return int(bool(missing))


def main(argv=None):
    parser = argparse.ArgumentParser(description="用 GNU Octave 完成本地数值计算实验报告。")
    parser.add_argument("report", nargs="?", help="提供题目的 Word、PDF、Excel、CSV、TSV 或文本文件。")
    parser.add_argument("--batch", action="append", default=[], help="批处理题目文件，可重复指定，共用报告模板和运行选项。")
    parser.add_argument("--report-template", help="用户指定的 Word 报告版式（.docx、.dotx、.doc）。")
    parser.add_argument("--image", action="append", default=[], help="题目图片，可按顺序重复指定。")
    parser.add_argument("--ocr-only", action="store_true", help="先提取题目图片中的文字，供人工核对。")
    parser.add_argument("--reviewed-text", help="已经核对的题目文字文件（.txt、.md）。")
    parser.add_argument("--replace-questions", action="store_true", help="核对文字替换题目来源的文字。")
    parser.add_argument("--ocr-language", help="选择可用的 Windows OCR 语言，例如 zh-Hans 或 en。")
    parser.add_argument("--output", help="输出的父目录，默认使用输入文件所在目录。")
    parser.add_argument("--octave", help="Octave 可执行文件或安装目录。")
    parser.add_argument("--template-dir", help="现有且可信的 Octave 方法模板目录。")
    parser.add_argument("--libreoffice", help="为 .doc 转换指定 LibreOffice 可执行文件。")
    parser.add_argument("--timeout", type=int, default=300, help="Octave 总超时秒数。")
    parser.add_argument("--resume", help="上次报告或 Octave 输出目录。仅重跑未完成或已改题目。")
    parser.add_argument("--screenshot-kind", choices=("auto", "result", "command", "editor", "command_editor"), default="auto", help="按题目要求选截图，或额外指定窗口。")
    parser.add_argument("--export", action="append", choices=("pdf","xlsx"), default=[], help="另存 PDF 或 Excel，可重复指定。")
    parser.add_argument("--exercise", type=int, help="从原文检出的一元方程中选第 N 道。")
    parser.add_argument("--clear-layout", action="store_true", help="清理模板中明确的旧结果和结论段落。")
    parser.add_argument("--sheet", help="仅从指定 Excel 工作表读取。")
    parser.add_argument("--data-range", help="Excel 数据区域，例如 A1:D12。")
    parser.add_argument("--trust-formula-cache", action="store_true", help="确认 Excel 公式已经重算保存，允许导入缓存值。")
    parser.add_argument("--no-render-check", action="store_true", help="跳过 LibreOffice 版式渲染验证。")
    capture = parser.add_mutually_exclusive_group()
    capture.add_argument("--screenshots", "--require-screenshot", dest="screenshots", action="store_true", default=None,
                         help="生成 Octave 截图。截图失败时保留部分报告。")
    capture.add_argument("--no-screenshots", dest="screenshots", action="store_false",
                         help="跳过运行截图，保留题目要求的计算图形。")
    parser.add_argument("--list-methods", action="store_true", help="显示当前支持的方法及模板文件名。")
    parser.add_argument("--check", action="store_true", help="检查 Python 依赖、Octave 和图形工具包。")
    args = parser.parse_args(argv)
    if args.check:
        return check_environment(args.octave, args.libreoffice, screenshot=bool(args.screenshots))
    if args.list_methods:
        for title, _, filename in METHODS.values():
            print(f"{title:<16} {filename or 'GNU Octave 内置'}")
        return 0
    if args.ocr_only:
        pdf = Path(args.report).resolve() if args.report and Path(args.report).suffix.lower() == ".pdf" else None
        if not args.image and not pdf:
            parser.error("--ocr-only 需要 PDF 文件或至少一张图片。")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                original = pdf_pages(pdf) if pdf else []
                missing = [i for i,page in enumerate(original,1) if not page]
                paths = pdf_images(pdf, Path(tmp), missing or None) if pdf else []
                pages = recognize([*paths, *args.image],language=args.ocr_language)
        except (LabError, OSError, ValueError) as exc:
            print(f"识别失败：{exc}", file=sys.stderr)
            return 1
        print(editable_text(pages))
        for page in pages:
            if page.warning:
                print(f"{page.path.name}：{page.warning}", file=sys.stderr)
        return 0 if any(page.text for page in pages) else 1
    if not args.report and not args.image and not args.reviewed_text and not args.report_template and not args.batch:
        try:
            from .desktop import launch
            launch(complete)
        except (ImportError, LabError, OSError) as exc:
            print(f"桌面界面启动失败：{exc}", file=sys.stderr)
            return 1
        return 0
    if args.timeout < 1:
        parser.error("--timeout 必须是正整数。")
    if args.batch and (args.image or args.reviewed_text or args.resume):
        parser.error("批处理请使用独立题目文件。图片核对和续跑每次处理一份报告。")

    def choose(assignment):
        print("\n" + format_plan(assignment) + "\n")
        if args.screenshots is not None:
            return args.screenshots
        if not sys.stdin.isatty():
            raise LabError("非交互运行请明确选择 --screenshots 或 --no-screenshots。")
        while True:
            try:
                answer = input("已核对上方清单。需要截取 Octave 窗口吗？[Y 截图 / N 不截图 / C 取消] ").strip().lower()
            except EOFError:
                return None
            if answer in {"y", "n", "c"}:
                return {"y": True, "n": False, "c": None}[answer]

    sources = ([args.report] if args.report else []) + args.batch or [None]
    failed = False
    for source in sources:
        try:
            problem_text = read_text(Path(args.reviewed_text)) if args.reviewed_text else None
            result = complete(source, output=args.output, octave=args.octave, template_dir=args.template_dir,
                              report_template=args.report_template, libreoffice=args.libreoffice, confirm=choose,
                              images=args.image, problem_text=problem_text, timeout=args.timeout, resume=args.resume,
                              screenshot_kind=args.screenshot_kind, render=not args.no_render_check, exports=args.export,
                              exercise=args.exercise, clear_layout=args.clear_layout, sheet=args.sheet,
                              data_range=args.data_range, trust_formula_cache=args.trust_formula_cache,
                              replace_questions=args.replace_questions)
        except Cancelled:
            print("已取消本次运行。")
            break
        except (LabError, OSError, PackageNotFoundError, ValueError) as exc:
            print(f"生成失败：{exc}", file=sys.stderr)
            failed = True
            continue
        failed |= bool(result.incomplete)
        print(f"{'已保存部分报告' if result.incomplete else '已生成'}：{result.document}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
