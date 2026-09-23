"""有状态校验的目录复制与删除；不使用无边界的递归删除。"""
import os
import shutil
from pathlib import Path

from .paths import fingerprint, metadata, safe_path
from .safety import check_tree, snapshot, same_content


def remove_verified(path, expected, cancel, changed):
    p = safe_path(path)
    if snapshot(p, cancel) != expected:
        raise ValueError('删除前项目已变化，请重新预览')
    if cancel.is_set():
        raise InterruptedError('任务已取消')
    if expected.get('kind') != 'directory':
        p.unlink()
        changed()
        return
    # 只删除快照中记录的项目；新出现的文件会导致 rmdir 失败并保留。
    for entry in reversed(expected['entries']):
        if cancel.is_set():
            raise InterruptedError('已停止，目录可能已部分处理，请查看记录')
        child = safe_path(p / entry['path'])
        current = metadata(child.stat())
        before = entry['snapshot']
        if (current['device'], current['inode']) != (before['device'], before['inode']):
            raise ValueError('子项目被替换，停止删除')
        if entry['kind'] == 'directory':
            child.rmdir()
        else:
            if fingerprint(child, cancel) != before:
                raise ValueError('子文件已变化，停止删除')
            if cancel.is_set():
                raise InterruptedError('已停止剩余删除')
            child.unlink()
        changed()
    safe_path(p)
    current = metadata(p.stat())
    if (current['device'], current['inode']) != (expected['device'], expected['inode']):
        raise ValueError('目录被替换，停止删除')
    p.rmdir()
    changed()


def copy_tree_exclusive(source, destination, expected, cancel, copy_file, changed):
    destination.mkdir()  # 独占创建；不合并、不覆盖已有目录。
    changed()
    for entry in expected['entries']:
        if cancel.is_set():
            raise InterruptedError('复制已停止；已生成部分目标，请核对记录')
        src = safe_path(source / entry['path'])
        dst = safe_path(destination / entry['path'])
        if entry['kind'] == 'directory':
            dst.mkdir()
        else:
            copy_file(src, dst, entry['snapshot'], cancel)
    for entry in reversed(expected['entries']):
        if entry['kind'] == 'directory':
            shutil.copystat(safe_path(source / entry['path']), safe_path(destination / entry['path']), follow_symlinks=False)
    shutil.copystat(source, destination, follow_symlinks=False)
    if snapshot(source, cancel) != expected:
        raise ValueError('复制期间源目录发生变化，保留源目录和已生成目标')
    after = snapshot(destination, cancel)
    if not same_content(expected, after):
        raise ValueError('目录复制校验失败，保留源目录')
    return after


def transfer(source, destination, expected, cancel, copy_file, move_file, changed, moving=True):
    if moving:
        try:
            move_file(source, destination)
        except OSError as exc:
            if getattr(exc, 'winerror', None) != 17 and exc.errno != 18:
                raise
        else:
            changed()
            after = snapshot(destination)
            if not same_content(expected, after):
                raise ValueError('移动后内容已变化，请人工核对')
            return after
    if expected.get('kind') == 'directory':
        after = copy_tree_exclusive(source, destination, expected, cancel, copy_file, changed)
    else:
        after = copy_file(source, destination, expected, cancel)
        changed()
    if moving:
        # 目标校验成功后再删源；失败保留两侧，并记录为需核对，绝不伪装成功。
        if snapshot(destination, cancel) != after:
            raise ValueError('目标在源项目删除前发生变化，保留源项目')
        remove_verified(source, expected, cancel, changed)
    return after
