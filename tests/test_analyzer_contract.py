import base64
import json
from pathlib import Path

import cv2
import numpy as np

from backend.analyzer import EvidenceFrame, Finding
from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.policy import load_policy
from backend.api.config import Settings
from backend.api.result import format_result


def test_real_back1_dataclasses_and_jpeg_survive_api_serialization(tmp_path):
    catalog = {'synthetic': {'name': 'Synthetic visual event', 'description': 'Synthetic pixels'}}
    (tmp_path / 'violation_types.json').write_text(json.dumps(catalog), encoding='utf-8')
    ok, encoded = cv2.imencode('.jpg', np.full((32, 48, 3), 100, dtype=np.uint8))
    assert ok
    data = encoded.tobytes()
    finding = Finding('synthetic', 1., 'Synthetic observation', .8, [EvidenceFrame(1., data)])
    result = format_result([finding], tmp_path)
    output = result['violations'][0]
    restored = base64.b64decode(output['evidence_frames'][0]['image_base64'], validate=True)
    assert restored == data and cv2.imdecode(np.frombuffer(restored, np.uint8), cv2.IMREAD_COLOR) is not None
    assert result['verdict'] == 'violation' and output['timecode_sec'] == 1.
    assert 'confidence' not in output and 'Источник' not in output['description']


def test_integrated_defaults_keep_real_models_catalog_and_deadline_margin():
    core = AnalyzerConfig()
    api = Settings(api_key='synthetic', data_dir=Path('var'))
    assert api.analyzer_target == 'backend.analyzer:analyze_video'
    assert core.timeout_sec == 260 and api.timeout_seconds == 270
    catalog = load_catalog(core.catalog_path)
    assert catalog and all(set(value) == {'name', 'description'} for value in catalog.values())
    assert set(load_policy(catalog, core.catalog_path, core.policy_path)) == set(catalog)
