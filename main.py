"""网页版和桌面窗口使用同一套本地界面与功能。"""
import sys

if __name__ == '__main__':
    # 双击打包程序进入桌面版；源码启动仍默认网页，--web 可明确选择网页。
    if '--desktop' in sys.argv or (getattr(sys, 'frozen', False) and '--web' not in sys.argv):
        from ui import run
    else:
        from web_app import run
    run()
