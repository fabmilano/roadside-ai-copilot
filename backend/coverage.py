import json
import re
from pathlib import Path

POLICY_FILES = {
    "bronze": "data/policy_bronze.txt",
    "silver": "data/policy_silver.txt",
    "gold": "data/policy_gold.txt",
}

VALID_INCIDENT_TYPES = {
    "breakdown", "flat_battery", "flat_tyre", "fuel",
    "accident", "key_issue", "other",
}

EMERGENCY_KEYWORDS = {
    "injured", "injury", "bleeding", "blood", "hurt", "hospital",
    "fire", "smoke", "trapped", "unconscious", "not breathing",
    "choking", "ambulance", "emergency", "can't move", "cannot move",
    "can't breathe", "cannot breathe", "stuck inside", "paramedic",
    "severe pain", "broken bone", "head injury",
}


def has_emergency_signal(text: str) -> bool:
    """True if the text contains any keyword suggesting a medical/safety emergency."""
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in EMERGENCY_KEYWORDS)


def names_plausibly_match(provided: str, record: str) -> bool:
    """True if the two names could plausibly be the same person (allows STT noise).
    Returns False only when there is zero token overlap - i.e. a completely different name.

    'Sara Michel' vs 'Sarah Mitchell' -> True  (same first-3-char prefix on 'sar')
    'John Rand'   vs 'Sarah Mitchell' -> False (no shared prefix at all)
    """
    if not provided:
        return True  # name not yet given - nothing to check
    a_words = [w for w in re.sub(r"[^a-z ]", "", provided.lower()).split() if len(w) >= 2]
    b_words = [w for w in re.sub(r"[^a-z ]", "", record.lower()).split() if len(w) >= 2]
    for aw in a_words:
        for bw in b_words:
            if aw[:3] == bw[:3]:
                return True
    return False


def load_policy(tier: str) -> str:
    """Read and return the raw policy text for the given tier. Raises ValueError for unknown tiers."""
    if tier not in POLICY_FILES:
        raise ValueError(f"Unknown policy tier: '{tier}'. Valid tiers: {list(POLICY_FILES.keys())}")
    path = Path(__file__).parent / POLICY_FILES[tier]
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")
    return path.read_text()


def normalize_policy_number(raw) -> str | None:
    """Extract digits from the input and reconstruct as ALZ-XXXXX.
    Accepts 'ALZ-10042', '10042', 'alz 10042', 'A L Z 1 0 0 4 2', etc.
    Returns None if no digits are found."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    return f"ALZ-{digits}"


def find_customer(policy_number: str) -> dict | None:
    """Look up a customer by policy number (accepts raw/normalised forms). Returns None if not found."""
    normalized = normalize_policy_number(policy_number)
    if not normalized:
        return None
    path = Path(__file__).parent / "data" / "customers.json"
    customers = json.loads(path.read_text())
    for c in customers:
        if c["policy_number"] == normalized:
            return c
    return None


def normalize_vehicle_reg(raw) -> str | None:
    """UK reg plates: strip all whitespace and uppercase. 'ab21 cde' -> 'AB21CDE'."""
    if raw is None:
        return None
    cleaned = re.sub(r"\s+", "", str(raw)).upper()
    return cleaned or None


def coerce_bool(raw):
    """Coerce LLM-extracted drivable/safe flags to Python bool. Returns None if the value is ambiguous."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in {"true", "yes", "y", "1"}:
        return True
    if s in {"false", "no", "n", "0"}:
        return False
    return None


def coerce_int(raw):
    """Coerce LLM-extracted passenger count to a non-negative int. Returns None if not representable."""
    if raw is None or raw == "":
        return None
    try:
        n = int(float(raw))
        return n if n >= 0 else None
    except (ValueError, TypeError):
        return None


def validate_incident_type(raw) -> str | None:
    """Snap a free-text incident type to the canonical enum value. Returns None if unrecognised."""
    if not raw:
        return None
    lower = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return lower if lower in VALID_INCIDENT_TYPES else None


def hydrate_from_customer_record(fields: dict, customer: dict) -> tuple[list[str], dict, list[str]]:
    """Verify customer-provided fields against the authoritative record.

    Rules:
    - Customer value matches DB  → accept it (write to fields), no note.
    - Customer gave a conflicting value → return in `proposed` (STT-noise repair; safe to read back).
    - Customer gave nothing      → return field key in `missing` (agent must ASK, never reveal DB value).

    Returns:
        notes:    human-readable descriptions of each discrepancy or gap.
        proposed: {field_key: db_value} for real conflicts the agent should read back for confirmation.
        missing:  [field_key, ...] for fields the customer hasn't provided yet (agent must ask).
    """
    notes = []
    proposed = {}
    missing = []
    vehicle = customer.get("vehicle") or {}

    def _check(field_key, truth, label):
        prior = fields.get(field_key)
        if truth is None:
            return
        if not prior:
            # Customer hasn't mentioned this yet - agent must ask, never reveal DB value.
            missing.append(field_key)
            notes.append(f"{label} not yet provided by customer")
        elif str(prior).strip().lower() != str(truth).strip().lower():
            # Customer gave a value that conflicts with DB - safe to read back (STT-noise repair).
            proposed[field_key] = truth
            notes.append(f"{label}: customer said '{prior}', records show '{truth}'")
        else:
            fields[field_key] = truth  # confirmed match - safe to accept

    _check("customer_name", customer.get("name"), "name")
    _check("vehicle_make", vehicle.get("make"), "vehicle make")
    _check("vehicle_model", vehicle.get("model"), "vehicle model")
    _check("vehicle_year", vehicle.get("year"), "vehicle year")
    _check("vehicle_reg", vehicle.get("reg"), "vehicle reg")
    return notes, proposed, missing
