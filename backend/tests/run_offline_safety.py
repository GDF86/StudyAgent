"""Offline-only test launcher: never reads .env or permits external HTTP transport."""
import ast
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.chdir(root)
tree = ast.parse((root / "app/core/config.py").read_text(encoding="utf-8"))
settings = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
for node in settings.body:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        os.environ.pop(node.target.id.upper(), None)
# Disable dotenv source BEFORE pytest imports app.main at collection time.
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_files = lambda self: {}
import httpx
import json
_original_sync = httpx.HTTPTransport.handle_request
_original_async = httpx.AsyncHTTPTransport.handle_async_request
httpx._studyagent_offline_proxy_ports = set()

def allowed_local_mock(transport, request):
    # Only test-registered ephemeral localhost proxies are allowed, never port 7897.
    proxy = getattr(getattr(transport,"_pool",None),"_proxy_url",None)
    if proxy is not None:
        return (proxy.host in (b"127.0.0.1",b"::1")
                and proxy.port in httpx._studyagent_offline_proxy_ports
                and request.url.host in ("127.0.0.1","offline.invalid"))
    if request.url.host == "127.0.0.1":
        try:return json.loads(request.content).get("model") == "unit-test-model"
        except (ValueError,TypeError):return False
    return False

def reject(self, request):
    if allowed_local_mock(self,request):return _original_sync(self,request)
    raise RuntimeError("OFFLINE_TEST_REAL_HTTP_FORBIDDEN")
async def reject_async(self, request):
    if allowed_local_mock(self,request):return await _original_async(self,request)
    raise RuntimeError("OFFLINE_TEST_REAL_HTTP_FORBIDDEN")
httpx.HTTPTransport.handle_request = reject
httpx.AsyncHTTPTransport.handle_async_request = reject_async
import pytest
if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:] or ["tests/test_live_test_safety.py", "-q"]))
