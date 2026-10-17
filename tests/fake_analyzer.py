"""Synthetic analyzer results for Back 2 integration tests only."""

import multiprocessing
from pathlib import Path
import time


def _nested_child(connection):
    connection.send("child-ready")
    connection.close()


def nested_spawn(video_path: str) -> list[dict]:
    """Exercise Back 1's required child-process pattern without loading models."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_nested_child, args=(sender,))
    child.start()
    sender.close()
    try:
        if not receiver.poll(5) or receiver.recv() != "child-ready":
            raise RuntimeError("Nested analyzer process did not respond")
    finally:
        receiver.close()
        child.join(timeout=5)
        if child.is_alive():
            child.terminate()
            child.join(timeout=2)
    return []


def one_finding(video_path: str) -> list[dict]:
    return [{
        "violation_type": "counter_event",
        "timecode_sec": 12.5,
        "description": "В кадре появился второй человек.",
        "confidence": 0.8,
        "evidence_frames": [{"timestamp_sec": 12.5, "jpeg_bytes": b"\xff\xd8\xff\xd9"}],
    }]


def _mark_after_delay(video_path):
    Path(video_path + '.started').write_text('started')
    time.sleep(6)
    Path(video_path + '.survived').write_text('orphan')


def nested_hang(video_path: str):
    process = multiprocessing.get_context('spawn').Process(target=_mark_after_delay, args=(video_path,))
    process.start()
    time.sleep(30)
