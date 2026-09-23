"""本机网页适配层；文件操作复用核心服务，计划仅在服务端保存。"""
import json
import os
import secrets
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from filenest.executor import Executor
from filenest.models import Rule, PlanItem, CATEGORIES
from filenest.browser_service import DirectoryBrowser, available_roots
from filenest.paths import inside, safe_path
from filenest.planner import build_plan
from filenest.rules import validate
from filenest.scanner import scan
from filenest.storage import Store
from window_shell import open_desktop, open_web

ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', ROOT))
# 冻结程序的资源目录可能只读或随更新替换，用户历史必须单独保存。
DATA_DIR = ((Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'FileNest')
            if getattr(sys, 'frozen', False) else ROOT / '.filenest')


class WebApp:
    def __init__(self, directory, base_url='http://127.0.0.1:8765/'):
        self.store = Store(directory)
        self.store.recover()
        self.executor = Executor(self.store)
        self.browser = DirectoryBrowser(self.store.directory)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.files = []
        self.scan_id = ''
        self.scan_settings = None
        self.plan = []
        self.plan_id = ''
        self.job = {'busy': False, 'message': '选择目录，开始第一次整理。', 'error': '', 'result': None}
        self.base_url = base_url
        self.web_handoff = threading.Event()
        self.desktop_process = None

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
        if path == '/api/browse':
            settings, _ = self.store.load_settings()
            return self.browser.browse(data.get('path', ''), settings.get('excludes', []))
        with self.lock:
            if path == '/api/state':
                settings, rules = self.store.load_settings()
                return {'settings': settings, 'rules': [asdict(r) for r in rules], 'job': dict(self.job),
                        'files': [asdict(f) for f in self.files], 'scan_id': self.scan_id, 'scan_settings': self.scan_settings, 'plan_id': self.plan_id, 'plan': [p.to_dict() for p in self.plan], 'history': self.store.batches()}
            if path == '/api/quit':
                if self.job['busy']:
                    raise ValueError('任务仍在运行，请等待完成或先取消任务')
                return {'stopping': True}
            if path == '/api/open-desktop':
                if self.desktop_process is not None and self.desktop_process.poll() is None:
                    return {'opened': False, 'already_open': True}
                profile = DATA_DIR / 'desktop-browser'
                self.desktop_process = open_desktop(self.base_url + '?mode=desktop', profile)
                return {'opened': True}
            if path == '/api/open-web':
                open_web(self.base_url)
                self.web_handoff.set()
                return {'opened': True}
            if path == '/api/directories':
                requested = data.get('path', '').strip()
                if not requested:
                    return {'path': '', 'parent': '', 'directories': [str(p) for p in available_roots()], 'errors': []}
                directory = safe_path(requested)
                if not directory.is_dir():
                    raise ValueError('目录不存在，请检查路径')
                children, errors = [], []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            child = safe_path(entry.path)
                            if entry.is_dir(follow_symlinks=False) and not inside(child, self.store.directory):
                                children.append(str(child))
                        except (ValueError, OSError) as exc:
                            errors.append(f'{entry.name}：{exc}')
                return {'path': str(directory), 'parent': str(directory.parent) if directory.parent != directory else '',
                        'directories': sorted(children, key=str.casefold), 'errors': errors}
            if path == '/api/scan':
                if self.job['busy']:
                    raise ValueError('请等待当前任务完成')
                settings = data['settings']
                sources = settings.get('sources', [])
                if not sources or not isinstance(sources, list):
                    raise ValueError('请填写来源目录')
                target = settings.get('target', '').strip()
                for source in sources:
                    if not safe_path(source).is_dir():
                        raise ValueError(f'来源目录不存在：{source}')
                    if target and inside(source, safe_path(target)):
                        raise ValueError('目标目录不能等于或包含来源目录')
                self.files, self.scan_id, self.plan, self.plan_id = [], '', [], ''
                self.scan_settings = {k: settings.get(k, default) for k, default in
                                      [('sources', []), ('target', ''), ('recursive', True), ('excludes', [])]}
                def scan_files():
                    files, errors = scan(sources, settings.get('recursive', True),
                        [*settings.get('excludes', []), *([target] if target else []), self.store.directory],
                        self.cancel, self.progress, include_temporary=True, include_directories=True)
                    with self.lock:
                        self.files = files
                        self.scan_id = '' if self.cancel.is_set() else secrets.token_hex(16)
                    return {'kind': 'scan', 'errors': errors, 'count': len(files)}
                return self.start(scan_files)
            if path == '/api/operations':
                return self.store.operations(data['batch'])
            if path == '/api/cancel':
                self.cancel.set()
                return {'ok': True}
            if self.job['busy']:
                raise ValueError('请等待当前任务完成，或先取消任务')
            if path == '/api/quick-preview':
                chosen = self.browser.selected(data.get('token'), data.get('indices'))
                action = data.get('action')
                if action not in {'move', 'copy', 'rename', 'recycle', 'delete', 'rules'}:
                    raise ValueError('不支持的快捷操作')
                settings, saved_rules = self.store.load_settings()
                target = data.get('target', '').strip() if action in {'move', 'copy', 'rules'} else ''
                if action in {'move', 'copy'} and not target:
                    raise ValueError('请在另一栏选择目标目录')
                if target and not safe_path(target).is_dir():
                    raise ValueError('目标目录不存在，请重新选择')
                if action == 'rules':
                    ids = data.get('rule_ids', [])
                    if not isinstance(ids, list) or not ids or any(uid not in {r.uid for r in saved_rules} for uid in ids):
                        raise ValueError('请选择有效规则')
                    quick_rules = [Rule(**{**asdict(r), 'enabled': True}) for r in saved_rules if r.uid in ids]
                else:
                    options = data.get('rename', {}) if action == 'rename' else {}
                    allowed = {'prefix', 'suffix', 'replace_from', 'replace_to', 'add_date', 'sequence'}
                    if not isinstance(options, dict) or not options.keys() <= allowed:
                        raise ValueError('重命名选项无效')
                    quick_rules = [Rule(name='手动批量操作', action=action, categories=[*CATEGORIES, '其他', '文件夹'],
                                        conflict=data.get('conflict', 'skip'), **options)]
                for rule in quick_rules:
                    validate(rule)
                self.plan, self.plan_id = [], ''
                def quick_preview():
                    plan = build_plan(chosen, quick_rules, target,
                                      internal=[self.store.directory, *settings.get('excludes', [])],
                                      cancel=self.cancel, progress=self.progress,
                                      source_excluded=[target] if target else [])
                    with self.lock:
                        self.plan = plan
                        self.plan_id = '' if self.cancel.is_set() else secrets.token_hex(16)
                    return {'kind': 'preview', 'count': len(chosen), 'errors': []}
                return self.start(quick_preview)
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
                    if not sources:
                        raise ValueError('请填写来源目录')
                    target = safe_path(target) if target else None
                    for source in sources:
                        if not safe_path(source).is_dir():
                            raise ValueError(f'来源目录不存在：{source}')
                        if target and inside(source, target):
                            raise ValueError('目标目录不能等于或包含来源目录')
                selected_files = None
                if path == '/api/preview' and 'scan_id' in data:
                    signature = {k: settings.get(k, default) for k, default in
                                 [('sources', []), ('target', ''), ('recursive', True), ('excludes', [])]}
                    if not self.scan_id or data['scan_id'] != self.scan_id or signature != self.scan_settings:
                        raise ValueError('文件列表已失效，请刷新文件列表后重试')
                    indices = data.get('file_indices', [])
                    if not indices or any(type(i) is not int or i < 0 or i >= len(self.files) for i in indices):
                        raise ValueError('请至少选择一个有效文件')
                    selected_files = [self.files[i] for i in sorted(set(indices))]
                if len({r.uid for r in rules}) != len(rules):
                    raise ValueError('规则标识重复，请重新加载')
                if path == '/api/settings':
                    self.store.save_settings(settings, rules)
                    return {'ok': True}
                chosen_ids = data.get('selected_rule_ids')
                if chosen_ids is not None:
                    if not isinstance(chosen_ids, list) or not chosen_ids or any(uid not in {r.uid for r in rules} for uid in chosen_ids):
                        raise ValueError('请勾选至少一条有效规则')
                    preview_rules = [Rule(**{**asdict(r), 'enabled': True, 'by_month': r.by_month or settings.get('by_month', False)}) for r in rules if r.uid in chosen_ids]
                else:
                    preview_rules = [Rule(**r) for r in data.get('active_rules', data['rules'])]
                for rule in preview_rules:
                    validate(rule)
                self.store.save_settings(settings, rules)
                def preview():
                    if selected_files is None:
                        files, errors = scan(sources, settings.get('recursive', True),
                            [*settings.get('excludes', []), *([target] if target else []), self.store.directory], self.cancel, self.progress, include_directories=True)
                    else:
                        files, errors = selected_files, []
                    temporary = {'.crdownload', '.part', '.tmp', '.download'}
                    processable = [f for f in files if f.kind == 'directory' or Path(f.path).suffix.lower() not in temporary]
                    plan = build_plan(processable, preview_rules, target, (self.store.directory, *settings.get('excludes', [])), self.cancel, self.progress, source_excluded=([target] if target else []))
                    plan.extend(PlanItem(f.path, '', '', 'skipped', '临时或下载中文件，暂不处理')
                                for f in files if f.kind != 'directory' and Path(f.path).suffix.lower() in temporary)
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
                if any(p.action == 'delete' for p in items) and data.get('delete_confirmation') != '永久删除':
                    raise ValueError('永久删除不可恢复，请输入“永久删除”确认')
                if any(p.warnings or p.action == 'recycle' for p in items) and data.get('risk_ack') is not True:
                    raise ValueError('请阅读并确认所选操作的风险提示')
                self.scan_id = ''
                self.plan_id = ''  # 消费计划，重复请求不能重复执行。
                return self.start(lambda: {'kind': 'execute', 'batch': self.executor.execute(items, self.cancel, self.progress, confirm_destructive=True)})
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
    authority = f'127.0.0.1:{port}'
    app = WebApp(DATA_DIR, f'http://{authority}/')
    token = secrets.token_urlsafe(32)
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
            names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/explorer.js': ('explorer.js', 'text/javascript'), '/explorer.css': ('explorer.css', 'text/css'), '/theme.css': ('theme.css', 'text/css')}
            if path not in names:
                return self.respond(404, {'error': '页面不存在'})
            name, mime = names[path]
            content = (RESOURCE_ROOT / 'web' / name).read_text(encoding='utf-8').replace('__TOKEN__', token)
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
                path = urlsplit(self.path).path
                self.respond(200, app.request(path, data))
                if path == '/api/quit':
                    threading.Thread(target=server.shutdown, daemon=True).start()
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.respond(400, {'error': str(exc)})
            except Exception:
                self.respond(500, {'error': '服务处理失败，请检查本地配置和历史数据库'})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.web_app = app
    return server


def run():
    server = make_server()
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'FileNest 网页版：{url} （Ctrl+C 停止）')
    if getattr(sys, 'frozen', False):
        # 分发包自动打开浏览器；服务先启动，避免首次访问遇到连接拒绝。
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            open_web(url)
            thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            if thread.ident is not None:
                server.shutdown()
            server.server_close()
            if thread.ident is not None:
                thread.join()
        return
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    run()
