"""Step 2A only: real SQLite/API; no Chat/Agent integration or model requests."""
from datetime import datetime
import json
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.database.models import Chunk, Conversation, Document, KnowledgeBase, Message
from app.main import create_app
from app.schemas.conversation import MessageWrite

API = '/api/conversations'


@pytest.fixture
def rig(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'history.db').as_posix()}", data_dir=tmp_path/'data', _env_file=None)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        first = client.post('/api/knowledge-bases', json={'name':'当前库'}).json()['id']
        other = client.post('/api/knowledge-bases', json={'name':'其他库'}).json()['id']
        yield client, first, other


def create(rig, mode='chat', title='测试会话', kb=None):
    response=rig[0].post(API,json={'knowledge_base_id':rig[1] if kb is None else kb,'title':title,'mode':mode})
    assert response.status_code==201, response.text
    return response.json()


def service(rig): return rig[0].app.state.conversations


def save(rig, conversation, role='user', content='问题', turn_no=1, attempt_no=1, **kwargs):
    return service(rig).save_message(UUID(conversation['id']), conversation['knowledge_base_id'],
        MessageWrite(role=role,content=content,turn_no=turn_no,attempt_no=attempt_no,**kwargs))


def source(rig,kb=None):
    doc_id, chunk_id = str(uuid4()), str(uuid4())
    with Session(rig[0].app.state.engine) as session, session.begin():
        session.add(Document(id=doc_id,knowledge_base_id=rig[1] if kb is None else kb,filename='课程.pdf',file_type='.pdf',file_size=10,
            content_hash=doc_id,status='ready',chunk_count=1,embedding_model='test-only',embedding_dimension=3))
        session.flush()
        session.add(Chunk(id=chunk_id,document_id=doc_id,ordinal=0,page=2,content='真实原文快照',vector_id=doc_id+':0'))
    return {'id':1,'knowledge_base_id':rig[1] if kb is None else kb,'document_id':doc_id,'document_name':'课程.pdf','page':2,
            'chunk_id':chunk_id,'content':'真实原文快照','score':0.8}


def count(rig,model):
    with Session(rig[0].app.state.engine) as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize('mode',['chat','agent'])
def test_crud_and_reserved_fields(rig,mode):
    client=rig[0]
    item=create(rig,mode,'  学习会话  ')
    assert str(UUID(item['id']))==item['id']
    assert item['title']=='学习会话' and item['mode']==mode
    assert item['summary']=='' and item['summary_through_turn']==0 and item['revision']==0
    assert item['created_at'].endswith('+00:00')
    url=f"{API}/{item['id']}"
    assert client.get(url).json()==item
    page=client.get(API).json()
    assert page=={'items':[item],'total':1,'limit':20,'offset':0}
    result=client.put(url,json={'title':'新的标题'}).json()
    assert result['title']=='新的标题' and result['revision']==1
    assert result['created_at']==item['created_at'] and result['updated_at']>=item['updated_at']
    assert result['knowledge_base_id']==item['knowledge_base_id'] and result['mode']==mode
    response=client.delete(url)
    assert response.status_code==204 and response.content==b''
    assert client.get(url).status_code==404 and client.get(API).json()['total']==0


@pytest.mark.parametrize('override',[
    {'mode':'other'},{'mode':None},{'title':''},{'title':'  '},{'title':None},{'title':'x'*101},
    {'knowledge_base_id':0},{'knowledge_base_id':True},{'knowledge_base_id':'1'},{'knowledge_base_id':None},
    {'summary':'forged'},{'unexpected':1},
])
def test_invalid_create(rig,override):
    body={'knowledge_base_id':rig[1],'title':'t','mode':'chat',**override}
    assert rig[0].post(API,json=body).status_code==422
    assert count(rig,Conversation)==0


