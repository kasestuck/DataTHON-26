"""Bounded, silent OpenCV video access; timestamps use frame index / FPS."""
from dataclasses import dataclass
import math
from pathlib import Path
import time

import cv2
import numpy as np

from .errors import AnalysisTimeoutError, VideoReadError


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    frame_count: int
    duration_sec: float
    width: int
    height: int


@dataclass(frozen=True)
class Frame:
    frame_index: int
    timestamp_sec: float
    bgr: np.ndarray

    def jpeg(self, quality=85):
        if not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be in [1, 100]")
        ok, encoded = cv2.imencode(".jpg", self.bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise VideoReadError("JPEG encoding failed")
        return encoded.tobytes()


def resize(image, max_side):
    if max_side < 1:
        raise ValueError("max_side must be positive")
    h, w = image.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    return cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else image


class VideoReader:
    def __init__(self, path):
        path = Path(path)
        if not path.is_file():
            raise VideoReadError(f"Video does not exist: {path}")
        self.capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        if not self.capture.isOpened():
            self.capture.release()
            raise VideoReadError(f"Cannot open video: {path}")
        fps = self.capture.get(cv2.CAP_PROP_FPS)
        count = self.capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width, height = self.capture.get(cv2.CAP_PROP_FRAME_WIDTH), self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not all(math.isfinite(v) and v > 0 for v in (fps, count, width, height)):
            self.close()
            raise VideoReadError(f"Invalid video metadata: {path}")
        self.info = VideoInfo(fps, int(count), int(count) / fps, int(width), int(height))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.capture.release()

    def _read_index(self, index, max_side):
        if not 0 <= index < self.info.frame_count:
            raise VideoReadError(f"Frame index out of range: {index}")
        if not self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0):
            raise VideoReadError("Cannot rewind video")
        # MOV/VFR random seek may land on a different decoded frame even when
        # CAP_PROP_POS_FRAMES reports the requested index. Count actual grabs.
        for _ in range(index + 1):
            if not self.capture.grab():
                raise VideoReadError(f"Unreadable frame {index}; video may be damaged")
        ok, image = self.capture.retrieve()
        if not ok or image is None:
            raise VideoReadError(f"Unreadable frame {index}; video may be damaged")
        return Frame(index, index / self.info.fps, resize(image, max_side))

    def at(self, timestamp_sec, max_side=640):
        if not math.isfinite(timestamp_sec) or not 0 <= timestamp_sec < self.info.duration_sec:
            raise VideoReadError("Timestamp outside video")
        return self._read_index(min(self.info.frame_count - 1, round(timestamp_sec * self.info.fps)), max_side)

    def sample(self, sample_fps=1.0, max_side=640, max_frames=600, deadline=None):
        if not math.isfinite(sample_fps) or not 0 < sample_fps <= self.info.fps or max_frames < 1:
            raise ValueError("Invalid sampling rate or frame budget")
        total = math.ceil(self.info.duration_sec * sample_fps)
        if total > max_frames:
            raise VideoReadError(f"Sampling requires {total} frames; budget is {max_frames}")
        if max_side < 1:
            raise ValueError("max_side must be positive")
        if not self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0):
            raise VideoReadError("Cannot rewind video")
        # Decode once. Repeated random seeks redo entire GOPs and are much slower
        # for the supplied MOV files. Only selected images are retrieved/resized.
        ordinal, target = 0, 0
        for index in range(self.info.frame_count):
            if deadline is not None and time.monotonic() >= deadline:
                raise AnalysisTimeoutError("Video extraction deadline exceeded")
            if not self.capture.grab():
                raise VideoReadError(f"Video decoding stopped at frame {index} of {self.info.frame_count}")
            if index == target:
                ok, image = self.capture.retrieve()
                if not ok or image is None:
                    raise VideoReadError(f"Cannot retrieve decoded frame {index}")
                yield Frame(index, index / self.info.fps, resize(image, max_side))
                ordinal += 1
                target = round(ordinal * self.info.fps / sample_fps)
