import json

import pytest

from backend.api.rules import RuleCatalogError, load_rules, sync_missing_rules, validate_catalog


def test_empty_rules_are_valid_before_types_arrive(tmp_path):
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    assert validate_catalog(tmp_path) == []


def test_sync_marks_new_type_as_needing_review(tmp_path):
    (tmp_path / "violation_types.json").write_text(
        json.dumps({"counter_event": {"name": "Событие", "description": "Видимое действие"}}), encoding="utf-8"
    )
    assert sync_missing_rules(tmp_path) == ["counter_event"]
    assert load_rules(tmp_path) == {"counter_event": {"status": "needs_review"}}
    assert validate_catalog(tmp_path) == []


def test_validator_reports_type_without_rule_status(tmp_path):
    (tmp_path / "violation_types.json").write_text('{"counter_event": {"name": "Событие", "description": "Признаки"}}', encoding="utf-8")
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    assert validate_catalog(tmp_path) == ["counter_event: rule entry is missing"]


def test_sync_rejects_invalid_type_metadata_before_writing_rules(tmp_path):
    (tmp_path / "violation_types.json").write_text('{"": {"name": "", "description": "Признаки"}}', encoding="utf-8")
    with pytest.raises(RuleCatalogError, match="type requires"):
        sync_missing_rules(tmp_path)
    assert not (tmp_path / "rules.json").exists()


def test_verified_rule_requires_official_https_source(tmp_path):
    (tmp_path / "rules.json").write_text(
        json.dumps({"counter_event": {"status": "verified", "document": "Документ", "url": "https://example.kz/rule"}}),
        encoding="utf-8",
    )
    with pytest.raises(RuleCatalogError, match="official"):
        load_rules(tmp_path)


def test_invalid_official_url_port_is_a_catalog_error(tmp_path):
    (tmp_path / "rules.json").write_text(
        json.dumps({"counter_event": {"status": "verified", "document": "Документ", "url": "https://adilet.zan.kz:bad/rus/docs/example"}}),
        encoding="utf-8",
    )
    with pytest.raises(RuleCatalogError, match="official"):
        load_rules(tmp_path)


def test_unverified_rule_cannot_carry_unchecked_legal_claim(tmp_path):
    (tmp_path / "rules.json").write_text(
        json.dumps({"counter_event": {"status": "needs_review", "document": "Непроверенный документ"}}),
        encoding="utf-8",
    )
    with pytest.raises(RuleCatalogError, match="needs_review"):
        load_rules(tmp_path)
