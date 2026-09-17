"""默认启动网页版；旧桌面入口通过 --desktop 保留。"""
import sys

if __name__ == '__main__':
    if '--desktop' in sys.argv:
        from ui import run
    else:
        from web_app import run
    run()
