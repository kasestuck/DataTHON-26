import pytest

from backend.api.config import Settings


def test_service_requires_explicit_analyzer_target(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.delenv("ANALYZER_TARGET", raising=False)
    with pytest.raises(RuntimeError, match="ANALYZER_TARGET"):
        Settings.from_env()
