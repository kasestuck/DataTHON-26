from pathlib import Path

import pytest

from backend.analyzer.contracts import EvidenceFrame, Finding
from backend.analyzer.video import VideoReader
from backend.scripts.evaluate import verify_findings
from backend.scripts.export_evidence import export_evidence


def test_reconstructed_jpegs_match_original_finding(synthetic_video, tmp_path):
    with VideoReader(synthetic_video) as reader:
        frame = reader.at(1., max_side=128)
        finding = Finding("synthetic", 1., "Synthetic", .9, [EvidenceFrame(1., frame.jpeg())])
    checked = verify_findings([finding])[0]
    report = {"run_inputs": {"config": {"temp_dir": str(tmp_path / "output"), "max_side": 128}},
              "finding_details": {synthetic_video.name: [{**checked, "description": finding.description}]}}
    actual = export_evidence(report, tmp_path, tmp_path / "output/examples")
    assert Path(actual[0]["evidence"][0]["path"]).read_bytes() == finding.evidence_frames[0].jpeg_bytes
    assert (tmp_path / "output/examples/finding_examples.json").exists()
    with pytest.raises(ValueError, match="authorized"):
        export_evidence(report, tmp_path, tmp_path / "outside")
    report["finding_details"][synthetic_video.name][0]["evidence"][0]["sha256"] = "wrong"
    with pytest.raises(ValueError, match="differs"):
        export_evidence(report, tmp_path, tmp_path / "output/examples")
