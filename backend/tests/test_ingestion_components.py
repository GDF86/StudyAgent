import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import DomainError
from app.rag.embeddings import CompatibleEmbeddingProvider
from app.rag.loaders import TextPage
from app.rag.splitter import split_pages


def test_chunk_overlap_and_pdf_boundary():
    pages = [TextPage("abcdefghijk", 1), TextPage("xyz", 2)]
    chunks = split_pages(pages, 5, 2, 20)
    assert [(c.content, c.page) for c in chunks] == [("abcde", 1), ("defgh", 1), ("ghijk", 1), ("xyz", 2)]


def test_chunk_limit_and_config():
    with pytest.raises(DomainError):
        split_pages([TextPage("a" * 20, None)], 5, 1, 2)
    with pytest.raises(ValueError):
        Settings(chunk_size=10, chunk_overlap=10, _env_file=None)


def config():
    return Settings(embedding_base_url="http://example.test/v1", embedding_model="unit-test-model", embedding_batch_size=2, _env_file=None)


def test_embedding_batching_order_and_authorization():
    settings = config()
    requests = []
    def reply(request):
        requests.append(request)
        batch = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [len(text), 1]} for i, text in reversed(list(enumerate(batch)))]})
    vectors = CompatibleEmbeddingProvider(settings, httpx.MockTransport(reply)).embed_texts(["a", "bb", "ccc"])
    assert vectors == [[1, 1], [2, 1], [3, 1]]
    assert len(requests) == 2
    assert str(requests[0].url) == "http://example.test/v1/embeddings"


@pytest.mark.parametrize("data", [
    [],
    [{"index": 1, "embedding": [1, 2]}],
    [{"index": 0, "embedding": [0, 0]}],
    [{"index": 0, "embedding": []}],
    [{"index": 0, "embedding": ["bad"]}],
    [{"index": 0, "embedding": [True]}],
])
def test_embedding_invalid_response(data):
    provider = CompatibleEmbeddingProvider(config(), httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data})))
    with pytest.raises(DomainError) as caught:
        provider.embed_texts(["text"])
    assert caught.value.code == "EMBEDDING_INVALID_RESPONSE"


def test_embedding_timeout():
    def reply(request):
        raise httpx.ReadTimeout("contains private URL")
    provider = CompatibleEmbeddingProvider(config(), httpx.MockTransport(reply))
    with pytest.raises(DomainError) as caught:
        provider.embed_texts(["text"])
    assert caught.value.code == "EMBEDDING_TIMEOUT"
    assert "private" not in caught.value.message


def test_embedding_dimension_mismatch():
    data = [{"index": 0, "embedding": [1, 2]}, {"index": 1, "embedding": [1]}]
    provider = CompatibleEmbeddingProvider(config(), httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data})))
    with pytest.raises(DomainError):
        provider.embed_texts(["one", "two"])


def test_real_local_http_ignores_system_proxy(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["input"] == ["local protocol test"]
            payload = json.dumps({"data": [{"index": 0, "embedding": [1, 2, 3]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    settings = config()
    settings.embedding_base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        assert CompatibleEmbeddingProvider(settings).embed_texts(["local protocol test"]) == [[1, 2, 3]]
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
