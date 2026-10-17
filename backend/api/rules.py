"""Validate and maintain the manually reviewed Kazakhstan rule catalog."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


class RuleCatalogError(ValueError):
    pass


def _read_object(path: Path, *, missing_ok: bool = False) -> dict:
    if missing_ok and not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleCatalogError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuleCatalogError(f"{path} must contain a JSON object")
    return value


def _official_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        has_port = parsed.port is not None
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and not has_port
        and bool(parsed.path and parsed.path != "/")
        and any(host == domain or host.endswith("." + domain) for domain in ("adilet.zan.kz", "nationalbank.kz"))
    )


def load_rules(catalog_dir: Path) -> dict:
    rules = _read_object(catalog_dir / "rules.json", missing_ok=True)
    for type_id, entry in rules.items():
        if not isinstance(type_id, str) or not type_id or not isinstance(entry, dict):
            raise RuleCatalogError("Each rule must have a nonempty type ID and an object value")
        status = entry.get("status")
        if status == "needs_review":
            if set(entry) - {"status", "note"}:
                raise RuleCatalogError(f"{type_id}: needs_review cannot contain an unchecked legal source")
            if "note" in entry and not isinstance(entry["note"], str):
                raise RuleCatalogError(f"{type_id}: note must be text")
        elif status == "verified":
            if set(entry) - {"status", "document", "section", "url"}:
                raise RuleCatalogError(f"{type_id}: unsupported verified rule fields")
            if not isinstance(entry.get("document"), str) or not entry["document"].strip():
                raise RuleCatalogError(f"{type_id}: verified rule requires document title")
            if not isinstance(entry.get("url"), str) or not _official_url(entry["url"]):
                raise RuleCatalogError(f"{type_id}: verified rule requires an official HTTPS source URL")
            if "section" in entry and (not isinstance(entry["section"], str) or not entry["section"].strip()):
                raise RuleCatalogError(f"{type_id}: section must be nonempty text")
        else:
            raise RuleCatalogError(f"{type_id}: status must be verified or needs_review")
    return rules


def _type_issues(types: dict) -> list[str]:
    issues = []
    for type_id, info in types.items():
        if (
            not isinstance(type_id, str)
            or not type_id.strip()
            or not isinstance(info, dict)
            or not isinstance(info.get("name"), str)
            or not info["name"].strip()
            or not isinstance(info.get("description"), str)
            or not info["description"].strip()
        ):
            issues.append(f"{type_id}: type requires a nonempty ID, name and description")
    return issues


def validate_catalog(catalog_dir: Path) -> list[str]:
    rules = load_rules(catalog_dir)
    types_path = catalog_dir / "violation_types.json"
    if not types_path.exists():
        return [] if not rules else ["violation_types.json is missing"]
    types = _read_object(types_path)
    issues = _type_issues(types)
    for type_id in types:
        if type_id not in rules:
            issues.append(f"{type_id}: rule entry is missing")
    for type_id in rules.keys() - types.keys():
        issues.append(f"{type_id}: rule has no matching violation type")
    return sorted(issues)


def sync_missing_rules(catalog_dir: Path) -> list[str]:
    types = _read_object(catalog_dir / "violation_types.json")
    issues = _type_issues(types)
    if issues:
        raise RuleCatalogError("; ".join(issues))
    rules = load_rules(catalog_dir)
    missing = sorted(types.keys() - rules.keys())
    if missing:
        rules.update({type_id: {"status": "needs_review"} for type_id in missing})
        path = catalog_dir / "rules.json"
        path.write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or scaffold the Back 2 rule catalog")
    parser.add_argument("command", choices=("check", "sync"))
    parser.add_argument("--catalog-dir", type=Path, default=Path("backend/data"))
    args = parser.parse_args()
    try:
        if args.command == "sync":
            for type_id in sync_missing_rules(args.catalog_dir):
                print(f"{type_id}: needs_review")
        issues = validate_catalog(args.catalog_dir)
    except RuleCatalogError as exc:
        print(exc)
        return 1
    for issue in issues:
        print(issue)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
