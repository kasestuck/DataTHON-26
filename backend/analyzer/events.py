"""Short-term IoU association and observable candidate signals, not verdicts."""
from dataclasses import dataclass
import math

import cv2
import numpy as np

from .detector import Detector


@dataclass(frozen=True)
class Event:
    type: str
    timecode_sec: float
    features: dict
    confidence: float


def iou(a, b):
    x = max(0., min(a[2], b[2]) - max(a[0], b[0]))
    y = max(0., min(a[3], b[3]) - max(a[1], b[1]))
    intersection = x * y
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.


class Tracker:
    """Greedy one-to-one IoU links between adjacent sampled frames; no identity."""
    def __init__(self, min_iou=.2):
        if not 0 < min_iou <= 1:
            raise ValueError("Invalid IoU threshold")
        self.min_iou, self.previous, self.next_id = min_iou, {}, 1

    def update(self, detections):
        candidates = sorted(((iou(old.box, new.box), track_id, index)
            for track_id, old in self.previous.items() for index, new in enumerate(detections)
            if old.label == new.label), reverse=True)
        assigned, used = {}, set()
        for score, track_id, index in candidates:
            if score >= self.min_iou and track_id not in used and index not in assigned:
                assigned[index] = track_id
                used.add(track_id)
        current, motion = {}, []
        for index, detection in enumerate(detections):
            track_id = assigned.get(index)
            if track_id is None:
                track_id, self.next_id = self.next_id, self.next_id + 1
            elif detection.label == "person":
                old = self.previous[track_id]
                distance = math.hypot((detection.box[0] + detection.box[2] - old.box[0] - old.box[2]) / 2,
                                      (detection.box[1] + detection.box[3] - old.box[1] - old.box[3]) / 2)
                motion.append((track_id, distance))
            current[track_id] = detection
        self.previous = current
        return current, motion


class EventExtractor:
    def __init__(self, detector: Detector, motion_threshold=.06, change_threshold=.08):
        self.detector, self.tracker = detector, Tracker()
        self.motion_threshold, self.change_threshold = motion_threshold, change_threshold
        self.previous_gray, self.people = None, None

    def update(self, frame):
        detections = self.detector.detect(frame.bgr)
        # Limit state even if a model produces an excessive number of proposals.
        detections = sorted(detections, key=lambda d: d.confidence, reverse=True)[:100]
        for d in detections:
            if (not math.isfinite(d.confidence) or not 0 <= d.confidence <= 1 or len(d.box) != 4
                    or not all(math.isfinite(x) and 0 <= x <= 1 for x in d.box)
                    or d.box[0] >= d.box[2] or d.box[1] >= d.box[3]):
                raise ValueError("Detector returned invalid confidence or normalized box")
        tracks, movement = self.tracker.update(detections)
        count = sum(d.label == "person" for d in detections)
        events = []
        if self.people is not None and count != self.people:
            events.append(Event("person_count_change", frame.timestamp_sec,
                {"before": self.people, "after": count}, .6))
        for track_id, distance in movement:
            if distance >= self.motion_threshold:
                events.append(Event("person_motion", frame.timestamp_sec,
                    {"track_id": track_id, "normalized_distance": distance}, .5))
        gray = cv2.resize(cv2.cvtColor(frame.bgr, cv2.COLOR_BGR2GRAY), (64, 64))
        if self.previous_gray is not None:
            change = float(np.mean(cv2.absdiff(gray, self.previous_gray)) / 255)
            if change >= self.change_threshold:
                events.append(Event("scene_change", frame.timestamp_sec, {"mean_change": change}, min(1., change)))
        self.previous_gray, self.people = gray, count
        return events, tracks


def extract_events(frames, detector=None):
    if detector is None:
        from pathlib import Path
        from .config import AnalyzerConfig
        from .detector import LocalRTDetr
        from .errors import ModelNotConfiguredError
        config = AnalyzerConfig.from_env()
        if not config.detector_dir or not Path(config.detector_dir).is_dir():
            raise ModelNotConfiguredError("extract_events requires installed local detector weights")
        detector = LocalRTDetr(config.detector_dir, config.device)
    extractor = EventExtractor(detector)
    for frame in frames:
        events, _ = extractor.update(frame)
        yield from events
