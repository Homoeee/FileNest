"""双栏目录浏览：只读取当前层，列表标识用于绑定后续操作。"""
import os
import ctypes
import secrets
import threading
from collections import OrderedDict
from pathlib import Path
from dataclasses import asdict

from .models import FileInfo
from .paths import safe_path
from .scanner import scan


def available_roots():
    """列出盘符而不访问磁盘；断开的网络盘在用户进入时才检查。"""
    if os.name == 'nt':
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [Path(f'{chr(65 + index)}:/') for index in range(26) if mask & (1 << index)]
    return [Path('/')]


class DirectoryBrowser:
    def __init__(self, internal):
        self.internal = internal
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def browse(self, path, excluded=()):
        if not isinstance(path, str):
            raise ValueError('目录路径无效')
        if not path.strip():
            # Windows 根目录只取已登记的盘符，避免逐盘 stat 卡在断开的网络盘。
            roots = available_roots()
            entries, errors = [], []
            for root in roots:
                # 真正进入盘符时再检查可访问性；此处不接触磁盘或网络设备。
                entries.append(FileInfo(str(root), size=0, mtime_ns=0, device=0, inode=0,
                                        category='文件夹', kind='directory'))
            current, parent = '', ''
        else:
            directory = safe_path(path.strip())
            if not directory.is_dir():
                raise ValueError('目录不存在或无法访问，请检查路径')
            entries, errors = scan([directory], recursive=False, excluded=[self.internal, *excluded],
                                   include_temporary=True, include_directories=True)
            current = str(directory)
            parent = str(directory.parent) if directory.parent != directory else ''
        token = secrets.token_hex(16)
        with self.lock:
            self.cache[token] = tuple(entries)
            while len(self.cache) > 24:
                self.cache.popitem(last=False)
        return {'path': current, 'parent': parent, 'token': token,
                'entries': [asdict(f) for f in entries], 'errors': errors}

    def selected(self, token, indices):
        with self.lock:
            entries = self.cache.get(token)
            if entries is None:
                raise ValueError('目录列表已失效，请刷新后重试')
            if not isinstance(indices, list) or not indices or any(type(i) is not int or not 0 <= i < len(entries) for i in indices):
                raise ValueError('请先选中有效的文件或文件夹')
            return [entries[i] for i in sorted(set(indices))]
