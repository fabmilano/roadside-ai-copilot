"""Deterministic coverage rule table.

Encodes the policy tiers in code so /check-coverage doesn't need an LLM call.
Citations come separately from the embedding index (see embeddings.py).
"""

COMMERCIAL_KEYWORDS = ("uber", "lyft", "rideshare", "taxi", "private hire",
                       "commercial", "hire/reward", "hire or reward")


def _is_commercial(customer: dict) -> bool:
    notes = (customer.get("notes") or "").lower()
    return any(kw in notes for kw in COMMERCIAL_KEYWORDS)


def _tier_upper(tier: str) -> str:
    return tier.replace("_", " ").upper()


def _base_roadside_services(tier: str) -> list[str]:
    if tier in ("bronze", "silver"):
        return [
            "Roadside attempt - up to 30 minutes labour (parts at customer cost)",
            "Local recovery - up to 10 miles to nearest suitable repairer",
        ]
    # gold / gold_plus
    return [
        "Roadside attempt - up to 60 minutes labour (parts at customer cost)",
        "National recovery - any single UK destination (up to 7 passengers)",
    ]


def _onward_travel_services() -> list[str]:
    return [
        "Replacement vehicle: Group A hire car for up to 24 hours",
        "Hotel accommodation: one night for driver and passengers, up to GBP 150 total",
        "Alternative transport: Standard class rail fare up to GBP 75 per person",
    ]


def _home_start_services() -> list[str]:
    return [
        "Home Start - up to 30 minutes labour at or within 1/4 mile of home",
        "Local recovery - up to 10 miles to nearest suitable repairer",
    ]


def _european_services() -> list[str]:
    return [
        "European roadside and local recovery",
        "Vehicle repatriation OR 14-day replacement vehicle (if repair > 48h)",
        "Emergency accommodation up to GBP 100 per person per night (max 3 nights)",
        "Return travel to UK - economy air or standard rail",
    ]


def evaluate(
    tier: str,
    incident_type: str | None,
    drivable: bool | None,
    customer: dict,
) -> dict:
    """Return a coverage_result dict matching the existing schema.

    Fields: covered, event_type, applicable_section, services_entitled,
    exclusions_flagged, reasoning. (citations are injected separately.)
    """
    tier = (tier or "").lower()
    incident = (incident_type or "").lower() or None
    tier_label = _tier_upper(tier)

    # ---------------- Commercial exclusion (Section A) ----------------
    if _is_commercial(customer):
        return {
            "covered": False,
            "event_type": "Commercial use exclusion",
            "applicable_section": "Section A - Eligibility",
            "services_entitled": [],
            "exclusions_flagged": [
                "Vehicle used for hire/reward or commercial passenger transport "
                "(Section A eligibility exclusion)"
            ],
            "reasoning": (
                f"Under {tier_label}, Section A excludes vehicles used for "
                "hire/reward, taxi, rideshare, or commercial passenger transport. "
                "The customer's account notes indicate commercial use, so the "
                "incident is not covered under this policy."
            ),
        }

    # ---------------- Accident exclusion ----------------
    # Motor breakdown policies do not cover collision damage; that belongs to
    # the comprehensive insurance line. Route the customer to the right team.
    if incident == "accident":
        return {
            "covered": False,
            "event_type": "Road traffic accident",
            "applicable_section": "Section B - Definitions (Breakdown)",
            "services_entitled": [],
            "exclusions_flagged": [
                "Accidents and collisions fall outside breakdown cover - refer to motor insurance"
            ],
            "reasoning": (
                "This policy covers sudden mechanical/electrical breakdown, not "
                "accidents or collisions. The customer should contact their motor "
                "insurance claims line for collision damage."
            ),
        }

    # ---------------- Fuel incidents (misfuelling) ----------------
    if incident == "fuel":
        if tier in ("bronze", "silver"):
            return {
                "covered": False,
                "event_type": "Misfuelling",
                "applicable_section": "NOT COVERED under " + tier_label,
                "services_entitled": [],
                "exclusions_flagged": [
                    f"Misfuelling is not covered under {tier_label}"
                ],
                "reasoning": (
                    f"{tier_label} explicitly excludes misfuelling. The customer "
                    "would need to arrange fuel drain/flush at their own cost."
                ),
            }
        # Gold / Gold Plus - Section G covers misfuelling
        return {
            "covered": True,
            "event_type": "Misfuelling",
            "applicable_section": "Section G - Additional Benefits",
            "services_entitled": [
                "Misfuelling: draining, flushing, and recovery (fuel replacement at customer cost)",
            ],
            "exclusions_flagged": [],
            "reasoning": (
                f"Under {tier_label} Section G, misfuelling is covered: we arrange "
                "draining, flushing, and recovery. Fuel replacement cost is the "
                "customer's responsibility."
            ),
        }

    # ---------------- Key issue ----------------
    # Lost/locked keys are not a mechanical breakdown - not covered across tiers.
    if incident == "key_issue":
        return {
            "covered": False,
            "event_type": "Key / lockout issue",
            "applicable_section": "Section B - Definitions (Breakdown)",
            "services_entitled": [],
            "exclusions_flagged": [
                "Lost or locked keys are not a mechanical breakdown under Section B"
            ],
            "reasoning": (
                "Section B defines breakdown as sudden mechanical, electrical, or "
                "electronic failure. Lost/locked keys fall outside this definition "
                "and are not covered."
            ),
        }

    # ---------------- Breakdown family (breakdown / flat_battery / flat_tyre / other) ----------------
    # Covered across all tiers. Services depend on tier + drivable.
    services = _base_roadside_services(tier)
    section = "Section C - Roadside Assist"
    event_type = {
        "breakdown": "Breakdown",
        "flat_battery": "Flat battery (electrical failure)",
        "flat_tyre": "Flat tyre",
        "other": "Breakdown",
        None: "Breakdown",
    }.get(incident, "Breakdown")

    # Silver+ adds home start (transparent, but cite if nearby-home hint in incident text)
    if tier in ("silver", "gold", "gold_plus"):
        section = "Section C - Roadside Assist / Section D - Home Start"

    # Gold+ adds onward travel when vehicle is not drivable (requires tow/recovery)
    if tier in ("gold", "gold_plus") and drivable is False:
        services = services + _onward_travel_services()
        section = "Sections C-F (Roadside, Recovery, Onward Travel)"

    # Gold Plus adds european cover as an entitlement line (not location-aware in demo)
    if tier == "gold_plus":
        services = services + ["European cover (Section H) available for EU breakdowns"]

    drivable_note = "vehicle is drivable" if drivable else "vehicle is not drivable" if drivable is False else "drivability unknown"
    reasoning = (
        f"Under {tier_label}, {event_type.lower()} is covered. "
        f"The {drivable_note}, so the relevant entitlements are listed."
    )

    return {
        "covered": True,
        "event_type": event_type,
        "applicable_section": section,
        "services_entitled": services,
        "exclusions_flagged": [],
        "reasoning": reasoning,
    }
