"""RAG numerical boundaries and actual FAISS/SQLite inconsistency cases."""
import json
import numpy as np
import pytest
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.database.models import Document, Chunk
from test_chat import rig


def query(rig, **extra):
    return rig[0].post('/api/chat', json={'knowledge_base_id': rig[2], 'question': 'ALPHA', **extra})


@pytest.mark.parametrize('delta,expected', [(-.000001, 1), (0, 1), (.000001, 0)])
def test_similarity_threshold_inclusive_boundary(rig, delta, expected):
    client, settings, _, doc, _ = rig
    client.app.state.documents.vectors.write(doc['id'], [[.8, .6, 0], [0, 1, 0]])
    score = query(rig).json()['citations'][0]['score']
    settings.retrieval_min_score = score + delta
    result = query(rig)
    assert result.status_code == 200
    assert len(result.json()['citations']) == expected
    if not expected: assert result.json()['status'] == 'no_relevant_content'


def test_equal_scores_have_stable_global_order_and_top_k(rig):
    client = rig[0]
    for i in range(3):
        assert client.post(f'/api/knowledge-bases/{rig[2]}/documents', files={'file': (f'tie{i}.txt', f'ALPHA tie {i}'.encode())}).status_code == 201
    all_sources = query(rig, top_k=20).json()['citations']
    identities = [(c['document_id'], c['chunk_id']) for c in all_sources]
    assert len(identities) == 4 and identities == sorted(identities)
    for _ in range(2):
        selected = query(rig, top_k=2).json()['citations']
        assert [(c['document_id'], c['chunk_id']) for c in selected] == identities[:2]
        assert [c['id'] for c in selected] == [1, 2]


@pytest.mark.parametrize('status', ['processing', 'embedding', 'failed', 'deleting'])
def test_non_ready_document_with_valid_index_is_not_retrieved(rig, status):
    client = rig[0]
    with Session(client.app.state.engine) as db, db.begin(): db.get(Document, rig[3]['id']).status = status
    result = query(rig)
    assert result.status_code == 200 and result.json()['status'] == 'empty_knowledge_base'
    assert result.json()['citations'] == [] and not rig[4]


@pytest.mark.parametrize('damage', ['bytes', 'count', 'index_dimension', 'document_dimension', 'missing_chunk', 'vector_id', 'nan_score'])
def test_inconsistent_index_rejected_before_llm(rig, damage):
    client, _, _, doc, captured = rig; store = client.app.state.documents.vectors
    if damage == 'bytes': store.path(doc['id']).write_bytes(b'broken-faiss')
    elif damage == 'count': store.write(doc['id'], [[1, 0, 0]])
    elif damage == 'index_dimension': store.write(doc['id'], [[1, 0], [0, 1]])
    elif damage == 'nan_score':
        # Serialized index with NaN exercises deserialization/search, not a mocked retriever.
        import faiss
        index = faiss.IndexFlatIP(3); index.add(np.array([[float('nan'), 0, 0], [0, 1, 0]], dtype='float32'))
        store.path(doc['id']).write_bytes(faiss.serialize_index(index).tobytes())
    else:
        with Session(client.app.state.engine) as db, db.begin():
            if damage == 'document_dimension': db.get(Document, doc['id']).embedding_dimension = 2
            else:
                chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc['id'], Chunk.ordinal == 0))
                if damage == 'missing_chunk': db.delete(chunk)
                else: chunk.vector_id = 'wrong:0'
    response = query(rig)
    assert response.status_code == 409, response.text
    assert response.json()['code'] == ('EMBEDDING_DIMENSION_MISMATCH' if damage == 'document_dimension' else 'VECTOR_INDEX_UNAVAILABLE')
    assert captured == []
