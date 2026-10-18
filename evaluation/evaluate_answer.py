"""Create a review template, or summarize explicit manual 0/1/2 scores."""
import argparse
import hashlib
import json
from pathlib import Path
from evaluation.metrics import grade_answers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--grades', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.results.read_bytes(); report = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if args.grades:
        grades = json.loads(args.grades.read_text(encoding='utf-8'))
        if grades.get('results_sha256') != digest:
            raise ValueError('grades do not match the exact results file')
        result = {'run_id': report['run_id'], 'environment': report['environment'],
                  'reviewer': grades.get('reviewer'), 'summary': grade_answers(report, grades)}
    else:
        result = {'run_id': report['run_id'], 'results_sha256': digest, 'reviewer': '',
                  'instructions': 'Manually review answers against source/reference: 0 wrong, 1 partial, 2 correct. Remove unreviewed entries; null is not a score.',
                  'scores': [{'id': row['id'], 'mode': mode, 'score': None}
                             for row in report['rows'] for mode in ('basic', 'reranked')
                             if row['runs'][mode].get('answer') is not None and not row['runs'][mode].get('error_code')]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Wrote answer review output; unscored answers have no accuracy value.')


if __name__ == '__main__':
    main()
