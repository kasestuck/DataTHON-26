import cv2
import numpy as np
import pytest


@pytest.fixture
def synthetic_video(tmp_path):
    path = tmp_path / "sample.mov"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 80))
    assert writer.isOpened()
    for index in range(30):
        image = np.full((80, 160, 3), index * 8, dtype=np.uint8)
        writer.write(image)
    writer.release()
    return path
