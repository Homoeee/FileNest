"""本机网页适配层；文件操作复用核心服务，计划仅在服务端保存。"""
import json
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from filenest.executor import Executor
from filenest.models import Rule
from filenest.paths import inside, safe_path
from filenest.planner import build_plan
from filenest.rules import validate
from filenest.scanner import scan
from filenest.storage import Store

ROOT = Path(__file__).resolve().parent


class WebApp:
    def __init__(self, directory):
        self.store = Store(directory)
        self.store.recover()
        self.executor = Executor(self.store)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.plan = []
        self.plan_id = ''
        self.job = {'busy': False, 'message': '选择目录，开始第一次整理。', 'error': '', 'result': None}

    def start(self, task):
        if self.job['busy']:
            raise ValueError('已有任务正在进行')
        self.cancel.clear()
        self.job = {'busy': True, 'message': '正在处理…', 'error': '', 'result': None}
        def work():
            try:
                result = task()
                with self.lock:
                    self.job['result'] = result
                    self.job['message'] = '任务已取消，请查看已完成结果' if self.cancel.is_set() else '任务完成'
            except Exception as exc:
                with self.lock:
                    self.plan_id = ''
                    self.job['error'] = str(exc)
                    self.job['message'] = '任务失败'
            finally:
                with self.lock:
                    self.job['busy'] = False
        threading.Thread(target=work, daemon=True).start()
        return {'started': True}

    def progress(self, message):
        with self.lock:
            self.job['message'] = message

    def request(self, path, data=None):
        with self.lock:
            if path == '/api/state':
                settings, rules = self.store.load_settings()
                return {'settings': settings, 'rules': [asdict(r) for r in rules], 'job': dict(self.job),
                        'plan_id': self.plan_id, 'plan': [p.to_dict() for p in self.plan], 'history': self.store.batches()}
            if path == '/api/operations':
                return self.store.operations(data['batch'])
            if path == '/api/cancel':
                self.cancel.set()
                return {'ok': True}
            if self.job['busy']:
                raise ValueError('请等待当前任务完成，或先取消任务')
            if path in {'/api/settings', '/api/preview'}:
                settings = data['settings']
                if not isinstance(settings, dict):
                    raise ValueError('设置格式无效')
                for field in ('sources', 'excludes'):
                    if not isinstance(settings.get(field, []), list) or not all(isinstance(p, str) for p in settings.get(field, [])):
                        raise ValueError('目录列表格式无效')
                rules = [Rule(**r) for r in data['rules']]
                for rule in rules:
                    validate(rule)
                self.plan, self.plan_id = [], ''
                if path == '/api/preview':
                    sources, target = settings.get('sources', []), settings.get('target', '').strip()
                    if not sources or not target:
                        raise ValueError('请填写来源目录和目标目录')
                    target = safe_path(target)
                    for source in sources:
                        if not safe_path(source).is_dir():
                            raise ValueError(f'来源目录不存在：{source}')
                        if inside(source, target):
                            raise ValueError('目标目录不能等于或包含来源目录')
                self.store.save_settings(settings, rules)
                if path == '/api/settings':
                    return {'ok': True}
                def preview():
                    files, errors = scan(sources, settings.get('recursive', True),
                        [*settings.get('excludes', []), target, self.store.directory], self.cancel, self.progress)
                    plan = build_plan(files, rules, target, (self.store.directory,), self.cancel, self.progress)
                    with self.lock:
                        self.plan = plan
                        self.plan_id = '' if self.cancel.is_set() else secrets.token_hex(16)
                    return {'kind': 'preview', 'errors': errors, 'count': len(files)}
                return self.start(preview)
            if path == '/api/execute':
                if not self.plan_id or data.get('plan_id') != self.plan_id:
                    raise ValueError('预览已失效，请重新生成')
                indices = data.get('indices', [])
                if not indices or any(type(i) is not int or i < 0 or i >= len(self.plan) for i in indices):
                    raise ValueError('请选择有效的预览项目')
                items = [self.plan[i] for i in sorted(set(indices))]
                if any(p.status != 'ready' for p in items):
                    raise ValueError('所选项目包含不可执行项')
                self.plan_id = ''  # 消费计划，重复请求不能重复执行。
                return self.start(lambda: {'kind': 'execute', 'batch': self.executor.execute(items, self.cancel, self.progress)})
            if path == '/api/undo':
                batch = data['batch']
                if not self.store.operations(batch):
                    raise ValueError('历史记录不存在')
                self.plan_id = ''
                def undo():
                    self.executor.undo(batch, self.cancel, self.progress)
                    return {'kind': 'undo', 'batch': batch}
                return self.start(undo)
            raise ValueError('未知请求')


def make_server(port=8765):
    app = WebApp(ROOT / '.filenest')
    token = secrets.token_urlsafe(32)
    authority = f'127.0.0.1:{port}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # 不将私人路径写入访问日志。

        def respond(self, code, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get('Host') != authority:
                return self.respond(403, {'error': '仅允许本机访问'})
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.respond(200, app.request(path))
            names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript')}
            if path not in names:
                return self.respond(404, {'error': '页面不存在'})
            name, mime = names[path]
            content = (ROOT / 'web' / name).read_text(encoding='utf-8').replace('__TOKEN__', token)
            self.respond(200, content.encode('utf-8'), mime + '; charset=utf-8')

        def do_POST(self):
            if (self.headers.get('Host') != authority or self.headers.get('X-FileNest-Token') != token
                    or self.headers.get('Origin') not in {None, f'http://{authority}'}):
                return self.respond(403, {'error': '请求来源无效，请刷新页面'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1048576:
                    raise ValueError('请求大小无效')
                data = json.loads(self.rfile.read(length))
                self.respond(200, app.request(urlsplit(self.path).path, data))
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.respond(400, {'error': str(exc)})
            except Exception:
                self.respond(500, {'error': '服务处理失败，请检查本地配置和历史数据库'})
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def run():
    server = make_server()
    print('FileNest 网页版：http://127.0.0.1:8765 （Ctrl+C 停止）')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    run()
