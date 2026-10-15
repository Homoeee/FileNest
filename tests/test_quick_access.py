"""快速访问只在项目专用测试目录中创建样例。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from filenest.browser_service import common_folders
from web_app import WebApp


TEST_ROOT = Path(__file__).resolve().parents[1] / 'test' / '_tmp'


class QuickAccessTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='quick_access_', dir=TEST_ROOT)
        self.addCleanup(self.clean_temporary)
        self.root = Path(self.temporary.name)
        self.directory = self.root / '中文文件夹'
        self.directory.mkdir()
        self.internal = self.root / '内部数据'
        self.app = WebApp(self.internal)

    def clean_temporary(self):
        if Path(self.temporary.name).resolve().parent != TEST_ROOT.resolve():
            raise AssertionError('测试目录超出项目内专用临时目录')
        self.temporary.cleanup()

    def test_pin_is_shared_deduplicated_and_removable(self):
        result = self.app.request('/api/quick-access', {'action': 'add', 'path': str(self.directory)})
        self.assertEqual(result['pinned'], [{'name': '中文文件夹', 'path': str(self.directory)}])
        self.app.request('/api/quick-access', {'action': 'add', 'path': str(self.directory)})
        self.assertEqual(len(self.app.request('/api/state')['quick_access']['pinned']), 1)
        reopened = WebApp(self.internal)
        self.assertEqual(reopened.request('/api/quick-access', {'action': 'list'})['pinned'], result['pinned'])
        reopened.request('/api/quick-access', {'action': 'remove', 'path': str(self.directory)})
        self.assertEqual(self.app.request('/api/quick-access', {'action': 'list'})['pinned'], [])

    def test_rejects_invalid_and_internal_paths(self):
        for path in ['', 'relative/path', str(self.root / '不存在'), str(self.internal)]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.app.request('/api/quick-access', {'action': 'add', 'path': path})
        with self.assertRaisesRegex(ValueError, '无效的快速访问操作'):
            self.app.request('/api/quick-access', {'action': 'erase', 'path': str(self.directory)})
        self.assertEqual(self.app.request('/api/quick-access', {'action': 'list'})['pinned'], [])

    def test_default_location_is_not_duplicated(self):
        with patch('web_app.common_folders', return_value=[
            {'name': '样例', 'path': str(self.directory)}
        ]):
            result = self.app.request('/api/quick-access', {'action': 'add', 'path': str(self.directory)})
        self.assertEqual(result['pinned'], [])

    def test_common_folders_only_show_existing_locations(self):
        (self.root / 'Desktop').mkdir()
        (self.root / 'Downloads').mkdir()
        with patch('filenest.browser_service.Path.home', return_value=self.root), \
             patch.dict(os.environ, {'OneDrive': ''}):
            places = common_folders()
        self.assertEqual(places, [
            {'name': '桌面', 'path': str(self.root / 'Desktop')},
            {'name': '下载', 'path': str(self.root / 'Downloads')},
        ])


if __name__ == '__main__':
    unittest.main()
