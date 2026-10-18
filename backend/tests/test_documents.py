from io import BytesIO
from uuid import uuid4

import httpx
import numpy as np
import pymupdf
import pytest
from docx import Document as DocxDocument
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.models import Chunk, Document
from app.main import create_app
from app.rag.embeddings import CompatibleEmbeddingProvider


def embedding_reply(request):
    import json
    inputs = json.loads(request.content)["input"]
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(len(text)), 1.0, 2.0]} for i, text in enumerate(inputs)]})


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}", data_dir=tmp_path / "资料",
                    chunk_size=40, chunk_overlap=8, embedding_model="test-only", embedding_base_url="http://test/v1", _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.documents.embeddings = CompatibleEmbeddingProvider(settings, httpx.MockTransport(embedding_reply))
        yield client


@pytest.fixture
def kb(client):
    return client.post('/api/knowledge-bases', json={"name": "文档测试"}).json()["id"]


def upload(client, kb, name="课程笔记.txt", data=None):
    if data is None:
        data = ("中文知识库，机器学习课程。\n" * 20).encode()
    return client.post(f"/api/knowledge-bases/{kb}/documents", files={"file": (name, data)})


def file_bytes(extension):
    if extension == "pdf":
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Learning notes page one. " * 3)
            pdf.new_page().insert_text((72, 72), "Second page study material.")
            return pdf.tobytes()
    if extension == "docx":
        doc = DocxDocument()
        doc.add_paragraph("机器学习文档正文，保留中文内容。")
        doc.add_table(rows=1, cols=2).cell(0, 0).text = "表格知识"
        doc.add_paragraph("表格之后的正文。")
        output = BytesIO()
        doc.save(output)
        return output.getvalue()
    return ("# 学习笔记\n\n机器学习课程资料。" * 20).encode()


@pytest.mark.parametrize("extension", ["pdf", "docx", "txt", "md"])
def test_ingestion_formats(client, kb, extension):
    response = upload(client, kb, f"中文 课程(1).{extension}", file_bytes(extension))
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["status"] == "ready" and doc["chunk_count"] > 0
    assert doc["filename"] == f"中文 课程(1).{extension}"
    assert doc["embedding_dimension"] == 3
    detail = client.get(f"/api/documents/{doc['id']}").json()
    assert len(detail["chunks"]) == doc["chunk_count"]
    assert detail["source"] == doc["filename"]
    assert all(chunk["document_id"] == doc["id"] for chunk in detail["chunks"])
    assert all(chunk["vector_id"] == f"{doc['id']}:{i}" for i, chunk in enumerate(detail["chunks"]))
    if extension == "pdf":
        assert {chunk["page"] for chunk in detail["chunks"]} == {1, 2}
    else:
        assert all(chunk["page"] is None for chunk in detail["chunks"])
    if extension == "docx":
        assert "表格知识" in "".join(chunk["content"] for chunk in detail["chunks"])
    store = client.app.state.documents.vectors
    index = store.read(doc["id"])
    assert index.ntotal == doc["chunk_count"]
    assert index.d == 3
    assert np.linalg.norm(index.reconstruct(0)) == pytest.approx(1)
    assert client.get(f"/api/knowledge-bases/{kb}/documents").json()[0]["id"] == doc["id"]
    assert len(list(client.app.state.documents.upload_dir.glob("*"))) == 1


@pytest.mark.parametrize("name,data,status,code", [
    ("empty.txt", b"", 400, "EMPTY_FILE"),
    ("test.exe", b"abc", 400, "UNSUPPORTED_FILE_TYPE"),
    ("../escape.txt", b"abc", 400, "INVALID_FILENAME"),
    ("bad.pdf", b"not a PDF", 422, "DOCUMENT_PARSE_FAILED"),
    ("bad.docx", b"not a zip", 422, "DOCUMENT_PARSE_FAILED"),
    ("spaces.txt", b" \n\t ", 422, "DOCUMENT_EMPTY_TEXT"),
    ("binary.txt", b"a\x00b", 422, "DOCUMENT_PARSE_FAILED"),
])
def test_bad_uploads(client, kb, name, data, status, code):
    response = upload(client, kb, name, data)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert not list(client.app.state.documents.vectors.directory.glob("*.faiss"))


def test_large_upload_and_body_limit(client, kb, settings):
    settings.max_upload_bytes = 5
    assert upload(client, kb, data=b"123456").status_code == 413
    # The middleware limit was captured on startup; exercise its own bound too.
    response = upload(client, kb, data=b"x" * (10 * 1024 * 1024 + 65537))
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"


