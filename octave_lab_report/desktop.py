"""Minimal Windows-friendly UI for selecting a report and generating it locally."""

from contextlib import contextmanager
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from .inputs import pdf_images, pdf_pages
from .ocr import MISSING_TEXT, editable_text, image_paths, question_lines, recognize
from .spec import Cancelled, LabError, format_plan


@contextmanager
def packaged_tk_libraries():
    base = Path(sys.base_prefix)
    if sys.platform != "win32" or "windowsapps" not in str(base).lower():
        yield
        return
    # Tcl's native loader may be unable to open scripts inside an MSIX package.
    libraries = {"TCL_LIBRARY": base / "tcl" / f"tcl{tk.TclVersion}",
                 "TK_LIBRARY": base / "tcl" / f"tk{tk.TkVersion}"}
    if not all(path.is_dir() for path in libraries.values()):
        yield
        return
    previous = {name: os.environ.get(name) for name in libraries}
    with tempfile.TemporaryDirectory(prefix="octave-lab-tk-") as scratch:
        try:
            for name, path in libraries.items():
                target = Path(scratch) / path.name
                shutil.copytree(path, target)
                os.environ[name] = target.as_posix()
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def launch(complete):
    try:
        with packaged_tk_libraries(), tempfile.TemporaryDirectory(prefix="octave-lab-preview-", ignore_cleanup_errors=True) as scratch:
            show_window(complete, Path(scratch))
    except tk.TclError as exc:
        raise LabError("无法打开桌面界面。请使用带 Tk 的 Python，或在命令行指定报告路径。") from exc


def review_images(root, pages, draft, callback):
    dialog = tk.Toplevel(root)
    dialog.title("核对图片题目")
    dialog.minsize(750, 450)
    dialog.transient(root)
    dialog.grab_set()
    layout = ttk.Frame(dialog, padding=14)
    layout.pack(fill="both", expand=True)
    layout.columnconfigure(1, weight=1)
    layout.rowconfigure(1, weight=1)
    ttk.Label(layout, text="对照原图修改右边的题目，尤其注意指数、正负号、括号和区间。幂可写成 x^2。", wraplength=700).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
    preview = ttk.Frame(layout, width=420, height=420)
    preview.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
    preview.pack_propagate(False)
    caption = ttk.Label(preview, wraplength=390)
    caption.pack(anchor="w")
    image_view = ttk.Label(preview, anchor="center")
    image_view.pack(fill="both", expand=True)
    navigation = ttk.Frame(preview)
    navigation.pack(fill="x")
    editor_frame = ttk.Frame(layout)
    editor_frame.grid(row=1, column=1, sticky="nsew")
    editor = tk.Text(editor_frame, wrap="word", width=40, height=18, undo=True)
    scroll = ttk.Scrollbar(editor_frame, orient="vertical", command=editor.yview)
    editor.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    editor.pack(side="left", fill="both", expand=True)
    editor.insert("1.0", draft if draft is not None else editable_text(pages))
    current = 0

    def page(offset):
        nonlocal current
        current += offset
        source = pages[current]
        label = f"{current + 1}/{len(pages)}  {source.path.name}"
        caption.configure(text=label + ("\n" + source.warning if source.warning else ""))
        try:
            with Image.open(source.path) as image:
                preview_image = ImageOps.exif_transpose(image)
                preview_image.thumbnail((410, 335), Image.Resampling.LANCZOS)
                image_view.image = ImageTk.PhotoImage(preview_image)
            image_view.configure(image=image_view.image, text="")
        except (OSError, ValueError, Image.DecompressionBombError):
            image_view.image = None
            image_view.configure(image="", text="无法预览该图片。")
        previous.configure(state="normal" if current else "disabled")
        following.configure(state="normal" if current + 1 < len(pages) else "disabled")

    previous = ttk.Button(navigation, text="上一张", command=lambda: page(-1))
    following = ttk.Button(navigation, text="下一张", command=lambda: page(1))
    previous.pack(side="left")
    following.pack(side="left", padx=6)
    if sys.platform == "win32":
        def original():
            try:
                os.startfile(pages[current].path)
            except OSError as exc:
                messagebox.showerror("无法打开图片", str(exc), parent=dialog)

        ttk.Button(navigation, text="查看原图", command=original).pack(side="left")

    buttons = ttk.Frame(layout)
    buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))

    def finish(confirm):
        text = editor.get("1.0", "end-1c").strip()
        if confirm and MISSING_TEXT in text:
            messagebox.showerror("还有图片待补录", "请替换“请补录”提示。若该图片没有题目信息，可删除对应提示。", parent=dialog)
            return
        if confirm and not any(line.strip() for line in question_lines(text)):
            messagebox.showerror("题目文字为空", "请对照图片填写方程和实验要求。", parent=dialog)
            return
        dialog.destroy()
        callback(confirm, text)

    ttk.Button(buttons, text="取消", command=lambda: finish(False)).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="核对运行清单", command=lambda: finish(True)).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    page(0)


