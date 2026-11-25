"""Extract question text from local images using Windows OCR."""

from dataclasses import dataclass
from base64 import b64encode
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from PIL import Image, ImageOps

from .engine import run_process
from .spec import Cancelled, LabError, NUMBER, PAGE_MARKER, normalize


IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MISSING_TEXT = "【请补录这张图片的题目文字】"


@dataclass(frozen=True)
class ImageText:
    path: Path
    text: str = ""
    warning: str = ""


def image_paths(paths):
    result = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
    for path in result:
        if path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
            raise LabError(f"请选择已有的 PNG、JPG、BMP 或 WebP 图片。路径：{path}")
    return result


def question_lines(text):
    return [line for line in text.splitlines() if not PAGE_MARKER.fullmatch(line.strip())]


def editable_text(pages):
    return "\n\n".join(f"【图片 {index}】 {page.path.name}\n{page.text or MISSING_TEXT}" for index, page in enumerate(pages, 1))


def clean_spacing(text):
    text = re.sub(r"(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])[ \t]+(?=[:：])", "", text)
    return re.sub(r"(?m)^((?:初值|区间|容差|方程)\s*[:：])[ \t]*\n(?=[ \t]*[\[\d.xf(+\-])", r"\1 ", text).strip()


def recognize(paths, cancel=None, language=None):
    paths = image_paths(paths)
    powershell = shutil.which("powershell.exe") if sys.platform == "win32" else None
    if powershell is None:
        return tuple(ImageText(path, warning="此电脑没有可用的 Windows OCR。请对照图片输入题目文字。") for path in paths)
    script = Path(__file__).with_name("windows_ocr.ps1")
    pages = {}
    variants = {path: {} for path in paths}
    with tempfile.TemporaryDirectory(prefix="octave-lab-ocr-", ignore_cleanup_errors=True) as temp:
        working = Path(temp)
        pending = {}
        for i, path in enumerate(paths, 1):
            if cancel and cancel.is_set():
                raise Cancelled()
            try:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((4000, 4000), Image.Resampling.LANCZOS)
                    if "A" in image.getbands() or "transparency" in image.info:
                        layer = image.convert("RGBA")
                        image = Image.new("RGB", layer.size, "white")
                        image.paste(layer, mask=layer.getchannel("A"))
                    for variant, candidate in enumerate((image.convert("RGB"), ImageOps.autocontrast(image.convert("L")).convert("RGB"))):
                        filename = f"{i:04}-{variant}.png"
                        candidate.save(working / filename)
                        pending[filename] = (path, variant)
            except (OSError, ValueError, Image.DecompressionBombError):
                pages[path] = ImageText(path, warning="图片无法读取。请重新保存图片，或手动输入题目。")
        if pending:
            try:
                command = b64encode(script.read_text(encoding="utf-8").encode("utf-16-le")).decode("ascii")
                env = dict(os.environ, OCTAVE_LAB_IMAGE_DIRECTORY=str(working), OCTAVE_LAB_OCR_LANGUAGE=language or "")
                result = run_process([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                                     working, "Windows OCR", timeout=25 + 10 * len(pending), env=env, cancel=cancel)
                if cancel and cancel.is_set():
                    raise Cancelled()
                if result.returncode:
                    raise LabError("Windows OCR 无法启动或没有可用的识别语言。请检查系统 OCR 语言，或对照图片输入题目。")
                data = json.loads(result.stdout.lstrip("\ufeff"))
                for record in data["pages"]:
                    path, variant = pending.pop(record["name"])
                    variants[path][variant] = clean_spacing(record["text"])
                    if record.get("error"):
                        pages[path] = ImageText(path, warning=f"OCR 遇到错误，请核对或补录。{record['error']}")
            except (LabError, ValueError, KeyError) as exc:
                for path, _ in pending.values():
                    warning = str(exc) if isinstance(exc, LabError) else "未能读取 OCR 结果。请对照原图输入题目。"
                    pages[path] = ImageText(path, warning=warning)
        for path, candidates in variants.items():
            if path in pages and not any(candidates.values()):
                continue
            original, contrasted = candidates.get(0, ""), candidates.get(1, "")
            text = original or contrasted
            warning = ""
            if not text:
                warning = "没有识别出文字。请查看原图并补录题目。"
            else:
                math = rf"{NUMBER}|[+\-*/^=()[\]<>≥≤]"
                if original and contrasted and re.findall(math, normalize(original)) != re.findall(math, normalize(contrasted)):
                    warning = "两次识别的数字或运算符不同。请逐项核对原图。"
                elif re.search(r"=|方程|区间|积分|迭代",text):
                    warning = "请对照原图逐个确认公式、运算符和数字。OCR 无法保证数学结构正确。"
            pages[path] = ImageText(path, text, warning)
    return tuple(pages[path] for path in paths)
