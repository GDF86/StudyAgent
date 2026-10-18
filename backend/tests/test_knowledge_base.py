import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

API = "/api/knowledge-bases"


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", data_dir=tmp_path / "data", _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_crud(client):
    assert client.get(API).json() == []
    created = client.post(API, json={"name": "  机器学习  ", "description": "课程笔记"})
    assert created.status_code == 201
    item = created.json()
    assert item["name"] == "机器学习"
    assert item["created_at"].endswith("+00:00")
    url = f"{API}/{item['id']}"
    assert client.get(url).json() == item
    assert client.get(API).json() == [item]
    updated = client.put(url, json={"name": "深度学习", "description": "新描述"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "深度学习"
    assert updated.json()["description"] == "新描述"
    assert updated.json()["created_at"] == item["created_at"]
    assert updated.json()["updated_at"] >= item["updated_at"]
    deleted = client.delete(url)
    assert deleted.status_code == 204 and deleted.content == b""
    assert client.get(url).status_code == 404
    assert client.get(API).json() == []


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "ok", "description": "x" * 2001}, {"name": None}, {}, {"name": "ok", "unexpected": 1}])
def test_invalid_create(client, body):
    response = client.post(API, json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert client.get(API).json() == []


def test_duplicate_and_rollback(client):
    first = client.post(API, json={"name": "课程"}).json()
    second = client.post(API, json={"name": "研究"}).json()
    assert client.post(API, json={"name": "  课程  "}).status_code == 409
    url = f"{API}/{second['id']}"
    assert client.put(url, json={"name": first["name"]}).status_code == 409
    assert client.get(url).json()["name"] == "研究"
    assert client.put(url, json={"name": "研究", "description": "同名自身可修改"}).status_code == 200


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_missing(client, method):
    kwargs = {"json": {"name": "不存在"}} if method == "put" else {}
    response = getattr(client, method)(f"{API}/999", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "知识库不存在", "detail": None}


def test_invalid_update_preserves_data(client):
    item = client.post(API, json={"name": "保留"}).json()
    url = f"{API}/{item['id']}"
    assert client.put(url, json={"name": " "}).status_code == 422
    assert client.get(url).json() == item


def test_cors(client):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    response = client.options(API, headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    headers["Origin"] = "https://untrusted.example"
    assert client.options(API, headers=headers).status_code == 400


def test_persistence(settings):
    with TestClient(create_app(settings)) as client:
        item = client.post(API, json={"name": "重启后仍在"}).json()
    with TestClient(create_app(settings)) as client:
        assert client.get(f"{API}/{item['id']}").json() == item