def test_missing_fields_and_kb(rig):
    assert rig[0].post(API,json={}).status_code==422
    assert rig[0].post(API,json={'knowledge_base_id':99999,'title':'x','mode':'chat'}).status_code==404
    assert rig[0].get(API,params={'knowledge_base_id':99999}).status_code==404
    assert count(rig,Conversation)==0


@pytest.mark.parametrize('method',['get','put','delete'])
def test_missing_and_bad_id(rig,method):
    extra={'json':{'title':'x'}} if method=='put' else {}
    assert getattr(rig[0],method)(f'{API}/{uuid4()}',**extra).status_code==404
    assert getattr(rig[0],method)(f'{API}/not-a-uuid',**extra).status_code==422
    assert rig[0].get(f'{API}/{uuid4()}/messages').status_code==404


@pytest.mark.parametrize('body',[{'title':''},{'title':'  '},{'title':None},{'title':'x'*101},
    {'title':'x','mode':'agent'},{'title':'x','knowledge_base_id':2},{'title':'x','summary':'fake'},{}])
def test_rename_only_title(rig,body):
    item=create(rig)
    assert rig[0].put(f"{API}/{item['id']}",json=body).status_code==422
    assert rig[0].get(f"{API}/{item['id']}").json()==item


@pytest.mark.parametrize('operation',['get','put','delete','messages'])
def test_scoped_access_cannot_cross_kb(rig,operation):
    item=create(rig)
    url=f"{API}/{item['id']}"+('/messages' if operation=='messages' else '')
    method='get' if operation=='messages' else operation
    extra={'json':{'title':'x'}} if method=='put' else {}
    response=getattr(rig[0],method)(url,params={'knowledge_base_id':rig[2]},**extra)
    assert response.status_code==404
    assert rig[0].get(f"{API}/{item['id']}").json()==item
    with pytest.raises(DomainError) as error:
        service(rig).save_message(UUID(item['id']),rig[2],MessageWrite(role='user',content='x',turn_no=1))
    assert error.value.code=='CONVERSATION_NOT_FOUND'


def test_filter_pagination_stable_ties_and_rename(rig):
    a=create(rig,'chat','A'); b=create(rig,'chat','B'); c=create(rig,'agent','C'); d=create(rig,'chat','D',rig[2])
    with Session(rig[0].app.state.engine) as session,session.begin():
        session.execute(update(Conversation).values(updated_at=datetime(2020,1,1)))
    ids=sorted([x['id'] for x in (a,b,c,d)],reverse=True)
    first=rig[0].get(API,params={'limit':2}).json()
    second=rig[0].get(API,params={'limit':2,'offset':2}).json()
    assert first['total']==second['total']==4
    assert [x['id'] for x in first['items']+second['items']]==ids
    assert rig[0].get(API,params={'limit':2}).json()==first
    filtered=rig[0].get(API,params={'knowledge_base_id':rig[1],'mode':'chat'}).json()
    assert filtered['total']==2 and {x['id'] for x in filtered['items']}=={a['id'],b['id']}
    assert rig[0].get(API,params={'offset':100}).json()['items']==[]
    rig[0].put(f"{API}/{a['id']}",json={'title':'renamed'})
    assert rig[0].get(API).json()['items'][0]['id']==a['id']


@pytest.mark.parametrize('params',[{'limit':0},{'limit':101},{'offset':-1},{'offset':1000001},{'limit':'abc'},
    {'mode':'bad'},{'knowledge_base_id':0},{'unknown':1}])
def test_query_validation(rig,params):
    assert rig[0].get(API,params=params).status_code==422


