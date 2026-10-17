import pytest

from backend.api.config import Settings


def test_service_defaults_to_real_analyzer(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.delenv("ANALYZER_TARGET", raising=False)
    assert Settings.from_env().analyzer_target == "backend.analyzer:analyze_video"


def test_service_rejects_empty_analyzer_target(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("ANALYZER_TARGET", "")
    with pytest.raises(RuntimeError, match="ANALYZER_TARGET"):
        Settings.from_env()


def test_stub_is_only_an_explicit_development_choice(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("ANALYZER_TARGET", "backend.api.stub:analyze_video")
    assert Settings.from_env().analyzer_target == "backend.api.stub:analyze_video"
