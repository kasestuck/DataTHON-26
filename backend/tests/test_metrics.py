from backend.analyzer.metrics import compute_metrics, match_events
from backend.analyzer.pipeline import AnalysisReport
from backend.scripts.benchmark import benchmark_video
from backend.scripts.check_environment import inspect_environment
from backend.scripts.evaluate import evaluate
import pytest


def label(video, positive, stamp="", kind=""):
    return dict(video=video, has_violation=str(positive).lower(), timecode_sec=stamp, violation_type=kind)


def test_video_localization_classification():
    labels = [label("a.mov", True, 10, "known"), label("b.mov", False),
              label("c.mov", True, 20, "unknown"), label("d.mov", True, 30, "known")]
    predictions = {"a.mov": [{"timecode_sec": 11., "violation_type": "known"}],
                   "b.mov": [{"timecode_sec": 2., "violation_type": "known"}],
                   "c.mov": [{"timecode_sec": 20., "violation_type": "known"}], "d.mov": []}
    result = compute_metrics(labels, predictions)
    assert result["video_detection"]["tp"] == 2
    assert result["video_detection"]["fp"] == result["video_detection"]["fn"] == 1
    assert result["video_detection"]["f1"] == 2 / 3
    assert result["localization"]["mean_abs_time_error_sec_matched"] == .5
    assert result["classification"]["evaluated_videos"] == 3
    assert result["classification"]["micro"]["tp"] == 1
    assert "unknown" not in result["classification"]["per_type"]


def test_no_metrics_for_unavailable_or_mock(tmp_path):
    labels = [label("a.mov", True, 0, "unknown")]
    report = evaluate(labels, tmp_path, runner=lambda _: AnalysisReport(is_mock=True))
    assert report["status"] == "unavailable"
    assert report["video_detection"] is None and report["classification"] is None
    assert report["skipped"][0]["reason"] == "mock_forbidden"
    assert compute_metrics(labels, {})["unevaluated_videos"] == ["a.mov"]
    report = evaluate(labels, tmp_path, runner=lambda _: AnalysisReport(diagnostic_only=True))
    assert report["video_detection"] is None
    assert report["skipped"][0]["reason"] == "empty_catalog_diagnostics_only"
    with pytest.raises(ValueError):
        compute_metrics(labels, {}, float("nan"))
    report = evaluate(labels, tmp_path, runner=lambda _: pytest.fail("Must not infer without a catalog"), catalog_available=False)
    assert report["status"] == "unavailable" and report["video_detection"] is None


def test_matching_maximum_cardinality():
    truth = [{"timecode_sec": 2}, {"timecode_sec": 4}]
    predicted = [{"timecode_sec": 3}, {"timecode_sec": 0}]
    assert len(match_events(truth, predicted, 2)) == 2


def test_review_labels_excluded_from_types_but_source_binary_retained():
    rows = [dict(label("a.mov", True, 10, "known"), review_status="needs_review"), label("b.mov", False)]
    report = compute_metrics(rows, {"a.mov": [], "b.mov": []})
    assert report["video_detection"]["fn"] == 1
    assert report["classification"]["evaluated_videos"] == 1
    assert report["classification"]["per_type"] == {}
    assert report["annotation_coverage"]["needs_review_videos"] == ["a.mov"]


def test_localization_reports_untruncated_error_and_five_second_coverage():
    rows = [label("a.mov", True, 10, "known"), label("b.mov", False)]
    report = compute_metrics(rows, {"a.mov": [{"timecode_sec": 14., "violation_type": "known"}], "b.mov": []})
    assert report["video_detection"]["accuracy"] == 1.
    assert report["localization"]["events_within_3_sec"] == 0
    assert report["localization"]["events_within_5_sec"] == 1
    assert report["localization"]["mean_abs_time_error_sec_nearest_match"] == 4.
    assert report["localization"]["mean_abs_time_error_sec_matched"] is None
    assert report["classification"]["confusion_matrix"]["known"]["__missed__"] == 1


def test_benchmark_extraction(synthetic_video):
    result = benchmark_video(synthetic_video)
    assert result["sampled_frames"] == 6
    assert result["full_analysis"] is None and result["peak_vram_bytes"] is None


def test_environment_check():
    result = inspect_environment()
    assert result["python"] and "available" in result["torch_cuda"]
