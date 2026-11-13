"""Proxy tests use only fake HTTP servers on ephemeral loopback ports."""
import asyncio
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from app.core.model_proxy import explicit_proxy, transport
from app.core.live_test import GuardSync, GuardAsync, Stage, Blocked, parse_http_error
from app.core.errors import DomainError
from test_live_test_safety import authorize,provision,settings,send,response,payload

@pytest.mark.parametrize('value',["http://127.0.0.1:7897","https://127.0.0.1:8888","http://user:password@127.0.0.1:7897"])
def test_proxy_valid(value):
    cfg=SimpleNamespace(model_http_proxy=SecretStr(value))
    assert explicit_proxy(cfg,'embedding')==value
    assert explicit_proxy(cfg,'llm')==value
    assert explicit_proxy(cfg,'reranker') is None
    assert 'password' not in repr(cfg)

@pytest.mark.parametrize('value',["socks5://127.0.0.1:7897","http://127.0.0.1:bad","http://127.0.0.1:7897/path","http://127.0.0.1:7897?secret=x","http://127.0.0.1:7897#frag","http://bad secret@host"])
def test_proxy_invalid_sanitized(value):
    with pytest.raises(DomainError) as exc:
        explicit_proxy(SimpleNamespace(model_http_proxy=SecretStr(value)),'embedding')
    assert exc.value.code=='MODEL_PROXY_INVALID'
    assert value not in str(exc.value)

def test_proxy_default_direct_no_environment(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:9')
    calls=[]
    monkeypatch.setattr(httpx,'HTTPTransport',lambda **kw:calls.append(kw))
    transport(SimpleNamespace(),'embedding')
    assert calls==[{'proxy':None,'retries':0,'trust_env':False}]

def test_explicit_proxy_in_async_transport(monkeypatch):
    calls=[]
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:calls.append(kw))
    transport(SimpleNamespace(model_http_proxy=SecretStr('http://127.0.0.1:7897')),'llm',asynchronous=True)
    assert calls==[{'proxy':'http://127.0.0.1:7897','retries':0,'trust_env':False}]

@contextmanager
def fake_proxy(status=200):
    events=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            # Deliberately do not record any incoming credentials or headers.
            events.append({'method':'POST','path':self.path,'model':data.get('model')})
            body=json.dumps({'data':[{'index':0,'embedding':[1,2,3]}],'usage':{'total_tokens':5}}).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_CONNECT(self):
            # Never forward or resolve the destination. Return a fake proxy rejection.
            events.append({'method':'CONNECT','path':self.path})
            self.send_response(407);self.send_header('Content-Length','0');self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    port=server.server_port
    allowed=getattr(httpx,'_studyagent_offline_proxy_ports',None)
    assert allowed is not None,'Use tests/run_offline_safety.py for this offline test suite'
    allowed.add(port)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield 'http://127.0.0.1:'+str(port),events
    finally:
        server.shutdown();worker.join();server.server_close();allowed.remove(port)

@pytest.mark.parametrize('asynchronous',[False,True])
def test_http_proxy_routes_locally_without_target_connection(asynchronous):
    with fake_proxy() as (url,events):
        cfg=SimpleNamespace(model_http_proxy=SecretStr(url),llm_model='unit-test-model',embedding_model='unit-test-model',live_test_mode=False)
        data={'model':'unit-test-model','input':['offline fixture']}
        if asynchronous:
            async def run():
                async with httpx.AsyncClient(transport=GuardAsync(cfg,'llm',None),trust_env=False) as client:
                    return await client.post('http://127.0.0.1:1/v1/chat/completions',json=data)
            result=asyncio.run(run())
        else:
            with httpx.Client(transport=GuardSync(cfg,'embedding',None),trust_env=False) as client:
                result=client.post('http://127.0.0.1:1/v1/embeddings',json=data)
        assert result.status_code==200
        assert len(events)==1 and events[0]['path'].startswith('http://127.0.0.1:1/')


def test_mock_mode_ignores_proxy_and_budget_still_applies(tmp_path):
    ledger=authorize(provision(tmp_path/'proxy-mock.sqlite'))
    cfg=settings(ledger,model_http_proxy=SecretStr('socks5://invalid-proxy'))
    seen=[]
    def reply(req):seen.append(1);return response(req)
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            with httpx.Client(transport=GuardSync(cfg,'embedding',httpx.MockTransport(reply)),trust_env=False) as client:
                client.post(cfg.embedding_base_url+'/embeddings',json=payload('embedding','ingestion'))
                client.post(cfg.embedding_base_url+'/embeddings',json=payload('embedding','ingestion'))
    assert seen==[1] and ledger.report()['used']==1


