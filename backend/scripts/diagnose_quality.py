"""Private, reproducible post-hoc error audit. Reference labels never reach models."""
import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from statistics import mean
import subprocess
import time

from backend.analyzer.candidates import CandidateSelector
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.events import EventExtractor
from backend.analyzer.labels import parse_verdict, read_labels
from backend.analyzer.metrics import compute_metrics
from backend.analyzer.video import VideoReader


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def replay_windows(video, detector, config):
    """No reference time, label, type or filename string is passed to the detector."""
    windows, frame_records = [], []
    extractor = EventExtractor(detector)
    with VideoReader(video) as reader:
        duration = reader.info.duration_sec
        interval = max(3., duration / config.max_windows - config.context_sec / 2)
        selector = CandidateSelector(config.window_frames, config.max_windows,
            min_gap_sec=interval, coverage_sec=interval, context_sec=config.context_sec,
            sample_fps=config.sample_fps, strategy=config.candidate_strategy)

        def record(candidate):
            if candidate:
                windows.append({'reasons': list(candidate.reasons), 'trigger_timestamp_sec': candidate.trigger_timestamp_sec, 'frames': [
                    {'frame_index': f.frame_index, 'timestamp_sec': f.timestamp_sec,
                     'sha256': hashlib.sha256(f.jpeg()).hexdigest()} for f in candidate.frames]})

        import cv2
        for frame in reader.sample(config.sample_fps, config.max_side, config.max_sample_frames):
            pts = reader.capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            events, tracks = extractor.update(frame)
            frame_records.append({'frame_index': frame.frame_index, 'timestamp_sec': frame.timestamp_sec,
                'opencv_pos_msec_sec': pts, 'object_counts': dict(Counter(d.label for d in tracks.values())),
                'selection_events': [e.type for e in events]})
            record(selector.update(frame, events))
        record(selector.finish())
    return {'duration_sec': duration, 'windows': windows, 'sampled_frames': frame_records}


