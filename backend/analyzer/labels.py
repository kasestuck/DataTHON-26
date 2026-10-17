"""Local label import. No inference, training, or network access."""
import csv
from datetime import datetime, time, timedelta
import json
import math
from pathlib import Path
import re
import unicodedata

FIELDS = ["video", "has_violation", "timecode_sec", "violation_type", "observed_action", "notes",
          "review_status", "source_row", "source_sha256", "proposed_type", "source_observed_action",
          "organizer_has_violation", "organizer_timecode_sec", "legal_basis_status", "annotation_source"]
ALIASES = {
    "video": {"video", "filename", "file", "видео", "файл", "название файла", "наименование файла"},
    "has_violation": {"has_violation", "label", "нарушение", "нарушение / без нарушения", "вердикт"},
    "timecode_sec": {"timecode_sec", "timestamp", "timecode", "таймкод", "таймстемп", "секунда", "время"},
    "observed_action": {"observed_action", "наблюдаемое действие"},
    "violation_type": {"violation_type", "тип нарушения"},
    "notes": {"notes", "примечания"},
    "review_status": {"review_status", "статус проверки"},
}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str("" if value is None else value)).strip().casefold().split())


def video_key(value):
    # Only X/Х confusables are intentional; ambiguous actual filenames are rejected.
    if value is None:
        return ""
    return normalized(Path(str(value).replace("\\", "/")).stem).replace("х", "x")


def parse_timecode(value):
    if value is None or normalized(value) == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a timestamp")
    if isinstance(value, time):
        result = value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    elif isinstance(value, timedelta):
        result = value.total_seconds()
    elif isinstance(value, datetime):
        return parse_timecode(value.time())
    elif isinstance(value, (int, float)):
        # Plain numeric spreadsheet cells mean seconds, not Excel day fractions.
        result = float(value)
    else:
        text = normalized(value).replace(",", ".")
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            result = float(text)
        elif re.fullmatch(r"\d+:\d{1,2}(?::\d{1,2})?(?:\.\d+)?", text):
            parts = [float(p) for p in text.split(":")]
            if any(p >= 60 for p in parts[1:]):
                raise ValueError(f"Invalid clock timestamp: {value!r}")
            result = sum(p * 60**i for i, p in enumerate(reversed(parts)))
        else:
            pattern = r"(\d+(?:\.\d+)?)\s*(час(?:а|ов)?|ч|минут(?:а|ы)?|мин|м|секунд(?:а|ы)?|сек|с)\.?"
            matches = list(re.finditer(pattern, text))
            if not matches or re.sub(pattern, "", text).strip():
                raise ValueError(f"Unsupported timestamp: {value!r}")
            result = sum(float(m[1]) * (3600 if m[2].startswith("ч") else 60 if m[2].startswith("м") else 1) for m in matches)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Invalid timestamp: {value!r}")
    return result


def parse_verdict(value):
    if normalized(value) in {"false", "0", "нет", "без нарушения", "без нарушений", "нет нарушения"}:
        return False
    if normalized(value) in {"true", "1", "да", "нарушение", "есть нарушение"}:
        return True
    raise ValueError(f"Unsupported verdict: {value!r}")


def read_table(path, required, columns=None, sheet=None):
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            rows = list((workbook[sheet] if sheet else workbook.active).values)
        finally:
            workbook.close()
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(stream, dialect))
    else:
        raise ValueError("Use .xlsx or UTF-8 .csv")
    for index, row in enumerate(rows[:20]):
        mapping = {}
        for col, header in enumerate(row):
            for field, aliases in ALIASES.items():
                names = {normalized(columns[field])} if columns and field in columns else aliases
                if normalized(header) in names:
                    if field in mapping:
                        raise ValueError(f"Ambiguous column {field}")
                    mapping[field] = col
        if required <= mapping.keys():
            return [{field: row[col] if col < len(row) else None for field, col in mapping.items()}
                    for row in rows[index + 1:] if any(v is not None and str(v).strip() for v in row)]
    raise ValueError(f"Cannot identify columns {sorted(required)}; use explicit --columns JSON mapping")


def video_index(videos_dir):
    root = Path(videos_dir)
    if not root.is_dir():
        raise FileNotFoundError(root)
    result = {}
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() == ".mov":
            key = video_key(path.name)
            if key in result:
                raise ValueError(f"Ambiguous video filenames: {result[key].name}, {path.name}")
            result[key] = path
    return result


