"""切换窗口时保留同一个服务和桌面转网页的生命周期。"""

import threading
import unittest
from unittest.mock import Mock, patch

import web_app
import window_shell


class ModeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.WebApp.__new__(web_app.WebApp)
        self.app.lock = threading.RLock()
        self.app.base_url = 'http://127.0.0.1:8765/'
        self.app.web_handoff = threading.Event()
        self.app.desktop_process = None

    def test_web_opens_desktop_window_once(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(web_app, 'open_desktop', return_value=process) as launch:
            self.assertEqual(self.app.request('/api/open-desktop', {}), {'opened': True})
            self.assertEqual(self.app.request('/api/open-desktop', {}),
                             {'opened': False, 'already_open': True})
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0],
                         'http://127.0.0.1:8765/?mode=desktop')

    def test_desktop_opens_web_and_keeps_server_handoff(self):
        with patch.object(web_app, 'open_web') as launch:
            self.assertEqual(self.app.request('/api/open-web', {}), {'opened': True})
        launch.assert_called_once_with('http://127.0.0.1:8765/')
        self.assertTrue(self.app.web_handoff.is_set())

    def test_failed_web_open_does_not_mark_handoff(self):
        with patch.object(web_app, 'open_web', side_effect=OSError('无法打开')):
            with self.assertRaises(OSError):
                self.app.request('/api/open-web', {})
        self.assertFalse(self.app.web_handoff.is_set())

    def test_desktop_shell_uses_app_mode_and_isolated_profile(self):
        process = Mock()
        with (patch.object(window_shell, 'find_edge', return_value=r'C:\Edge\msedge.exe'),
              patch.object(window_shell.Path, 'mkdir'),
              patch.object(window_shell.subprocess, 'Popen', return_value=process) as launch):
            result = window_shell.open_desktop('http://127.0.0.1:8765/?mode=desktop',
                                               r'D:\FileNest\.filenest\desktop-browser')
        self.assertIs(result, process)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[0], r'C:\Edge\msedge.exe')
        self.assertIn('--app=http://127.0.0.1:8765/?mode=desktop', arguments)
        self.assertTrue(any(arg.startswith('--user-data-dir=') for arg in arguments))


if __name__ == '__main__':
    unittest.main()
