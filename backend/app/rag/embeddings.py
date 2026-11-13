from typing import Protocol
from urllib.parse import urlsplit
from ipaddress import ip_address
import math

import httpx

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.live_test import GuardSync


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class CompatibleEmbeddingProvider:
    """Call an OpenAI-compatible embeddings endpoint; no fake/local fallback."""

    def __init__(self, settings: Settings, transport=None):
        self.settings, self.transport = settings, transport

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        config = self.settings
        url = config.embedding_base_url.rstrip("/")
        if not url or not config.embedding_model or urlsplit(url).scheme not in {"https", "http"}:
            raise DomainError(503, "EMBEDDING_NOT_CONFIGURED", "请在后端 .env 配置 Embedding 服务地址和模型")
        headers = {}
        if config.embedding_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + config.embedding_api_key.get_secret_value()
        vectors: list[list[float]] = []
        dimension = None
        hostname = urlsplit(url).hostname or ""
        try:
            local = ip_address(hostname).is_loopback
        except ValueError:
            local = hostname.lower() == "localhost"
        try:
            with httpx.Client(timeout=config.embedding_timeout_seconds, transport=GuardSync(self.settings, "embedding", self.transport), follow_redirects=False, trust_env=False) as client:
                for start in range(0, len(texts), config.embedding_batch_size):
                    batch = texts[start:start + config.embedding_batch_size]
                    response = client.post(url + "/embeddings", headers=headers, json={"model": config.embedding_model, "input": batch, "encoding_format": "float"})
                    if response.status_code == 429:
                        raise DomainError(429, "EMBEDDING_RATE_LIMITED", "Embedding 服务限流，请稍后重新上传")
                    if not response.is_success:
                        raise DomainError(502, "EMBEDDING_FAILED", "Embedding 服务请求失败，请检查服务配置")
                    data = sorted(response.json()["data"], key=lambda row: row["index"])
                    if any(type(row["index"]) is not int for row in data) or [row["index"] for row in data] != list(range(len(batch))):
                        raise ValueError("invalid count/index")
                    for row in data:
                        raw = row["embedding"]
                        if not isinstance(raw, list) or not raw or any(type(n) not in (int, float) for n in raw):
                            raise ValueError("invalid vector")
                        vector = [float(n) for n in raw]
                        if any(not math.isfinite(n) or abs(n) > 1e20 for n in vector) or not any(vector):
                            raise ValueError("invalid vector values")
                        dimension = dimension or len(vector)
                        if len(vector) != dimension:
                            raise ValueError("inconsistent dimensions")
                        vectors.append(vector)
        except DomainError:
            raise
        except httpx.TimeoutException:
            raise DomainError(502, "EMBEDDING_TIMEOUT", "Embedding 服务超时，请稍后重新上传") from None
        except Exception:
            raise DomainError(502, "EMBEDDING_INVALID_RESPONSE", "Embedding 服务不可用或返回了无效向量") from None
        return vectors
