"""Private fixtures and generated artifacts must remain outside Git's candidate set."""
from pathlib import Path
import subprocess


def test_private_artifacts_ignored():
    root = Path(__file__).resolve().parents[2]
    private = ["dataset/open_labels.xlsx", "dataset/videos/X-1.mov", "models/example/model.safetensors",
        "backend/analyzer/output/frame.jpg", "backend/data/labels.csv", "backend/data/manual_labels.xlsx",
        "backend/data/reports/evaluation.json", "backend/data/review.csv",
        "backend/data/annotations/source_cells.json", "backend/data/annotations/local_response_1.txt"]
    result = subprocess.run(["git", "check-ignore", "--stdin", "-z"], input=("\0".join(private) + "\0").encode(),
        cwd=root, capture_output=True, check=True)
    assert set(result.stdout.decode().strip("\0").split("\0")) == set(private)
    assert subprocess.run(["git", "check-ignore", "backend/data/manual_labels_template.csv"],
        cwd=root, capture_output=True).returncode != 0


def test_no_tracked_private_data():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["git", "ls-files", "dataset", "models", "backend/analyzer/output", "backend/data/labels.csv"],
        cwd=root, capture_output=True, text=True, check=True)
    assert not result.stdout.strip()
