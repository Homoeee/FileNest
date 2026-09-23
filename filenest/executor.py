import json
import os
import shutil
import threading
from pathlib import Path

from .paths import fingerprint, safe_path
from .safety import check_operable, check_tree, snapshot
from .file_operations import remove_verified, transfer
from .recycle import recycle


def move_no_replace(source, destination):
    """Windows rename 原子拒绝覆盖；跨盘由经过校验的 transfer 处理。"""
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

    def execute(self, items, cancel=None, progress=None, confirm_destructive=False):
        if any(p.status == "ready" and p.action in {"delete", "recycle"} for p in items) and not confirm_destructive:
            raise ValueError("删除或回收站操作需要明确确认")
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
            def mark_changed():
                nonlocal changed
                changed = True
            try:
                source = check_operable(item.source, (self.store.directory,))
                before = snapshot(source, cancel)
                if before != item.snapshot:
                    raise ValueError('项目自预览后发生变化，请重新扫描并预览')
                check_tree(source, before, (self.store.directory,))
                if cancel.is_set():
                    raise InterruptedError('任务已取消')
                if item.action == 'delete':
                    remove_verified(source, before, cancel, mark_changed)
                    after = {}
                elif item.action == 'recycle':
                    # Shell 失败可能已部分处理目录；保守记录为需核对。
                    mark_changed()
                    recycle(source)
                    after = {}
                else:
                    destination = check_operable(item.destination, (self.store.directory,))
                    if os.path.lexists(destination):
                        self.store.update(row_id, status='skipped', reason='预览后目标出现同名项目，请刷新预览')
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    safe_path(destination)
                    safe_path(source)
                    if item.action not in {'copy', 'move', 'rename'}:
                        raise ValueError('无效操作')
                    after = transfer(source, destination, before, cancel, copy_exclusive,
                                     move_no_replace, mark_changed, moving=item.action != 'copy')
            except InterruptedError as exc:
                self.store.update(row_id, status='uncertain' if changed else 'cancelled', reason=str(exc))
            except (OSError, ValueError) as exc:
                self.store.update(row_id, status='uncertain' if changed else 'failed', reason=str(exc))
            else:
                reason = {'delete': '已永久删除，无法撤销', 'recycle': '已移入 Windows 回收站，可从回收站还原'}.get(item.action, '处理完成')
                self.store.update(row_id, status='success', after_snapshot=json.dumps(after), reason=reason)
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
            if (row["status"] != "success" or row["action"] not in {"move", "rename"}
                    or row["undo_status"] in {"success", "running", "uncertain"}):
                continue
            self.store.update(row["id"], undo_status="running", undo_reason="")
            changed = False
            def mark_changed():
                nonlocal changed
                changed = True
            try:
                source = check_operable(row['destination'], (self.store.directory,))
                destination = check_operable(row['source'], (self.store.directory,))
                expected = json.loads(row['after_snapshot'])
                if snapshot(source, cancel) != expected:
                    raise ValueError('整理后的项目已被修改或替换，不执行撤销')
                check_tree(source, expected, (self.store.directory,))
                if os.path.lexists(destination):
                    raise FileExistsError('原位置已有项目，不覆盖；请解决冲突后重试')
                destination.parent.mkdir(parents=True, exist_ok=True)
                safe_path(source)
                safe_path(destination)
                if cancel.is_set():
                    raise InterruptedError('撤销已取消')
                transfer(source, destination, expected, cancel, copy_exclusive, move_no_replace, mark_changed)
            except InterruptedError as exc:
                self.store.update(row['id'], undo_status='uncertain' if changed else 'cancelled', undo_reason=str(exc))
            except (OSError, ValueError) as exc:
                self.store.update(row['id'], undo_status='uncertain' if changed else 'failed', undo_reason=str(exc))
            else:
                self.store.update(row['id'], undo_status='success', undo_reason='已恢复原位置')
            if progress:
                progress(f"撤销 {index} / {len(rows)}")
