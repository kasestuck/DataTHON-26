"""Non-binding risk descriptions, separate from the reviewed legal rules."""
from pathlib import Path

from .rules import RuleCatalogError, _official_url, _read_object, load_rules


REVIEW_NOTICE = "Нормативное основание требует дополнительной проверки"


def load_risk_catalog(catalog_dir: Path, types: dict, rules: dict) -> dict:
    path = catalog_dir / "risk_references.json"
    if not path.exists():
        return {}
    catalog = _read_object(path)
    if set(catalog) != set(types):
        raise RuleCatalogError("Risk references must cover exactly the visual type catalog")
    for type_id, entry in catalog.items():
        if not isinstance(entry, dict) or set(entry) != {"risk_description", "status", "reference", "review_note"}:
            raise RuleCatalogError(f"{type_id}: invalid risk reference fields")
        for field in ("risk_description", "review_note"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise RuleCatalogError(f"{type_id}: {field} must be nonempty text")
        status, reference = entry["status"], entry["reference"]
        if status not in {"verified", "contextual_reference", "internal_policy_required"}:
            raise RuleCatalogError(f"{type_id}: invalid risk reference status")
        if status == "internal_policy_required":
            if reference is not None:
                raise RuleCatalogError(f"{type_id}: internal policy cannot claim an external source")
        else:
            if (not isinstance(reference, dict) or set(reference) != {"document", "url"}
                    or not isinstance(reference["document"], str) or not reference["document"].strip()
                    or not isinstance(reference["url"], str) or not _official_url(reference["url"])):
                raise RuleCatalogError(f"{type_id}: reference requires an official document and HTTPS URL, without an unchecked section")
            if status == "verified":
                rule = rules.get(type_id, {})
                if (rule.get("status") != "verified" or not rule.get("section")
                        or any(reference[key] != rule.get(key) for key in ("document", "url"))):
                    raise RuleCatalogError(f"{type_id}: verified risk reference requires a matching reviewed rule and section")
    return catalog


def risk_description(observation: str, entry: dict | None, *, verified: bool) -> str:
    parts = [f"Возможный риск: {entry['risk_description']}", f"Наблюдение: {observation}"] if entry else [observation]
    if not verified:
        parts.append(REVIEW_NOTICE + ".")
    if entry:
        if entry["status"] == "contextual_reference":
            reference = entry["reference"]
            parts.append(f"Тематическая справка: {reference['document']} ({reference['url']}). "
                         "Ссылка приведена для справки и не подтверждает нарушение конкретного пункта.")
        parts.append(entry["review_note"])
    return " ".join(parts)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate non-binding visual event risk references")
    parser.add_argument("--catalog-dir", type=Path, default=Path("backend/data"))
    args = parser.parse_args()
    try:
        types = _read_object(args.catalog_dir / "violation_types.json")
        risks = load_risk_catalog(args.catalog_dir, types, load_rules(args.catalog_dir))
        print(f"Validated {len(risks)} risk references; confirmed legal rules are maintained separately.")
    except RuleCatalogError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
