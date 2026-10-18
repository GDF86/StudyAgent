import json
from pathlib import Path
import sys
import httpx
import pytest
from pydantic import ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.metrics import Case, Dataset, retrieval_hit, summarize, grade_answers
from evaluation.evaluate_retrieval import evaluate


def case():
    return Case(id='a', question='question', expected_document='a.pdf', expected_page=2, reference_answer='answer')


def report():
    return {'run_id':'run', 'rows':[{'id':'a','runs':{m:{'hit':True,'elapsed_ms':10,'error_code':None,'answer':{'answer':'text'}} for m in ('basic','reranked')}}]}


def test_hit_requires_document_and_page():
    assert retrieval_hit(case(), [{'document_name':'a.pdf','page':2}])
    assert not retrieval_hit(case(), [{'document_name':'a.pdf','page':1}, {'document_name':'b.pdf','page':2}])
    assert not retrieval_hit(case(), [])


def test_failures_in_denominator_and_no_invented_accuracy():
    rows = report()['rows']
    rows.append({'id':'b','runs':{m:{'hit':True,'elapsed_ms':30,'error_code':'FAIL','answer':None} for m in ('basic','reranked')}})
    for result in summarize(rows).values():
        assert result == {'cases':2,'errors':1,'retrieval_hit_rate':.5,'mean_response_ms':20,'answer_accuracy':None}


def test_manual_grading_partial_coverage_explicit():
    data = report(); data['rows'].append({'id':'b', 'runs': data['rows'][0]['runs']})
    result = grade_answers(data, {'run_id':'run','reviewer':'human','scores':[{'id':'a','mode':'basic','score':1}]})
    assert result['basic'] == {'scored':1,'total':2,'complete':False,'answer_accuracy':.5}
    assert result['reranked']['answer_accuracy'] is None and result['reranked']['scored'] == 0


@pytest.mark.parametrize('score', [None, True, .5, -1, 3, '2'])
def test_invalid_manual_scores_rejected(score):
    with pytest.raises(ValueError):
        grade_answers(report(), {'run_id':'run','reviewer':'human','scores':[{'id':'a','mode':'basic','score':score}]})


@pytest.mark.parametrize('mutation', ['duplicate','unknown','failed','missing','wrong_run','reviewer'])
def test_grade_binding_and_failed_answers(mutation):
    data = report(); grade = {'run_id':'run','reviewer':'human','scores':[{'id':'a','mode':'basic','score':2}]}
    if mutation == 'duplicate': grade['scores'] *= 2
    elif mutation == 'unknown': grade['scores'][0]['id'] = 'unknown'
    elif mutation == 'failed': data['rows'][0]['runs']['basic']['error_code'] = 'FAILED'
    elif mutation == 'missing': data['rows'][0]['runs']['basic']['answer'] = None
    elif mutation == 'wrong_run': grade['run_id'] = 'other'
    else: grade['reviewer'] = ''
    with pytest.raises(ValueError): grade_answers(data, grade)


def test_dataset_requires_unique_nonempty_cases():
    with pytest.raises(ValidationError): Dataset(version=1, source='fixture', cases=[])
    with pytest.raises(ValidationError): Dataset(version=1, source='fixture', cases=[case(),case()])


def test_evaluator_network_error_is_recorded_not_omitted():
    def fail(request): raise httpx.ConnectError('private credential', request=request)
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(fail)) as client:
        rows = evaluate(client, 1, Dataset(version=1, source='fixture', cases=[case()]), 5, False)
    assert all(r['error_code'] == 'EVALUATION_REQUEST_FAILED' and not r['hit'] for r in rows[0]['runs'].values())
    assert 'private credential' not in json.dumps(rows)


def test_committed_dataset_matches_public_corpus():
    root = Path(__file__).resolve().parents[2]/'evaluation'
    data = Dataset.model_validate_json((root/'qa_dataset.json').read_bytes())
    assert len(data.cases) == 6 and 'synthetic' in data.source
    for item in data.cases:
        assert (root/'corpus'/item.expected_document).is_file() and item.expected_page is None
