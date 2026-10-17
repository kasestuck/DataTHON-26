import pytest

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.pipeline import AnalysisReport
from backend.analyzer.video import VideoReader
from backend.analyzer.errors import InferenceError
from backend.scripts.smoke_models import make_synthetic_video, smoke


def test_generated_geometric_fixture(tmp_path):
    path = tmp_path / "synthetic.mov"
    make_synthetic_video(path)
    with VideoReader(path) as reader:
        assert reader.info.duration_sec == 6.
        assert len(list(reader.sample(1))) == 6


def test_smoke_rejects_mock_and_cleans_up():
    captured = []

    def fake_runner(path, config):
        captured.append(path)
        return AnalysisReport(is_mock=True)

    with pytest.raises(ValueError, match="mock"):
        smoke(AnalyzerConfig(), runner=fake_runner)
    from pathlib import Path
    assert not Path(captured[0]).exists()


def test_oom_smoke_requires_an_explicit_memory_error():
    def failing_runner(path, config):
        assert config.memory_fraction == .005
        raise InferenceError("Synthetic model memory exhausted")

    assert smoke(AnalyzerConfig(), runner=failing_runner, oom_check=True)["oom_handled"]
