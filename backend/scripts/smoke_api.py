"""Real loopback HTTP + Uvicorn + local AI smoke. No mocks or cloud requests."""
import argparse
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import cv2
import httpx
import numpy as np

from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import _save


def run(video: Path, *, require_finding=False):
    source_hash = digest(video)
    root = Path(__file__).resolve().parents[2]
    private = root / 'backend/analyzer/output'
    private.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((root / 'backend/data/violation_types.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='http-smoke-', dir=private) as directory:
        data = Path(directory)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        key = secrets.token_urlsafe(32)
        env = os.environ.copy()
        for name in ('FRONTEND_DIST', 'PUBLIC_ORIGIN', 'CORS_ORIGINS'):
            env.pop(name, None)
        env.update(API_KEY=key, DATA_DIR=str(data), ANALYZER_TARGET='backend.analyzer:analyze_video',
                   CATALOG_DIR=str(root / 'backend/data'), ANALYZER_TEMP_DIR=str(data / 'analyzer'))
        with (data / 'uvicorn.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.api.app:app_from_env',
                '--factory', '--host', '127.0.0.1', '--port', str(port), '--workers', '1', '--log-level', 'warning'],
                cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), start_new_session=os.name == 'posix')
            started = time.monotonic()
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'X-API-Key': key},
                                  timeout=15, trust_env=False) as client:
                    ready_deadline = time.monotonic() + 30
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError('Uvicorn exited before startup')
                        try:
                            if client.get('/health').json() == {'status': 'ok'}:
                                break
                        except (httpx.HTTPError, ValueError):
                            pass
                        if time.monotonic() >= ready_deadline:
                            raise TimeoutError('Uvicorn startup timeout')
                        time.sleep(.2)
                    with video.open('rb') as stream:
                        uploaded = client.post('/api/v1/analyze', files={'file': ('input.mov', stream, 'video/quicktime')})
                    uploaded.raise_for_status()
                    valid_id = uploaded.json()['job_id']
                    invalid = b'\x00\x00\x00\x18ftypqt  ' + bytes(12)
                    bad = client.post('/api/v1/analyze', files={'file': ('invalid.mov', invalid, 'video/quicktime')})
                    bad.raise_for_status()
                    bad_id = bad.json()['job_id']
                    jobs, max_active = {}, 0
                    deadline = time.monotonic() + 290
                    while len(jobs) < 2:
                        with closing(sqlite3.connect(data / 'jobs.sqlite3')) as db:
                            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status='processing' AND claimed=1").fetchone()[0]
                        max_active = max(max_active, active)
                        if active > 1:
                            raise AssertionError('More than one video claimed simultaneously')
                        for job_id in (valid_id, bad_id):
                            response = client.get(f'/api/v1/jobs/{job_id}')
                            response.raise_for_status()
                            value = response.json()
                            if value['status'] in {'done', 'failed'}:
                                jobs[job_id] = value
                        if time.monotonic() >= deadline:
                            raise TimeoutError('API jobs did not complete within their deadlines')
                        time.sleep(.3)
                    success, failure = jobs[valid_id], jobs[bad_id]
                    assert success['status'] == 'done', 'Real AI job failed; no negative verdict substituted'
                    assert failure['status'] == 'failed' and 'verdict' not in failure
                    assert max_active == 1
                    findings = success['violations']
                    assert success['verdict'] == ('violation' if findings else 'no_violation')
                    if require_finding:
                        assert findings, 'The real call returned no evidence to validate'
                    frames = []
                    for finding in findings:
                        assert finding['type'] in {v['name'] for v in catalog.values()}
                        evidence = finding['evidence_frames']
                        assert 1 <= len(evidence) <= 3
                        assert finding['timecode_sec'] in [e['timestamp_sec'] for e in evidence]
                        for frame in evidence:
                            jpeg = base64.b64decode(frame['image_base64'], validate=True)
                            assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) is not None
                            frames.append({'timestamp_sec': frame['timestamp_sec'], 'sha256': hashlib.sha256(jpeg).hexdigest()})
                    cleanup_deadline = time.monotonic() + 8
                    while list((data / 'uploads').glob('*.mov')) and time.monotonic() < cleanup_deadline:
                        time.sleep(.1)
                    assert not list((data / 'uploads').glob('*.mov'))
                    assert digest(video) == source_hash
                    return {'success': True, 'transport': 'real HTTP on loopback via Uvicorn',
                        'analyzer_target': 'backend.analyzer:analyze_video', 'is_mock': False,
                        'source_sha256': source_hash, 'wall_sec': time.monotonic() - started,
                        'real_video_status': success['status'], 'verdict': success['verdict'],
                        'findings': len(findings), 'decoded_jpegs': len(frames), 'evidence': frames,
                        'corrupt_video_status': failure['status'], 'error_has_no_verdict': 'verdict' not in failure,
                        'max_claimed_processing': max_active, 'uploads_removed': True}
            finally:
                # Windows children inherit the log handle. Stop the owned tree
                # before TemporaryDirectory removes it, as in smoke_fullstack.
                if os.name == 'nt' and server.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                    server.kill()
                    server.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('backend/data/reports/integration_BACK/e2e_api.json'))
    parser.add_argument('--require-finding', action='store_true')
    args = parser.parse_args()
    result = run(args.video, require_finding=args.require_finding)
    _save(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence'}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
