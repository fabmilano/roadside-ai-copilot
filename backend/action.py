import json
import math
from pathlib import Path

# Keyword -> (lat, lng) lookup for UK cities and roads.
# Keys are lowercase substrings matched against location_description.
LOCATION_KEYWORDS = {
    # Manchester area
    "manchester": (53.4800, -2.2400),
    "salford": (53.4800, -2.2900),
    "trafford": (53.4600, -2.3200),
    "m60": (53.4700, -2.2800),
    "m62": (53.4650, -2.2600),
    "piccadilly": (53.4800, -2.2350),
    "deansgate": (53.4780, -2.2500),
    "stockport": (53.4060, -2.1575),
    "stretford": (53.4480, -2.3000),

    # Birmingham area
    "birmingham": (52.4860, -1.8900),
    "coventry": (52.4068, -1.5197),
    "wolverhampton": (52.5870, -2.1288),
    "solihull": (52.4130, -1.7780),
    "a38": (52.4860, -1.8900),
    "m6": (52.5000, -1.9200),
    "new street": (52.4779, -1.8997),

    # Edinburgh area
    "edinburgh": (55.9530, -3.1880),
    "leith": (55.9760, -3.1700),
    "lothian": (55.9000, -3.2000),
    "a1": (55.9300, -3.1200),
    "a720": (55.9000, -3.2500),
    "princes street": (55.9521, -3.1965),
    "corstorphine": (55.9420, -3.2820),

    # Leeds / Yorkshire area
    "leeds": (53.7990, -1.5500),
    "bradford": (53.7960, -1.7594),
    "a1(m)": (53.8000, -1.5600),
    "headingley": (53.8200, -1.5780),
    "kirkstall": (53.8150, -1.5900),
    "wakefield": (53.6833, -1.4977),
    "m1": (53.7500, -1.5000),

    # Bristol area
    "bristol": (51.4540, -2.5870),
    "clifton": (51.4570, -2.6200),
    "m5": (51.4500, -2.6000),
    "bath road": (51.4540, -2.5800),
    "cabot circus": (51.4600, -2.5850),
    "bath": (51.3758, -2.3599),
    "filton": (51.5000, -2.5800),

    # London area (general + central)
    "london": (51.5074, -0.1278),
    "central london": (51.5074, -0.1278),
    "city of london": (51.5155, -0.0922),
    "westminster": (51.4994, -0.1273),
    "camden": (51.5390, -0.1426),
    "islington": (51.5362, -0.1033),
    "hackney": (51.5452, -0.0553),
    "dalston": (51.5460, -0.0750),
    "shoreditch": (51.5230, -0.0780),
    "stratford": (51.5414, -0.0034),
    "canary wharf": (51.5054, -0.0235),
    "brixton": (51.4613, -0.1156),
    "clapham": (51.4620, -0.1380),

    # Glasgow area
    "glasgow": (55.8640, -4.2520),
    "paisley": (55.8450, -4.4230),
    "m8": (55.8640, -4.2800),
    "sauchiehall": (55.8637, -4.2644),
    "merchant city": (55.8590, -4.2420),
    "east kilbride": (55.7640, -4.1770),

    # Cardiff area
    "cardiff": (51.4816, -3.1791),
    "newport": (51.5842, -2.9977),
    "canton": (51.4840, -3.2040),
    "roath": (51.4940, -3.1620),
    "cardiff bay": (51.4638, -3.1620),
    "m4": (51.5000, -3.0000),
}

# Default: central London
DEFAULT_LOCATION = (51.5074, -0.1278)


def resolve_location(description: str) -> tuple[float, float]:
    """Map a free-text location description to approximate lat/lng via keyword lookup.

    Scans the description for known city, road, or landmark keywords. Falls back
    to central London if no keyword matches - callers should treat the fallback
    as "location unknown" for any distance-sensitive logic.
    """
    if not description:
        return DEFAULT_LOCATION
    lower = description.lower()
    for keyword, coords in LOCATION_KEYWORDS.items():
        if keyword in lower:
            return coords
    return DEFAULT_LOCATION


def load_garages() -> list:
    """Load the garage records from data/garages.json."""
    path = Path(__file__).parent / "data" / "garages.json"
    return json.loads(path.read_text())


