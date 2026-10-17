"""Verify cached model-selected windows, never ground-truth timestamps."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.ipc import send_result
from backend.analyzer.pipeline import _supervise, _worker_error
from backend.scripts.evaluate import _save


def _worker(connection, requests, output, extended):
    try:
        from backend.analyzer.network_guard import install_network_guard
        from backend.analyzer.performance import request_inference_performance
        from backend.analyzer.video import VideoReader
        from backend.analyzer.vlm import LocalTransformersVLM
        from backend.experiments.object_check import schema, prompt, parse, supported_type
        import torch
        install_network_guard()
        request_inference_performance()
        torch.set_num_threads(1)
        config = AnalyzerConfig()
        vlm = LocalTransformersVLM(config.vlm_dir, trace_dir=Path(output)/'diagnostics', release_cuda_cache=True)
        results = []
        for request in requests:
            with VideoReader(request['video']) as reader:
                if extended:
                    lo = max(0, request['anchor']-5)
                    hi = min(reader.info.duration_sec-1/reader.info.fps, request['anchor']+10)
                    frames = [reader.at(lo + (hi-lo)*i/5, 640) for i in range(6)]
                else:
                    frames = [reader.at(item['timestamp_sec'], 640) for item in request['frames']]
            images = [frame.jpeg() for frame in frames]
            if not extended and any(hashlib.sha256(data).hexdigest() != item['sha256'] for data,item in zip(images, request['frames'])):
                raise ValueError('Re-extracted frames differ from baseline evidence')
            stamps = [f.timestamp_sec for f in frames]
            vlm.set_response_schema(schema(len(frames)))
            started = time.monotonic()
            raw = vlm.infer(images, prompt(stamps, request['anchor'], arrival_movement=extended), 40)
            data = parse(raw, len(frames))
            record = dict(number=request['number'], original_type=request['type'],
                checked_type=supported_type(data, stamps), checks=data, seconds=time.monotonic()-started)
            results.append(record)
            _save(Path(output)/'results.json',results)
        message = (True, results)
    except Exception as exc:
        message = _worker_error(exc)
    try:
        send_result(connection,message)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extended', action='store_true')
    args = parser.parse_args()
    baseline = json.loads(Path('backend/data/reports/quality/stable_anchor_cache_v7/full.json').read_text())
    requests = []
    for video, windows in baseline['window_audits'].items():
        number = video.rsplit('-',1)[-1].split('.')[0]
        if number not in {'1','4','32','25'}:
            continue
        path = next(Path('dataset/videos').glob(f'*-{number}.mov'))
        for window in windows:
            response = window.get('response',{})
            if window['outcome'] == 'accepted' and response.get('violation_type') in {'safe_door_open','cash_room_door_left_open'}:
                requests.append(dict(video=str(path.resolve()),number=number,type=response['violation_type'],
                    anchor=window.get('finding_timecode_sec',response['timecode_sec']),frames=window['frames']))
    output='backend/data/reports/object_identity/object_check_v2' if args.extended else 'backend/data/reports/object_identity/object_check_v1'
    results = _supervise(_worker,(requests,output,args.extended),260)
    print(json.dumps(results),flush=True)


if __name__ == '__main__':
    main()
