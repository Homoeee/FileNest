"""本地界面在普通浏览器和独立桌面窗口之间切换。"""

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path


def find_edge():
    """使用系统已有的 Edge，不下载或安装浏览器。"""
    candidates = [shutil.which("msedge.exe")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        root = os.environ.get(variable)
        if root:
            candidates.append(str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)


def open_desktop(url, profile):
    edge = find_edge()
    if edge is None:
        raise FileNotFoundError("未找到 Edge，无法打开独立桌面窗口；可继续使用网页版")
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([
        edge, f"--app={url}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
    ])


def open_web(url):
    if not webbrowser.open(url, new=1):
        raise OSError(f"无法打开默认浏览器，请手动访问 {url}")