def test_message_order_attempts_pagination_and_metadata(rig):
    item=create(rig)
    request_id=uuid4()
    save(rig,item,role='assistant',content='旧回答',turn_no=2,request_id=request_id,result_metadata={'chat_status':'answered'})
    save(rig,item,content='第二问',turn_no=2,request_id=request_id)
    save(rig,item,content='第一问',turn_no=1)
    save(rig,item,role='assistant',content='第一答',turn_no=1)
    save(rig,item,role='assistant',content='新回答',turn_no=2,attempt_no=2)
    url=f"{API}/{item['id']}/messages"
    page=rig[0].get(url).json()
    assert [x['content'] for x in page['items']]==['第一问','第一答','第二问','旧回答','新回答']
    assert page['total']==5
    assert len({UUID(m['id']) for m in page['items']})==5
    assert page['items'][3]['request_id']==str(request_id)
    assert page['items'][3]['result_metadata']['chat_status']=='answered'
    assert rig[0].get(url,params={'offset':2,'limit':2}).json()['items']==page['items'][2:4]
    assert rig[0].get(url,params={'limit':0}).status_code==422
    assert rig[0].get(url,params={'unknown':'x'}).status_code==422
    assert rig[0].get(f"{API}/{item['id']}").json()['revision']==5


def test_snapshot_round_trip_and_source_deletion(rig):
    item=create(rig)
    citation=source(rig)
    save(rig,item,role='assistant',content='原文结论 [1]',citations=[citation])
    url=f"{API}/{item['id']}/messages"
    assert rig[0].get(url).json()['items'][0]['citations']==[citation]
    # Historical snapshots deliberately survive deletion of the current source.
    assert rig[0].delete('/api/documents/'+citation['document_id']).status_code==204
    assert rig[0].get(url).json()['items'][0]['citations']==[citation]


@pytest.mark.parametrize('change',['other_kb','content','page','name','chunk','document'])
def test_forged_or_cross_kb_snapshot_rejected(rig,change):
    item=create(rig)
    citation=source(rig,rig[2] if change=='other_kb' else rig[1])
    if change=='content': citation['content']='伪造'
    if change=='page': citation['page']=99
    if change=='name': citation['document_name']='伪造.pdf'
    if change=='chunk': citation['chunk_id']=str(uuid4())
    if change=='document': citation['document_id']=str(uuid4())
    with pytest.raises(DomainError) as error:
        save(rig,item,role='assistant',content='x [1]',citations=[citation])
    assert error.value.code=='INVALID_CITATION_SNAPSHOT'
    assert count(rig,Message)==0


@pytest.mark.parametrize('body',[{'role':'tool'},{'turn_no':0},{'attempt_no':0},{'status':'unknown'},
    {'content':''},{'role':'assistant','status':'pending','content':'unverified'},
    {'role':'user','status':'failed','content':''},{'result_metadata':{'reasoning':'private'}},
    {'role':'assistant','error_code':'PRIVATE raw exception'}, {'role':'assistant','status':'completed','error_code':'ERR'}])
def test_internal_message_schema_validation(body):
    with pytest.raises(ValidationError): MessageWrite(**{'role':'user','content':'x','turn_no':1,**body})


@pytest.mark.parametrize('status',['pending','failed','cancelled','interrupted'])
def test_unfinished_messages_have_no_final_content(rig,status):
    item=create(rig)
    save(rig,item,role='assistant',content='',status=status,error_code=None if status=='pending' else 'TASK_STOPPED')
    msg=rig[0].get(f"{API}/{item['id']}/messages").json()['items'][0]
    assert msg['content']=='' and msg['citations']==[] and msg['status']==status


def test_mode_metadata_and_agent_storage(rig):
    chat=create(rig); agent=create(rig,'agent')
    for item,metadata in [(chat,{'tool_calls':1}),(agent,{'chat_status':'answered'})]:
        with pytest.raises(DomainError) as error:
            save(rig,item,role='assistant',content='x',result_metadata=metadata)
        assert error.value.code=='INVALID_MESSAGE_METADATA'
    save(rig,agent,role='assistant',content='42',result_metadata={'tool_calls':2})
    assert rig[0].get(f"{API}/{agent['id']}/messages").json()['items'][0]['result_metadata']['tool_calls']==2


