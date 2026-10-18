"""Transparent retrieval metrics and explicit manual 0/1/2 answer grading."""
import math
from statistics import mean
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4000)
    expected_document: str = Field(min_length=1)
    expected_page: int | None = Field(ge=1)
    reference_answer: str = Field(min_length=1)


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    source: str = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case IDs")
        return self


def retrieval_hit(case: Case, citations: list[dict]) -> bool:
    """Match filename and page; null means the source has no page numbering."""
    return any(c['document_name'] == case.expected_document and c['page'] == case.expected_page for c in citations)


def summarize(rows: list[dict]) -> dict:
    """Failures count in the denominator; latency includes failed attempts."""
    if not rows:
        raise ValueError("empty evaluation")
    results = {}
    for mode in ('basic', 'reranked'):
        runs = [row['runs'][mode] for row in rows]
        times = [run['elapsed_ms'] for run in runs]
        if any(type(t) not in (float, int) or not math.isfinite(t) or t < 0 for t in times):
            raise ValueError("invalid measured latency")
        results[mode] = {'cases': len(runs), 'errors': sum(bool(r.get('error_code')) for r in runs),
            'retrieval_hit_rate': sum(bool(r['hit']) and not r.get('error_code') for r in runs)/len(runs),
            'mean_response_ms': mean(times), 'answer_accuracy': None}
    return results


def grade_answers(report: dict, grades: dict) -> dict:
    """Bind reviewer scores to one run; never infer semantic accuracy from text overlap."""
    if grades.get('run_id') != report['run_id'] or not isinstance(grades.get('reviewer'), str) or not grades['reviewer'].strip():
        raise ValueError("run_id and reviewer required")
    entries = grades.get('scores', [])
    indexed = {row['id']: row for row in report['rows']}
    seen = set()
    scores = {'basic': [], 'reranked': []}
    for entry in entries:
        key = (entry['id'], entry['mode'])
        score = entry['score']
        if key in seen or key[0] not in indexed or key[1] not in scores or type(score) is not int or score not in (0, 1, 2):
            raise ValueError("invalid/duplicate grade")
        run = indexed[key[0]]['runs'][key[1]]
        if run.get('error_code') or run.get('answer') is None:
            raise ValueError("cannot grade a failed/missing answer")
        seen.add(key); scores[key[1]].append(score)
    return {mode: {'scored': len(values), 'total': len(indexed),
                  'complete': len(values) == len(indexed),
                  'answer_accuracy': sum(values)/(2*len(values)) if values else None}
            for mode, values in scores.items()}
