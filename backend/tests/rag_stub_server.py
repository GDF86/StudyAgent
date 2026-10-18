"""TEST ONLY: deterministic protocol fixtures, not an embedding or language model.

Run: python tests/rag_stub_server.py. Listens only on 127.0.0.1:18082.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time


def vector(text):
    if 'NO_MATCH' in text:
        return [0, 0, 1]
    if 'BETA' in text:
        return [0, 1, 0]
    return [1, 0, 0]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self.respond()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Expected when a timeout/cancellation test disconnects.

    def respond(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/v1/embeddings':
            self.json_response(200, {'data': [{'index': i, 'embedding': vector(text)} for i, text in enumerate(body['input'])]})
            return
        if self.path != '/v1/chat/completions':
            self.send_error(404)
            return
        context = json.loads(body['messages'][-1]['content'])
        question = context['question']
        if '超时测试' in question:
            time.sleep(3)
        if '异常测试' in question:
            self.json_response(500, {'error': 'test-only upstream failure'})
            return
        source = context['reference_material'][0]
        answer = f"## 根据当前资料\n\n{source['content']} [{source['id']}]\n\n```python\nprint('资料示例')\n```"
        if '恶意内容测试' in question:
            answer += '\n\n<img src=x onerror="window.__xss=true">\n<script>window.__xss=true</script>\n[恶意链接](javascript:alert(1))'
        if '无效引用测试' in question:
            answer = '虚构来源 [999]'
        if not body.get('stream'):
            self.json_response(200, {'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}]})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        parts = [answer[:10], answer[10:30], answer[30:]]
        for part in parts:
            event = {'choices': [{'delta': {'content': part}, 'finish_reason': None}]}
            self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\r\n\r\n').encode())
            self.wfile.flush()
            time.sleep(0.4 if 'PHASE6:SLOW' in question else 0.12)
        self.wfile.write(b'data: [DONE]\r\n\r\n')
        self.wfile.flush()

    def json_response(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    print('TEST ONLY synthetic RAG protocol service: http://127.0.0.1:18082/v1', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 18082), Handler).serve_forever()
