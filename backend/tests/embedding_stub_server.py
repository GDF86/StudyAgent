"""TEST ONLY: protocol stub with synthetic vectors; never a semantic model.

Run manually for local integration tests: python tests/embedding_stub_server.py
Production never imports this module or silently falls back to it.
"""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = [{"index": i, "embedding": [float(value + 1) for value in sha256(text.encode()).digest()[:8]]}
                for i, text in enumerate(body["input"])]
        result = json.dumps({"model": "TEST-ONLY-SYNTHETIC", "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("TEST ONLY synthetic embedding stub: http://127.0.0.1:18081/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 18081), Handler).serve_forever()
