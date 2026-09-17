import hashlib
import os
import stat
from pathlib import Path


def absolute(path) -> Path:
    # 不调用 resolve：保留原始路径，便于识别并拒绝目录联接。
    return Path(os.path.abspath(path))


def key(path) -> str:
    return os.path.normcase(str(absolute(path)))


def inside(path, root) -> bool:
    try:
        return os.path.commonpath([key(path), key(root)]) == key(root)
    except ValueError:
        return False


def safe_path(path):
    p = absolute(path)
    for part in [p, *p.parents]:
        try:
            s = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"不处理符号链接或目录联接：{part}")
    return p


def metadata(s):
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns,
                device=s.st_dev, inode=s.st_ino)


def fingerprint(path, cancel=None):
    p = safe_path(path)
    before = p.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("不是普通文件")
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        if metadata(os.fstat(stream.fileno())) != metadata(before):
            raise ValueError("文件在读取前已变化")
        while chunk := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("任务已取消")
            digest.update(chunk)
        if metadata(os.fstat(stream.fileno())) != metadata(before):
            raise ValueError("文件正在写入，请稍后重试")
    safe_path(p)
    if metadata(p.stat()) != metadata(before):
        raise ValueError("文件在读取时发生变化")
    return {**metadata(before), "sha256": digest.hexdigest()}


def validate_name(name):
    if (not name or name in {".", ".."} or name[-1] in " ."
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
            or name.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError(f"无效的 Windows 文件或目录名：{name!r}")


def relative_folder(value):
    parts = value.replace("\\", "/").split("/") if value else []
    for part in parts:
        validate_name(part)
    return Path(*parts)


def available(path, reserved=()):
    p = Path(path)
    index = 0
    candidate = p
    while os.path.lexists(candidate) or key(candidate) in reserved:
        index += 1
        candidate = p.with_name(f"{p.stem} ({index}){p.suffix}")
    return candidate
