import asyncio,json
import httpx,pytest
from test_development_budget import dev
from test_live_test_safety import authorize,usage
from test_rag_live_round import config
from remaining_tools_live import check,TOOLS,QUESTIONS,SECOND
from rag_live_round import DOCUMENT
from app.core.live_test import Stage

@pytest.mark.parametrize('name',TOOLS)
def test_four_real_tool_implementations_mock_http(tmp_path,dev,name):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.llm.provider import CompatibleLLMProvider
    dev.halt();ledger=authorize(dev.begin_development_session(grant_id=name,allowed_tools=[name],allow_embedding=True,max_requests=12))
    cfg=config(tmp_path,ledger).model_copy(update={'agent_max_rounds':2,'agent_max_tool_calls':1})
    ids=[];calls=[]
    text='第一篇 SA-2026、深圳、RAG-1024；第二篇 SA-2027、广州、RAG-2048。共同支持文档检索和知识库问答；第二篇新增学习进度分析。[1][2]'
    if name!='compare_documents':text='第一篇资料的项目代号是 SA-2026，城市是深圳，编号是 RAG-1024，支持文档检索、知识库问答和学习辅助。[1]'
    quiz={'questions':[{'question':'第一篇项目代号？','options':['SA-2026','A','B','C'],'answer':'SA-2026','explanation':'资料明确说明。','citation_ids':[1]},{'question':'第一篇城市？','options':['深圳','A','B','C'],'answer':'深圳','explanation':'资料明确说明。','citation_ids':[1]}]}
    def reply(req):
        p=json.loads(req.content);calls.append(p)
        if 'input' in p:return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0]+[0.0]*1023}],'usage':usage('embedding')})
        if 'tools' in p:
            if p['messages'][-1]['role']=='tool':msg={'role':'assistant','content':text+' 两道题答案分别是 SA-2026 和深圳。[1]'};finish='stop'
            else:
                args={'search_documents':{'query':'项目代号'},'summarize_document':{'document_id':ids[0]},'compare_documents':{'document_ids':ids},'generate_quiz':{'topic':'StudyAgent测试资料','count':2,'question_type':'multiple_choice'}}[name]
                msg={'role':'assistant','content':None,'tool_calls':[{'id':'tool1','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]};finish='tool_calls'
        else:msg={'role':'assistant','content':json.dumps(quiz) if name=='generate_quiz' else text};finish='stop'
        return httpx.Response(200,json={'choices':[{'message':msg,'finish_reason':finish}],'usage':usage('llm')})
    with TestClient(create_app(cfg)) as client:
        client.app.state.documents.embeddings=CompatibleEmbeddingProvider(cfg,httpx.MockTransport(reply))
        client.app.state.agent.provider=CompatibleLLMProvider(cfg,httpx.MockTransport(reply))
        kb=client.post('/api/knowledge-bases',json={'name':'test-tools'}).json()['id']
        for i,body in enumerate([DOCUMENT,SECOND] if name=='compare_documents' else [DOCUMENT]):
            with Stage(ledger,'ingestion') as scope:
                r=client.post(f'/api/knowledge-bases/{kb}/documents',files={'file':(f'fixture{i}.txt',body.encode(),'text/plain')})
                assert r.status_code==201;ids.append(r.json()['id']);scope.verify(True)
        result=asyncio.run(check(client.app.state.agent,kb,ledger,name,QUESTIONS[TOOLS.index(name)],ids,offline=True))
    assert result['status']=='PASS',result
    assert result['executed'] and result['returned']
