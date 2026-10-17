import json
from pathlib import Path
import socket

import numpy as np
import pytest

from backend.analyzer.candidates import CandidateSelector
from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.contracts import EvidenceFrame, Finding
from backend.analyzer.detector import MockDetector
from backend.analyzer.errors import InferenceError
from backend.analyzer.labels import parse_verdict, read_labels, video_key
from backend.analyzer.pipeline import Analyzer, _supervise
from backend.analyzer.policy import load_policy
from backend.analyzer.video import Frame
from backend.analyzer.vlm import MockVLM


def test_temporal_windows_span_context_without_unbounded_memory():
    selector = CandidateSelector(6, max_windows=3, context_sec=10, sample_fps=2, coverage_sec=3)
    windows = []
    for i in range(100):
        result = selector.update(Frame(i, i / 2, np.zeros((32, 64, 3), np.uint8)), [])
        if result:
            windows.append(result)
    assert len(windows) == 3 and len(selector.buffer) <= 21
    assert all(len(w.frames) == 6 for w in windows)
    assert windows[-1].frames[-1].timestamp_sec - windows[-1].frames[0].timestamp_sec == 10


def test_sequence_requires_temporally_separated_evidence(synthetic_video, tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"sequence": {"name": "Synthetic", "description": "Synthetic sequence"}}))
    policy = tmp_path / "violation_type_policy.json"
    policy.write_text(json.dumps({"sequence": {"enabled": True, "min_evidence": 2, "min_context_sec": 2.}}))
    config = AnalyzerConfig(catalog_path=str(catalog))
    response = dict(observed_action="Synthetic", has_violation=True, violation_type="sequence",
                    timecode_sec=1., confidence=.9, evidence_indices=[0])
    report = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    assert report.findings == [] and "insufficient_temporal_evidence" in report.diagnostics
    response["evidence_indices"] = [0, 5]
    result = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))
    assert len(result.findings) == 1 and not result.diagnostic_only


def _network_guard_worker(connection):
    from backend.analyzer.network_guard import install_network_guard
    install_network_guard()
    blocked = []
    for action in (lambda: socket.getaddrinfo("example.invalid", 443),
                   lambda: socket.socket().connect(("127.0.0.1", 9))):
        try:
            action()
        except InferenceError:
            blocked.append(True)
    connection.send((True, blocked))
    connection.close()


def _finding_worker(connection, finding):
    connection.send((True, finding))
    connection.close()


def test_network_is_blocked_inside_spawn_worker():
    assert _supervise(_network_guard_worker, (), 5) == [True, True]


def test_finding_bytes_survive_windows_spawn(synthetic_video):
    from backend.analyzer.video import VideoReader
    with VideoReader(synthetic_video) as reader:
        jpeg = reader.at(0).jpeg()
    finding = Finding("synthetic", 0., "Synthetic", .9, [EvidenceFrame(0., jpeg)])
    actual = _supervise(_finding_worker, (finding,), 5)
    assert actual == finding and isinstance(actual.evidence_frames[0].jpeg_bytes, bytes)


def test_270_second_back2_budget():
    assert AnalyzerConfig().timeout_sec <= 260
    with pytest.raises(ValueError):
        AnalyzerConfig(timeout_sec=270)


def test_actual_local_annotation_integrity():
    root = Path(__file__).resolve().parents[2]
    path = root / "backend/data/labels.csv"
    decisions_path = root / "backend/data/annotations/final_review.json"
    if not path.exists() or not decisions_path.exists():
        pytest.skip("Private local annotations are intentionally not shipped")
    labels = read_labels(path)
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    catalog_path = root / "backend/data/violation_types.json"
    catalog = load_catalog(catalog_path)
    policy = load_policy(catalog, catalog_path)
    assert len(labels) == decisions["expected_rows"] == 42
    positive = [r for r in labels if parse_verdict(r["has_violation"])]
    assert len(positive) == decisions["expected_positive"] == 22
    assert len({video_key(r["video"]) for r in labels}) == 42
    for row in labels:
        if not parse_verdict(row["has_violation"]):
            assert row["violation_type"] == row["timecode_sec"] == ""
            continue
        choice = next(v for k, v in decisions["decisions"].items() if video_key(k) == video_key(row["video"]))
        assert row["timecode_sec"] == float(row["organizer_timecode_sec"])
        assert row["proposed_type"] == choice["type_id"] and row["proposed_type"] in catalog
        assert row["review_status"] == choice["review_status"]
        if row["review_status"] == "confirmed_visual":
            assert row["violation_type"] == choice["type_id"] and policy[row["violation_type"]]["enabled"]
        else:
            assert row["violation_type"] == "unknown"
        assert row["observed_action"] == choice.get("observed_action", row["source_observed_action"])


def test_catalog_policy_cannot_silently_omit_types(tmp_path):
    path = tmp_path / "catalog.json"
    policy = tmp_path / "violation_type_policy.json"
    catalog = {"a": {"name": "A", "description": "Visible"}}
    policy.write_text("{}")
    with pytest.raises(ValueError, match="exact catalog"):
        load_policy(catalog, path)


def test_schema_retry_is_bounded_and_never_fakes_a_positive(synthetic_video, tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"synthetic": {"name": "Synthetic", "description": "Synthetic"}}))

    class FlakyVLM(MockVLM):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def infer(self, *args):
            self.calls += 1
            return "{}" if self.calls == 1 else super().infer(*args)

    vlm = FlakyVLM()
    config = AnalyzerConfig(catalog_path=str(catalog), temp_dir=str(tmp_path / "logs"))
    report = Analyzer(MockDetector(), vlm, config, allow_mock=True).analyze(str(synthetic_video))
    assert vlm.calls == 2 and report.findings == []
    assert "schema_retry" in report.diagnostics
    assert len(list((tmp_path / "logs/diagnostics").glob("*.json"))) == 1
