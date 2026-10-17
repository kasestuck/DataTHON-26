import json
from pathlib import Path
import re


def load_catalog(path):
    return validate_catalog(json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_keys))


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate catalog key")
        result[key] = value
    return result


def validate_catalog(catalog):
    """Back 2 contract: {type_id: {name, description}}. No legal verdict implied."""
    if not isinstance(catalog, dict):
        raise ValueError("Catalog must map type identifiers to name/description")
    for kind, item in catalog.items():
        if (not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind)
                or kind in {"unknown", "needs_review"}):
            raise ValueError("Catalog types need non-reserved ASCII identifiers")
        if (not isinstance(item, dict) or set(item) != {"name", "description"}
                or any(not isinstance(item[field], str) or not item[field].strip() for field in item)):
            raise ValueError("Each catalog entry needs exactly a non-empty name and visual description")
    return catalog
