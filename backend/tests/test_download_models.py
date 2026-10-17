from types import SimpleNamespace
import hashlib
from pathlib import Path

import pytest

from backend.scripts.download_models import curl_snapshot, download_models


def test_provisioning_pins_revision_and_license(tmp_path):
    class Card:
        def to_dict(self):
            return {"license": "apache-2.0"}

    class API:
        def model_info(self, repository, **kwargs):
            return SimpleNamespace(card_data=Card(), sha="synthetic-revision", siblings=[SimpleNamespace(size=100)])

    calls = []
    records = download_models(tmp_path, ["detector"], api=API(), downloader=lambda *args, **kwargs: calls.append((args, kwargs)))
    assert records[0]["revision"] == calls[0][1]["revision"] == "synthetic-revision"
    assert records[0]["license"] == "apache-2.0"
    assert not any(pattern.endswith(".py") for pattern in calls[0][1]["allow_patterns"])


def test_provisioning_rejects_unexpected_license(tmp_path):
    class Card:
        def to_dict(self):
            return {"license": "non-commercial"}

    class API:
        def model_info(self, *args, **kwargs):
            return SimpleNamespace(card_data=Card())

    with pytest.raises(ValueError, match="license"):
        download_models(tmp_path, ["vlm"], api=API(), downloader=lambda *args, **kwargs: pytest.fail("Must not download"))


def test_curl_verifies_file_and_reuses_complete_download(tmp_path):
    data = b"synthetic weight fixture"
    file = SimpleNamespace(rfilename="model.safetensors", size=len(data),
        lfs={"sha256": hashlib.sha256(data).hexdigest()})
    info = SimpleNamespace(sha="synthetic-revision", siblings=[file])
    calls = []

    def fake_curl(args, **kwargs):
        calls.append(args)
        Path(args[args.index("--output") + 1]).write_bytes(data)

    curl_snapshot("synthetic/repo", info, tmp_path, runner=fake_curl)
    curl_snapshot("synthetic/repo", info, tmp_path, runner=fake_curl)
    assert len(calls) == 1
    assert (tmp_path / "model.safetensors").read_bytes() == data
    file.lfs["sha256"] = "invalid"
    with pytest.raises(ValueError, match="hash"):
        curl_snapshot("synthetic/repo", info, tmp_path, runner=fake_curl)
