import base64
import json
import math
from pathlib import Path

from .rules import load_rules
from .risk_catalog import load_risk_catalog, risk_description
from .display_text import normalize_observation, save_description_diagnostics


def _value(item, name):
    try:
        return item[name] if isinstance(item, dict) else getattr(item, name)
    except (KeyError, AttributeError, TypeError) as exc:
        raise ValueError(f"Finding requires {name}") from exc


def _read_catalog(path: Path) -> dict:
    if not path.exists():
        return {}
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return catalog


def _number(value, name: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return float(value)


def _validated_finding(finding):
    type_id = _value(finding, "violation_type")
    description = _value(finding, "description")
    if not isinstance(type_id, str) or not type_id.strip():
        raise ValueError("violation_type must be nonempty text")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be nonempty text")
    timecode = _number(_value(finding, "timecode_sec"), "timecode_sec")
    _number(_value(finding, "confidence"), "confidence", maximum=1)
    evidence = _value(finding, "evidence_frames")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
        raise ValueError("evidence_frames must contain 1–3 evidence frames")
    frames = []
    for frame in evidence:
        timestamp = _number(_value(frame, "timestamp_sec"), "timestamp_sec")
        jpeg = _value(frame, "jpeg_bytes")
        if not isinstance(jpeg, bytes) or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("jpeg_bytes must contain JPEG evidence bytes")
        frames.append({"timestamp_sec": timestamp, "image_base64": base64.b64encode(jpeg).decode("ascii")})
    return type_id, timecode, description, frames


def format_result(findings: list, catalog_dir: Path, *, diagnostics_path: Path | None = None) -> dict:
    if not isinstance(findings, list):
        raise ValueError("analyze_video must return a list of Finding")
    types = _read_catalog(catalog_dir / "violation_types.json")
    rules = load_rules(catalog_dir)
    risks = load_risk_catalog(catalog_dir, types, rules)
    violations = []
    observations = []
    for finding in findings:
        type_id, timecode, description, frames = _validated_finding(finding)
        original_description = description
        description = normalize_observation(description)
        observations.append({"violation_type": type_id, "original_description": original_description,
                             "display_description": description})
        if type_id not in types:
            raise ValueError(f"violation_type {type_id!r} is absent from violation_types.json")
        type_info = types[type_id]
        name = type_info["name"]
        rule = rules.get(type_id)
        risk = risks.get(type_id)
        verified = bool(rule and rule["status"] == "verified" and rule.get("section")
                        and (risk is None or risk["status"] == "verified"))
        description = risk_description(description, risk, verified=verified)
        if verified:
            source = rule["document"]
            if rule.get("section"):
                source += f", раздел/пункт {rule['section']}"
            if rule.get("url"):
                source += f" ({rule['url']})"
            description = f"{description} Источник: {source}."
        violations.append(
            {
                "type": name,
                "timecode_sec": timecode,
                "description": description,
                "evidence_frames": frames,
            }
        )
    if diagnostics_path is not None and observations:
        save_description_diagnostics(diagnostics_path, observations)
    return {"verdict": "violation" if violations else "no_violation", "violations": violations}
