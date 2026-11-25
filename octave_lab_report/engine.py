"""Prepare a reproducible Octave run and retain its execution evidence."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont

from .spec import ExtraTask, LabError, METHODS


ASSETS = Path(__file__).resolve().parent / "octave"
WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Evidence:
    path: Path
    screenshot: bool
    chart: bool = False
    plot_index: int = 0
    kind: str = "result"
    source: str = ""


def find_octave(explicit=None):
    explicit = explicit or os.environ.get("OCTAVE_EXECUTABLE")
    exe_names = ("octave-cli.exe", "octave.exe", "octave-gui.exe") if WINDOWS else ("octave", "octave-cli")
    if explicit:
        given = Path(explicit).expanduser()
        if given.is_dir():
            for base in (given, given / "bin", given / "mingw64" / "bin", given / "mingw32" / "bin"):
                for name in exe_names:
                    if (base / name).is_file():
                        return (base / name).resolve()
        elif given.is_file():
            if WINDOWS and given.suffix.lower() != ".exe":
                raise LabError("请选择 Octave 的 .exe 文件，例如 mingw64\\bin\\octave-cli.exe。")
            return given.resolve()
        raise LabError(f"找不到指定的 Octave：{explicit}")
    for name in exe_names:
        if found := shutil.which(name):
            return Path(found).resolve()
    if WINDOWS:
        for root in (Path(os.environ.get("ProgramFiles", "C:/Program Files")), Path(os.environ.get("LOCALAPPDATA", "")) / "Programs", Path.home() / "Downloads"):
            patterns = ("*octave*/mingw*/bin/octave-cli.exe", "*octave*/*octave*/mingw*/bin/octave-cli.exe", "*octave*/bin/octave-cli.exe")
            for pattern in patterns:
                matches = sorted(root.glob(pattern), key=lambda p: tuple(map(int, re.findall(r"\d+", str(p.relative_to(root))))), reverse=True)
                if matches:
                    return matches[0].resolve()
    raise LabError("没有找到 GNU Octave。请安装或解压 Octave，再在界面选择其 .exe，或使用 --octave 指定路径。")


def find_libreoffice(explicit=None):
    explicit = explicit or os.environ.get("LIBREOFFICE_EXECUTABLE")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and (not WINDOWS or path.suffix.lower() in {".com", ".exe"}):
            return path.resolve()
        raise LabError(f"找不到指定的 LibreOffice：{explicit}")
    if found := shutil.which("soffice.com" if WINDOWS else "soffice") or shutil.which("soffice.exe"):
        return Path(found).resolve()
    if WINDOWS:
        for name in ("soffice.com", "soffice.exe"):
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice" / "program" / name
            if candidate.is_file():
                return candidate
    raise LabError("未找到 LibreOffice。读取 .doc 和自动检查 Word 版式可通过 --libreoffice 指定其路径。")


def convert_doc(source, scratch, libreoffice=None, cancel=None):
    executable = find_libreoffice(libreoffice)
    converted = scratch / "converted"
    converted.mkdir()
    # Use an isolated profile so an already-open Writer session cannot consume the request.
    command = [str(executable), f"-env:UserInstallation={(scratch / 'office-profile').as_uri()}",
               "--headless", "--convert-to", "docx", "--outdir", str(converted), str(source)]
    result = run_process(command, scratch, "LibreOffice", timeout=90, cancel=cancel)
    document = converted / (source.stem + ".docx")
    if not document.is_file():
        raise LabError(f"LibreOffice 未生成 .docx。{result.stdout.strip()}")
    return document


def render_report(source, scratch, libreoffice=None, cancel=None, pdf_output=None):
    from .inputs import pdf_pages, pdf_images
    try:
        import pypdfium2
    except ImportError as exc:
        raise LabError("版式检查需要 pypdfium2。请运行安装脚本。") from exc

    executable = find_libreoffice(libreoffice)
    scratch.mkdir()
    command = [str(executable), f"-env:UserInstallation={(scratch / 'profile').as_uri()}",
               "--headless", "--convert-to", "pdf", "--outdir", str(scratch), str(source)]
    result = run_process(command, scratch, "LibreOffice 版式检查", timeout=90, cancel=cancel)
    pdf = scratch / source.with_suffix(".pdf").name
    if not pdf.is_file():
        raise LabError(f"Word 渲染未生成 PDF。{result.stdout.strip()}")
    pages = pdf_pages(pdf)
    if not any(pages):
        raise LabError("渲染后的 PDF 未检出可阅读的报告文字。请打开 Word 复核。")
    try:
        with pypdfium2.PdfDocument(pdf) as rendered:
            for i,page in enumerate(rendered,1):
                try:
                    left,bottom,right,top = page.get_bbox()
                    content = page.get_textpage()
                    try:
                        for n in range(content.count_rects()):
                            x0,y0,x1,y1 = content.get_rect(n)
                            if x0 < left-2 or x1 > right+2 or y0 < bottom-2 or y1 > top+2:
                                raise LabError(f"渲染后的第 {i} 页有文字超出可见页面。请调整模板版式。")
                    finally:
                        content.close()
                finally:
                    page.close()
    except pypdfium2.PdfiumError as exc:
        raise LabError(f"无法检查渲染的页面边界。{exc}") from exc
    pdf_images(pdf, scratch / "pages")
    if pdf_output is not None:
        shutil.copyfile(pdf, pdf_output)
    return len(pages)


def run_process(command, cwd, name, timeout, env=None, on_line=None, cancel=None):
    if on_line is not None or cancel is not None:
        return stream_process(command, cwd, name, timeout, env, on_line, cancel)
    try:
        return subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
    except subprocess.TimeoutExpired as exc:
        raise LabError(f"{name} 超过 {timeout} 秒。请检查输入数据、收敛条件和软件安装。") from exc
    except OSError as exc:
        raise LabError(f"无法启动 {name}：{exc}") from exc


def stream_process(command, cwd, name, timeout, env, on_line, cancel):
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
    except OSError as exc:
        raise LabError(f"无法启动 {name}：{exc}") from exc
    lines = []

    def collect():
        for line in process.stdout:
            lines.append(line)
            if on_line:
                on_line(line)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    stopped = ""
    try:
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                stopped = "用户停止运行"
            elif time.monotonic() >= deadline:
                stopped = f"运行超过 {timeout} 秒"
            if stopped:
                process.kill()
                break
            time.sleep(.08)
    except KeyboardInterrupt:
        stopped = "用户停止运行"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=3)
    if stopped:
        lines.append(f"\n{stopped}\n")
    return subprocess.CompletedProcess(command, 124 if stopped else process.returncode, "".join(lines))


def octave_command(executable, graphics=True):
    program = executable
    env = os.environ.copy()
    env.pop("OCTAVE_PATH", None)
    env.pop("OCTAVE_INITFILE", None)
    if WINDOWS:
        binary = executable.parent
        gui = binary / "octave-gui.exe"
        cli = binary / "octave-cli.exe"
        if graphics and gui.is_file():
            program = gui
        elif not graphics and cli.is_file():
            program = cli
        install = binary.parent
        additions = [binary, install.parent / "usr" / "bin"]
        for qt in (install / "qt6", install / "qt5"):
            if (qt / "plugins").is_dir():
                env["QT_PLUGIN_PATH"] = str(qt / "plugins")
                additions.insert(0, qt / "bin")
                break
        env["PATH"] = os.pathsep.join([*(str(folder) for folder in additions if folder.is_dir()), env.get("PATH", "")])
    command = [str(program), "--quiet", "--norc"]
    if "octave-cli" not in program.name:
        command.append("--no-gui")
    return command, env


def octave_string(value):
    return "'" + value.replace("'", "''") + "'"


def octave_vector(values):
    return "[" + ", ".join(format(v, ".17g") for v in values) + "]"


def octave_cell(values):
    return "{" + ", ".join(map(octave_string, values)) + "}"


def octave_job(job):
    kind = job.kind if isinstance(job, ExtraTask) else "scalar"
    fields = [f"'kind', '{kind}'", f"'label', {octave_string(job.label)}",
              f"'methods', {{{octave_cell(job.methods)}}}", f"'plot', '{job.plot}'", f"'plots', {{{octave_cell(job.plots)}}}",
              f"'tol', {job.tolerance:.17g}", f"'digits', {job.digits}", f"'limit', {job.max_steps}",
              f"'initial', {octave_vector(job.initial)}", f"'decimals', {job.decimals if job.decimals is not None else '[]'}"]
    if kind == "scalar":
        fields.extend((f"'f', @(x) {job.expression}", f"'df', " + (f"@(x) {job.derivative}" if job.derivative else "[]"),
                       f"'g', " + (f"@(x) {job.iteration_function}" if job.iteration_function else "[]"),
                       f"'coeffs', {octave_vector(job.coefficients)}", f"'interval', {octave_vector(job.interval)}",
                       f"'target', '{job.root_target}'", "'reference', " + (format(job.reference, ".17g") if job.reference is not None else "[]")))
        factors = ", ".join(f"{{{octave_vector(coeffs)}, {n}}}" for coeffs, n in job.factors)
        fields.extend((f"'factors', {{{{{factors}}}}}", f"'real_count', {job.real_count if job.real_count is not None else '[]'}",
                       f"'fixed_steps', {job.fixed_steps or 0}", f"'residual_tol', {job.residual_tolerance if job.residual_tolerance is not None else '[]'}"))
        starts = ", ".join(f"'{name}', {octave_vector(values)}" for name, values in job.method_initials)
        fields.extend((f"'method_initials', struct({starts})", f"'error_mode', '{job.error_mode}'",
                       f"'error_tol', {job.error_tolerance if job.error_tolerance is not None else '[]'}",
                       f"'open_interval', {octave_vector(job.open_interval)}",
                       f"'domain_problem', {octave_string(job.domain_problem)}",
                       f"'domain_warning', {octave_string(job.domain_warning)}",
                       f"'excluded_points', {octave_vector(job.excluded_points)}"))
    elif kind == "system":
        residuals = "; ".join(job.expressions)
        fields.extend((f"'f', @(v) [{residuals}]", f"'variables', {{{octave_cell(job.variables)}}}"))
    elif kind == "linear":
        entries = "; ".join(octave_vector(row)[1:-1] for row in job.matrix)
        names = [f"x{i}" for i in range(1, len(job.rhs) + 1)]
        fields.extend((f"'A', [{entries}]", f"'b', {octave_vector(job.rhs)}", f"'variables', {{{octave_cell(names)}}}"))
    elif kind in {"integral", "ode"}:
        arguments = "x,y" if kind == "ode" else "x"
        expression = "[" + "; ".join(job.expressions) + "]" if kind == "ode" else job.expressions[0]
        fields.extend((f"'f', @({arguments}) {expression}", f"'interval', {octave_vector(job.nodes)}",
                       f"'segments', {job.segments}", f"'domain_problem', {octave_string(job.domain_problem)}",
                       f"'domain_warning', {octave_string(job.domain_warning)}",
                       f"'accuracy_tol', {job.accuracy_tolerance if job.accuracy_tolerance is not None else '[]'}",
                       f"'variables', {{{octave_cell(job.variables)}}}"))
    else:
        fields.extend((f"'nodes', {octave_vector(job.nodes)}", f"'samples', {octave_vector(job.values)}",
                       f"'query', {octave_vector(job.query)}", f"'degree', {job.degree}"))
    return "    struct(" + ", ...\n      ".join(fields) + ")"


def same_template(path, expected):
    if not path.is_file():
        return False
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return content == expected


def prepare_run(assignment, source, work, template_folder):
    work.mkdir()
    if template_folder and not template_folder.is_dir():
        raise LabError(f"方法模板目录不存在：{template_folder}")
    workspace = ASSETS.parents[1]
    compatible_folders = (source.parent, source.parent / "NA1_Octave", workspace / "NA1_Octave")
    templates = {}
    for name in dict.fromkeys(key for equation in assignment.equations for key in equation.methods):
        filename = METHODS[name][2]
        if filename is None:
            continue
        bundled = ASSETS / filename
        default = bundled.read_bytes().replace(b"\r\n", b"\n")
        supplied = template_folder / filename if template_folder else None
        if supplied and supplied.is_file():
            if not same_template(supplied, default):
                raise LabError(f"方法文件 {filename} 与随附版本不一致。程序只运行已核验版本，请在 Octave 中单独运行自编代码。")
            original = supplied
        else:
            original = next((folder / filename for folder in compatible_folders
                             if same_template(folder / filename, default)), bundled)
        (work / filename).write_bytes(default)
        templates[name] = (filename, original != bundled)
    shutil.copyfile(ASSETS / "lab_execute.m", work / "lab_execute.m")
    if any(isinstance(job, ExtraTask) for job in assignment.equations):
        shutil.copyfile(ASSETS / "lab_extra.m", work / "lab_extra.m")
    for filename in ("lab_root_checks.m", "lab_traced_solver.m", "lab_display.m"):
        shutil.copyfile(ASSETS / filename, work / filename)
    if any(set(job.methods) & {"jacobi","gauss_seidel"} for job in assignment.equations):
        shutil.copyfile(ASSETS / "lab_stationary.m", work / "lab_stationary.m")
    if any(job.plots for job in assignment.equations):
        shutil.copyfile(ASSETS / "lab_plot.m", work / "lab_plot.m")
    rows = [octave_job(job) for job in assignment.equations]
    help_items = ", ".join(map(octave_string, assignment.help_commands))
    program = ("function run_experiment(capture_images, resume)\n  if nargin == 0, capture_images = false; end\n"
               "  if nargin < 2, resume = false; end\n  jobs = { ...\n")
    program += ", ...\n".join(rows) + f" ...\n  }};\n  lab_execute(jobs, {{{help_items}}}, capture_images, resume);\nend\n"
    (work / "run_experiment.m").write_text(program, encoding="utf-8")
    return templates


def render_output(path, lines):
    candidates = ("consola.ttf", "DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc")
    font = None
    for name in candidates:
        try:
            font = ImageFont.truetype(name, 19)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default(size=18)
    line_width = max(font.getlength(line) for line in lines)
    image = Image.new("RGB", (max(750, int(line_width) + 44), 65 + 26 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 38), fill="#eff2f6")
    draw.text((20, 11), "GNU Octave  /  execution log", font=font, fill="#23364d")
    for index, line in enumerate(lines):
        draw.text((20, 49 + 26 * index), line, font=font, fill="#14253a")
    image.save(path)


def execute(executable, work, require_screenshot=False, *, screenshots=True, charts=False, task_count=None,
            progress=None, cancel=None, timeout=150, resume=False, checkpoint=None):
    command, env = octave_command(executable, graphics=screenshots or charts)
    env["OCTAVE_HISTFILE"] = str(work / ".octave-history")
    capture = ("true" if screenshots else "false") if isinstance(screenshots, bool) else octave_vector(screenshots)
    invocation = f"run_experiment({capture})"
    if resume:
        invocation = invocation[:-1] + ", true)"

    def notify(line):
        if checkpoint and line.startswith("LAB_DONE "):
            try:
                checkpoint(int(line.split()[1]))
            except OSError as exc:
                if progress:
                    progress(f"本题已算完，续跑记录写入失败。{exc}")
        if progress and line.startswith(("LAB_TASK ", "LAB_REUSED ")):
            tag, number = line.strip().split(maxsplit=1)
            progress(("Octave 正在处理任务 " if tag == "LAB_TASK" else "已复用上次结果，任务 ") + number)

    result = run_process([*command, "--eval", invocation], work, "GNU Octave", timeout=timeout, env=env,
                         on_line=notify, cancel=cancel)
    (work / "octave.log").write_text(result.stdout, encoding="utf-8")
    (work / ".octave-history").unlink(missing_ok=True)
    data_path = work / "results.json"
    if (result.returncode or not data_path.is_file()) and task_count is None:
        output = "\n".join(result.stdout.strip().splitlines()[-10:])
        if "Multiple candidate roots detected" in output or "Both endpoints are roots" in output:
            raise LabError("发现多个候选根。请为每个目标根缩小区间，或在题目中明确指定正根或负根。\n" + output)
        raise LabError(f"Octave 未完成实验。\n{output or 'Octave 没有返回结果，请检查安装。'}")
    if data_path.is_file():
        data = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        version = next((line.split()[-1] for line in result.stdout.splitlines() if line.startswith("GNU Octave ")), "未知")
        cases = []
        for index in range(1, task_count + 1):
            checkpoint = work / f"task-{index:02}.json"
            if checkpoint.is_file():
                cases.append(json.loads(checkpoint.read_text(encoding="utf-8")))
            else:
                cases.append({"error": "任务未执行完成。请查看 octave.log，修正后可续跑。", "results": [], "pages": []})
        data = {"version": version, "cases": cases}
        data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    figures = []
    for index, item in enumerate(data["cases"], 1):
        pictures = []
        files = item.get("plot_files") or [item.get("plot_file")]
        for plot_index, filename in enumerate(files):
            if not filename:
                continue
            expected = f"plot-{index:02}.png" if plot_index == 0 else f"plot-{index:02}-{plot_index+1:02}.png"
            if filename != expected:
                item["issue"] = "计算图形的文件名与当前任务不一致。"
                continue
            chart_path = work / expected
            try:
                with Image.open(chart_path) as image:
                    image.verify()
            except (OSError, SyntaxError) as exc:
                item["issue"] = f"第 {index} 题的计算图形无法读取。{exc}"
                continue
            pictures.append(Evidence(chart_path, False, chart=True, plot_index=plot_index))
        capture_task = screenshots if isinstance(screenshots, bool) else screenshots[index-1]
        for page, lines in enumerate(item.get("pages", []) if capture_task else (), 1):
            target = work / f"case-{index:02}-{page:02}.png"
            screenshot = target.is_file()
            if screenshot:
                try:
                    with Image.open(target) as image:
                        image.verify()
                except (OSError, SyntaxError):
                    target.unlink()
                    screenshot = False
            if not screenshot:
                if require_screenshot:
                    item["issue"] = item.get("issue", "") + " 未能生成请求的 Octave 截图。请检查 Qt 图形支持。"
                    continue
                target = work / f"case-{index:02}-{page:02}-log.png"
                render_output(target, lines)
            pictures.append(Evidence(target, screenshot))
        figures.append(pictures)
    if task_count is None and any(case.get("error") for case in data["cases"]):
        raise LabError("\n".join(case["error"] for case in data["cases"] if case.get("error")))
    return data, figures
