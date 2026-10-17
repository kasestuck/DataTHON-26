import time


def analyze_video(video_path: str) -> list:
    """Temporary integration stub; replace target with backend.analyzer:analyze_video."""
    return []


def raise_error(video_path: str) -> list:
    raise ValueError("test analyzer failure")


def hang(video_path: str) -> list:
    time.sleep(10)
    return []
