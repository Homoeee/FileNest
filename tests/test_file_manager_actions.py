"""文件管理中的打开和快捷重命名使用项目内生成的样例文件。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from web_app import WebApp


TEST_ROOT = Path(__file__).resolve().parents[1] / 'test' / '_tmp'


@unittest.skipUnless(os.name == 'nt', '系统应用打开仅适用于 Windows')
class FileManagerActionsTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='file_manager_', dir=TEST_ROOT)
        self.addCleanup(self.clean_temporary)
        self.root = Path(self.temporary.name)
        self.source = self.root / '来源'
        self.source.mkdir()
        self.file = self.source / '样例.txt'
        self.file.write_text('样例内容', encoding='utf-8')
        self.app = WebApp(self.root / '内部数据')
        self.refresh()

    def clean_temporary(self):
        # 仅清理由本测试创建且位于专用目录内的临时文件。
        if Path(self.temporary.name).resolve().parent != TEST_ROOT.resolve():
            raise AssertionError('测试目录超出项目内专用临时目录')
        self.temporary.cleanup()

    def refresh(self):
        listing = self.app.request('/api/browse', {'path': str(self.source)})
        self.token = listing['token']
        self.index = next(index for index, item in enumerate(listing['entries'])
                          if item['path'] == str(self.file))

    def wait_job(self):
        deadline = time.monotonic() + 5
        while self.app.job['busy'] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.app.job['busy'], '后台任务未结束')
        self.assertEqual(self.app.job['error'], '')

    def test_default_and_choose_app_use_selected_file(self):
        with patch('filenest.launcher.os.startfile') as start:
            result = self.app.request('/api/open', {'token': self.token, 'index': self.index})
            self.assertEqual(result, {'handed_to_system': True})
            start.assert_called_once_with(str(self.file))
            start.reset_mock()
            self.app.request('/api/open', {'token': self.token, 'index': self.index, 'mode': 'choose'})
            start.assert_called_once_with(str(self.file), 'openas')

    def test_open_rejects_changed_file_and_invalid_mode(self):
        self.file.write_text('文件已被修改，长度不同', encoding='utf-8')
        with patch('filenest.launcher.os.startfile') as start:
            with self.assertRaisesRegex(ValueError, '文件已变化'):
                self.app.request('/api/open', {'token': self.token, 'index': self.index})
            start.assert_not_called()
        with self.assertRaisesRegex(ValueError, '打开方式无效'):
            self.app.request('/api/open', {'token': self.token, 'index': self.index, 'mode': 'runas'})

    def test_open_rejects_stale_index_and_directory(self):
        folder = self.source / '文件夹'
        folder.mkdir()
        listing = self.app.request('/api/browse', {'path': str(self.source)})
        folder_index = next(index for index, item in enumerate(listing['entries'])
                            if item['path'] == str(folder))
        with patch('filenest.launcher.os.startfile') as start:
            with self.assertRaisesRegex(ValueError, '请选择一个文件'):
                self.app.request('/api/open', {'token': listing['token'], 'index': folder_index})
            with self.assertRaisesRegex(ValueError, '目录列表已失效|有效文件'):
                self.app.request('/api/open', {'token': 'expired', 'index': self.index})
            start.assert_not_called()

    def test_quick_rename_previews_executes_and_undoes(self):
        self.app.request('/api/quick-preview', {'token': self.token, 'indices': [self.index],
                                                'action': 'rename', 'new_name': '改名.txt'})
        self.wait_job()
        self.assertEqual(self.app.plan[0].status, 'ready')
        self.assertEqual(self.app.plan[0].destination, str(self.source / '改名.txt'))
        plan_id = self.app.plan_id
        self.app.request('/api/execute', {'plan_id': plan_id, 'indices': [0], 'risk_ack': True})
        self.wait_job()
        batch = self.app.job['result']['batch']
        self.assertFalse(self.file.exists())
        self.assertTrue((self.source / '改名.txt').exists())
        self.app.request('/api/undo', {'batch': batch})
        self.wait_job()
        self.assertTrue(self.file.exists())
        self.assertFalse((self.source / '改名.txt').exists())

    def test_quick_rename_rejects_extension_change_and_conflict(self):
        with self.assertRaisesRegex(ValueError, '保留文件扩展名'):
            self.app.request('/api/quick-preview', {'token': self.token, 'indices': [self.index],
                                                    'action': 'rename', 'new_name': '改名.exe'})
        (self.source / '改名.txt').write_text('保留原文件', encoding='utf-8')
        self.app.request('/api/quick-preview', {'token': self.token, 'indices': [self.index],
                                                'action': 'rename', 'new_name': '改名.txt'})
        self.wait_job()
        self.assertEqual(self.app.plan[0].status, 'skipped')
        self.assertEqual((self.source / '改名.txt').read_text(encoding='utf-8'), '保留原文件')

    def test_quick_rename_empty_directory(self):
        folder = self.source / '空目录'
        folder.mkdir()
        listing = self.app.request('/api/browse', {'path': str(self.source)})
        index = next(index for index, item in enumerate(listing['entries'])
                     if item['path'] == str(folder))
        self.app.request('/api/quick-preview', {'token': listing['token'], 'indices': [index],
                                                'action': 'rename', 'new_name': '新目录'})
        self.wait_job()
        self.assertEqual(self.app.plan[0].status, 'ready')
        self.assertEqual(self.app.plan[0].destination, str(self.source / '新目录'))


if __name__ == '__main__':
    unittest.main()
