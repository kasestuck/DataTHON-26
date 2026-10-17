"""Local perturbation probes; transformations do not establish new-camera robustness."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.pipeline import analyze_video_report
from backend.analyzer.errors import AnalyzerError
from backend.analyzer.video import VideoReader
from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import _save, verify_findings


def transform_frame(image, kind):
    if kind == 'mirror':
        return cv2.flip(image, 1)
    if kind == 'scale':
        return cv2.resize(image, None, fx=.75, fy=.75, interpolation=cv2.INTER_AREA)
    if kind == 'crop':
        h, w = image.shape[:2]
        # A controlled image perturbation, never a role/zone decision rule.
        dy, dx = max(1, round(h * .05)), max(1, round(w * .05))
        return image[dy:h-dy, dx:w-dx].copy()
    if kind == 'lighting':
        return np.clip(image.astype(np.float32) * .85 + 10, 0, 255).astype(np.uint8)
    raise ValueError('Unknown robustness transformation')


def derived_video(source, target, kind):
    capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise ValueError('Cannot open robustness source')
    writer = None
    count = 0
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            transformed = transform_frame(frame, kind)
            h, w = transformed.shape[:2]
            # MPEG4 requires even dimensions. Bound the adjustment to one pixel.
            transformed = transformed[:h-h % 2, :w-w % 2]
            if writer is None:
                writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'mp4v'), fps,
                                        (transformed.shape[1], transformed.shape[0]))
                if not writer.isOpened():
                    raise ValueError('Cannot encode private robustness copy')
            writer.write(transformed)
            count += 1
    finally:
        capture.release()
        if writer:
            writer.release()
    if not count:
        raise ValueError('No frames in robustness source')
    with VideoReader(source) as original, VideoReader(target) as derived:
        if original.info.frame_count != derived.info.frame_count:
            raise ValueError('Robustness copy changed the source frame count')
        return {'source': original.info.__dict__, 'derived': derived.info.__dict__, 'frames': count,
            'notice': 'Derived constant-rate MOV has no audio; source frames are never overwritten.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', required=True, help='Completed experiment pilot/full JSON')
    parser.add_argument('--videos', nargs='+', required=True)
    parser.add_argument('--videos-dir', default='dataset/videos')
    parser.add_argument('--output', default='backend/data/reports/quality/robustness.json')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    experiment = json.loads(Path(args.experiment).read_text(encoding='utf-8'))
    config = replace(AnalyzerConfig(**experiment['run_inputs']['config']), record_window_audit=True)
    results = []
    output = Path(args.output)
    if args.resume and output.exists():
        existing = json.loads(output.read_text(encoding='utf-8'))
        if existing['experiment_fingerprint'] != experiment['fingerprint']:
            raise ValueError('Robustness checkpoint belongs to a different experiment')
        results = existing['results']
    for video in args.videos:
        if Path(video).name != video or '\\' in video:
            raise ValueError('Invalid source filename')
        source = Path(args.videos_dir) / video
        source_digest = digest(source)
        for kind in ('mirror', 'scale', 'crop', 'lighting'):
            if any(r['video'] == video and r['transform'] == kind for r in results):
                continue
            started = time.monotonic()
            record = {'video': video, 'transform': kind, 'source_sha256': source_digest,
                'baseline_predictions': experiment['predictions'].get(video)}
            with tempfile.TemporaryDirectory(prefix='robustness-', dir=config.temp_dir) as directory:
                target = Path(directory) / 'input.mov'
                record['video_metadata'] = derived_video(source, target, kind)
                try:
                    result = analyze_video_report(str(target), config)
                    record.update(status='evaluated', predictions=verify_findings(result.findings,
                        experiment['run_inputs']['catalog']), timings=result.timings,
                        peak_vram_bytes=result.peak_vram_bytes, window_audit=result.window_audit)
                except AnalyzerError as exc:
                    record.update(status='error', error_type=type(exc).__name__, error=str(exc))
            if digest(source) != source_digest:
                raise ValueError('Original video unexpectedly changed')
            record['wall_including_copy_sec'] = time.monotonic() - started
            results.append(record)
            _save(output, {'experiment_fingerprint': experiment['fingerprint'], 'results': results,
                'notice': 'Small perturbation probes, not evidence of generalization to another camera.'})
            print(json.dumps({k: record[k] for k in ('video', 'transform', 'status', 'wall_including_copy_sec')}
                | {'findings': len(record.get('predictions', [])) if record['status'] == 'evaluated' else None},
                ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
