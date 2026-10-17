import csv
from datetime import time, timedelta

from openpyxl import Workbook
import pytest

from backend.analyzer.labels import (import_open_labels, merge_manual, parse_timecode,
    parse_verdict, read_labels, video_index, write_labels)


@pytest.mark.parametrize("value,expected", [("1минута 8 секунда", 68), ("22 секунда", 22),
    ("01:08", 68), ("00:01:08,5", 68.5), ("8.25", 8.25), (8, 8), (None, None),
    (time(0, 1, 8), 68), (timedelta(seconds=68), 68), ("1 ч 2 мин 3 с", 3723), (0, 0)])
def test_timecode(value, expected):
    assert parse_timecode(value) == expected


@pytest.mark.parametrize("value", ["abc", "01:99", -1, float("nan"), float("inf"), True, "-3 с"])
def test_bad_timecode(value):
    with pytest.raises(ValueError):
        parse_timecode(value)


def workbook(tmp_path, rows):
    path = tmp_path / "labels.xlsx"
    book = Workbook()
    book.active.append(["наименование файла", "таймстемп", "нарушение / без нарушения"])
    for row in rows:
        book.active.append(row)
    book.save(path)
    return path


def test_real_headers_and_confusables(tmp_path):
    (tmp_path / "Х-1.mov").touch()
    (tmp_path / "X-2.MOV").touch()
    path = workbook(tmp_path, [("X-1", "1минута 8 секунда", "нарушение"), ("Х-2", "junk", "без нарушения")])
    labels, report = import_open_labels(path, tmp_path)
    assert report["positive"] == 1
    assert labels[0]["video"] == "Х-1.mov"
    assert labels[0]["timecode_sec"] == 68
    assert labels[0]["violation_type"] == "unknown"
    assert labels[1]["violation_type"] == labels[1]["timecode_sec"] == ""
    output = tmp_path / "labels.csv"
    write_labels(labels, output)
    assert read_labels(output)[0]["timecode_sec"] == 68


def test_duplicates_and_missing(tmp_path):
    (tmp_path / "X-1.mov").touch()
    path = workbook(tmp_path, [("X-1", 3, "да"), ("Х-1", 3, "да")])
    with pytest.raises(ValueError, match="duplicate"):
        import_open_labels(path, tmp_path)
    path = workbook(tmp_path, [("X-9", 3, "да")])
    with pytest.raises(ValueError, match="Missing"):
        import_open_labels(path, tmp_path)
    _, report = import_open_labels(path, tmp_path, allow_missing=True)
    assert report["missing_videos"] == ["X-9"]
    (tmp_path / "Х-1.mov").touch()
    with pytest.raises(ValueError, match="Ambiguous"):
        video_index(tmp_path)


def test_verdict_not_guessed():
    assert parse_verdict(False) is False
    assert parse_verdict(0) is False
    with pytest.raises(ValueError):
        parse_verdict("maybe")


def test_manual_multi_event(tmp_path):
    labels = [dict(video="Х-1.mov", has_violation="true", timecode_sec=3, violation_type="unknown", observed_action="", notes="")]
    path = tmp_path / "manual.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["video", "timecode_sec", "observed_action", "violation_type", "notes"])
        writer.writerows([("X-1", 3, "synthetic action", "synthetic_type", ""),
                          ("X-1", 6, "synthetic action", "synthetic_type", "")])
    merged, catalog = merge_manual(labels, path, {"synthetic_type": {"name": "Synthetic", "description": "Synthetic action"}})
    assert len(merged) == 2
    assert set(catalog["synthetic_type"]) == {"name", "description"}
    assert labels[0]["violation_type"] == "unknown"
    labels[0]["has_violation"] = "false"
    with pytest.raises(ValueError, match="contradicts"):
        merge_manual(labels, path, catalog)


def test_manual_negative_keeps_empty_type_and_time(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text("video,timecode_sec,observed_action,violation_type,notes\nX-1,,synthetic routine,,reviewed\n", encoding="utf-8")
    labels = [dict(video="X-1.mov", has_violation="false", timecode_sec="", violation_type="", observed_action="", notes="")]
    merged, catalog = merge_manual(labels, path, {})
    assert merged[0]["has_violation"] == "false"
    assert merged[0]["violation_type"] == merged[0]["timecode_sec"] == ""
    assert merged[0]["notes"] == "reviewed" and catalog == {}