def edit_input(root, text, apply, cancel=None):
    dialog = tk.Toplevel(root)
    dialog.title("编辑题目")
    dialog.transient(root)
    dialog.grab_set()
    element = tk.Text(dialog, width=76, height=19, wrap="word", undo=True)
    element.pack(fill="both", expand=True, padx=12, pady=12)
    element.insert("1.0",text)

    def save():
        contents = element.get("1.0","end-1c").strip()
        dialog.destroy()
        apply(contents)

    ttk.Button(dialog,text="应用并重新识别",command=save).pack(anchor="e",padx=12,pady=(0,12))
    def close():
        dialog.destroy()
        if cancel:
            cancel()
    dialog.protocol("WM_DELETE_WINDOW",close)


def choose_run(root, plan, original, callback):
    dialog = tk.Toplevel(root)
    dialog.title("核对运行清单")
    dialog.minsize(620, 390)
    dialog.transient(root)
    dialog.grab_set()
    layout = ttk.Frame(dialog, padding=16)
    layout.pack(fill="both", expand=True)
    ttk.Label(layout, text="逐题确认方程、方法和参数，再选择本次是否需要 Octave 运行截图。计算图形按题目要求生成。", wraplength=570).pack(anchor="w", pady=(0, 8))
    body = ttk.Frame(layout)
    body.pack(fill="both", expand=True)
    editor = tk.Text(body, width=66, height=16, wrap="word")
    scroll = ttk.Scrollbar(body, orient="vertical", command=editor.yview)
    editor.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    editor.pack(side="left", fill="both", expand=True)
    editor.insert("1.0", plan)
    editor.configure(state="disabled")

    def finish(choice):
        dialog.destroy()
        callback(choice)

    buttons = ttk.Frame(layout)
    buttons.pack(anchor="e", pady=(12, 0))
    modes = {"按题目": "auto", "结果图窗": "result", "命令窗口": "command", "编辑器": "editor",
             "命令窗口和编辑器":"command_editor"}
    mode = tk.StringVar(value="按题目")
    ttk.Combobox(buttons,textvariable=mode,values=list(modes),state="readonly",width=17).pack(side="left")

    def edit():
        dialog.grab_release()
        edit_input(dialog,original,lambda text: finish({"text":text}) if text else finish(None),dialog.grab_set)

    ttk.Button(buttons,text="修改题目",command=edit).pack(side="left",padx=(8,0))
    for label, choice in (("返回", None), ("不截图并运行", False), ("截图并运行", True)):
        ttk.Button(buttons, text=label, command=lambda value=choice: finish((value,modes[mode.get()]) if value is not None else None)).pack(side="left", padx=(8, 0))
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))


