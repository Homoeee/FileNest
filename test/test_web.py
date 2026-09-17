import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from filenest.models import Rule
from dataclasses import asdict
from web_app import WebApp


def wait(app):
    deadline = time.monotonic() + 10
    while app.job['busy'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert not app.job['busy']
    assert not app.job['error'], app.job['error']


@pytest.fixture
def app_workspace():
    import uuid
    import shutil
    root = Path(__file__).parent / '_tmp' / ('web-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    yield root
    assert root.resolve().parent == (Path(__file__).parent / '_tmp').resolve()
    shutil.rmtree(root)


def test_web_preview_execute_replay_and_undo(app_workspace):
    root = app_workspace
    source = root / '来源'
    source.mkdir()
    original = source / '中文合同.txt'
    original.write_text('测试', encoding='utf-8')
    app = WebApp(root / 'data')
    app.request('/api/preview', {'settings': {'sources': [str(source)], 'target': str(root / '目标')},
                                'rules': [asdict(Rule('文档', folder='文档', extensions='txt'))]})
    wait(app)
    assert original.exists()
    assert app.plan[0].status == 'ready'
    payload = {'plan_id': app.plan_id, 'indices': [0]}
    app.request('/api/execute', payload)
    wait(app)
    assert not original.exists()
    with pytest.raises(ValueError):
        app.request('/api/execute', payload)
    batch = app.job['result']['batch']
    app.request('/api/undo', {'batch': batch})
    wait(app)
    assert original.read_text(encoding='utf-8') == '测试'


def test_web_invalid_relationship_and_cancel(app_workspace):
    root = app_workspace
    app = WebApp(root / 'data')
    with pytest.raises(ValueError, match='目标目录不能'):
        app.request('/api/preview', {'settings': {'sources': [str(root)], 'target': str(root)}, 'rules': []})
    started = threading.Event()
    def fake_scan(*args):
        started.set()
        assert app.cancel.wait(3)
        return [], []
    with patch('web_app.scan', fake_scan):
        app.request('/api/preview', {'settings': {'sources': [str(root)], 'target': str(root / 'out')}, 'rules': []})
        assert started.wait(3)
        with pytest.raises(ValueError, match='等待'):
            app.request('/api/settings', {'settings': {}, 'rules': []})
        app.request('/api/cancel', {})
        wait(app)
    assert not app.plan_id
