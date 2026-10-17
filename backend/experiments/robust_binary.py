"""Sequential mirror/lighting probes of a frozen binary experiment; private MOV copies only."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import time

from backend.analyzer.config import AnalyzerConfig
from backend.experiments.binary_sequence import run_video
from backend.scripts.check_robustness import derived_video
from backend.scripts.evaluate import _save, verify_findings


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--videos-dir', default='dataset/videos')
    parser.add_argument('--transforms', nargs='+', choices=['mirror', 'lighting', 'scale', 'crop'], default=['mirror', 'lighting'])
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    config = AnalyzerConfig(**source['manifest']['config'])
    # Fixed diagnostic TP and FP selected before this experiment's predictions.
    # No reference event timestamps are used by transformation or inference.
    diagnostic = source['manifest']['split']['diagnostic']
    videos = [diagnostic[0], diagnostic[2]]
    output = Path(args.source).parent / 'robustness.json'
    if output.exists():
        raise ValueError('Probe exists; never overwrite recorded results')
    records = []
    frozen = {'source_sha256': sha(args.source), 'probe_code_sha256': sha(__file__),
        'config': asdict(config), 'transforms': args.transforms, 'videos': videos,
        'notice': 'Perturbation consistency only; not a new physical camera or proof of generalization.'}
    for video in videos:
        original = Path(args.videos_dir) / video
        original_hash = sha(original)
        if original_hash != source['manifest']['video_hashes'][video]:
            raise ValueError('Source video changed')
        for kind in args.transforms:
            record = dict(video=video, transform=kind)
            tick = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='probe-', dir=config.temp_dir) as directory:
                target = Path(directory) / 'input.mov'
                record['metadata'] = derived_video(original, target, kind)
                record['copy_sec'] = time.monotonic() - tick
                try:
                    report, windows = run_video(str(target.resolve()), config)
                    record.update(status='evaluated', predictions=verify_findings(report.findings),
                        timings=report.timings, windows=windows, peak_vram_bytes=report.peak_vram_bytes)
                except Exception as exc:
                    record.update(status='error', error_type=type(exc).__name__, message=str(exc))
            if sha(original) != original_hash:
                raise ValueError('Original video changed during probe')
            record['wall_including_copy_sec'] = time.monotonic() - tick
            records.append(record)
            _save(output, {'manifest': frozen, 'records': records})
            print(json.dumps(dict(completed=len(records), transform=kind, status=record['status'],
                wall_sec=record['wall_including_copy_sec'], findings=len(record.get('predictions', []))), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