def show_window(complete, scratch):
    root = tk.Tk()
    root.title("Octave 实验报告助手")
    root.minsize(730, 400)
    body = ttk.Frame(root, padding=22)
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    progress = tk.StringVar(value="请选择题目来源。Word 报告模板可按要求指定。")
    values = {}
    events = queue.Queue()
    destination = None
    images = ()
    pages = None
    draft = None
    review_key = None
    cancellation = None
    manual = ""

    def field(row, name, label, picker):
        value = tk.StringVar()
        values[name] = value
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        ttk.Entry(body, textvariable=value).grid(row=row, column=1, sticky="ew", pady=5)

        def choose():
            if selected := picker():
                value.set(selected)

        ttk.Button(body, text="浏览", command=choose).grid(row=row, column=2, padx=(8, 0))

    field(0, "source", "题目文档（选填）", lambda: filedialog.askopenfilename(
        title="选择题目文档", filetypes=[("题目文档", "*.docx *.doc *.txt *.md *.pdf *.xlsx *.xls *.csv *.tsv"), ("所有文件", "*")]))
    field(1, "report_template", "Word 报告模板（选填）", lambda: filedialog.askopenfilename(
        parent=root, title="选择 Word 报告模板", filetypes=[("Word 模板", "*.docx *.dotx *.doc")]))
    field(2, "octave", "Octave 程序（选填）", lambda: filedialog.askopenfilename(
        title="选择 Octave 程序", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*")]))
    field(3, "template_dir", "Octave 方法模板目录（选填）", lambda: filedialog.askdirectory(title="选择已有方法模板目录"))
    field(4, "output", "输出目录（选填）", lambda: filedialog.askdirectory(title="选择输出目录"))
    field(5, "resume", "上次输出目录（续跑）", lambda: filedialog.askdirectory(title="选择上次的报告输出目录"))
    selected_images = tk.StringVar(value="未选择")
    ttk.Label(body, text="题目图片（可多选）").grid(row=6, column=0, sticky="w", pady=5)
    ttk.Label(body, textvariable=selected_images, wraplength=360).grid(row=6, column=1, sticky="w", pady=5)
    image_buttons = ttk.Frame(body)
    image_buttons.grid(row=6, column=2, sticky="e")

    def update_images(paths):
        nonlocal images, pages, draft
        images = tuple(paths)
        pages, draft = None, None
        selected_images.set(f"已选 {len(images)} 张。" + "、".join(path.name for path in images[:3]) if images else "未选择")

    def select_images(select):
        if select:
            picked = filedialog.askopenfilenames(parent=root, title="选择题目图片", filetypes=[("题目图片", "*.png *.jpg *.jpeg *.bmp *.webp")])
            if not picked:
                return
            try:
                paths = image_paths(picked)
            except LabError as exc:
                messagebox.showerror("无法选取图片", str(exc), parent=root)
                return
            update_images(sorted(paths, key=lambda path: tuple(int(v) if v.isdigit() else v.casefold() for v in re.split(r"(\d+)", path.name))))
        else:
            update_images(())

    def reorder():
        if len(images) < 2:
            return
        dialog = tk.Toplevel(root)
        dialog.title("调整图片顺序")
        dialog.transient(root)
        dialog.grab_set()
        layout = ttk.Frame(dialog, padding=12)
        layout.pack(fill="both", expand=True)
        order = list(images)
        listing = tk.Listbox(layout, width=60, exportselection=False)
        listing.pack(fill="both", expand=True)
        for path in order:
            listing.insert("end", path.name)
        listing.selection_set(0)

        def move(offset):
            indexes = listing.curselection()
            if not indexes or not 0 <= (target := indexes[0] + offset) < len(order):
                return
            index = indexes[0]
            order[index], order[target] = order[target], order[index]
            listing.delete(0, "end")
            for path in order:
                listing.insert("end", path.name)
            listing.selection_set(target)
            listing.see(target)

        def apply():
            if tuple(order) != images:
                update_images(order)
            dialog.destroy()

        controls = ttk.Frame(layout)
        controls.pack(anchor="e", pady=(8, 0))
        for name, command in (("上移", lambda: move(-1)), ("下移", lambda: move(1)), ("应用", apply)):
            ttk.Button(controls, text=name, command=command).pack(side="left", padx=4)

    pick_images = ttk.Button(image_buttons, text="选图片", command=lambda: select_images(True))
    clear_images = ttk.Button(image_buttons, text="清除", command=lambda: select_images(False))
    order_images = ttk.Button(image_buttons, text="排序", command=reorder)
    pick_images.pack(side="left", padx=(0, 4))
    order_images.pack(side="left", padx=(0, 4))
    clear_images.pack(side="left")
    options = ttk.Frame(body)
    options.grid(row=7,column=0,columnspan=3,sticky="ew",pady=5)
    for title,key,size in (("仅做第几题","exercise",5),("Excel 工作表","sheet",13),("区域","data_range",12),
                           ("OCR 语言","language",8)):
        ttk.Label(options,text=title).pack(side="left",padx=(8,3))
        value=tk.StringVar()
        values[key]=value
        ttk.Entry(options,textvariable=value,width=size).pack(side="left")
    document_options = ttk.Frame(body)
    document_options.grid(row=8,column=0,columnspan=3,sticky="w",pady=5)
    clear_layout, trust_cache, export_pdf, export_xlsx = (tk.BooleanVar() for _ in range(4))
    for label,flag in (("清理旧结果",clear_layout),("已重算 Excel 公式",trust_cache),("导出 PDF",export_pdf),("导出 Excel",export_xlsx)):
        ttk.Checkbutton(document_options,text=label,variable=flag).pack(side="left",padx=(0,8))
    ttk.Label(body, textvariable=progress, wraplength=580, justify="left").grid(
        row=10, column=0, columnspan=3, sticky="ew", pady=(12, 0))

    def open_folder():
        if sys.platform == "win32" and destination:
            try:
                os.startfile(destination)
            except OSError as exc:
                messagebox.showerror("无法打开目录", str(exc), parent=root)

    def busy(active):
        state = "disabled" if active else "normal"
        for button in (run, batch, manual_button, pick_images, order_images, clear_images):
            button.configure(state=state)
        stop.configure(state="normal" if active else "disabled")
        folder.configure(state="normal" if destination and not active else "disabled")

    def background(action, work):
        def run_job():
            try:
                events.put((action, work()))
            except Cancelled:
                events.put(("cancelled", None))
            except Exception as exc:
                events.put(("error", str(exc)))

        threading.Thread(target=run_job, daemon=True).start()

    def confirm(assignment):
        response = queue.Queue(maxsize=1)
        events.put(("confirm", (format_plan(assignment), assignment.original_requirements, response)))
        return response.get()

    def generate(settings):
        progress.set("正在读取实验要求…")
        background("done", lambda: complete(**settings, confirm=confirm, render=True))

    def review(settings):
        def closed(confirm, text):
            nonlocal draft
            draft = text
            if confirm:
                settings["problem_text"] = text
                generate(settings)
            else:
                busy(False)
                progress.set("已保留本次编辑，可重新核对。")

        progress.set("请对照原图核对题目。")
        review_images(root, pages, draft, closed)

    settings = {}

    def collect_settings():
        picked = {key: value.get().strip() or None for key,value in values.items() if key!="language"}
        if picked["exercise"] and not re.fullmatch(r"[1-9]\d*",picked["exercise"]):
            raise LabError("仅做第几题请填写正整数。")
        picked.update(clear_layout=clear_layout.get(),trust_formula_cache=trust_cache.get(),
                      exports=tuple(name for name,flag in (("pdf",export_pdf),("xlsx",export_xlsx)) if flag.get()),
                      progress=lambda text: events.put(("progress",text)))
        return picked

    def save_manual(text):
        nonlocal manual
        manual=text
        progress.set("已录入手工题目。" if text else "已清除手工题目。")

    def start():
        nonlocal settings, review_key, pages, draft, cancellation
        try:
            settings=collect_settings()
        except LabError as exc:
            messagebox.showerror("核对设置",str(exc),parent=root)
            return
        if not settings["source"] and not settings["report_template"] and not images and not manual:
            messagebox.showerror("缺少输入", "请选择题目文档、题目图片，或包含题目的 Word 报告模板。", parent=root)
            return
        settings["images"] = images
        cancellation = threading.Event()
        settings["cancel"] = cancellation
        busy(True)
        source = Path(settings["source"]) if settings["source"] else None
        pdf = source if source and source.suffix.lower() == ".pdf" else None
        try:
            key = (tuple((path,path.stat().st_mtime_ns,path.stat().st_size) for path in images), pdf,
                   pdf.stat().st_mtime_ns if pdf and pdf.is_file() else None,values["language"].get().strip())
        except OSError as exc:
            busy(False)
            messagebox.showerror("题目图片无法读取",str(exc),parent=root)
            return
        if key != review_key:
            pages, draft = None, None
            review_key = key
        if manual:
            settings.update(problem_text=manual, replace_questions=True, exercise=None)
            generate(settings)
        elif not images and not pdf:
            generate(settings)
        elif pages is not None:
            review(settings)
        else:
            progress.set("正在本机读取题目和识别图片…")

            def read_images():
                paths = images
                if pdf:
                    missing = [i for i,page in enumerate(pdf_pages(pdf,cancellation),1) if not page]
                    if missing:
                        folder = Path(tempfile.mkdtemp(dir=scratch))
                        paths = (*pdf_images(pdf, folder, missing, cancellation), *images)
                return recognize(paths,cancellation,key[3]) if paths else ()

            background("review", read_images)

    def start_batch():
        nonlocal cancellation
        files = filedialog.askopenfilenames(parent=root,title="选择批量题目文件")
        if not files:
            return
        try:
            shared=collect_settings()
        except LabError as exc:
            messagebox.showerror("核对设置",str(exc),parent=root)
            return
        cancellation=threading.Event()
        shared.update(resume=None,cancel=cancellation,render=True)
        busy(True)

        def process_batch():
            result=[]
            for filename in files:
                if cancellation.is_set():
                    break
                name=Path(filename).name
                try:
                    output=complete(filename,**shared,confirm=confirm)
                    result.append((name,output,""))
                except (LabError, OSError, ValueError, Cancelled) as exc:
                    result.append((name,None,str(exc) or "已跳过"))
            return result

        shared.pop("source",None)
        background("batch",process_batch)

    actions=ttk.Frame(body)
    actions.grid(row=9,column=0,columnspan=2,sticky="w",pady=(8,0))
    run = ttk.Button(actions, text="生成报告", command=start)
    run.pack(side="left",padx=(0,5))
    manual_button=ttk.Button(actions,text="手工题目",command=lambda: edit_input(root,manual,save_manual))
    manual_button.pack(side="left",padx=(0,5))
    batch=ttk.Button(actions,text="批量运行",command=start_batch)
    batch.pack(side="left",padx=(0,5))
    stop = ttk.Button(body, text="停止运行", command=lambda: cancellation.set(), state="disabled")
    stop.grid(row=9, column=1, sticky="e", pady=(8, 0))
    folder = ttk.Button(body, text="打开输出目录", command=open_folder, state="disabled")
    if sys.platform == "win32":
        folder.grid(row=9, column=2, sticky="e", padx=(8, 0), pady=(8, 0))

    def update():
        nonlocal destination, pages
        while not events.empty():
            action, value = events.get_nowait()
            if action == "progress":
                progress.set(value)
            elif action == "review":
                pages = value
                if pages:
                    review(settings)
                else:
                    generate(settings)
            elif action == "confirm":
                plan, original, response = value
                progress.set("请核对清单并选择本次是否截图。")
                def confirmed(choice):
                    if choice is not None:
                        stop.configure(state="normal")
                    response.put(choice)
                choose_run(root, plan, original, confirmed)
            elif action == "cancelled":
                busy(False)
                progress.set("已取消运行。可修改题目后重新确认。")
            elif action == "done":
                destination = value.document.parent
                status = f"已保存部分报告，{value.incomplete} 个任务有待处理项。" if value.incomplete else f"已完成 {value.equations} 个实验任务。"
                progress.set(f"{status} 截图 {value.screenshots} 张，计算图 {value.charts} 张。\n{value.document}")
                busy(False)
            elif action == "batch":
                for _,output,_ in value:
                    if output:
                        destination=output.document.parent
                busy(False)
                progress.set("批量运行已结束。")
                summary="\n".join(f"{name}  {('待复核 ' if output.incomplete else '已完成 ') + str(output.document) if output else error}" for name,output,error in value)
                messagebox.showinfo("批量处理结果",summary or "没有已处理的题目。",parent=root)
            else:
                busy(False)
                progress.set("生成失败。请按错误信息调整输入后重试。")
                messagebox.showerror("生成失败", value, parent=root)
        root.after(100, update)

    def close():
        if str(run.cget("state")) == "disabled":
            if str(stop.cget("state")) == "normal":
                cancellation.set()
                progress.set("正在停止 Octave 并保存已完成结果…")
            else:
                messagebox.showinfo("任务进行中", "请结束当前识别或题目核对后再关闭。", parent=root)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, update)
    root.mainloop()
