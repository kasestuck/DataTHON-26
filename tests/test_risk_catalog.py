"""Risk display/serialization fixtures only; these are not AI quality tests."""
import base64
from collections import Counter
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.api.app import create_app
from backend.api.config import Settings
from backend.api.jobs import JobStore
from backend.api.result import format_result
from backend.api.risk_catalog import REVIEW_NOTICE, load_risk_catalog
from backend.api.rules import RuleCatalogError, load_rules, validate_catalog


DATA = Path(__file__).resolve().parents[1] / "backend/data"
TYPES = json.loads((DATA / "violation_types.json").read_text(encoding="utf-8"))
JPEG = b"\xff\xd8\xff\xd9"


def fixture_finding(type_id):
    return dict(violation_type=type_id, timecode_sec=1.5, confidence=.8,
                description="Синтетическое наблюдение для проверки формата.",
                evidence_frames=[dict(timestamp_sec=1.5, jpeg_bytes=JPEG)])


def copy_catalog(destination, *, rules=True):
    for name in ("violation_types.json", "risk_references.json", *(["rules.json"] if rules else [])):
        (destination / name).write_bytes((DATA / name).read_bytes())


def test_all_categories_have_separate_nonbinding_risk_and_unchanged_legal_review():
    rules = load_rules(DATA)
    risks = load_risk_catalog(DATA, TYPES, rules)
    assert len(risks) == 18 and set(risks) == set(TYPES) == set(rules)
    assert Counter(entry["status"] for entry in risks.values()) == {"contextual_reference": 7, "internal_policy_required": 11}
    assert all(rule == {"status": "needs_review"} for rule in rules.values())
    assert validate_catalog(DATA) == []
    for entry in risks.values():
        if entry["status"] == "internal_policy_required":
            assert "Внутренний регламент банка не предоставлен" in entry["review_note"]
        else:
            assert entry["reference"]["url"] == "https://adilet.zan.kz/rus/docs/V1900019680"
            assert "section" not in entry["reference"]


@pytest.mark.parametrize("type_id", TYPES)
def test_each_risk_reaches_existing_api_description_without_changing_contract(type_id):
    finding = fixture_finding(type_id)
    result = format_result([finding], DATA)
    risk = load_risk_catalog(DATA, TYPES, load_rules(DATA))[type_id]
    value = result["violations"][0]
    assert set(value) == {"type", "timecode_sec", "description", "evidence_frames"}
    assert value["type"] == TYPES[type_id]["name"]
    assert risk["risk_description"] in value["description"]
    assert finding["description"] in value["description"]
    assert REVIEW_NOTICE in value["description"] and "Источник:" not in value["description"]
    assert value["timecode_sec"] == 1.5 and base64.b64decode(value["evidence_frames"][0]["image_base64"]) == JPEG
    if risk["status"] == "contextual_reference":
        assert "Тематическая справка" in value["description"]
        assert "Ссылка приведена для справки и не подтверждает нарушение конкретного пункта" in value["description"]
    else:
        assert "Внутренний регламент банка не предоставлен" in value["description"]


def test_absent_verified_rules_do_not_prevent_a_risk_result(tmp_path):
    copy_catalog(tmp_path, rules=False)
    result = format_result([fixture_finding("cash_split_transfer")], tmp_path)
    assert result["verdict"] == "violation" and REVIEW_NOTICE in result["violations"][0]["description"]
    assert format_result([], tmp_path) == {"verdict": "no_violation", "violations": []}


