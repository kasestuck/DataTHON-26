import hashlib
import json

from openpyxl import Workbook
import pytest

from backend.analyzer.annotations import apply_groups, confirm_groups
from backend.analyzer.catalog import load_catalog, validate_catalog
from backend.analyzer.labels import read_labels, write_labels
from backend.scripts.prepare_annotations import snapshot_workbook

CATALOG = {"synthetic_action": {"name": "Synthetic action", "description": "A visible synthetic action"}}


def source(tmp_path, descriptions):
    book = Workbook()
    book.active.append(["наименование файла", "таймстемп", "нарушение / без нарушения", "Описание"])
    for i, (positive, text) in enumerate(descriptions, 1):
        (tmp_path / f"X-{i}.mov").touch()
        book.active.append([f"X-{i}", 3 if positive else None,
                            "нарушение" if positive else "без нарушения", text])
    path = tmp_path / "source.xlsx"
    book.save(path)
    return path


def proposal(path, assignments):
    return {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "catalog": CATALOG, "assignments": assignments}


def assignment(number, kind="synthetic_action", status="source_observed"):
    return {"source_row": number, "type_id": kind, "status": status}


def test_lossless_snapshot_and_reusable_groups(tmp_path):
    text = "  Synthetic observation\nSecond line; Ё  "
    path = source(tmp_path, [(True, text), (True, text), (False, "Routine synthetic observation")])
    archive = snapshot_workbook(path, tmp_path / "archive")
    assert (tmp_path / "archive" / f"source_{archive['sha256']}.xlsx").read_bytes() == path.read_bytes()
    assert next(c for c in archive["sheets"][0]["cells"] if c["coordinate"] == "D2")["value"] == text
    labels, catalog, report = apply_groups(path, tmp_path, proposal(path, [assignment(2), assignment(3),
        assignment(4, None, "negative")]))
    assert labels[0]["observed_action"] == labels[1]["observed_action"] == text
    assert catalog == CATALOG and report["examples_per_type"] == {"synthetic_action": 2}
    assert report["explicit_human_type_approval"] is False
    assert labels[2]["violation_type"] == labels[2]["timecode_sec"] == ""
    write_labels(labels, tmp_path / "labels.csv")
    assert read_labels(tmp_path / "labels.csv")[0]["observed_action"] == text


def test_ambiguity_and_negative_contradictions_never_promoted(tmp_path):
    path = source(tmp_path, [(True, "Возможно синтетическое событие?"),
        (True, "Synthetic unclear event"), (False, "Routine")])
    labels, catalog, report = apply_groups(path, tmp_path, proposal(path,
        [assignment(2), assignment(3, None, "needs_review"), assignment(4)]))
    assert catalog == {} and len(report["needs_review"]) == 3
    assert all(row["review_status"] == "needs_review" for row in labels)
    assert labels[0]["violation_type"] == labels[1]["violation_type"] == "unknown"
    assert labels[2]["has_violation"] == "false" and labels[2]["violation_type"] == ""


def test_stale_duplicate_and_unknown_groupings_fail_closed(tmp_path):
    path = source(tmp_path, [(True, "Synthetic")])
    values = proposal(path, [assignment(2)])
    values["source_sha256"] = "stale"
    with pytest.raises(ValueError, match="source workbook"):
        apply_groups(path, tmp_path, values)
    for assignments in ([assignment(2), assignment(2)], [], [assignment(2, "invented")]):
        with pytest.raises(ValueError):
            apply_groups(path, tmp_path, proposal(path, assignments))


@pytest.mark.parametrize("catalog", [[], {"unknown": {"name": "n", "description": "d"}},
    {"needs_review": {"name": "n", "description": "d"}},
    {"bad-id": {"name": "n", "description": "d"}},
    {"valid": {"name": "n", "description": ""}},
    {"valid": {"name": "n", "description": "d", "legal_source": "unverified"}},
    {"schema_version": 1, "types": []}])
def test_catalog_exact_back2_contract(catalog):
    with pytest.raises(ValueError):
        validate_catalog(catalog)


def test_duplicate_catalog_keys_rejected(tmp_path):
    path = tmp_path / "catalog.json"
    value = json.dumps(CATALOG["synthetic_action"])
    path.write_text('{"synthetic_action":' + value + ',"synthetic_action":' + value + '}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_catalog(path)
    path.write_text(json.dumps(CATALOG))
    assert load_catalog(path) == CATALOG


def test_local_proposals_cannot_become_manual_truth(tmp_path):
    path = source(tmp_path, [(True, "Synthetic observation")])
    labels, catalog, _ = apply_groups(path, tmp_path, proposal(path, [assignment(2)]))
    pending, active = confirm_groups(labels, catalog)
    assert active == {} and pending[0]["violation_type"] == "unknown"
    assert pending[0]["proposed_type"] == "synthetic_action"
    assert pending[0]["review_status"] == "needs_review"
    reviewed, active = confirm_groups(labels, catalog, {"source_sha256": labels[0]["source_sha256"],
        "events": [{"source_row": 2, "type_id": "synthetic_action"}]})
    assert active == CATALOG and reviewed[0]["review_status"] == "confirmed"


def test_invalid_human_review_rejected(tmp_path):
    path = source(tmp_path, [(True, "Synthetic observation"), (False, "Routine")])
    labels, catalog, _ = apply_groups(path, tmp_path, proposal(path, [assignment(2), assignment(3, None, "negative")]))
    for confirmation in ({"source_sha256": "stale", "events": []},
        {"source_sha256": labels[0]["source_sha256"], "events": [{"source_row": 3, "type_id": "synthetic_action"}]},
        {"source_sha256": labels[0]["source_sha256"], "events": [{"source_row": 2, "type_id": "invented"}]}):
        with pytest.raises(ValueError):
            confirm_groups(labels, catalog, confirmation)
