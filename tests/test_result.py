import base64
import json

import pytest

from backend.api.result import format_result


class Finding:
    violation_type = "counter_event"
    timecode_sec = 12.5
    description = "У стойки появился второй человек."
    confidence = 0.8
    evidence_frames = [{"timestamp_sec": 12.5, "jpeg_bytes": b"\xff\xd8\xff\xd9"}]


def test_result_uses_required_schema_and_verified_rule(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "rules.json").write_text(json.dumps({"counter_event": {"status": "verified", "document": "Акт РК", "section": "2", "url": "https://old.adilet.zan.kz/rus/docs/V1900019680"}}), encoding="utf-8")
    (data / "violation_types.json").write_text('{"counter_event": {"name": "Событие у кассы", "description": "Признаки"}}', encoding="utf-8")
    result = format_result([Finding()], data)
    assert result["verdict"] == "violation"
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["type"] == "Событие у кассы"
    assert violation["timecode_sec"] == 12.5
    assert "Акт РК" in violation["description"]
    assert violation["evidence_frames"] == [{"timestamp_sec": 12.5, "image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode()}]
    assert "confidence" not in violation
    assert "rule" not in violation


def test_result_handles_empty_findings_without_catalog(tmp_path):
    assert format_result([], tmp_path) == {"verdict": "no_violation", "violations": []}


def test_result_rejects_violation_without_evidence(tmp_path):
    finding = {"violation_type": "counter_event", "timecode_sec": 12.5, "description": "Событие", "confidence": 0.5, "evidence_frames": []}
    with pytest.raises(ValueError, match="evidence"):
        format_result([finding], tmp_path)


def test_pending_rule_does_not_add_unverified_source(tmp_path):
    (tmp_path / "violation_types.json").write_text('{"counter_event": {"name": "Событие", "description": "Признаки"}}', encoding="utf-8")
    (tmp_path / "rules.json").write_text('{"counter_event": {"status": "needs_review"}}', encoding="utf-8")
    result = format_result([Finding()], tmp_path)
    assert result["violations"][0]["description"] == Finding.description


def test_missing_rule_keeps_violation_without_legal_claim(tmp_path):
    (tmp_path / "violation_types.json").write_text('{"counter_event": {"name": "Событие", "description": "Признаки"}}', encoding="utf-8")
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    result = format_result([Finding()], tmp_path)
    assert result["verdict"] == "violation"
    assert result["violations"][0]["description"] == Finding.description


def test_unknown_violation_type_is_rejected(tmp_path):
    (tmp_path / "violation_types.json").write_text('{"another_type": {"name": "Иное", "description": "Признаки"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="violation_type"):
        format_result([Finding()], tmp_path)


@pytest.mark.parametrize("change", [
    {"confidence": 1.5},
    {"timecode_sec": -1},
    {"description": " "},
    {"evidence_frames": [{"timestamp_sec": 1, "jpeg_bytes": "not bytes"}]},
    {"evidence_frames": [{"timestamp_sec": 1, "jpeg_bytes": b"broken"}]},
])
def test_invalid_finding_is_rejected(tmp_path, change):
    finding = {
        "violation_type": "counter_event",
        "timecode_sec": 12.5,
        "description": "Событие",
        "confidence": 0.8,
        "evidence_frames": [{"timestamp_sec": 12.5, "jpeg_bytes": b"\xff\xd8\xff\xd9"}],
    }
    finding.update(change)
    with pytest.raises(ValueError):
        format_result([finding], tmp_path)
