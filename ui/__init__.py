"""FileNest 桌面入口；旧 Tk 界面源码保留供参考。"""

from .desktop_window import run

__all__ = ["FileNestApp", "run"]


def __getattr__(name):
    # 旧 Tk 界面仅在明确访问时加载，分发包不需要捆绑 Tcl/Tk。
    if name == 'FileNestApp':
        from .main_window import FileNestApp
        return FileNestApp
    raise AttributeError(name)
