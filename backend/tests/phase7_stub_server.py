"""TEST ONLY lexical rerank protocol, plus unchanged Phase 1-6 model fixtures."""
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import re
from agent_stub_server import Handler as AgentHandler


class Handler(AgentHandler):
    def respond(self):
        if self.path != '/v1/rerank':
            return super().respond()
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        words = set(re.findall(r"[a-z]+", body['query'].lower()))
        rows = [{'index': i, 'relevance_score': len(words & set(re.findall(r"[a-z]+", doc.lower())))}
                for i, doc in enumerate(body['documents'])]
        self.json_response(200, {'results': rows})


if __name__ == '__main__':
    print('TEST ONLY Mock LLM/Embedding/lexical rerank at 127.0.0.1:18084', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 18084), Handler).serve_forever()
