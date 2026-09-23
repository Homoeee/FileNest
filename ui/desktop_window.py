"""以独立浏览器窗口承载统一的本地界面。"""

import threading

from web_app import DATA_DIR, make_server
from window_shell import find_edge, open_desktop, open_web


def run():
    # 与网页版绑定相同的本机地址；端口占用时拒绝启动第二个实例。
    server = make_server()
    url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    process = None
    try:
        thread.start()
        edge = find_edge()
        if edge is None:
            print("未找到 Edge；将在默认浏览器中打开统一界面。点击页面右上角“退出”或按 Ctrl+C 停止服务。")
            open_web(url)
        else:
            # Edge 可能复用已有进程，启动器退出不代表窗口关闭，不能据此停止后台。
            profile = DATA_DIR / "desktop-browser"
            process = open_desktop(url + "?mode=desktop", profile)
            server.web_app.desktop_process = process
            print("FileNest 桌面窗口已打开；点击页面右上角“退出”或按 Ctrl+C 停止服务。")
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
        if thread.ident is not None:
            server.shutdown()
        server.server_close()
        if thread.ident is not None:
            thread.join()
