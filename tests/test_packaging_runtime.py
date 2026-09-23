"""打包路径与网页启动行为，不实际监听端口或打开浏览器。"""

import importlib.util
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import web_app


class PackagingRuntimeTests(unittest.TestCase):
    def test_quit_rejected_while_job_is_running(self):
        app = web_app.WebApp.__new__(web_app.WebApp)
        import threading
        app.lock = threading.RLock()
        app.job = {'busy': True}
        with self.assertRaisesRegex(ValueError, '任务仍在运行'):
            app.request('/api/quit', {})
        app.job = {'busy': False}
        self.assertEqual(app.request('/api/quit', {}), {'stopping': True})

    def test_frozen_resources_and_user_data_are_separate(self):
        spec = importlib.util.spec_from_file_location('_filenest_frozen_test', web_app.__file__)
        module = importlib.util.module_from_spec(spec)
        with (patch.object(sys, 'frozen', True, create=True),
              patch.object(sys, '_MEIPASS', r'C:\bundle\_internal', create=True),
              patch.dict(os.environ, {'LOCALAPPDATA': r'C:\Users\Tester\AppData\Local'})):
            spec.loader.exec_module(module)
        self.assertEqual(module.RESOURCE_ROOT, Path(r'C:\bundle\_internal'))
        self.assertEqual(module.DATA_DIR, Path(r'C:\Users\Tester\AppData\Local\FileNest'))

    def test_frozen_web_starts_server_before_opening_browser(self):
        server = Mock(server_port=8765)
        thread = Mock(ident=1)
        calls = []
        thread.start.side_effect = lambda: calls.append('start')
        with (patch.object(web_app.sys, 'frozen', True, create=True),
              patch.object(web_app, 'make_server', return_value=server),
              patch.object(web_app.threading, 'Thread', return_value=thread),
              patch.object(web_app, 'open_web', side_effect=lambda url: calls.append(url)),
              redirect_stdout(StringIO())):
            web_app.run()
        self.assertEqual(calls, ['start', 'http://127.0.0.1:8765/'])
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
