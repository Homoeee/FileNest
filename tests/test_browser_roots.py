"""盘符首页不探测设备，断开的盘也不会拖住页面初始化。"""

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from filenest.browser_service import DirectoryBrowser
from web_app import WebApp


@unittest.skipUnless(os.name == 'nt', '仅适用于 Windows 盘符')
class BrowserRootsTests(unittest.TestCase):
    def test_root_listing_does_not_touch_drives(self):
        browser = DirectoryBrowser(Path('C:/FileNest-internal'))
        with patch('filenest.browser_service.ctypes.windll.kernel32.GetLogicalDrives', return_value=(1 << 2) | (1 << 25)), \
             patch.object(Path, 'stat', side_effect=AssertionError('不应读取盘符状态')):
            result = browser.browse('')
        self.assertEqual([item['path'] for item in result['entries']], ['C:\\', 'Z:\\'])
        self.assertEqual(result['errors'], [])

    def test_folder_picker_root_uses_same_fast_drive_list(self):
        app = WebApp.__new__(WebApp)
        app.lock = threading.RLock()
        with patch('filenest.browser_service.ctypes.windll.kernel32.GetLogicalDrives', return_value=(1 << 2) | (1 << 25)), \
             patch.object(Path, 'stat', side_effect=AssertionError('不应读取盘符状态')):
            result = app.request('/api/directories', {'path': ''})
        self.assertEqual(result['directories'], ['C:\\', 'Z:\\'])


if __name__ == '__main__':
    unittest.main()
