import time

import cv2
import numpy as np
import pytest

from backend.analyzer.errors import AnalysisTimeoutError, VideoReadError
from backend.analyzer.video import VideoReader


def test_sampling(synthetic_video):
    with VideoReader(synthetic_video) as reader:
        assert reader.info.fps == 10
        assert reader.info.duration_sec == 3
        frames = list(reader.sample(2, max_side=80))
        assert [f.frame_index for f in frames] == [0, 5, 10, 15, 20, 25]
        assert [f.timestamp_sec for f in frames] == [0, .5, 1, 1.5, 2, 2.5]
        assert frames[0].bgr.shape == (40, 80, 3)
        assert cv2.imdecode(np.frombuffer(frames[-1].jpeg(), np.uint8), cv2.IMREAD_COLOR).shape == (40, 80, 3)
        assert reader.at(1.25).timestamp_sec == 1.2  # Python nearest-even rounding
    assert not reader.capture.isOpened()


def test_errors(tmp_path, synthetic_video):
    with pytest.raises(VideoReadError):
        VideoReader(tmp_path / "absent.mov")
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"not a movie")
    with pytest.raises(VideoReadError):
        VideoReader(bad)
    with VideoReader(synthetic_video) as reader:
        for t in (-1, 3, float("nan")):
            with pytest.raises(VideoReadError):
                reader.at(t)
        with pytest.raises(ValueError):
            list(reader.sample(0))
        with pytest.raises(VideoReadError, match="budget"):
            list(reader.sample(2, max_frames=2))
        with pytest.raises(AnalysisTimeoutError):
            list(reader.sample(deadline=time.monotonic() - 1))
        with pytest.raises(ValueError):
            reader.at(0).jpeg(101)


def test_damaged_decode(synthetic_video, monkeypatch):
    class BrokenCapture:
        def set(self, *_):
            return True

        def read(self):
            return False, None

        def get(self, *_):
            return 0

        def grab(self):
            return False

        def release(self):
            pass

    with VideoReader(synthetic_video) as reader:
        reader.capture.release()
        monkeypatch.setattr(reader, "capture", BrokenCapture())
        with pytest.raises(VideoReadError, match="damaged"):
            reader.at(0)
        with pytest.raises(VideoReadError, match="decoding stopped"):
            list(reader.sample())
