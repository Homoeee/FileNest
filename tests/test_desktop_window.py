"""桌面入口的无端口、无浏览器启动测试。"""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from ui import desktop_window


class DesktopWindowTests(unittest.TestCase):
    def setUp(self):
        self.server = Mock(server_port=8765)
        self.server.web_app.web_handoff.is_set.return_value = False

    def test_edge_launcher_exit_does_not_control_service_lifetime(self):
        process = Mock()
        process.wait.return_value = 0
        with (patch.object(desktop_window, "make_server", return_value=self.server),
              patch.object(desktop_window, "find_edge", return_value=r"C:\Edge\msedge.exe"),
              patch.object(desktop_window, "open_desktop", return_value=process) as launch,
              redirect_stdout(StringIO())):
            desktop_window.run()

        self.assertEqual(launch.call_args.args[0], "http://127.0.0.1:8765/?mode=desktop")
        self.assertEqual(launch.call_args.args[1].name, "desktop-browser")
        process.wait.assert_not_called()
        self.server.serve_forever.assert_called_once()
        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_default_browser_fallback_uses_same_service(self):
        with (patch.object(desktop_window, "make_server", return_value=self.server),
              patch.object(desktop_window, "find_edge", return_value=None),
              patch.object(desktop_window, "open_web") as open_browser,
              redirect_stdout(StringIO())):
            desktop_window.run()

        open_browser.assert_called_once_with("http://127.0.0.1:8765/")
        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_edge_start_failure_still_closes_service(self):
        with (patch.object(desktop_window, "make_server", return_value=self.server),
              patch.object(desktop_window, "find_edge", return_value=r"C:\Edge\msedge.exe"),
              patch.object(desktop_window, "open_desktop", side_effect=OSError('无法启动 Edge')),
              redirect_stdout(StringIO())):
            with self.assertRaisesRegex(OSError, "无法启动 Edge"):
                desktop_window.run()

        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_switch_to_web_keeps_service_after_desktop_window_closes(self):
        process = Mock()
        process.wait.return_value = 0
        self.server.web_app.web_handoff.is_set.return_value = True
        thread = Mock(ident=1)
        with (patch.object(desktop_window, "make_server", return_value=self.server),
              patch.object(desktop_window, "find_edge", return_value=r"C:\Edge\msedge.exe"),
              patch.object(desktop_window, "open_desktop", return_value=process),
              patch.object(desktop_window.threading, "Thread", return_value=thread),
              redirect_stdout(StringIO())):
            desktop_window.run()

        self.assertEqual(thread.join.call_count, 2)
        self.server.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
