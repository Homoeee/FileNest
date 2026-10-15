"""将用户在目录列表中选中的文件交给 Windows 默认应用。"""

import os
import stat

from .paths import metadata, safe_path


def open_file(entry, mode='default'):
    if mode not in {'default', 'choose'}:
        raise ValueError('打开方式无效')
    if entry.kind != 'file':
        raise ValueError('请选择一个文件；文件夹请在列表中打开')
    if os.name != 'nt':
        raise OSError('使用系统应用打开文件目前仅支持 Windows')

    path = safe_path(entry.path)
    current = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode):
        raise ValueError('所选项目已不是普通文件，请刷新列表')
    if any(metadata(current)[field] != getattr(entry, field)
           for field in ('size', 'mtime_ns', 'device', 'inode')):
        raise ValueError('文件已变化，请刷新列表后重试')

    # openas 使用 Windows 自带的“打开方式”选择器；默认模式使用文件关联。
    if mode == 'choose':
        os.startfile(str(path), 'openas')
    else:
        os.startfile(str(path))
