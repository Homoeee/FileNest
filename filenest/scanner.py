import os
from pathlib import Path

from .models import FileInfo, category
from .paths import absolute, inside, key, metadata, safe_path


def scan(roots, recursive=True, excluded=(), cancel=None, progress=None):
    files, errors, seen = [], [], set()
    excludes = [absolute(p) for p in excluded]

    def walk(directory):
        if cancel is not None and cancel.is_set():
            return
        if any(inside(directory, p) for p in excludes):
            return
        try:
            safe_path(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cancel is not None and cancel.is_set():
                        return
                    path = Path(entry.path)
                    if any(inside(path, p) for p in excludes):
                        continue
                    try:
                        safe_path(path)
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                walk(path)
                        elif entry.is_file(follow_symlinks=False) and key(path) not in seen:
                            seen.add(key(path))
                            if path.suffix.lower() in {".crdownload", ".part", ".tmp", ".download"}:
                                continue
                            # Windows 上 DirEntry.stat 可能不给出可用的 st_dev/st_ino，
                            # 与执行前 Path.stat 的结果不一致，因此统一从路径读取。
                            files.append(FileInfo(str(path), **metadata(path.stat()),
                                                  category=category(path)))
                            if progress and len(files) % 100 == 0:
                                progress(f"已扫描 {len(files)} 个文件")
                    except (OSError, ValueError) as exc:
                        errors.append(f"{path}：{exc}")
        except (OSError, ValueError) as exc:
            errors.append(f"{directory}：{exc}")

    for root in roots:
        walk(absolute(root))
    return sorted(files, key=lambda f: key(f.path)), errors
