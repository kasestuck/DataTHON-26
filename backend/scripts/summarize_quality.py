"""Private post-inference comparison. Never called by the video analyzer."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from backend.analyzer.labels import read_labels, parse_verdict
from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import _save


def outcome(truth, findings):
    return ('TP' if findings else 'FN') if truth else ('FP' if findings else 'TN')


def experiment_register(root):
    entries = []
    for manifest in sorted(Path(root).glob('*/manifest.json')):
        inputs = json.loads(manifest.read_text(encoding='utf-8'))
        directory = manifest.parent
        report = next((p for name in ('full.json', 'results.json', 'pilot.json', 'checkpoint.json')
                       if (p := directory / name).exists()), None)
        if report is None:
            continue
        value = json.loads(report.read_text(encoding='utf-8'))
        metrics = value.get('metrics', value)
        timings = value.get('runtime', value.get('timings', {}))
        entries.append({'id': directory.name, 'manifest_sha256': digest(manifest),
            'code_version': inputs.get('code_sha256'), 'source_commit': inputs.get('source_commit'),
            'config': inputs.get('config'), 'context_sec': inputs.get('context_sec'),
            'report': str(report), 'evaluated': len(value.get('predictions', {})),
            'technical_errors': len(value.get('skipped', value.get('errors', []))),
            'video_detection': metrics.get('video_detection'), 'localization': metrics.get('localization'),
            'timing': timings, 'timing_notice': value.get('notice')})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', required=True)
    parser.add_argument('--baseline-dir', default='backend/data/reports/quality/baseline_2f11600')
    parser.add_argument('--labels', default='backend/data/labels.csv')
    parser.add_argument('--robustness')
    parser.add_argument('--output-dir', default='backend/data/reports/quality/final_review')
    args = parser.parse_args()
    final = json.loads(Path(args.final).read_text(encoding='utf-8'))
    baseline_dir = Path(args.baseline_dir)
    baseline = json.loads((baseline_dir / 'snapshot/backend/data/reports/evaluation.json').read_text(encoding='utf-8'))
    if digest(args.labels) != final['run_inputs']['labels_sha256'] or digest(args.labels) != baseline['run_inputs']['labels_sha256']:
        raise ValueError('Comparison labels changed')
    labels = read_labels(args.labels)
    original = json.loads((baseline_dir / 'error_diagnostics.json').read_text(encoding='utf-8'))
    originals = {row['video']: row for row in original}
    cases, improved, worsened = [], [], []
    for row in labels:
        video = row['video']
        case = dict(originals[video])
        if video not in final['predictions']:
            case['current_group'] = 'unevaluated'
            cases.append(case)
            continue
        truth = parse_verdict(row['has_violation'])
        findings = final['predictions'][video]
        current = outcome(truth, findings)
        if current != case['group']:
            (improved if current in {'TP', 'TN'} else worsened).append(video)
        windows = final['window_audits'][video]
        # Deliberately omit private captions from the diagnostic table.
        audit = [{k: value for k, value in w.items() if k != 'response'} | {
            'response': {k: value for k, value in w.get('response', {}).items() if k != 'observed_action'}} for w in windows]
        case.update(current_group=current, current_predictions=findings, current_window_audit=audit,
                    current_filter_counts=dict(Counter(w['outcome'] for w in windows)),
                    current_timings=final['timings'][video])
        if case['group'] == 'FN':
            case['deduplication_error'] = 'excluded_as_sole_cause: deduplicate preserves at least one nonempty input finding'
        if truth:
            stamp = row['timecode_sec']
            case['current_windows_containing_reference_time'] = [w['window'] for w in windows
                if w['frames'][0]['timestamp_sec'] <= stamp <= w['frames'][-1]['timestamp_sec']]
            case['context_notice'] = 'Time coverage and frame hashes do not prove that an object/action is visually identifiable.'
        case['causality_notice'] = ('Current per-window responses are actual new inference. Baseline has aggregate filters only; '
                                    'do not treat a reconstructed hypothesis as a proven baseline visual cause.')
        cases.append(case)
    output = Path(args.output_dir)
    _save(output / 'cases.json', cases)
    fields = sorted({key for case in cases for key in case})
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'cases.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                         for k, v in case.items()} for case in cases)
    keys = ('video_detection', 'classification', 'localization', 'runtime', 'evaluated_videos')
    evidence = [f for fs in final['predictions'].values() for f in fs]
    hash_groups = {}
    for video, checksum in final['run_inputs']['video_hashes'].items():
        hash_groups.setdefault(checksum, []).append(video)
    duplicate_consistency = []
    for videos in hash_groups.values():
        if len(videos) > 1 and all(v in final['predictions'] for v in videos):
            duplicate_consistency.append({'videos': videos, 'same_predictions':
                all(final['predictions'][v] == final['predictions'][videos[0]] for v in videos[1:])})
    summary = {'baseline': {k: baseline.get(k) for k in keys}, 'final': {k: final.get(k) for k in keys},
        'experiments': experiment_register(Path(args.final).parent.parent),
        'duplicate_consistency': duplicate_consistency,
        'final_fingerprint': final['fingerprint'], 'source_report_sha256': digest(args.final),
        'errors': final['skipped'], 'improved_videos': improved, 'worsened_videos': worsened,
        'baseline_groups': dict(Counter(c['group'] for c in cases)),
        'final_groups': dict(Counter(c['current_group'] for c in cases)),
        'finding_count': len(evidence), 'evidence_counts': dict(Counter(len(f['evidence']) for f in evidence)),
        'all_timecodes_in_returned_evidence': all(any(f['timecode_sec'] == e['timestamp_sec'] for e in f['evidence']) for f in evidence),
        'notices': ['Binary TP means any finding in a positive video, not a verified correct event type.',
                    '19 positive types need human review; all categories lack established legal grounds.',
                    'The 32-video control subset was previously evaluated in the baseline; it is not unseen-camera validation.']}
    if args.robustness:
        robust = json.loads(Path(args.robustness).read_text(encoding='utf-8'))
        if robust['experiment_fingerprint'] != final['fingerprint']:
            raise ValueError('Robustness and final configuration differ')
        summary['robustness'] = [{k: r.get(k) for k in ('video', 'transform', 'status', 'predictions',
            'baseline_predictions', 'wall_including_copy_sec', 'error_type')} for r in robust['results']]
        summary['notices'].append('Perturbations include re-encoding; this small probe does not establish new-camera robustness.')
    _save(output / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in {'robustness', 'notices', 'experiments'}}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
