"""Small 12-vs-18 type sensitivity probe. Never change the production policy."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

from backend.analyzer.config import AnalyzerConfig
from backend.experiments.binary_sequence import run_video
from backend.scripts.evaluate import _save, verify_findings


def experimental_policy(policy):
    """Enable coverage only; preserve every evidence/context/review requirement."""
    return {key: {**rule, 'enabled': True} for key, rule in policy.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--videos-dir', default='dataset/videos')
    args = parser.parse_args()
    source_path = Path(args.source)
    source = json.loads(source_path.read_text(encoding='utf-8'))
    original = AnalyzerConfig(**source['manifest']['config'])
    path = Path(original.catalog_path).with_name('violation_type_policy.json')
    original_bytes = path.read_bytes()
    policy = json.loads(original_bytes)
    output = source_path.parent / 'policy18'
    if (output / 'results.json').exists():
        raise ValueError('Policy probe already exists')
    shadow = output / 'experimental_policy.json'
    _save(shadow, experimental_policy(policy))
    config = replace(original, policy_path=str(shadow.resolve()), temp_dir=str(output / 'runtime'))
    videos = source['manifest']['split']['diagnostic'][1:3]  # frozen FN and FP
    records = []
    for video in videos:
        record = {'video': video, 'policy12_predictions': source['predictions'][video]}
        tick = time.monotonic()
        try:
            report, windows = run_video(str((Path(args.videos_dir) / video).resolve()), config)
            record.update(status='evaluated', predictions=verify_findings(report.findings),
                timings=report.timings, windows=windows, window_audit=report.window_audit,
                peak_vram_bytes=report.peak_vram_bytes)
        except Exception as exc:
            record.update(status='error', error_type=type(exc).__name__, message=str(exc))
        record['wall_sec'] = time.monotonic() - tick
        records.append(record)
        if path.read_bytes() != original_bytes:
            raise ValueError('Production policy unexpectedly changed')
        _save(output / 'results.json', {'records': records,
            'source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'Two previously inspected development videos; type coverage only, not approval of all categories.'})
        print(json.dumps(dict(completed=len(records), status=record['status'], wall_sec=record['wall_sec'],
            findings=len(record.get('predictions', []))), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