def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    R = 3959
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def find_nearby_garages(lat: float, lng: float, max_miles: float = 15.0) -> list:
    """Return all garages within max_miles of the given coordinates, sorted by distance."""
    garages = load_garages()
    results = []
    for g in garages:
        dist = haversine_miles(lat, lng, g["lat"], g["lng"])
        g_copy = {**g, "distance_miles": round(dist, 1)}
        if dist <= max_miles:
            results.append(g_copy)
    return sorted(results, key=lambda x: x["distance_miles"])


# ---------------------------------------------------------------------------
# Deterministic action selector
# ---------------------------------------------------------------------------

INCIDENT_CAPABILITY = {
    "flat_battery": "battery",
    "flat_tyre": "tyre",
    "breakdown": "mechanical",
    "fuel": "mechanical",
    "accident": "bodywork",
    "key_issue": "mechanical",
    "other": "mechanical",
}


def _required_capability(incident_type: str | None) -> str:
    """Map incident type to the garage capability needed for a mobile repair."""
    return INCIDENT_CAPABILITY.get((incident_type or "").lower(), "mechanical")


_ONWARD_KEYWORDS = {
    "hire_car": ("hire car", "replacement vehicle"),
    "rail": ("rail", "train"),
    "hotel": ("hotel", "accommodation"),
}
_ONWARD_PRIORITY = ["hire_car", "rail", "hotel"]

_ROADSIDE_KEYS = ("roadside attempt", "local recovery", "national recovery",
                  "home start", "labour")


def _classify_onward(services: list[str]) -> list[str]:
    """Return a deduplicated list of onward-travel option keys present in coverage services."""
    found = []
    for key in _ONWARD_PRIORITY:
        if any(kw in svc.lower() for svc in services for kw in _ONWARD_KEYWORDS[key]):
            found.append(key)
    return found


def select_action(
    garages: list,
    incident_type: str | None,
    drivable: bool | None,
    coverage_services: list[str],
    tier: str,
) -> dict:
    """Pick recovery action + onward travel deterministically.

    Returns dict with:
      recovery_action: "tow" | "mobile_repair" | "none"
      garage: garage dict or None
      garage_index: int
      onward_travel: "hire_car" | "rail" | "hotel" | "none"
      onward_travel_options: list of available onward-travel keys
      estimated_response_minutes: int
      reasoning: str

    Rules:
    - drivable=False -> tow; garage must have has_tow_truck=True.
    - drivable=True/None -> mobile_repair; nearest garage with the right capability.
    - onward_travel: first available entitlement from coverage when vehicle is not drivable.
    - ETA = 15 + distance * 3 (minutes).
    """
    if not garages:
        return {
            "recovery_action": "none",
            "garage": None,
            "garage_index": 0,
            "onward_travel": "none",
            "onward_travel_options": [],
            "estimated_response_minutes": 0,
            "reasoning": "No garages found within range.",
        }

    needs_tow = drivable is False
    required_cap = _required_capability(incident_type)

    def _eligible(g):
        if needs_tow and not g.get("has_tow_truck"):
            return False
        caps = g.get("capabilities") or []
        if needs_tow:
            return True  # any tow-capable garage can receive a recovered vehicle
        return required_cap in caps

    chosen_idx = None
    for i, g in enumerate(garages):
        if _eligible(g):
            chosen_idx = i
            break
    if chosen_idx is None:
        chosen_idx = 0  # fall back to closest garage; dispatch can adjust later

    selected = garages[chosen_idx]
    distance = selected["distance_miles"]
    eta = int(15 + distance * 3)

    recovery_action = "tow" if needs_tow else "mobile_repair"
    tier_label = (tier or "").replace("_", " ").upper()
    reasoning_bits = [
        f"{tier_label} tier, {'non-drivable' if needs_tow else 'drivable'} vehicle -> {recovery_action.replace('_', ' ')}.",
        f"Nearest eligible garage is {selected['name']} ({distance} miles away).",
    ]
    if needs_tow:
        reasoning_bits.append("Tow truck available at this garage.")
    else:
        reasoning_bits.append(f"Garage has the '{required_cap}' capability needed for this incident.")

    # Onward-travel entitlements apply when the vehicle is not drivable.
    onward_travel_options = _classify_onward(coverage_services or []) if needs_tow else []
    onward_travel = onward_travel_options[0] if onward_travel_options else "none"

    return {
        "recovery_action": recovery_action,
        "garage": selected,
        "garage_index": chosen_idx,
        "onward_travel": onward_travel,
        "onward_travel_options": onward_travel_options,
        "estimated_response_minutes": eta,
        "reasoning": " ".join(reasoning_bits),
    }
