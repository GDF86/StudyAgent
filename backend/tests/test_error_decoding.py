"""All bodies and credentials here are generated offline fixtures."""
import gzip
import json
import zlib
import asyncio
import httpx
import pytest
from app.core.live_test import Blocked,Stage,parse_http_error,decode_captured_body
from app.rag.embeddings import CompatibleEmbeddingProvider
from app.llm.provider import CompatibleLLMProvider
from test_live_test_safety import authorize,provision,settings,send,complete_until,usage

BODY={'error':{'code':'AccessDenied.Unpurchased','message':'Permission denied for fixture.'},'request_id':'req-compressed-fixture'}

def compress(body,coding):
    if coding=='gzip':return gzip.compress(body)
    if coding=='deflate':return zlib.compress(body)
    encoder=zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return encoder.compress(body)+encoder.flush()

@pytest.mark.parametrize('coding',['gzip','deflate','raw-deflate'])
def test_compressed_transport_403_preserves_fields(tmp_path,coding):
    ledger=authorize(provision(tmp_path/'budget.sqlite'))
    wire=compress(json.dumps(BODY).encode(),coding)
    encoding='deflate' if coding=='raw-deflate' else coding
    def reply(req):return httpx.Response(403,headers={'content-type':'application/json','content-encoding':encoding},stream=httpx.ByteStream(wire))
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            CompatibleEmbeddingProvider(settings(ledger),httpx.MockTransport(reply)).embed_texts(['fixture'])
    row=ledger.report()['requests'][0]
    assert row['http_status']==403 and row['error_type']=='HTTP_403'
    assert row['server_error']['code']=='AccessDenied.Unpurchased'
    assert row['server_error']['message']=='Permission denied for fixture.'
    assert row['server_error']['request_id']=='req-compressed-fixture'


def test_predecoded_mock_response_not_decompressed_twice(tmp_path):
    ledger=authorize(provision(tmp_path/'budget.sqlite'))
    def reply(req):return httpx.Response(403,headers={'content-type':'application/json','content-encoding':'gzip'},content=gzip.compress(json.dumps(BODY).encode()))
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):send(ledger,'ingestion','embedding',httpx.MockTransport(reply))
    assert ledger.report()['requests'][0]['server_error']['code']=='AccessDenied.Unpurchased'

@pytest.mark.parametrize('body,expected',[({'error':'Forbidden'},'Forbidden'),({'detail':'Access denied'},'Access denied'),({'message':'Denied'},'Denied'),({'error':{'message':'Denied'}},'Denied')])
def test_json_error_variants(body,expected):
    result=parse_http_error(json.dumps(body).encode(),403,{'content-type':'application/json'})
    assert result['message']==expected and 'Non-JSON' not in result['message']


def test_malformed_json_is_not_claimed_to_be_nonjson():
    result=parse_http_error(b'{broken',403,{'content-type':'application/json'})
    assert 'declared JSON' in result['message'] and result['code']=='HTTP_403'

@pytest.mark.parametrize('body',[b'',b'<html>Do not save me</html>',b'null',b'["private fixture"]'])
def test_unexpected_error_bodies_never_saved(body):
    result=parse_http_error(body,403,{'content-type':'application/json','x-request-id':'req-header'})
    assert result['request_id']=='req-header' and result['code']=='HTTP_403'
    assert 'Do not save me' not in json.dumps(result) and 'private fixture' not in json.dumps(result)


def test_compressed_credential_redaction():
    body={'error':{'code':'Forbidden','message':'Bearer sk-offline-fixture-secret api_key=sk-offline-fixture-secret'}}
    decoded=decode_captured_body(gzip.compress(json.dumps(body).encode()),'gzip')
    result=parse_http_error(decoded,403,{'content-type':'application/json'})
    assert 'sk-offline-fixture-secret' not in json.dumps(result)

@pytest.mark.parametrize('coding,body',[('gzip',b'not gzip'),('deflate',b'broken'),('br',b'unsupported')])
def test_invalid_encoding_fails_closed(coding,body):
    with pytest.raises(ValueError):decode_captured_body(body,coding)


def test_decompression_bomb_is_bounded():
    with pytest.raises(ValueError,match='DECODED_RESPONSE_TOO_LARGE'):
        decode_captured_body(gzip.compress(b'x'*2000001),'gzip')


def test_compressed_success_usage_parsed(tmp_path):
    ledger=authorize(provision(tmp_path/'usage.sqlite'))
    body={'data':[{'index':0,'embedding':[1.0,0.0]}],'usage':{'total_tokens':8}}
    def reply(req):return httpx.Response(200,headers={'content-type':'application/json','content-encoding':'gzip'},stream=httpx.ByteStream(gzip.compress(json.dumps(body).encode())))
    with Stage(ledger,'ingestion') as scope:
        vectors=CompatibleEmbeddingProvider(settings(ledger),httpx.MockTransport(reply)).embed_texts(['fixture'])
        scope.verify(vectors==[[1.0,0.0]])
    assert ledger.report()['requests'][0]['input_tokens']==8


def test_async_compressed_error_same_fields(tmp_path):
    ledger=authorize(provision(tmp_path/'async.sqlite'))
    complete_until(ledger,'rag')
    def reply(req):return httpx.Response(403,headers={'content-type':'application/json','content-encoding':'gzip'},stream=httpx.ByteStream(gzip.compress(json.dumps(BODY).encode())))
    with pytest.raises(Blocked):
        with Stage(ledger,'rag'):
            asyncio.run(CompatibleLLMProvider(settings(ledger),httpx.MockTransport(reply)).complete([{'role':'user','content':'fixture'}]))
    row=ledger.report()['requests'][-1]
    assert row['error_type']=='HTTP_403' and row['server_error']['code']=='AccessDenied.Unpurchased'
