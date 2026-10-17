import numpy as np
import pytest

from backend.analyzer.candidates import CandidateSelector
from backend.analyzer.detector import Detection, LocalRTDetr, MockDetector
from backend.analyzer.errors import ModelNotConfiguredError
from backend.analyzer.events import Event, EventExtractor, Tracker, extract_events, iou
from backend.analyzer.video import Frame


def frame(index, value=0):
    return Frame(index, float(index), np.full((40, 80, 3), value, np.uint8))


def person(box=(.1, .1, .5, .8)):
    return Detection("person", .9, box)


def test_tracking_one_to_one():
    tracker = Tracker()
    original, _ = tracker.update([person()])
    current, motion = tracker.update([person((.15, .1, .55, .8)), person((.6, .1, .9, .8))])
    assert len(current) == 2
    assert next(iter(original)) in current
    assert motion[0][1] == pytest.approx(.05)
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1
    assert iou((0, 0, .1, .1), (.5, .5, 1, 1)) == 0
    tracker.update([])
    new, _ = tracker.update([person()])
    assert next(iter(new)) not in original


def test_events_and_count_changes(monkeypatch):
    detector = MockDetector([person()])
    extractor = EventExtractor(detector)
    assert extractor.update(frame(0))[0] == []
    detector.detections = []
    events, _ = extractor.update(frame(1, 255))
    assert {event.type for event in events} == {"scene_change", "person_count_change"}
    assert events[0].features == {"before": 1, "after": 0}
    assert list(extract_events([frame(0), frame(1, 255)], detector))[0].type == "scene_change"
    monkeypatch.setenv("ANALYZER_DETECTOR_DIR", "")
    with pytest.raises(ModelNotConfiguredError):
        list(extract_events([frame(0)]))


def test_invalid_detection_and_missing_weights(tmp_path):
    extractor = EventExtractor(MockDetector([person((0, 0, 2, 1))]))
    with pytest.raises(ValueError, match="invalid"):
        extractor.update(frame(0))
    with pytest.raises(ModelNotConfiguredError):
        LocalRTDetr(tmp_path / "missing")


def test_windows_bounded_with_future_context():
    selector = CandidateSelector(window_frames=6, max_windows=2, coverage_sec=5)
    windows = []
    for i in range(30):
        candidate = selector.update(frame(i), [Event("scene_change", i, {}, .5)])
        if candidate:
            windows.append(candidate)
    assert len(windows) == 2
    assert all(4 <= len(window.frames) <= 8 for window in windows)
    assert len(selector.buffer) == 6
    assert selector.finish() is None


def test_short_window_flush():
    selector = CandidateSelector(8)
    for i in range(4):
        assert selector.update(frame(i), []) is None
    assert len(selector.finish().frames) == 4
    with pytest.raises(ValueError):
        CandidateSelector(9)