def test_https_connect_proxy_failure_consumes_budget_once(tmp_path):
    ledger=authorize(provision(tmp_path/'connect-fixture.sqlite',offline=False))
    with fake_proxy() as (url,events):
        cfg=settings(ledger,model_http_proxy=SecretStr(url))
        with pytest.raises(Blocked):
            with Stage(ledger,'ingestion',offline=False):
                with httpx.Client(transport=GuardSync(cfg,'embedding',None),trust_env=False) as client:
                    client.post('https://offline.invalid/v1/embeddings',json=payload('embedding','ingestion'))
        assert events==[{'method':'CONNECT','path':'offline.invalid:443'}]
    report=ledger.report()
    assert report['used']==1 and report['state']=='HALTED'
    assert report['requests'][0]['error_type']=='ProxyError'


def test_real_key_and_proxy_without_authorization_cannot_reach_proxy(tmp_path):
    ledger=authorize(provision(tmp_path/'unauthorized.sqlite'))
    with fake_proxy() as (url,events):
        cfg=settings(ledger,model_http_proxy=SecretStr(url))
        with httpx.Client(transport=GuardSync(cfg,'embedding',None),trust_env=False) as client:
            with pytest.raises(Blocked):client.post('https://offline.invalid/v1/embeddings',json=payload('embedding','ingestion'))
        assert events==[] and ledger.report()['used']==0


def test_proxy_credentials_redacted_from_server_errors():
    from app.core.model_proxy import credential_redactions
    cfg=SimpleNamespace(model_http_proxy=SecretStr('http://fixtureuser:fixturepassword@127.0.0.1:7897'))
    body=json.dumps({'error':{'code':'ProxyDenied','message':'fixtureuser:fixturepassword rejected'},'request_id':'fixturepassword'}).encode()
    result=parse_http_error(body,407,{},credential_redactions(cfg,'embedding'))
    assert 'fixtureuser' not in json.dumps(result) and 'fixturepassword' not in json.dumps(result)
    assert result['request_id'] is None


def test_observer_does_not_capture_secrets():
    from app.core.model_proxy import ProxyObservation
    from types import SimpleNamespace
    probe=ProxyObservation()
    probe.attach(httpx.Request("POST","https://offline.invalid/v1/embeddings"),SimpleNamespace(model_http_proxy=SecretStr("http://127.0.0.1:7897")),"embedding")
    probe.trace('connection.connect_tcp.started',{'host':'127.0.0.1','port':7897,'password':'NEVER_PERSIST'})
    probe.trace('connection.connect_tcp.complete',{'return_value':object()})
    probe.trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'CONNECT',headers={'Authorization':'NEVER_PERSIST'})})
    probe.trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',200,b'OK',[])})
    probe.trace('proxy.start_tls.complete',{})
    probe.response(httpx.Response(403,headers={'content-type':'application/json','content-encoding':'gzip','server':'fixture','set-cookie':'NEVER_PERSIST'}))
    result=probe.report()
    assert result['proxy_path_confirmed'] and result['failure_layer']=='HTTP_AFTER_TUNNEL_ORIGIN_UNKNOWN'
    assert 'NEVER_PERSIST' not in json.dumps(result)
    assert result['response_headers']['content-encoding']=='gzip'


def test_observer_connect_failure_local_mock_only(tmp_path):
    from app.core.model_proxy import ProxyObservation
    ledger=authorize(provision(tmp_path/'observation.sqlite',offline=False))
    with fake_proxy() as (url,events):
        cfg=settings(ledger,model_http_proxy=SecretStr(url))
        with ProxyObservation(url) as observer:
            with pytest.raises(Blocked):
                with Stage(ledger,'ingestion',offline=False):
                    with httpx.Client(transport=GuardSync(cfg,'embedding',None),trust_env=False) as client:
                        client.post('https://offline.invalid/v1/embeddings',json=payload('embedding','ingestion'))
        assert observer.report()['connected_to_expected_proxy']
        assert observer.report()['connect_status']==407
        assert observer.report()['failure_layer']=='PROXY_CONNECT_REJECTED'
        assert len(events)==1 and ledger.report()['used']==1


def test_observer_optional_proxy_does_not_switch_direct_path():
    from app.core.model_proxy import ProxyObservation
    probe=ProxyObservation(None)
    probe.attach(httpx.Request('POST','https://offline.invalid/v1/embeddings'),SimpleNamespace(),'embedding')
    probe.trace('connection.connect_tcp.started',{'host':'offline.invalid','port':443})
    probe.trace('connection.start_tls.complete',{})
    probe.response(httpx.Response(403,headers={'content-type':'application/json'}))
    result=probe.report()
    assert not result['explicit_proxy_configured'] and not result['proxy_path_confirmed']
    assert result['failure_layer']=='HTTP_ORIGIN_UNKNOWN'
