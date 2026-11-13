from io import BytesIO
from pathlib import Path
import hashlib,json
import pytest,httpx
from docx import Document as Word
from app.rag.loaders import parse_document,TextPage
from app.rag.splitter import split_pages
from app.core.errors import DomainError
from test_documents import client,settings,kb,upload,embedding_reply
from app.rag.embeddings import CompatibleEmbeddingProvider

def long_docx(n=600):
    d=Word()
    for level in (1,2,3):d.add_heading('课程标题'+str(level),level)
    for i in range(n):
        d.add_paragraph(f'第{i}项复习要点：进程调度、线程共享资源和死锁条件，必须结合例子理解。')
        if i%10==0:d.add_paragraph(' \t\u200b\xa0 ')
    t=d.add_table(rows=120,cols=3)
    for i,row in enumerate(t.rows):
        for j,c in enumerate(row.cells):c.text=f'表格{i}-{j}知识点'
    t.cell(0,0).merge(t.cell(0,1)).text='唯一合并单元格'
    nested=t.cell(1,0).add_table(rows=1,cols=1);nested.cell(0,0).text='嵌套表格知识'
    out=BytesIO();d.save(out);return out.getvalue()

def test_long_docx_paragraphs_headings_tables_and_blanks(tmp_path):
    p=tmp_path/'course.docx';p.write_bytes(long_docx())
    pages=parse_document(p,'.docx',120000);text=pages[0].content
    assert '# 课程标题1' in text and '### 课程标题3' in text
    assert text.count('唯一合并单元格')==1 and '嵌套表格知识' in text
    assert '\u200b' not in text and '\n\n\n' not in text
    chunks=split_pages(pages,640,80,128,paragraph_aware=True,max_utf8_bytes=1984)
    assert 32<len(chunks)<=128 and all(c.content.strip() for c in chunks)
    assert len({c.content for c in chunks})==len(chunks)
    assert all(len(c.content.encode('utf-8'))<=1984 for c in chunks)

@pytest.mark.parametrize('size,overlap',[(0,0),(3,3),(3,-1)])
def test_invalid_boundaries(size,overlap):
    with pytest.raises(ValueError):split_pages([TextPage('abcdef',1)],size,overlap,128)

def test_byte_boundary_coverage_and_pdf_pages():
    text='😀'*1000+'最后的重要结论'
    chunks=split_pages([TextPage(text,1),TextPage('另一页',2)],640,80,128,paragraph_aware=True,max_utf8_bytes=1984)
    assert all(len(c.content.encode())<=1984 for c in chunks)
    assert '最后的重要结论' in chunks[-2].content and chunks[-1].page==2

def test_limit_reports_actual_work_without_embedding(tmp_path):
    p=tmp_path/'large.docx';p.write_bytes(long_docx(1600))
    pages=parse_document(p,'.docx',120000)
    with pytest.raises(DomainError) as e:split_pages(pages,640,80,2,paragraph_aware=True,max_utf8_bytes=1984)
    assert e.value.detail['planned_chunks']>2 and e.value.detail['embedding_called'] is False
    with pytest.raises(DomainError):parse_document(p,'.docx',100)

def tune(client,limit):
    cfg=client.app.state.settings
    cfg.chunk_size=640;cfg.chunk_overlap=80;cfg.max_chunks=limit;cfg.max_text_chars=120000
    cfg.paragraph_aware_chunks=True;cfg.chunk_max_utf8_bytes=1984;cfg.embedding_batch_size=8

def test_preview_and_explicit_retry_preserves_original(client,kb):
    tune(client,2);seen=[]
    def reply(req):seen.append(1);return embedding_reply(req)
    client.app.state.documents.embeddings=CompatibleEmbeddingProvider(client.app.state.settings,httpx.MockTransport(reply))
    data=long_docx();r=upload(client,kb,'long.docx',data)
    assert r.status_code==413 and '未调用Embedding' in r.json()['message'] and not seen
    did=r.json()['detail']['document_id'];service=client.app.state.documents
    raw=service.upload_dir/(did+'.docx');before=raw.read_bytes()
    assert before==data and not service.vectors.path(did).exists()
    assert client.get('/api/documents/'+did).json()['chunks']==[]
    tune(client,128)
    plan=client.get(f'/api/documents/{did}/processing-plan').json()
    assert plan['planned_chunks']>32 and plan['embedding_called'] is False and not seen
    assert client.post(f'/api/documents/{did}/retry',json={}).status_code==403
    r=client.post(f'/api/documents/{did}/retry',json={},headers={'X-Confirm-Retry':'true'})
    assert r.status_code==200,r.text
    assert r.json()['status']=='ready' and r.json()['id']==did and raw.read_bytes()==before
    assert len(seen)==plan['embedding_requests']
    assert service.vectors.read(did).ntotal==len(client.get('/api/documents/'+did).json()['chunks'])
    assert client.post(f'/api/documents/{did}/retry',json={},headers={'X-Confirm-Retry':'true'}).status_code==409

def test_mid_batch_failure_no_partial_index_and_retry(client,kb):
    tune(client,128);seen=[]
    def fail(req):
        seen.append(1)
        if len(seen)==2:raise httpx.ReadTimeout('OFFLINE_ONLY')
        return embedding_reply(req)
    client.app.state.documents.embeddings=CompatibleEmbeddingProvider(client.app.state.settings,httpx.MockTransport(fail))
    r=upload(client,kb,'partial.docx',long_docx());assert r.status_code==502
    did=r.json()['detail']['document_id'];svc=client.app.state.documents
    assert client.get('/api/documents/'+did).json()['status']=='failed'
    assert client.get('/api/documents/'+did).json()['chunks']==[] and not svc.vectors.path(did).exists()
    assert (svc.upload_dir/(did+'.docx')).exists()
    client.app.state.documents.embeddings=CompatibleEmbeddingProvider(client.app.state.settings,httpx.MockTransport(embedding_reply))
    r=client.post(f'/api/documents/{did}/retry',json={},headers={'X-Confirm-Retry':'true'})
    assert r.status_code==200 and svc.vectors.read(did).ntotal==r.json()['chunk_count']

def test_upload_preview_does_not_create_records_or_model_calls(client,kb):
    tune(client,128)
    class Never:
        def embed_texts(self,texts):raise AssertionError('NO_MODEL_ALLOWED')
    client.app.state.documents.embeddings=Never()
    r=client.post(f'/api/knowledge-bases/{kb}/documents/preview',files={'file':('long.docx',long_docx())})
    assert r.status_code==200 and r.json()['embedding_called'] is False
    assert client.get(f'/api/knowledge-bases/{kb}/documents').json()==[]