def test_delete_conversation_and_kb_cleanup(rig):
    a=create(rig); b=create(rig,'agent'); other=create(rig,kb=rig[2])
    for item in (a,b,other): save(rig,item)
    assert rig[0].delete(f"{API}/{a['id']}").status_code==204
    assert count(rig,Message)==2
    assert rig[0].delete(f'/api/knowledge-bases/{rig[1]}').status_code==204
    assert count(rig,Conversation)==1 and count(rig,Message)==1
    assert rig[0].get(f"{API}/{b['id']}").status_code==404
    assert rig[0].get(f"{API}/{other['id']}/messages").json()['total']==1


def test_duplicate_batch_rolls_back_all_rows_and_revision(rig):
    item=create(rig)
    save(rig,item)
    before=rig[0].get(f"{API}/{item['id']}").json()
    with pytest.raises(DomainError) as error:
        service(rig).save_messages(UUID(item['id']),rig[1],[MessageWrite(role='user',content='new',turn_no=2),MessageWrite(role='user',content='duplicate',turn_no=1)])
    assert error.value.code=='HISTORY_CONFLICT'
    assert count(rig,Message)==1
    assert rig[0].get(f"{API}/{item['id']}").json()==before
    save(rig,item,turn_no=2)
    assert count(rig,Message)==2


def test_duplicate_request_id_and_missing_conversation(rig):
    item=create(rig); token=uuid4()
    save(rig,item,request_id=token)
    with pytest.raises(DomainError) as error: save(rig,item,turn_no=2,request_id=token)
    assert error.value.code=='HISTORY_CONFLICT'
    with pytest.raises(DomainError) as error:
        service(rig).save_message(uuid4(),rig[1],MessageWrite(role='user',content='x',turn_no=1))
    assert error.value.code=='CONVERSATION_NOT_FOUND'
    with pytest.raises(DomainError): service(rig).save_messages(UUID(item['id']),rig[1],[])


@pytest.mark.parametrize('operation',['create','rename','delete','save','delete_kb'])
def test_storage_failure_sanitized_and_rolled_back(rig,operation):
    item=create(rig); save(rig,item)
    before=rig[0].get(f"{API}/{item['id']}").json()
    prefix={'create':'INSERT INTO conversations','rename':'UPDATE conversations','delete':'DELETE FROM conversations',
            'save':'INSERT INTO messages','delete_kb':'DELETE FROM knowledge_bases'}[operation]
    def fail(_conn,_cursor,statement,parameters,_context,_executemany):
        if statement.startswith(prefix): raise OperationalError(statement,parameters,Exception('PRIVATE_STORAGE_SECRET'))
    engine=rig[0].app.state.engine
    event.listen(engine,'before_cursor_execute',fail)
    try:
        if operation=='save':
            with pytest.raises(DomainError) as error: save(rig,item,turn_no=2)
            assert error.value.code=='HISTORY_STORAGE_ERROR'
            assert 'PRIVATE' not in error.value.message
        else:
            if operation=='create': response=rig[0].post(API,json={'knowledge_base_id':rig[1],'title':'new','mode':'chat'})
            elif operation=='rename': response=rig[0].put(f"{API}/{item['id']}",json={'title':'changed'})
            elif operation=='delete': response=rig[0].delete(f"{API}/{item['id']}")
            else: response=rig[0].delete(f'/api/knowledge-bases/{rig[1]}')
            assert response.status_code==500 and 'PRIVATE' not in response.text
    finally: event.remove(engine,'before_cursor_execute',fail)
    assert count(rig,Conversation)==1 and count(rig,Message)==1
    assert rig[0].get(f"{API}/{item['id']}").json()==before
    assert rig[0].get(f'/api/knowledge-bases/{rig[1]}').status_code==200


