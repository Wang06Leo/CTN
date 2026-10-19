"""Docs-driven engineering checklist, not a substitute for organisers' approval."""
import json
from pathlib import Path

REQUIRED = (
    "official_2026_docs_verified",
    "authentication_confirmed",
    "per_action_schema_confirmed",
    "rate_limits_confirmed",
    "account_limits_confirmed",
    "telemetry_requirements_confirmed",
    "data_sharing_rules_confirmed",
    "team_authorization_confirmed",
    "live_test_procedure_approved",
)

def check_preflight(path: Path) -> dict[str, object]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("Preflight must be a JSON object")
    missing = [key for key in REQUIRED if doc.get(key) is not True]
    return {"ready_for_review": not missing, "missing": missing,
            "note": "This is a human verification checklist, NOT a live enable switch."}
