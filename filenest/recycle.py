"""通过 Windows IFileOperation 送入回收站，不降级为永久删除。"""
import ctypes
import os
import uuid
from pathlib import Path


def recycle(path):
    if os.name != 'nt':
        raise OSError('移入回收站目前仅支持 Windows')
    p = Path(path)
    if not p.is_absolute() or str(p).startswith('\\\\'):
        raise ValueError('回收站操作只支持本机磁盘的绝对路径')
    shell32 = ctypes.WinDLL('shell32')
    ole32 = ctypes.WinDLL('ole32')
    kernel32 = ctypes.WinDLL('kernel32')
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    if kernel32.GetDriveTypeW(p.anchor) != 3:
        raise ValueError('此磁盘不保证支持回收站；已停止，不会改为永久删除')
    class GUID(ctypes.Structure):
        _fields_ = [('bytes', ctypes.c_ubyte * 16)]
    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    def check(hr):
        if hr < 0:
            raise OSError(f'Windows 回收站操作失败（0x{hr & 0xffffffff:08X}），未尝试永久删除')
    def call(pointer, slot, argtypes, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[slot])
        return method(pointer, *args)
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    shell32.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p,
                                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    check(ole32.CoInitializeEx(None, 2))
    operation, item = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = guid('3ad05575-8857-4850-9277-11b85bdb8e09')
        iid = guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        shell_iid = guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        check(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(operation)))
        # RECYCLEONDELETE + EARLYFAILURE + ADDUNDORECORD；禁止提权与错误跳过。
        flags = 0x00080000 | 0x00100000 | 0x20000000 | 0x0004 | 0x0010 | 0x0400
        check(call(operation, 5, [ctypes.c_uint], flags))
        check(shell32.SHCreateItemFromParsingName(str(p), None, ctypes.byref(shell_iid), ctypes.byref(item)))
        check(call(operation, 18, [ctypes.c_void_p, ctypes.c_void_p], item, None))
        check(call(operation, 21, []))
        aborted = ctypes.c_int()
        check(call(operation, 22, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(aborted)))
        if aborted.value or os.path.lexists(p):
            raise OSError('移入回收站未完成，请核对源位置及 Windows 回收站')
    finally:
        if item:
            call(item, 2, [])
        if operation:
            call(operation, 2, [])
        ole32.CoUninitialize()
