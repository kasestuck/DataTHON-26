"""Runtime resource regression; synthetic model adapters, never a quality test."""
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

from backend.analyzer import detector, network_guard, pipeline, vlm
from backend.analyzer.config import AnalyzerConfig


@pytest.mark.parametrize("platform,device,expected_threads", [
    ("win32", "cuda", 1), ("linux", "cuda", 4), ("win32", "cpu", 4),
])
def test_cuda_worker_bounds_windows_cpu_threads_before_loading_models(
        monkeypatch, synthetic_video, tmp_path, platform, device, expected_threads):
    torch = pytest.importorskip("torch")
    previous = torch.get_num_threads()
    observed, messages = [], []
    monkeypatch.setattr(sys, "platform", platform)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HOME",
                 "TORCH_HOME", "CUDA_CACHE_PATH", "TORCHINDUCTOR_CACHE_DIR", "TOKENIZERS_PARALLELISM"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(network_guard, "install_network_guard", lambda: None)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda: None)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda: 0)
    monkeypatch.setattr(torch.cuda, "max_memory_reserved", lambda: 0)

    def load_detector(*args, **kwargs):
        observed.append(("detector", torch.get_num_threads()))
        return object()

    def load_vlm(*args, **kwargs):
        observed.append(("vlm", torch.get_num_threads()))
        return object()

    monkeypatch.setattr(detector, "LocalRTDetr", load_detector)
    monkeypatch.setattr(vlm, "LocalTransformersVLM", load_vlm)
    monkeypatch.setattr(pipeline, "Analyzer", lambda *args: SimpleNamespace(
        analyze=lambda path: pipeline.AnalysisReport(is_mock=True, timings={"total_sec": 0.})))
    connection = SimpleNamespace(send=messages.append, close=lambda: None)
    config = AnalyzerConfig(device=device, temp_dir=str(tmp_path))
    try:
        torch.set_num_threads(4)
        pipeline._production_worker(connection, str(synthetic_video), config)
        assert observed == [("detector", expected_threads), ("vlm", expected_threads)]
        assert messages[0][0] is True
        assert (config.vlm_timeout_sec, config.timeout_sec) == (35., 260.)
    finally:
        torch.set_num_threads(previous)


def test_public_api_and_analyzer_imports_preserve_host_cpu_pool():
    pytest.importorskip("torch")
    code = (
        "import torch; torch.set_num_threads(4); "
        "import backend.api.app; import backend.analyzer; "
        "assert torch.get_num_threads() == 4; "
        "assert not torch.cuda.is_initialized()"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
