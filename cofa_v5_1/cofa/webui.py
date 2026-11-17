"""Loopback-only, token-authenticated local console; no external Python packages."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs
import uuid
import webbrowser


def serve(exam, execute, port=8765, open_browser=False):
    token = secrets.token_urlsafe(32)
    jobs = {}
    jobs_lock = threading.Lock()
    address = f'http://127.0.0.1:{port}'
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    allowed_origins = {address, f'http://localhost:{port}'}
    stop = threading.Event()

    def launch(action, params):
        job = uuid.uuid4().hex[:12]
        with jobs_lock:
            if any(x['status'] == 'running' for x in jobs.values()):
                raise RuntimeError('실행 중인 작업이 있습니다.')
            jobs[job] = {'id': job, 'action': action, 'status': 'running'}
        def work():
            try:
                result = execute(exam, action, **params)
                done = {'id': job, 'action': action, 'status': 'done', 'result': result}
            except Exception as exc:
                done = {'id': job, 'action': action, 'status': 'error', 'error': str(exc)}
            with jobs_lock:
                jobs[job] = done
        threading.Thread(target=work, daemon=True).start()
        return job

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not echo tokens or content into a console access log.

        def reply(self, code, body, content_type='application/json; charset=utf-8'):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if self.headers.get('Host') not in allowed_hosts:
                self.reply(403, {'error': 'Invalid Host'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in allowed_origins:
                self.reply(403, {'error': 'Invalid Origin'})
                return False
            supplied = self.headers.get('X-COFA-Token', '')
            if not hmac.compare_digest(token, supplied):
                self.reply(403, {'error': '실행 터미널에 표시된 토큰 포함 주소로 여세요.'})
                return False
            return True

        def do_GET(self):
            parsed = urlparse(self.path)
            routes = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path in routes:
                if self.headers.get('Host') not in allowed_hosts:
                    return self.reply(403, {'error': 'Invalid Host'})
                name, kind = routes[parsed.path]
                return self.reply(200, (exam.root / 'web' / name).read_bytes(), kind)
            if not self.authorized(): return
            try:
                query = parse_qs(parsed.query)
                if parsed.path == '/api/problems':
                    from .problems import list_problems
                    return self.reply(200, list_problems(exam.root))
                if parsed.path == '/api/state':
                    result = exam.status()
                    with jobs_lock:
                        result['jobs'] = list(jobs.values())
                    return self.reply(200, result)
                if parsed.path == '/api/events':
                    after = int(query.get('after', ['0'])[0])
                    return self.reply(200, exam.store.events(after=after, public=True))
                if parsed.path == '/api/files': return self.reply(200, exam.list_files())
                if parsed.path == '/api/file':
                    return self.reply(200, {'content': exam.read_file(query.get('path', [''])[0])})
                if parsed.path == '/api/usage': return self.reply(200, exam.usage())
                if parsed.path == '/api/tasks':
                    return self.reply(200, json.loads((exam.root / 'tasks.json').read_text(encoding='utf-8')))
                if parsed.path in ('/api/feedback','/api/feedback-text'):
                    from .feedback import require_finished
                    require_finished(exam)
                    filename = 'feedback.json' if parsed.path == '/api/feedback' else 'feedback.md'
                    path = exam.root / 'reports' / filename
                    if not path.exists(): return self.reply(200, {})
                    return self.reply(200, json.loads(path.read_text(encoding='utf-8')) if filename.endswith('.json') else {'text':path.read_text(encoding='utf-8')})
                if parsed.path == '/api/report':
                    if exam.status()['phase'] != 'evaluated': return self.reply(403, {'error':'최종 평가 후 공개합니다.'})
                    from .feedback import require_finished
                    require_finished(exam)
                    path = exam.root / 'reports' / 'final_report.json'
                    return self.reply(200, json.loads(path.read_text(encoding='utf-8')) if path.exists() else {})
                return self.reply(404, {'error': 'Not found'})
            except Exception as exc:
                return self.reply(400, {'error': str(exc)})

        def do_POST(self):
            if not self.authorized(): return
            if self.path != '/api/action': return self.reply(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('요청 크기 제한: 2MB')
                if 'application/json' not in self.headers.get('Content-Type', ''):
                    raise ValueError('JSON 요청만 허용합니다.')
                data = json.loads(self.rfile.read(length))
                action = data.pop('action')
                permitted = {'start', 'model', 'open_cli', 'ask', 'chat', 'note', 'run', 'check', 'audit', 'parallel',
                             'agent', 'merge', 'submit', 'qa_start', 'qa_answer', 'qa_retry',
                             'qa_finish', 'evaluate', 'save_file', 'doctor', 'export',
                             'problem_select', 'problem_validate', 'problem_debrief', 'usage', 'requirements_confirm', 'requirements_link', 'feedback', 'feedback_chat', 'quality_validate', 'stakeholder_reconsider'}
                if action not in permitted: raise ValueError('지원하지 않는 동작')
                return self.reply(202, {'job': launch(action, data)})
            except Exception as exc:
                return self.reply(400, {'error': str(exc)})

    def clock_guard():
        # Freezes as soon as possible after the deadline while the local server is alive.
        # Submission records actual capture delay rather than pretending an exact freeze.
        while not stop.wait(0.5):
            state = exam.status()
            try:
                if state.get('development_expired'):
                    launch('submit', {'reason': 'server_timer_expired'})
                elif state.get('interview_expired'):
                    launch('qa_finish', {'reason': 'time_expired'})
            except RuntimeError:
                pass  # A running call is itself bounded by the same phase deadline.

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=clock_guard, daemon=True).start()
    url = address + '/#token=' + token
    print('로컬 콘솔:', url, flush=True)
    print('서버 창을 닫으면 자동 마감 동작도 멈춥니다. 외부 접속/터널링을 사용하지 마세요.', flush=True)
    if open_browser:
        webbrowser.open(url)
    state = exam.status()
    if state['phase'] == 'development' and state['mode'] == 'llm' and not state.get('development_expired'):
        launch('open_cli', {})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
