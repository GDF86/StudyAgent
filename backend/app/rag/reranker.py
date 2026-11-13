"""Explicit HTTP rerank protocol; never substitute synthetic scores in production."""
import math
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.live_test import GuardSync
from app.schemas.chat import Citation


class Reranker:
    def __init__(self, settings: Settings, transport=None):
        self.settings, self.transport = settings, transport

    def rank(self, question: str, candidates: list[Citation], top_k: int) -> list[Citation]:
        """Validate a complete permutation, stably rank, and preserve vector scores/sources."""
        if not candidates:
            return []
        config = self.settings
        url = config.reranker_base_url.rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not config.reranker_model:
            raise DomainError(503, "RERANKER_NOT_CONFIGURED", "请配置重排服务地址和模型")
        if len(question) + sum(len(c.content) for c in candidates) > config.reranker_max_input_chars:
            raise DomainError(413, "RERANKER_INPUT_TOO_LARGE", "重排候选原文超过输入限制")
        try:
            local = ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname.lower() == "localhost"
        headers = {}
        if config.reranker_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + config.reranker_api_key.get_secret_value()
        try:
            with httpx.Client(timeout=config.reranker_timeout_seconds, transport=GuardSync(self.settings, "reranker", self.transport), follow_redirects=False, trust_env=False) as client:
                response = client.post(url + "/rerank", headers=headers, json={
                    "model": config.reranker_model, "query": question,
                    "documents": [c.content for c in candidates], "top_n": len(candidates)})
            if response.status_code == 429:
                raise DomainError(429, "RERANKER_RATE_LIMITED", "重排服务限流，请稍后重试")
            if not response.is_success:
                raise DomainError(502, "RERANKER_FAILED", "重排服务请求失败")
            rows = response.json()["results"]
            if not isinstance(rows, list) or len(rows) != len(candidates):
                raise ValueError("incomplete permutation")
            scores = {}
            for row in rows:
                index, score = row["index"], row["relevance_score"]
                if type(index) is not int or not 0 <= index < len(candidates) or index in scores:
                    raise ValueError("invalid index")
                if type(score) not in (int, float) or not math.isfinite(score):
                    raise ValueError("invalid score")
                scores[index] = score
            order = sorted(scores, key=lambda i: (-scores[i], i))[:top_k]
            return [candidates[i].model_copy(deep=True) for i in order]
        except DomainError:
            raise
        except httpx.TimeoutException:
            raise DomainError(502, "RERANKER_TIMEOUT", "重排服务超时") from None
        except Exception:
            raise DomainError(502, "RERANKER_INVALID_RESPONSE", "重排服务不可用或返回无效排序") from None
