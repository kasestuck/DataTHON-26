"""Visual support requirements, separate from Back 2's catalog JSON contract."""
import json
from pathlib import Path


def load_policy(catalog, catalog_path, policy_path=None):
    path = Path(policy_path) if policy_path else Path(catalog_path).with_name("violation_type_policy.json")
    if not path.exists():
        if policy_path:
            raise ValueError("Configured type policy is missing")
        return {kind: {"enabled": True, "min_evidence": 1, "min_context_sec": 0.} for kind in catalog}
    policy = json.loads(path.read_text(encoding="utf-8"))
    if set(policy) != set(catalog):
        raise ValueError("Type policy must cover the exact catalog")
    for rule in policy.values():
        if (type(rule.get("enabled")) is not bool or type(rule.get("min_evidence")) is not int
                or not 1 <= rule["min_evidence"] <= 3
                or type(rule.get("min_context_sec")) not in (int, float)
                or not 0 <= rule["min_context_sec"] <= 30):
            raise ValueError("Invalid visual evidence policy")
    return policy
