"""文件与目录共有的安全边界及内容快照。"""
import hashlib
import json
import os
from pathlib import Path

from .paths import fingerprint, inside, metadata, safe_path

TEMPORARY = {'.crdownload', '.part', '.tmp', '.download'}


def protected_reason(path):
    p = safe_path(path)
    if p.parent == p:
        return '磁盘根目录不能作为处理项目'
    roots = [os.environ.get(k) for k in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData')]
    if os.name == 'nt':
        roots.extend(str(Path(p.anchor) / n) for n in ('Windows', 'Program Files', 'Program Files (x86)',
                                                       'ProgramData', '$Recycle.Bin', 'System Volume Information',
                                                       'Recovery', 'Boot', 'EFI'))
    if any(inside(p, root) or inside(root, p) for root in roots if root):
        return '系统或程序目录受保护，不允许批量修改、删除或移动'
    try:
        if getattr(p.stat(), 'st_file_attributes', 0) & 0x4:
            return '带有系统属性的项目受保护'
    except FileNotFoundError:
        pass
    return ''


def check_operable(path, internal=()):
    p = safe_path(path)
    reason = protected_reason(p)
    if reason:
        raise ValueError(reason)
    if any(inside(p, root) or inside(root, p) for root in internal):
        raise ValueError('项目包含内部数据或排除目录，不能整体处理')
    if p.is_file() and p.suffix.lower() in TEMPORARY:
        raise ValueError('临时或下载中文件不能整理')
    return p


def snapshot(path, cancel=None):
    p = safe_path(path)
    if not p.is_dir():
        return fingerprint(p, cancel)
    before = metadata(p.stat())
    entries = []
    def walk(directory):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('任务已取消')
        before_dir = metadata(directory.stat())
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda e: e.name.casefold())
        for entry in children:
            child = safe_path(entry.path)
            rel = str(child.relative_to(p))
            if child.is_dir():
                entries.append({'path': rel, 'kind': 'directory', 'snapshot': metadata(child.stat())})
                walk(child)
            else:
                entries.append({'path': rel, 'kind': 'file', 'snapshot': fingerprint(child, cancel)})
        if metadata(directory.stat()) != before_dir:
            raise ValueError('目录在读取期间发生变化，请刷新预览')
    walk(p)
    if metadata(p.stat()) != before:
        raise ValueError('目录已变化，请刷新预览')
    content = [(e['path'], e['kind'], e['snapshot'].get('size') if e['kind'] == 'file' else None,
                e['snapshot'].get('sha256')) for e in entries]
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {**before, 'kind': 'directory', 'entries': entries, 'sha256': digest,
            'total_size': sum(e['snapshot']['size'] for e in entries if e['kind'] == 'file')}


def check_tree(path, snap, internal=()):
    check_operable(path, internal)
    for entry in snap.get('entries', []):
        check_operable(Path(path) / entry['path'], internal)


def same_content(before, after):
    return before.get('kind', 'file') == after.get('kind', 'file') and before['sha256'] == after['sha256']
