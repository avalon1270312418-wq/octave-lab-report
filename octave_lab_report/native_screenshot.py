"""Capture a window belonging to an Octave instance started for this report."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from PIL import ImageGrab, ImageStat

from .engine import WINDOWS, Evidence, octave_command, octave_string
from .spec import LabError, METHODS


def windows_for(pid):
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def inspect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            position = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(position)):
                area = (position.right - position.left) * (position.bottom - position.top)
                if area > 120000:
                    found.append((area, hwnd))
        return True

    user32.EnumWindows(inspect, 0)
    return sorted(found, reverse=True)


def capture_window(executable, work, kind, *, index, task, entry, cancel=None):
    if not WINDOWS:
        raise LabError("原生 Octave 窗口截图需要 Windows 桌面会话。")
    command, env = octave_command(executable, graphics=True)
    if "octave-cli" in command[0].lower():
        raise LabError("截图需要同一套 Octave 安装中的图形界面程序。")
    command = [part for part in command if part != "--no-gui"]
    with tempfile.TemporaryDirectory(prefix="octave-lab-gui-", ignore_cleanup_errors=True) as folder:
        profile = Path(folder)
        appdata = Path(os.environ.get("APPDATA", ""))
        # QSettings resolves the Windows roaming directory independently of APPDATA.
        try:
            nickname = os.path.relpath(profile / "settings", appdata / "octave").replace("\\", "/")
        except ValueError:
            nickname = "octave-lab-settings"
        local = profile / "octave"
        local.mkdir()
        settings = ("[General]\nlanguage=en_US\nglobal_skip_welcome_wizard=true\n"
                    "\n[editor]\nrestore_session=false\n\n[news]\nallow_web_connection=false\n")
        (profile / "settings.ini").write_text(settings, encoding="ascii")
        (local / "octave-lab-settings.ini").write_text(settings, encoding="ascii")
        env.update(APPDATA=str(profile), XDG_CONFIG_HOME=str(profile), OCTAVE_GUI_SETTINGS=nickname,
                   OCTAVE_HISTFILE=str(profile / "history"), QT_QPA_PLATFORM="windows:darkmode=0")
        destinations = []
        if kind == "command":
            log = (work / "octave.log").read_text(encoding="utf-8")
            sections = re.split(r"(?m)^LAB_TASK (\d+)/\d+\s*\n", log)
            part = dict(zip(sections[1::2],sections[2::2])).get(str(index),"")
            lines = [line for line in part.splitlines() if not line.startswith("LAB_")]
            if not any(line.strip() for line in lines):
                raise LabError("本题没有可显示的 Octave 运行日志。")
            for number, start in enumerate(range(0,len(lines),18),1):
                filename = f"octave-task-{index:02}-{number:02}.txt"
                (work / filename).write_text("\n".join(lines[start:start+18])+"\n",encoding="utf-8")
                destinations.append((f"fprintf('%s', fileread({octave_string(filename)}))", f"octave-command-{index:02}-{number:02}.png", filename))
        else:
            methods = [METHODS[key][2] for key in task.methods if METHODS[key][2]]
            for number, filename in enumerate(["run_experiment.m", *methods],1):
                destinations.append((f"edit({octave_string(filename)})", f"octave-editor-{index:02}-{number:02}.png", filename))
        captures = []
        for action, filename, source in destinations:
            captures.append(run_capture(command, env, work, kind, action, filename, source, cancel))
    return captures


def run_capture(command, env, work, kind, action, output, source, cancel):
    marker = work / ".native-ready"
    command = [*command, "--gui", "--persist", "--eval", action + "; fid = fopen('.native-ready', 'w'); fclose(fid); clear fid;"]
    out = work / output
    with out.with_suffix(".log").open("wb") as log:
        try:
            process = subprocess.Popen(command, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise LabError(f"无法启动 Octave 图形界面。{exc}") from exc
        try:
            deadline = time.monotonic() + 22
            while time.monotonic() < deadline and process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise LabError("已取消窗口截图。")
                if marker.is_file():
                    time.sleep(.4)
                    for _, hwnd in windows_for(process.pid):
                        try:
                            image = ImageGrab.grab(window=hwnd)
                            if not any(ch > 3 for ch in ImageStat.Stat(image.convert("RGB")).stddev):
                                continue
                            image.save(out)
                            return Evidence(out, True, kind=kind, source=source)
                        except (TypeError, OSError, ValueError):
                            continue
                time.sleep(.35)
            raise LabError(f"未取得 Octave {('命令窗口' if kind == 'command' else '编辑器')}截图。请查看 GUI 日志并确认桌面未锁定。")
        finally:
            marker.unlink(missing_ok=True)
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