def test_duplicate_scope_and_delete(client, kb):
    first = upload(client, kb).json()
    assert upload(client, kb, "different-name.txt").status_code == 409
    second_kb = client.post('/api/knowledge-bases', json={"name": "另一个知识库"}).json()["id"]
    other = upload(client, second_kb).json()
    assert client.delete(f"/api/documents/{first['id']}").status_code == 204
    assert client.get(f"/api/documents/{first['id']}").status_code == 404
    assert not client.app.state.documents.vectors.path(first['id']).exists()
    assert client.app.state.documents.vectors.path(other['id']).exists()
    assert upload(client, kb).status_code == 201


def test_delete_knowledge_base_cleans_everything(client, kb):
    ready = upload(client, kb).json()
    upload(client, kb, "broken.pdf", b"broken")
    assert client.delete(f"/api/knowledge-bases/{kb}").status_code == 204
    assert not list(client.app.state.documents.upload_dir.glob("*"))
    assert not list(client.app.state.documents.vectors.directory.glob("*.faiss"))
    with Session(client.app.state.engine) as session:
        assert session.scalars(select(Document)).all() == []
        assert session.scalars(select(Chunk)).all() == []
    assert client.get(f"/api/documents/{ready['id']}").status_code == 404


@pytest.mark.parametrize("status,code", [(401, "EMBEDDING_FAILED"), (429, "EMBEDDING_RATE_LIMITED"), (500, "EMBEDDING_FAILED")])
def test_upstream_failure_is_saved_without_vectors(client, kb, settings, status, code):
    client.app.state.documents.embeddings = CompatibleEmbeddingProvider(settings, httpx.MockTransport(lambda _: httpx.Response(status, text="secret-key-upstream-body")))
    result = upload(client, kb)
    assert result.status_code == (429 if status == 429 else 502)
    assert result.json()["code"] == code
    assert "secret" not in result.text
    document_id = result.json()["detail"]["document_id"]
    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["status"] == "failed" and detail["chunks"] == []
    assert not client.app.state.documents.vectors.path(document_id).exists()
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert not list(client.app.state.documents.upload_dir.glob("*"))


def test_missing_config_is_explicit(client, kb, settings):
    settings.embedding_base_url = ""
    response = upload(client, kb)
    assert response.status_code == 503
    assert response.json()["code"] == "EMBEDDING_NOT_CONFIGURED"


def test_vector_write_failure_rolls_back(client, kb, monkeypatch):
    def fail(*args):
        raise OSError("private path should not leak")
    monkeypatch.setattr(client.app.state.documents.vectors, "write", fail)
    response = upload(client, kb)
    assert response.status_code == 500 and "private" not in response.text
    with Session(client.app.state.engine) as session:
        assert session.scalars(select(Chunk)).all() == []
    assert client.get(f"/api/knowledge-bases/{kb}/documents").json()[0]["status"] == "failed"


def test_restart_persistence_and_recovery(client, kb, settings):
    ready = upload(client, kb).json()
    interrupted_id = str(uuid4())
    with Session(client.app.state.engine) as session:
        session.add(Document(id=interrupted_id, knowledge_base_id=kb, filename="interrupted.txt", file_type=".txt", file_size=1,
                             content_hash="interrupted", status="embedding"))
        session.commit()
    with TestClient(create_app(settings)) as reopened:
        assert reopened.get(f"/api/documents/{ready['id']}").json()["status"] == "ready"
        assert reopened.app.state.documents.vectors.read(ready['id']).ntotal == ready["chunk_count"]
        assert reopened.get(f"/api/documents/{interrupted_id}").json()["status"] == "failed"


def test_not_found_and_validation(client):
    assert upload(client, 999).status_code == 404
    assert client.get('/api/knowledge-bases/999/documents').status_code == 404
    assert client.get(f'/api/documents/{uuid4()}').status_code == 404
    assert client.delete(f'/api/documents/{uuid4()}').status_code == 404
    assert client.get('/api/documents/invalid').status_code == 422


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_encoding(client, kb, encoding):
    result = upload(client, kb, data="中文学习资料".encode(encoding))
    assert result.status_code == 201
    assert client.get(f"/api/documents/{result.json()['id']}").json()["chunks"][0]["content"] == "中文学习资料"