def audit_rows(labels, report, replay=None):
    rows = []
    for source in labels:
        video = source['video']
        truth = parse_verdict(source['has_violation'])
        actual = report['predictions'].get(video)
        group = ('TP' if actual else 'FN') if truth else ('FP' if actual else 'TN')
        if actual is None:
            group = 'unevaluated'
        proposed = source.get('proposed_type') or source.get('violation_type')
        enabled = report['run_inputs']['policy'].get(proposed, {}).get('enabled')
        timing = report['timings'].get(video, {})
        filters = timing.get('diagnostic_reasons', {})
        stamp = source['timecode_sec'] if truth else None
        windows = (replay or {}).get(video, {}).get('windows', [])
        spans = [(w['frames'][0]['timestamp_sec'], w['frames'][-1]['timestamp_sec']) for w in windows]
        times = [f['timestamp_sec'] for w in windows for f in w['frames']]
        covered = any(a <= stamp <= b for a, b in spans) if truth and windows else None
        nearest = min((abs(t - stamp) for t in times), default=None) if truth else None
        reasons = []
        if truth and enabled is False:
            reasons.append({'category': 'policy_disabled', 'status': 'confirmed_for_proposed_type'})
        if source.get('review_status') == 'needs_review':
            reasons.append({'category': 'ambiguous_annotation', 'status': 'confirmed_preliminary_type'})
        if filters.get('insufficient_temporal_evidence'):
            reasons.append({'category': 'insufficient_temporal_evidence', 'status': 'confirmed_some_windows_only'})
        if covered is False:
            reasons.append({'category': 'missed_event_window', 'status': 'confirmed_timestamp_not_covered'})
        if nearest is not None and nearest > .5:
            reasons.append({'category': 'sparse_frame_sampling', 'status': 'confirmed_spacing_not_visibility'})
        if group == 'FP':
            reasons.append({'category': 'vlm_classification_or_reference_scope', 'status': 'unresolved'})
            reasons.append({'category': 'confidence_not_calibrated', 'status': 'confirmed_false_positive'})
        if truth and actual and min(abs(f['timecode_sec'] - stamp) for f in actual) > 3:
            reasons.append({'category': 'localization', 'status': 'confirmed_against_source_time'})
        rows.append({'video': video, 'group': group, 'expected_verdict': truth,
            'expected_timecode_sec': stamp, 'expected_confirmed_type': source.get('violation_type'),
            'proposed_type': proposed, 'review_status': source.get('review_status'),
            'proposed_type_enabled': enabled,
            'predicted_types': [f['violation_type'] for f in actual or []],
            'predicted_timecodes_sec': [f['timecode_sec'] for f in actual or []],
            'predicted_confidences': [f['confidence'] for f in actual or []],
            'timecode_is_selected_evidence_frame': [any(abs(f['timecode_sec'] - e['timestamp_sec']) < 1e-6
                for e in f['evidence']) for f in actual or []],
            'window_frame_times': [[f['timestamp_sec'] for f in w['frames']] for w in windows],
            'source_time_in_window_span': covered, 'nearest_selected_frame_error_sec': nearest,
            'baseline_window_count': timing.get('candidate_windows'), 'replayed_window_count': len(windows) if windows else None,
            'baseline_filter_counts': filters,
            'event_filtered': 'unknown: baseline has no per-window response audit',
            'context_sufficient': 'unverified: temporal coverage does not prove visual visibility',
            'detector_error': 'unverified: no independent object annotations',
            'deduplication_error': 'unverified: baseline lacks pre-dedup findings',
            'cause_assessment': reasons})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='backend/data/reports/evaluation.json')
    parser.add_argument('--labels', default='backend/data/labels.csv')
    parser.add_argument('--videos-dir', default='dataset/videos')
    parser.add_argument('--output-dir', default='backend/data/reports/quality/baseline_2f11600')
    parser.add_argument('--replay-detector', action='store_true')
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = json.loads(Path(args.baseline).read_text(encoding='utf-8'))
    labels = read_labels(args.labels)
    if digest(args.labels) != report['run_inputs']['labels_sha256']:
        raise ValueError('Labels differ from the evaluated baseline')
    copies = [Path(args.baseline), Path(args.baseline + '.checkpoint.json'), Path(args.labels),
        Path('backend/data/reports/evaluation_videos.csv'), Path('backend/data/needs_review.csv'),
        Path('backend/analyzer/output/review_evidence/finding_examples.json'),
        Path('backend/data/violation_types.json'), Path('backend/data/violation_type_policy.json')]
    for source in copies:
        target = output / 'snapshot' / source
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError('Immutable baseline snapshot already exists with different contents')
        if not target.exists():
            shutil.copy2(source, target)
    archive = output / 'baseline_code.zip'
    if not archive.exists():
        subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(archive), '2f11600'], check=True)
    replay_path = output / 'candidate_replay.json'
    replay = json.loads(replay_path.read_text(encoding='utf-8')) if replay_path.exists() else {}
    if args.replay_detector:
        import os
        config = AnalyzerConfig(**report['run_inputs']['config'])
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        for variable, name in (('HF_HOME', 'huggingface'), ('TORCH_HOME', 'torch'), ('CUDA_CACHE_PATH', 'cuda')):
            os.environ[variable] = str(Path(config.temp_dir) / 'runtime-cache' / name)
        from backend.analyzer.network_guard import install_network_guard
        install_network_guard()
        from backend.analyzer.detector import LocalRTDetr
        detector = LocalRTDetr(config.detector_dir, config.device)
        for video in dict.fromkeys(r['video'] for r in labels):
            if video in replay:
                continue
            started = time.monotonic()
            replay[video] = replay_windows(Path(args.videos_dir) / video, detector, config)
            save(replay_path, replay)
            print(json.dumps({'replayed': len(replay), 'total': 42, 'video': video,
                'windows': len(replay[video]['windows']), 'sec': round(time.monotonic() - started, 2)}), flush=True)
    rows = audit_rows(labels, report, replay)
    save(output / 'error_diagnostics.json', rows)
    with (output / 'error_diagnostics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                         for k, v in row.items()} for row in rows)
    confidence = defaultdict(list)
    for row in rows:
        confidence[row['group']].extend(row['predicted_confidences'])
    curves = []
    for threshold in (.75, .9, .95, .97, .98, .99):
        predictions = {v: [f for f in fs if f['confidence'] >= threshold] for v, fs in report['predictions'].items()}
        curves.append({'threshold': threshold, 'metrics': compute_metrics(labels, predictions)['video_detection']})
    duplicates = defaultdict(list)
    for video in report['predictions']:
        duplicates[digest(Path(args.videos_dir) / video)].append(video)
    jpeg_hashes = {f['sha256'] for item in replay.values() for w in item['windows'] for f in w['frames']}
    evidence_hashes = [f['sha256'] for fs in report['predictions'].values() for finding in fs for f in finding['evidence']]
    pts = [abs(f['opencv_pos_msec_sec'] - f['timestamp_sec']) for item in replay.values() for f in item['sampled_frames']]
    summary = {'baseline_commit': '2f116002330b2b8be32cdc452c1fa2b5a3004403',
        'baseline_fingerprint': report['fingerprint'], 'groups': dict(Counter(r['group'] for r in rows)),
        'rows': len(rows), 'replayed_videos': len(replay),
        'temporal_filter_windows': sum(r['baseline_filter_counts'].get('insufficient_temporal_evidence', 0) for r in rows),
        'fn_disabled_types': [r['video'] for r in rows if r['group'] == 'FN' and r['proposed_type_enabled'] is False],
        'source_time_not_covered': [r['video'] for r in rows if r['source_time_in_window_span'] is False],
        'window_count_mismatches': [r['video'] for r in rows if r['replayed_window_count'] is not None and r['baseline_window_count'] != r['replayed_window_count']],
        'baseline_evidence_hashes_reproduced': sum(h in jpeg_hashes for h in evidence_hashes),
        'baseline_evidence_frames': len(evidence_hashes),
        'max_opencv_pts_minus_average_fps_sec': max(pts) if pts else None,
        'confidence_by_video_group': {k: {'n': len(v), 'min': min(v), 'mean': mean(v), 'max': max(v)} for k, v in confidence.items() if v},
        'threshold_sensitivity_not_a_selected_threshold': curves,
        'content_duplicate_groups': [v for v in duplicates.values() if len(v) > 1],
        'snapshot_sha256': {str(p): digest(p) for p in copies},
        'inference_config': asdict(AnalyzerConfig(**report['run_inputs']['config']))}
    save(output / 'diagnosis_summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in {'snapshot_sha256', 'inference_config'}}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
