"""Isolated object-first prompt trial; no new calls, labels or model training."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.pipeline import _supervise
from backend.scripts.evaluate import _save, verify_findings


OBJECT_IDENTITY = (
    "Before selecting ANY safe/door category, identify the physical object from visible structure. "
    "A ROOM DOOR closes a human passage connected to the floor and surrounding wall/partition. "
    "A person walking through its opening is strong evidence of a room passage, NOT a safe. "
    "A SAFE is an enclosed storage body with a bounded internal cavity and clearly visible security "
    "construction such as a reinforced door edge, locking bolts or an identifiable safe lock. "
    "A rectangular swinging panel, a handle, dark color, or a person bending down do NOT identify a safe. "
    "A cupboard/drawer is not automatically a safe. Do not infer a safe from the banking context. "
    "If the object's physical identity is unclear, do not assign a safe or door category. "
    "Only AFTER identifying the object, inspect the sequence: safe_door_open needs an identified "
    "safe with its door and cavity visibly open in multiple frames; cash_room_door_left_open needs "
    "a person returning to the workstation while the identified room door remains visibly open "
    "afterwards. Opening a room door alone is not this sequence. key_left_in_safe additionally "
    "requires a clearly resolved key in that safe's lock. Do not automatically swap category names. "
    "Do not use fixed screen coordinates, color, camera angle or person identity as a rule. "
    "These checks do not prohibit independent cash/document events in the same frames. "
)


def _experiment_worker(connection, video_path, config, deadline):
    from backend.analyzer import pipeline
    original = pipeline.build_prompt

    def object_first(*args, **kwargs):
        return OBJECT_IDENTITY + original(*args, **kwargs)

    pipeline.build_prompt = object_first
    pipeline._production_worker(connection, video_path, config, deadline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--numbers', nargs='+', required=True)
    parser.add_argument('--output', default='backend/data/reports/object_identity/prompt_v1')
    args = parser.parse_args()
    output = Path(args.output)
    results = {}
    for number in args.numbers:
        candidates = list(Path('dataset/videos').glob(f'*-{int(number)}.mov'))
        if len(candidates) != 1:
            raise ValueError('Expected exactly one authorized local source')
        path = candidates[0]
        config = replace(AnalyzerConfig(), record_window_audit=True,
                         temp_dir=str(output / number / 'runtime'))
        started = time.monotonic()
        try:
            report = _supervise(_experiment_worker,
                (str(path.resolve()), config, started + config.timeout_sec), config.timeout_sec)
            record = dict(status='done', wall_sec=time.monotonic() - started,
                findings=verify_findings(report.findings), windows=report.candidate_windows,
                peak_reserved_vram_bytes=report.peak_reserved_vram_bytes,
                timings=report.timings)
            _save(output / number / 'private_audit.json', report.window_audit)
        except Exception as exc:
            record = dict(status='failed', error_type=type(exc).__name__, wall_sec=time.monotonic()-started)
        results[number] = record
        _save(output / 'results.json', results)
        print(json.dumps(dict(video_number=number, status=record['status'],
            seconds=round(record['wall_sec'],2), types=[f['violation_type'] for f in record.get('findings', [])])), flush=True)


if __name__ == '__main__':
    main()