@pytest.mark.parametrize("mutation", ["missing_type", "unknown_type", "unknown_status", "unchecked_section", "unofficial_url", "unreviewed_verified", "internal_external_source"])
def test_risk_validator_rejects_bad_or_self_verified_catalog(tmp_path, mutation):
    copy_catalog(tmp_path)
    path = tmp_path / "risk_references.json"
    risks = json.loads(path.read_text(encoding="utf-8"))
    entry = risks["cash_split_transfer"]
    if mutation == "missing_type":
        risks.pop("drinks_at_workstation")
    elif mutation == "unknown_type":
        risks["invented_type"] = entry.copy()
    elif mutation == "unknown_status":
        entry["status"] = "confirmed_violation"
    elif mutation == "unchecked_section":
        entry["reference"]["section"] = "invented"
    elif mutation == "unofficial_url":
        entry["reference"]["url"] = "https://adilet.zan.kz.evil.example/law"
    elif mutation == "unreviewed_verified":
        entry["status"] = "verified"
    else:
        risks["drinks_at_workstation"]["reference"] = entry["reference"]
    path.write_text(json.dumps(risks, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuleCatalogError):
        load_risk_catalog(tmp_path, TYPES, load_rules(tmp_path))


def test_verified_document_without_a_reviewed_section_still_requires_check(tmp_path):
    (tmp_path / "violation_types.json").write_text(json.dumps({"event": {"name": "Synthetic", "description": "Pixels"}}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({"event": {"status": "verified", "document": "Synthetic document metadata only", "url": "https://adilet.zan.kz/rus/docs/V1900019680"}}), encoding="utf-8")
    text = format_result([fixture_finding("event")], tmp_path)["violations"][0]["description"]
    assert REVIEW_NOTICE in text and "Источник:" not in text


def test_contextual_reference_is_never_promoted_by_an_independent_verified_rule(tmp_path):
    copy_catalog(tmp_path)
    rules = load_rules(tmp_path)
    rules["cash_split_transfer"] = dict(status="verified", document="Synthetic reviewed rule", section="synthetic test section", url="https://adilet.zan.kz/rus/docs/V1900019680")
    (tmp_path / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    text = format_result([fixture_finding("cash_split_transfer")], tmp_path)["violations"][0]["description"]
    assert REVIEW_NOTICE in text and "Тематическая справка" in text and "Источник:" not in text


def test_verified_reference_requires_and_displays_the_matching_reviewed_section(tmp_path):
    # Synthetic catalog only; this does not verify a real section of law.
    types = {"event": {"name": "Synthetic", "description": "Synthetic pixels"}}
    reference = dict(document="Synthetic reviewed document fixture", url="https://adilet.zan.kz/rus/docs/synthetic-fixture")
    rule = {"event": dict(status="verified", section="synthetic section", **reference)}
    risk = {"event": dict(status="verified", risk_description="Synthetic potential risk.",
                           reference=reference, review_note="Synthetic reviewed fixture.")}
    for filename, value in [("violation_types.json", types), ("rules.json", rule), ("risk_references.json", risk)]:
        (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    assert load_risk_catalog(tmp_path, types, load_rules(tmp_path))["event"]["status"] == "verified"
    text = format_result([fixture_finding("event")], tmp_path)["violations"][0]["description"]
    assert "раздел/пункт synthetic section" in text and REVIEW_NOTICE not in text
    risk["event"]["reference"] = {**reference, "document": "Different document"}
    (tmp_path / "risk_references.json").write_text(json.dumps(risk), encoding="utf-8")
    with pytest.raises(RuleCatalogError, match="matching reviewed rule"):
        load_risk_catalog(tmp_path, types, load_rules(tmp_path))


def test_risk_display_survives_fastapi_and_browser_job_routes_without_model_calls(tmp_path):
    settings = Settings(api_key="synthetic-legal-test-key", data_dir=tmp_path, catalog_dir=DATA,
                        frontend_dist=Path("frontend"), start_worker=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    result = format_result([fixture_finding(type_id) for type_id in TYPES], DATA)
    job_id = store.create(tmp_path / "synthetic-no-video-fixture.mov")
    store.finish(job_id, result)
    with TestClient(create_app(settings)) as api:
        direct = api.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": settings.api_key})
        csrf = api.get("/ui-api/v1/session").json()["csrf_token"]
        browser = api.get(f"/ui-api/v1/jobs/{job_id}", headers={"X-CSRF-Token": csrf})
    assert direct.status_code == browser.status_code == 200
    assert direct.json() == browser.json()
    assert len(browser.json()["violations"]) == 18
    assert all(REVIEW_NOTICE in finding["description"] for finding in browser.json()["violations"])
