"""Compare two Ollama models on the same tiny contract suite; promotion is advisory."""
from __future__ import annotations
import argparse
import json
import requests
from brain.learning_engine import LearningEngine
from evaluation.regression_suite import CASES, evaluate_responses, save_report


def ask(base_url: str, model: str, prompt: str) -> str:
    r = requests.post(base_url.rstrip('/') + '/api/chat', json={
        'model': model, 'stream': False,
        'messages': [
            {'role': 'system', 'content': 'Answer honestly. If the prompt cannot be answered from available evidence, say so plainly.'},
            {'role': 'user', 'content': prompt}
        ], 'options': {'temperature': 0}
    }, timeout=180)
    r.raise_for_status()
    return r.json().get('message', {}).get('content', '')


def run(model: str, url: str):
    answers = {case['id']: ask(url, model, case['prompt']) for case in CASES}
    report = evaluate_responses(answers)
    report.update({'model': model, 'responses': answers})
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    ap.add_argument('--baseline-version', default='baseline')
    ap.add_argument('--candidate-version', default='candidate')
    args = ap.parse_args()
    baseline = run(args.baseline, args.ollama_url)
    candidate = run(args.candidate, args.ollama_url)
    engine = LearningEngine()
    engine.record_eval(args.baseline_version, baseline['suite'], baseline['score'], baseline)
    engine.record_eval(args.candidate_version, candidate['suite'], candidate['score'], candidate)
    promoted = engine.compare_and_promote(args.candidate_version, args.baseline_version,
                                          candidate['score'], baseline['score'])
    report = {'baseline': baseline, 'candidate': candidate, 'promotion_gate_passed': promoted,
              'warning': 'Tiny lexical smoke test only; this is not a broad capability benchmark.'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    save_report(report, 'data/evaluations/model_comparison.json')

if __name__ == '__main__':
    main()
