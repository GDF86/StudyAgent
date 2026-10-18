"""Run against an explicit API/KB; optional seeding creates and removes its own KB."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

import httpx
from evaluation.metrics import Dataset, retrieval_hit, summarize


def evaluate(client, kb_id: int, dataset: Dataset, top_k: int, include_answers: bool) -> list[dict]:
    rows = []
    for case in dataset.cases:
        start = perf_counter()
        try:
            response = client.post('/api/rag/compare', json={'knowledge_base_id': kb_id, 'question': case.question,
                                   'top_k': top_k, 'include_answer': include_answers})
            response.raise_for_status()
            runs = response.json()['runs']
            if len(runs) != 2 or {r['mode'] for r in runs} != {'basic', 'reranked'}:
                raise ValueError('missing/duplicate modes')
        except (httpx.HTTPError, ValueError, KeyError):
            # No upstream body/URL is copied into the report (could contain credentials).
            runs = [{'mode': mode, 'citations': [], 'answer': None, 'error_code': 'EVALUATION_REQUEST_FAILED',
                     'elapsed_ms': (perf_counter()-start)*1000} for mode in ('basic', 'reranked')]
        for run in runs:
            run['hit'] = not run.get('error_code') and retrieval_hit(case, run['citations'])
        rows.append({**case.model_dump(), 'runs': {r['mode']: r for r in runs}})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--dataset', type=Path, default=Path(__file__).with_name('qa_dataset.json'))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--kb-id', type=int)
    target.add_argument('--seed-corpus', type=Path, help='Create an isolated KB from these public fixture files; delete after evaluation')
    parser.add_argument('--environment', required=True, choices=['mock', 'real'], help='Operator declaration, not automatic provider detection')
    parser.add_argument('--provider-note', required=True, help='Non-secret model/service identifiers; never supply keys')
    parser.add_argument('--top-k', type=int, default=5, choices=range(1,21))
    parser.add_argument('--include-answers', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('evaluation/results/latest.json'))
    args = parser.parse_args()
    raw = args.dataset.read_bytes(); dataset = Dataset.model_validate_json(raw)
    with httpx.Client(base_url=args.base_url.rstrip('/'), timeout=180, trust_env=False) as client:
        created = None
        try:
            kb = args.kb_id
            if args.seed_corpus:
                response = client.post('/api/knowledge-bases', json={'name': 'evaluation-' + uuid4().hex})
                response.raise_for_status(); created = kb = response.json()['id']
                for name in sorted({case.expected_document for case in dataset.cases}):
                    path = (args.seed_corpus / name).resolve()
                    if not path.is_relative_to(args.seed_corpus.resolve()):
                        raise ValueError('corpus path escapes directory')
                    with path.open('rb') as stream:
                        response = client.post(f'/api/knowledge-bases/{kb}/documents', files={'file': (name, stream)})
                    response.raise_for_status()
                    if response.json().get('status') != 'ready':
                        raise ValueError('fixture ingestion failed')
            rows = evaluate(client, kb, dataset, args.top_k, args.include_answers)
            report = {'run_id': uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(),
                'environment': args.environment, 'provider_note': args.provider_note,
                'dataset_sha256': hashlib.sha256(raw).hexdigest(), 'dataset_source': dataset.source,
                'top_k': args.top_k, 'include_answers': args.include_answers,
                'latency_scope': 'server retrieval including rerank; includes answer generation only when requested; sequential basic then reranked, not a benchmark',
                'summary': summarize(rows), 'rows': rows}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            print(json.dumps({'environment': args.environment, 'summary': report['summary']}, ensure_ascii=False))
            return 1 if any(v['errors'] for v in report['summary'].values()) else 0
        finally:
            if created is not None:
                response = client.delete(f'/api/knowledge-bases/{created}')
                response.raise_for_status()


if __name__ == '__main__':
    sys.exit(main())