def import_open_labels(path, videos_dir, columns=None, sheet=None, allow_missing=False):
    index = video_index(videos_dir)
    rows = read_table(path, {"video", "has_violation", "timecode_sec"}, columns, sheet)
    labels, seen, missing = [], set(), []
    for row in rows:
        key = video_key(row["video"])
        if not key or key in seen:
            raise ValueError(f"Empty or duplicate video: {row['video']!r}")
        seen.add(key)
        actual = index.get(key)
        if actual is None:
            missing.append(str(row["video"]))
        positive = parse_verdict(row["has_violation"])
        timestamp = parse_timecode(row["timecode_sec"]) if positive else None
        labels.append(dict(video=actual.name if actual else f"{Path(str(row['video'])).stem}.mov",
                           has_violation="true" if positive else "false",
                           timecode_sec="" if timestamp is None else timestamp,
                           violation_type="unknown" if positive else "", observed_action="", notes=""))
    if missing and not allow_missing:
        raise ValueError(f"Missing videos: {missing}")
    return labels, {"rows": len(labels), "positive": sum(r["has_violation"] == "true" for r in labels),
                    "missing_videos": missing, "unlabeled_videos": [p.name for k, p in index.items() if k not in seen]}


def write_labels(labels, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(labels)
    temporary.replace(path)


def read_labels(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        positive = parse_verdict(row["has_violation"])
        row["has_violation"] = "true" if positive else "false"
        row["timecode_sec"] = parse_timecode(row["timecode_sec"]) if positive else ""
        row["violation_type"] = (row.get("violation_type") or "unknown") if positive else ""
    return rows


def merge_manual(labels, table_path, catalog):
    rows = read_table(table_path, {"video", "timecode_sec", "observed_action", "violation_type", "notes"})
    by_video, seen = {}, set()
    known = {video_key(row["video"]): row for row in labels}
    from .catalog import validate_catalog
    types = {key: dict(value) for key, value in validate_catalog(catalog).items()}
    for row in rows:
        key = video_key(row["video"])
        if key not in known:
            raise ValueError(f"Manual video not present in labels: {row['video']}")
        kind, action = str(row["violation_type"] or "").strip(), str(row["observed_action"] or "").strip()
        timestamp = parse_timecode(row["timecode_sec"])
        if known[key]["has_violation"] != "true":
            if kind or timestamp is not None:
                raise ValueError("Manual classification contradicts a negative verdict; review source labels explicitly")
            if key in by_video:
                raise ValueError(f"Duplicate negative manual video: {row['video']}")
            by_video[key] = [dict(video=known[key]["video"], has_violation="false", timecode_sec="",
                violation_type="", observed_action=action, notes=str(row["notes"] or ""))]
            continue
        status = str(row.get("review_status") or "confirmed").strip()
        if status == "needs_review":
            by_video.setdefault(key, []).append(dict(video=known[key]["video"], has_violation="true",
                timecode_sec="" if timestamp is None else timestamp, violation_type="unknown",
                observed_action=str(row["observed_action"] or ""), notes=str(row["notes"] or ""),
                review_status="needs_review", proposed_type=kind if kind in types else ""))
            continue
        if status != "confirmed" or not kind or kind.casefold() == "unknown" or not action or timestamp is None:
            raise ValueError("Manual classification requires a confirmed type, action and timestamp")
        identity = (key, timestamp, kind)
        if identity in seen:
            raise ValueError(f"Duplicate manual event: {identity}")
        seen.add(identity)
        by_video.setdefault(key, []).append(dict(video=known[key]["video"], has_violation="true",
            timecode_sec=timestamp, violation_type=kind, observed_action=str(row["observed_action"]),
            notes=str(row["notes"] or ""), review_status="confirmed"))
        if kind not in types:
            raise ValueError("Manual type must already have reviewed visual criteria in the catalog")
    merged, emitted = [], set()
    for row in labels:
        key = video_key(row["video"])
        if key not in by_video:
            merged.append(dict(row))
        elif key not in emitted:
            merged.extend(by_video[key])
            emitted.add(key)
    return merged, types


def write_catalog(catalog, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
