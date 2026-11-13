"""Explicit model proxy configuration. Never inherit environment proxy settings."""
from urllib.parse import urlsplit, unquote
import httpx
from app.core.errors import DomainError


def explicit_proxy(settings, kind):
    if kind not in ("llm", "embedding"):
        return None
    secret = getattr(settings, "model_http_proxy", None)
    raw = secret.get_secret_value() if hasattr(secret, "get_secret_value") else (secret or "")
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c)<32 for c in raw)):
            raise ValueError()
        _ = parsed.port
        httpx.URL(raw)
    except Exception:
        raise DomainError(503, "MODEL_PROXY_INVALID", "模型显式代理配置无效，仅支持 HTTP/HTTPS 代理地址") from None
    return raw


def credential_redactions(settings, kind):
    raw = explicit_proxy(settings, kind)
    if not raw:
        return ()
    parsed = urlsplit(raw)
    return tuple(x for raw_part in (parsed.username,parsed.password) if raw_part
                 for x in (raw_part,unquote(raw_part)))


def transport(settings, kind, *, asynchronous=False):
    proxy = explicit_proxy(settings,kind)
    try:
        cls = httpx.AsyncHTTPTransport if asynchronous else httpx.HTTPTransport
        return cls(proxy=proxy, retries=0, trust_env=False)
    except Exception:
        raise DomainError(503,"MODEL_PROXY_INVALID","无法初始化模型HTTP传输，请检查显式代理配置") from None


# Optional diagnostic observer. It never stores request objects, bodies or headers.
from contextvars import ContextVar
PROXY_OBSERVER = ContextVar("model_proxy_observer", default=None)

class ProxyObservation:
    def __init__(self, expected_proxy="http://127.0.0.1:7897"):
        self.expected_proxy=expected_proxy
        self.method=None
        self.data={"explicit_proxy_configured":False,"connected_to_expected_proxy":False,
                   "connect_status":None,"tls_completed":False,"tls_failed":False,
                   "http_status":None,"response_headers":{},"failure_type":None}
    def __enter__(self):
        self.token=PROXY_OBSERVER.set(self)
        return self
    def __exit__(self,*args):PROXY_OBSERVER.reset(self.token)
    def attach(self, request, settings, kind):
        if explicit_proxy(settings,kind)!=self.expected_proxy:
            raise DomainError(503,"PROXY_ROUTE_MISMATCH","诊断请求显式代理与授权地址不一致")
        self.data["explicit_proxy_configured"]=bool(self.expected_proxy)
        request.extensions["trace"]=self.trace
    def trace(self,name,info):
        # Never stringify info: it can contain Authorization or a request body.
        if name.endswith("connect_tcp.started"):
            host=info.get("host")
            if isinstance(host,bytes):host=host.decode("ascii",errors="ignore")
            expected=urlsplit(self.expected_proxy) if self.expected_proxy else None
            self._expected_tcp=bool(expected and host==expected.hostname and info.get("port")==expected.port)
        elif name.endswith("connect_tcp.complete"):
            self.data["connected_to_expected_proxy"]=getattr(self,"_expected_tcp",False)
        elif name.endswith("send_request_headers.started"):
            method=getattr(info.get("request"),"method",None)
            self.method=method.decode("ascii",errors="ignore") if isinstance(method,bytes) else method
        elif name.endswith("receive_response_headers.complete") and self.method=="CONNECT":
            result=info.get("return_value")
            if isinstance(result,tuple) and len(result)>1:self.data["connect_status"]=result[1]
        elif name.endswith("start_tls.complete"):
            self.data["tls_completed"]=True
        elif name.endswith("start_tls.failed"):
            self.data["tls_failed"]=True
    def response(self,response):
        from app.core.live_test import sanitize_error_text
        self.data["http_status"]=response.status_code
        self.data["response_headers"]={name:sanitize_error_text(response.headers[name])
            for name in ("content-type","content-encoding","server","via","x-cache","x-request-id","x-acs-request-id","x-dashscope-request-id") if name in response.headers}
    def failure(self,exception):self.data["failure_type"]=type(exception).__name__
    def report(self):
        data=dict(self.data)
        data["proxy_path_confirmed"]=(data["explicit_proxy_configured"] and data["connected_to_expected_proxy"]
                                       and data["connect_status"]==200 and data["tls_completed"])
        if data["connect_status"] is not None and data["connect_status"]!=200:
            data["failure_layer"]="PROXY_CONNECT_REJECTED"
        elif data["tls_failed"]:data["failure_layer"]="TLS"
        elif data["http_status"] is not None:
            data["failure_layer"]="NONE" if data["http_status"]==200 else ("HTTP_AFTER_TUNNEL_ORIGIN_UNKNOWN" if self.expected_proxy else "HTTP_ORIGIN_UNKNOWN")
        else:data["failure_layer"]="UNKNOWN"
        return data