def test_foreign_keys_and_check_constraints(rig):
    engine=rig[0].app.state.engine
    with engine.connect() as connection: assert connection.scalar(text('PRAGMA foreign_keys'))==1
    with pytest.raises(IntegrityError),Session(engine) as session,session.begin():
        session.add(Conversation(knowledge_base_id=99999,title='orphan',mode='chat'))
    with pytest.raises(IntegrityError),Session(engine) as session,session.begin():
        session.add(Message(conversation_id=str(uuid4()),role='user',content='orphan',turn_no=1))
    with pytest.raises(IntegrityError),Session(engine) as session,session.begin():
        session.add(Conversation(knowledge_base_id=rig[1],title='bad mode',mode='invalid'))
    assert count(rig,Conversation)==0 and count(rig,Message)==0


def test_legacy_upgrade_and_repeated_initialization(tmp_path):
    path=tmp_path/'legacy.db'; doc_id=str(uuid4()); chunk_id=str(uuid4())
    # Independent pre-Phase-5 DDL, not generated from the new metadata.
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE knowledge_bases (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
          description TEXT NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE documents (id VARCHAR(36) PRIMARY KEY, knowledge_base_id INTEGER NOT NULL REFERENCES knowledge_bases(id),
          filename VARCHAR(255) NOT NULL, file_type VARCHAR(10) NOT NULL, file_size INTEGER NOT NULL, content_hash VARCHAR(64) NOT NULL,
          status VARCHAR(20) NOT NULL, error_message TEXT, chunk_count INTEGER NOT NULL, embedding_model VARCHAR(255),
          embedding_dimension INTEGER, created_at DATETIME NOT NULL, UNIQUE(knowledge_base_id,content_hash));
        CREATE TABLE chunks (id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL REFERENCES documents(id),
          ordinal INTEGER NOT NULL,page INTEGER,content TEXT NOT NULL,vector_id VARCHAR(60) NOT NULL UNIQUE,
          created_at DATETIME NOT NULL,UNIQUE(document_id,ordinal));
        INSERT INTO knowledge_bases VALUES(1,'legacy KB','keep description','2020-01-01','2020-01-01');
        """)
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(doc_id,1,'旧资料.txt','.txt',10,'legacy-hash','ready',None,1,'legacy-model',3,'2020-01-01'))
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',(chunk_id,doc_id,0,None,'keep original content',doc_id+':0','2020-01-01'))
        old_schema=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        old_data={table:db.execute(f'SELECT * FROM {table}').fetchall() for table in ('knowledge_bases','documents','chunks')}
    settings=Settings(database_url=f'sqlite:///{path.as_posix()}',data_dir=tmp_path/'data',_env_file=None)
    for iteration in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get('/api/knowledge-bases/1').json()['name']=='legacy KB'
            assert client.get(f'/api/documents/{doc_id}').json()['chunks'][0]['content']=='keep original content'
            if iteration==0:
                response=client.post(API,json={'knowledge_base_id':1,'title':'persist','mode':'chat'})
                assert response.status_code==201
                saved=response.json()['id']
                client.app.state.conversations.save_message(UUID(saved),1,MessageWrite(role='user',content='persist message',turn_no=1))
            else:
                assert client.get(f'{API}/{saved}/messages').json()['items'][0]['content']=='persist message'
    with sqlite3.connect(path) as db:
        for table,sql in old_schema:
            assert db.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]==sql
            assert db.execute(f'SELECT * FROM {table}').fetchall()==old_data[table]
        assert {'conversations','messages'}.issubset({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})


def test_no_message_write_memory_or_generation_endpoints_added(rig):
    item=create(rig)
    assert rig[0].post(f"{API}/{item['id']}/messages",json={}).status_code==405
    preferences = rig[0].get('/api/memory')
    assert preferences.status_code == 200
    assert preferences.json() == {
        'preferences': {'answer_style': 'concise', 'learning_goal': 'general', 'preferred_language': 'zh-CN'},
        'defaults': {'answer_style': 'concise', 'learning_goal': 'general', 'preferred_language': 'zh-CN'},
        'overrides': {},
    }
    assert count(rig,Message)==0
