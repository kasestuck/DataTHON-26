"""Frozen local inference experiment with grouped development/control subsets."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import random
import subprocess

from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.labels import read_labels, parse_verdict
from backend.analyzer.metrics import compute_metrics
from backend.analyzer.pipeline import analyze_video_report
from backend.analyzer.policy import load_policy
from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import evaluate, _save


def grouped_split(labels, predictions, hashes, seed=26, policy=None):
    """Keep exact content duplicates together; select two groups per baseline outcome."""
    groups = {}
    for row in labels:
        video = row['video']
        truth, actual = parse_verdict(row['has_violation']), bool(predictions[video])
        category = ('TP' if actual else 'FN') if truth else ('FP' if actual else 'TN')
        entry = groups.setdefault(hashes[video], {'videos': [], 'categories': set()})
        entry['videos'].append(video)
        entry['categories'].add(category)
    rng = random.Random(seed)
    diagnostic_groups = set()
    for category in ('FN', 'FP', 'TP', 'TN'):
        options = sorted(k for k, value in groups.items() if category in value['categories'])
        rng.shuffle(options)
        diagnostic_groups.update(options[:2])
    if policy:
        enabled_fn = [r for r in labels if parse_verdict(r['has_violation']) and not predictions[r['video']]
            and policy.get(r.get('proposed_type') or r.get('violation_type'), {}).get('enabled')]
        trusted = sorted({hashes[r['video']] for r in enabled_fn if r.get('review_status') == 'confirmed_visual'})
        choices = sorted({hashes[r['video']] for r in enabled_fn} - set(trusted))
        rng.shuffle(choices)
        diagnostic_groups.update((trusted + choices)[:2])
    diagnostic = [row['video'] for row in labels if hashes[row['video']] in diagnostic_groups]
    return {'seed': seed, 'diagnostic': diagnostic,
        'control': [row['video'] for row in labels if row['video'] not in diagnostic],
        'notice': 'All 42 videos were previously evaluated; control is not an unseen-camera or jury test.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True)
    parser.add_argument('--prompt-version', choices=('baseline', 'sequence_v1', 'frame_bound_v2'), default='sequence_v1')
    parser.add_argument('--phase', choices=('pilot', 'full'), default='pilot')
    parser.add_argument('--candidate-strategy', choices=('uniform', 'trigger'), default='uniform')
    parser.add_argument('--include-event-anchor', action='store_true')
    parser.add_argument('--release-cuda-cache', action='store_true')
    parser.add_argument('--baseline', default='backend/data/reports/quality/baseline_2f11600/snapshot/backend/data/reports/evaluation.json')
    parser.add_argument('--labels', default='backend/data/labels.csv')
    parser.add_argument('--videos-dir', default='dataset/videos')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not args.id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in args.id):
        parser.error('Experiment id must contain lowercase ASCII letters, digits, underscore or hyphen')
    output = Path('backend/data/reports/quality') / args.id
    if (output / 'checkpoint.json').exists() and not args.resume:
        parser.error('Experiment already has results: use --resume with unchanged inputs or a new id')
    baseline = json.loads(Path(args.baseline).read_text(encoding='utf-8'))
    labels = read_labels(args.labels)
    if digest(args.labels) != baseline['run_inputs']['labels_sha256']:
        raise ValueError('Reference labels changed since the baseline')
    catalog = load_catalog(AnalyzerConfig.from_env().catalog_path)
    config = replace(AnalyzerConfig.from_env(), prompt_version=args.prompt_version, record_window_audit=True,
        candidate_strategy=args.candidate_strategy, include_event_anchor=args.include_event_anchor,
        release_cuda_cache=args.release_cuda_cache)
    policy = load_policy(catalog, config.catalog_path, config.policy_path)
    hashes = {row['video']: digest(Path(args.videos_dir) / row['video']) for row in labels}
    split = grouped_split(labels, baseline['predictions'], hashes, policy=policy)
    inputs = {'experiment_id': args.id, 'baseline_fingerprint': baseline['fingerprint'],
        'labels_sha256': digest(args.labels), 'catalog': catalog, 'policy': policy,
        'config': asdict(config), 'tolerance_sec': 3., 'video_hashes': hashes, 'split': split,
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'code_sha256': {str(p): digest(p) for p in sorted(Path('backend/analyzer').glob('*.py'))}
            | {str(p): digest(p) for p in (Path(__file__), Path('backend/scripts/evaluate.py'))},
        'models': baseline['run_inputs']['models']}
    fingerprint = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    manifest = output / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != inputs:
        raise ValueError('Experiment inputs changed: use a new id, never overwrite an experiment')
    _save(manifest, inputs)
    for name, expected in inputs['code_sha256'].items():
        source = Path(name)
        target = output / 'code_snapshot' / source.resolve().relative_to(Path.cwd())
        if digest(source) != expected or (target.exists() and digest(target) != expected):
            raise ValueError('Experiment source snapshot mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(source.read_bytes())
    selected = labels if args.phase == 'full' else [r for r in labels if r['video'] in split['diagnostic']]
    # Model receives only the video path and one global config, never a source row.
    result = evaluate(selected, args.videos_dir, runner=lambda path: analyze_video_report(path, config),
        valid_types=catalog, checkpoint=output / 'checkpoint.json', fingerprint=fingerprint,
        resume=args.resume, progress=lambda value: print(json.dumps(value, ensure_ascii=True), flush=True), stop_on_error=True)
    result.update(run_inputs=inputs, fingerprint=fingerprint, phase=args.phase,
        planned_videos=len(selected), planned_full_videos=len(labels))
    _save(output / (args.phase + '.json'), result)
    comparison = {'experiment_id': args.id, 'phase': args.phase, 'evaluated': result['evaluated_videos'],
        'errors': len(result['skipped']), 'runtime': result['runtime'],
        'baseline_on_same_videos': compute_metrics([r for r in selected if r['video'] in result['predictions']],
            {v: baseline['predictions'][v] for v in result['predictions']}),
        'experiment_metrics': {k: result[k] for k in ('video_detection', 'localization', 'classification')},
        'improved_videos': [], 'worsened_videos': [],
        'scope_notice': split['notice']}
    for row in selected:
        video = row['video']
        if video not in result['predictions']:
            continue
        truth = parse_verdict(row['has_violation'])
        before = bool(baseline['predictions'][video]) == truth
        after = bool(result['predictions'][video]) == truth
        if before != after:
            comparison['improved_videos' if after else 'worsened_videos'].append(video)
    if args.phase == 'full':
        comparison['control_metrics'] = compute_metrics([r for r in labels if r['video'] in split['control']],
            {v: fs for v, fs in result['predictions'].items() if v in split['control']})
        comparison['baseline_control_metrics'] = compute_metrics([r for r in labels if r['video'] in split['control']],
            {v: fs for v, fs in baseline['predictions'].items() if v in split['control']})
    _save(output / (args.phase + '_comparison.json'), comparison)
    print(json.dumps({k: v for k, v in comparison.items() if k not in
        {'baseline_on_same_videos', 'control_metrics', 'baseline_control_metrics'}}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
