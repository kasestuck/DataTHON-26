"""Submission regression checks; simulated faults never count as real AI tests."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.api.app import create_app
from backend.api.config import Settings
from backend.api.display_text import ANALYSIS_ERROR
from backend.api.jobs import JobStore
from backend.api.worker import JobWorker
from tests.fixtures import MOV_BYTES


def synthetic_memory_error(video_path):
    raise MemoryError('synthetic private diagnostic C:/private/synthetic.mov token=synthetic-secret')


def synthetic_cuda_oom(video_path):
    exception = type('OutOfMemoryError', (RuntimeError,), {})
    raise exception('CUDA synthetic diagnostic C:/private/synthetic.mov token=synthetic-secret')


@pytest.mark.parametrize('target', ['synthetic_memory_error', 'synthetic_cuda_oom'])
def test_memory_failure_is_failed_without_a_verdict_or_private_details(tmp_path, target):
    settings = Settings(api_key='synthetic-access-key', data_dir=tmp_path,
        analyzer_target=f'tests.test_submission_freeze:{target}', start_worker=False)
    with TestClient(create_app(settings)) as api:
        headers = {'X-API-Key': settings.api_key}
        job_id = api.post('/api/v1/analyze', headers=headers,
            files={'file': ('synthetic.mov', MOV_BYTES)}).json()['job_id']
        assert JobWorker(JobStore(tmp_path / 'jobs.sqlite3'), settings).process_next()
        response = api.get(f'/api/v1/jobs/{job_id}', headers=headers)
        assert response.status_code == 200
        assert response.json() == {'job_id': job_id, 'status': 'failed', 'error': ANALYSIS_ERROR}
        assert not any(word in response.text for word in ['CUDA', 'private', 'synthetic-secret', settings.api_key])
    assert not list((tmp_path / 'uploads').glob('*.mov'))


def test_jury_routes_require_key_for_every_job_status(tmp_path):
    settings = Settings(api_key='synthetic-access-key', data_dir=tmp_path,
        frontend_dist=Path('frontend'), start_worker=False)
    store = JobStore(tmp_path / 'jobs.sqlite3')
    jobs = [store.create(tmp_path / f'{i}.mov') for i in range(3)]
    store.finish(jobs[1], {'verdict': 'no_violation', 'violations': []})
    store.fail(jobs[2], ANALYSIS_ERROR)
    with TestClient(create_app(settings)) as api:
        session = api.get('/ui-api/v1/session').json()['csrf_token']
        for job_id in jobs:
            for headers in ({}, {'X-API-Key': 'wrong'}, {'X-CSRF-Token': session}):
                response = api.get(f'/api/v1/jobs/{job_id}', headers=headers)
                assert response.status_code == 401
                assert job_id not in response.text and settings.api_key not in response.text
            assert api.get(f'/api/v1/jobs/{job_id}', headers={'X-API-Key': settings.api_key}).status_code == 200
