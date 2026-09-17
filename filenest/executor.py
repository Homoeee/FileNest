import json
import os
import shutil
import threading
from pathlib import Path

from .paths import fingerprint, safe_path


def move_no_replace(source, destination):
    """Windows rename 原子拒绝覆盖；不隐式降级为跨盘复制再删除。"""
    if os.name == "nt":
        os.rename(source, destination)
    else:
        # 其他平台 rename 会覆盖目标；使用独占硬链接保护已有文件。
        os.link(source, destination)
        os.unlink(source)


def copy_exclusive(source, destination, snapshot, cancel):
    created = None
    try:
        with open(source, "rb") as src, open(destination, "xb") as dst:
            created = os.fstat(dst.fileno())
            while chunk := src.read(1024 * 1024):
                if cancel.is_set():
                    raise InterruptedError("复制已取消")
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if fingerprint(source, cancel) != snapshot:
            raise ValueError("复制期间源文件发生变化")
        after = fingerprint(destination, cancel)
        if after["sha256"] != snapshot["sha256"]:
            raise ValueError("复制内容校验失败")
        shutil.copystat(source, destination, follow_symlinks=False)
        return fingerprint(destination, cancel)
    except BaseException:
        # 只清理本次独占创建且身份未变的副本，绝不删除已有目标。
        if created is not None:
            try:
                current = safe_path(destination).stat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    Path(destination).unlink()
            except FileNotFoundError:
                pass
        raise


class Executor:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()

    def execute(self, items, cancel=None, progress=None):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在执行")
        try:
            return self._execute(items, cancel or threading.Event(), progress)
        finally:
            self._lock.release()

    def _execute(self, items, cancel, progress):
        batch, ids = self.store.begin(items)
        for index, (item, row_id) in enumerate(zip(items, ids), 1):
            if item.status != "ready":
                continue
            if cancel.is_set():
                self.store.update(row_id, status="cancelled", reason="取消后未执行")
                continue
            self.store.update(row_id, status="running")
            changed = False
            try:
                source, destination = safe_path(item.source), safe_path(item.destination)
                if fingerprint(source, cancel) != item.snapshot:
                    raise ValueError("文件自预览后发生变化，请重新扫描并预览")
                if destination.exists():
                    # 不能静默采用与用户预览不同的路径。
                    self.store.update(row_id, status="skipped", reason="预览后目标出现同名文件，请刷新预览")
                    continue
                safe_path(destination.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                safe_path(destination)
                safe_path(source)
                if cancel.is_set():
                    raise InterruptedError("任务已取消")
                if item.action == "copy":
                    after = copy_exclusive(source, destination, item.snapshot, cancel)
                    changed = True
                elif item.action in {"move", "rename"}:
                    move_no_replace(source, destination)
                    changed = True
                    # 不能在移动完成后因用户取消而把已经完成的文件记为取消。
                    after = fingerprint(destination)
                    if after != item.snapshot:
                        raise ValueError("移动后文件状态变化，请人工核对")
                else:
                    raise ValueError("无效操作")
            except InterruptedError as exc:
                self.store.update(row_id, status="cancelled", reason=str(exc))
            except (OSError, ValueError) as exc:
                self.store.update(row_id, status="uncertain" if changed else "failed",
                                  reason=f"{exc}。移动仅支持同一磁盘；跨盘请使用复制。" if
                                  isinstance(exc, OSError) and getattr(exc, "winerror", None) == 17 else str(exc))
            else:
                # 记录失败必须上抛并停止，不能继续产生未记录的文件操作。
                self.store.update(row_id, status="success", after_snapshot=json.dumps(after), reason="处理完成")
            if progress:
                progress(f"执行 {index} / {len(items)}")
        return batch

    def undo(self, batch, cancel=None, progress=None):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在执行")
        try:
            self._undo(batch, cancel or threading.Event(), progress)
        finally:
            self._lock.release()

    def _undo(self, batch, cancel, progress):
        rows = list(reversed(self.store.operations(batch)))
        for index, row in enumerate(rows, 1):
            if cancel.is_set():
                break
            if (row["status"] != "success" or row["action"] == "copy"
                    or row["undo_status"] in {"success", "running", "uncertain"}):
                continue
            self.store.update(row["id"], undo_status="running", undo_reason="")
            changed = False
            try:
                source = safe_path(row["destination"])
                destination = safe_path(row["source"])
                if fingerprint(source, cancel) != json.loads(row["after_snapshot"]):
                    raise ValueError("整理后的文件已被修改或替换，不执行撤销")
                if os.path.lexists(destination):
                    raise FileExistsError("原位置已有文件，不覆盖；请解决冲突后重试")
                destination.parent.mkdir(parents=True, exist_ok=True)
                safe_path(source)
                safe_path(destination)
                if cancel.is_set():
                    raise InterruptedError("撤销已取消")
                move_no_replace(source, destination)
                changed = True
                if fingerprint(destination) != json.loads(row["after_snapshot"]):
                    raise ValueError("恢复后文件状态变化，请人工核对")
            except InterruptedError as exc:
                self.store.update(row["id"], undo_status="cancelled", undo_reason=str(exc))
            except (OSError, ValueError) as exc:
                self.store.update(row["id"], undo_status="uncertain" if changed else "failed", undo_reason=str(exc))
            else:
                self.store.update(row["id"], undo_status="success", undo_reason="已恢复原位置")
            if progress:
                progress(f"撤销 {index} / {len(rows)}")
