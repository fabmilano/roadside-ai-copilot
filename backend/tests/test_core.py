import json
from unittest.mock import AsyncMock, patch

import pytest

from action import find_nearby_garages, haversine_miles, resolve_location
from coverage import (
    coerce_bool,
    coerce_int,
    find_customer,
    has_emergency_signal,
    hydrate_from_customer_record,
    load_policy,
    names_plausibly_match,
    normalize_policy_number,
    normalize_vehicle_reg,
    validate_incident_type,
)
from session import create_session, get_session, sessions


# ---------------------------------------------------------------------------
# 1. Customer lookup
# ---------------------------------------------------------------------------

class TestCustomerLookup:
    def test_find_known_customer(self):
        customer = find_customer("ALZ-10042")
        assert customer is not None
        assert customer["name"] == "Sarah Mitchell"
        assert customer["tier"] == "gold"

    def test_find_unknown_customer_returns_none(self):
        assert find_customer("ALZ-99999") is None

    def test_find_commercial_customer(self):
        customer = find_customer("ALZ-60099")
        assert customer is not None
        assert "notes" in customer
        assert "commercial" in customer["notes"].lower()

    def test_find_by_bare_digits(self):
        """Customer can provide just the numeric part."""
        customer = find_customer("10042")
        assert customer is not None
        assert customer["name"] == "Sarah Mitchell"

    def test_find_with_spelled_out_prefix(self):
        """Lossy STT might give 'A L Z 1 0 0 4 2' - digits should still be extracted."""
        customer = find_customer("A L Z 1 0 0 4 2")
        assert customer is not None
        assert customer["policy_number"] == "ALZ-10042"


class TestFieldValidators:
    def test_normalize_vehicle_reg(self):
        assert normalize_vehicle_reg("ab21 cde") == "AB21CDE"
        assert normalize_vehicle_reg("  AB21 CDE  ") == "AB21CDE"
        assert normalize_vehicle_reg("AB21CDE") == "AB21CDE"
        assert normalize_vehicle_reg(None) is None
        assert normalize_vehicle_reg("") is None

    def test_coerce_bool(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False
        assert coerce_bool("yes") is True
        assert coerce_bool("no") is False
        assert coerce_bool("True") is True
        assert coerce_bool("false") is False
        assert coerce_bool("maybe") is None
        assert coerce_bool(None) is None

    def test_coerce_int(self):
        assert coerce_int(3) == 3
        assert coerce_int("2") == 2
        assert coerce_int("2.0") == 2
        assert coerce_int("one") is None
        assert coerce_int(-1) is None
        assert coerce_int(None) is None
        assert coerce_int("") is None

    def test_validate_incident_type(self):
        assert validate_incident_type("breakdown") == "breakdown"
        assert validate_incident_type("Breakdown") == "breakdown"
        assert validate_incident_type("flat tyre") == "flat_tyre"
        assert validate_incident_type("flat-battery") == "flat_battery"
        assert validate_incident_type("spontaneous combustion") is None
        assert validate_incident_type(None) is None

    def test_hydrate_from_customer_record_stt_noise_not_overwritten(self, sample_customer):
        """STT-mangled fields must NOT be silently overwritten; they go into proposed."""
        fields = {
            "customer_name": "Sara Michel",        # STT noise
            "vehicle_make": "Four",                # 'Ford' misheard
            "vehicle_model": "Focus",
            "vehicle_year": None,
            "vehicle_reg": "AB21 CDE",
        }
        notes, proposed, missing = hydrate_from_customer_record(fields, sample_customer)
        # Mismatched fields go into proposed (STT-noise repair - safe to read back)
        assert fields["customer_name"] == "Sara Michel"   # unchanged - customer must confirm
        assert fields["vehicle_make"] == "Four"            # unchanged - customer must confirm
        assert proposed["customer_name"] == "Sarah Mitchell"
        assert proposed["vehicle_make"] == "Ford"
        # Missing fields go into missing, NOT into proposed (never revealed)
        assert "vehicle_year" in missing
        assert "vehicle_year" not in proposed
        assert fields.get("vehicle_year") is None          # not filled in from DB
        assert len(notes) >= 2                             # name + make + year discrepancies noted

    def test_hydrate_matching_fields_accepted(self, sample_customer):
        """When customer values match DB exactly, accept them and generate no notes."""
        fields = {
            "customer_name": "Sarah Mitchell",
            "vehicle_make": "Ford",
            "vehicle_model": "Focus",
            "vehicle_year": 2021,
            "vehicle_reg": "AB21 CDE",
        }
        notes, proposed, missing = hydrate_from_customer_record(fields, sample_customer)
        assert notes == []
        assert proposed == {}
        assert missing == []
        # Matching fields written back (no-op in practice since values already match)
        assert fields["customer_name"] == "Sarah Mitchell"
        assert fields["vehicle_make"] == "Ford"

    def test_hydrate_missing_fields_not_revealed(self, sample_customer):
        """When customer has provided nothing, DB values must never appear in proposed."""
        fields = {
            "customer_name": None,
            "vehicle_make": None,
            "vehicle_model": None,
            "vehicle_year": None,
            "vehicle_reg": None,
        }
        notes, proposed, missing = hydrate_from_customer_record(fields, sample_customer)
        assert proposed == {}  # no DB value surfaced - agent must ask
        assert set(missing) >= {"customer_name", "vehicle_make", "vehicle_model", "vehicle_year", "vehicle_reg"}


class TestNormalizePolicyNumber:
    def test_canonical_format_unchanged(self):
        assert normalize_policy_number("ALZ-10042") == "ALZ-10042"

    def test_bare_digits_prefixed(self):
        assert normalize_policy_number("10042") == "ALZ-10042"

    def test_spaced_digits(self):
        assert normalize_policy_number("1 0 0 4 2") == "ALZ-10042"

    def test_lowercase_and_no_dash(self):
        assert normalize_policy_number("alz 10042") == "ALZ-10042"

    def test_empty_returns_none(self):
        assert normalize_policy_number("") is None
        assert normalize_policy_number(None) is None
        assert normalize_policy_number("no digits here") is None


# ---------------------------------------------------------------------------
# 2. Policy loading
# ---------------------------------------------------------------------------

class TestPolicyLoading:
    @pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "gold_plus"])
    def test_load_policy_returns_text(self, tier):
        text = load_policy(tier)
        assert isinstance(text, str)
        assert len(text) > 100
        assert "ALLIANZ" in text.upper()

    def test_load_invalid_tier_raises(self):
        with pytest.raises(ValueError, match="Unknown policy tier"):
            load_policy("platinum")


# ---------------------------------------------------------------------------
# 3. Garage finder / haversine
# ---------------------------------------------------------------------------

class TestGarageFinder:
    def test_haversine_manchester_to_trafford(self):
        # Manchester city centre -> Trafford (~2.5 miles west)
        dist = haversine_miles(53.4800, -2.2400, 53.4600, -2.3200)
        assert 2.0 < dist < 5.0, f"Expected ~3 miles, got {dist}"

    def test_find_nearby_garages_sorted_by_distance(self):
        # Manchester city centre - should find Manchester and Trafford garages
        garages = find_nearby_garages(53.4808, -2.2426, max_miles=15.0)
        assert len(garages) > 0
        distances = [g["distance_miles"] for g in garages]
        assert distances == sorted(distances)

    def test_find_nearby_garages_tight_radius_returns_empty(self):
        # Middle of the North Sea - no garages within 0.01 miles
        garages = find_nearby_garages(56.0, 3.0, max_miles=0.01)
        assert garages == []

    def test_resolve_location_keyword_match(self):
        lat, lng = resolve_location("I'm on the M60 near Manchester city centre")
        # Should match "manchester" keyword
        assert abs(lat - 53.4800) < 0.1
        assert abs(lng - (-2.2400)) < 0.2

    def test_resolve_location_fallback(self):
        lat, lng = resolve_location("somewhere in Outer Mongolia")
        # Should fall back to central London default
        assert lat == 51.5074
        assert lng == -0.1278


# ---------------------------------------------------------------------------
# 4. Session management
# ---------------------------------------------------------------------------

class TestSessionManagement:
    def test_create_session_initialises_all_fields(self):
        sid = "test-create-001"
        sessions.pop(sid, None)
        create_session(sid)
        s = sessions[sid]
        assert s["id"] == sid
        assert s["status"] == "intake"
        assert s["conversation_history"] == []
        assert s["extracted_fields"]["customer_name"] is None
        assert s["coverage_result"] is None
        sessions.pop(sid, None)

    def test_get_session_after_creation(self):
        sid = "test-get-001"
        sessions.pop(sid, None)
        create_session(sid)
        s = get_session(sid)
        assert s is not None
        assert s["id"] == sid
        sessions.pop(sid, None)

    def test_get_missing_session_returns_none(self):
        assert get_session("does-not-exist") is None


# ---------------------------------------------------------------------------
# 5. LLM JSON parsing (mocked)
# ---------------------------------------------------------------------------

class TestLLMJsonParsing:
    @pytest.mark.asyncio
    async def test_valid_json_response(self):
        mock_response = AsyncMock()
        mock_response.choices[0].message.content = '{"reply": "hello", "extracted": {}}'

        with patch("litellm.acompletion", return_value=mock_response):
            from llm import call_llm
            result = await call_llm("sys", "user", response_format="json")
        assert result == {"reply": "hello", "extracted": {}}

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_is_stripped(self):
        mock_response = AsyncMock()
        mock_response.choices[0].message.content = '```json\n{"key": "value"}\n```'

        with patch("litellm.acompletion", return_value=mock_response):
            from llm import call_llm
            result = await call_llm("sys", "user", response_format="json")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_raw_text_response(self):
        mock_response = AsyncMock()
        mock_response.choices[0].message.content = "Hi there!"

        with patch("litellm.acompletion", return_value=mock_response):
            from llm import call_llm
            result = await call_llm("sys", "user", response_format="text")
        assert result == "Hi there!"


# ---------------------------------------------------------------------------
# 6. Coverage rule table (deterministic)
# ---------------------------------------------------------------------------

from coverage_rules import evaluate as evaluate_coverage


class TestCoverageRuleTable:
    def test_commercial_use_denies_across_tiers(self, commercial_customer):
        for tier in ("bronze", "silver", "gold", "gold_plus"):
            r = evaluate_coverage(tier, "breakdown", True, commercial_customer)
            assert r["covered"] is False
            assert "Section A" in r["applicable_section"]
            assert r["services_entitled"] == []

    def test_accident_denied_breakdown_policy(self):
        r = evaluate_coverage("gold_plus", "accident", True, {"notes": ""})
        assert r["covered"] is False
        assert "motor insurance" in r["reasoning"].lower()

    def test_fuel_denied_bronze_silver(self):
        for tier in ("bronze", "silver"):
            r = evaluate_coverage(tier, "fuel", True, {"notes": ""})
            assert r["covered"] is False

    def test_fuel_covered_gold_misfuelling(self):
        r = evaluate_coverage("gold", "fuel", True, {"notes": ""})
        assert r["covered"] is True
        assert "Section G" in r["applicable_section"]
        assert any("misfuelling" in s.lower() for s in r["services_entitled"])

    def test_key_issue_denied_all_tiers(self):
        r = evaluate_coverage("gold_plus", "key_issue", True, {"notes": ""})
        assert r["covered"] is False

    def test_gold_nondrivable_includes_onward_travel(self):
        r = evaluate_coverage("gold", "breakdown", False, {"notes": ""})
        assert r["covered"] is True
        assert any("hire car" in s.lower() for s in r["services_entitled"])
        assert any("rail fare" in s.lower() for s in r["services_entitled"])

    def test_silver_drivable_no_onward_travel(self):
        r = evaluate_coverage("silver", "flat_tyre", True, {"notes": ""})
        assert r["covered"] is True
        assert not any("hire car" in s.lower() for s in r["services_entitled"])

    def test_gold_plus_adds_european_line(self):
        r = evaluate_coverage("gold_plus", "breakdown", True, {"notes": ""})
        assert any("European" in s for s in r["services_entitled"])


# ---------------------------------------------------------------------------
# 6b. Deterministic action selector
# ---------------------------------------------------------------------------

from action import select_action


class TestActionSelector:
    GARAGES = [
        {"name": "Far Mech", "distance_miles": 10.0, "capabilities": ["mechanical"], "has_tow_truck": True, "lat": 0, "lng": 0, "hours": "24/7"},
        {"name": "Closer EV", "distance_miles": 3.0, "capabilities": ["ev", "battery"], "has_tow_truck": False, "lat": 0, "lng": 0, "hours": "8-6"},
        {"name": "Closest Mech", "distance_miles": 1.5, "capabilities": ["mechanical", "battery", "tyre"], "has_tow_truck": True, "lat": 0, "lng": 0, "hours": "24/7"},
    ]

    def _sorted(self):
        return sorted(self.GARAGES, key=lambda g: g["distance_miles"])

    def test_nondrivable_selects_tow_capable_garage(self):
        r = select_action(self._sorted(), "breakdown", False, [], "gold")
        assert r["action"] == "tow"
        assert r["garage"]["has_tow_truck"] is True

    def test_drivable_flat_tyre_picks_nearest_tyre_capable(self):
        r = select_action(self._sorted(), "flat_tyre", True, [], "silver")
        assert r["action"] == "mobile_repair"
        assert "tyre" in r["garage"]["capabilities"]

    def test_eta_scales_with_distance(self):
        r = select_action(self._sorted(), "breakdown", False, [], "gold")
        # closest tow = 1.5 miles -> 15 + 1.5*3 = 19
        assert r["estimated_response_minutes"] == 19

    def test_additional_services_filters_roadside_items(self):
        services = [
            "Roadside attempt - up to 60 minutes labour (parts at customer cost)",
            "National recovery - any single UK destination",
            "Replacement vehicle: Group A hire car for up to 24 hours",
            "Alternative transport: Standard class rail fare up to GBP 75 per person",
        ]
        r = select_action(self._sorted(), "breakdown", False, services, "gold")
        assert any("hire car" in s.lower() for s in r["additional_services"])
        assert any("rail fare" in s.lower() for s in r["additional_services"])
        assert not any("roadside attempt" in s.lower() for s in r["additional_services"])
        assert not any("national recovery" in s.lower() for s in r["additional_services"])

    def test_empty_garages_returns_none_action(self):
        r = select_action([], "breakdown", False, [], "bronze")
        assert r["action"] == "none"
        assert r["garage"] is None


# ---------------------------------------------------------------------------
# 6c. Policy section parsing (for embedding index)
# ---------------------------------------------------------------------------

from embeddings import _parse_sections, _load_all_sections


class TestPolicySectionParser:
    def test_parses_sections_from_gold_policy(self):
        text = (
            "ALLIANZ GOLD\n\n"
            "Section E - UK Recovery\n"
            "If the vehicle cannot be repaired...\n"
            "- national recovery\n\n"
            "Section F - Onward Travel\n"
            "If the vehicle cannot be repaired same-day...\n"
            "- hire car\n\n"
            "NOT COVERED under Gold:\n"
            "- European cover\n"
        )
        secs = _parse_sections(text, "gold")
        assert len(secs) == 3
        keys = [s["section"] for s in secs]
        assert keys == ["E", "F", "NOT_COVERED_GOLD"]
        assert secs[0]["tier"] == "gold"

    def test_load_all_sections_covers_all_tiers(self):
        secs = _load_all_sections()
        tiers = {s["tier"] for s in secs}
        assert tiers == {"bronze", "silver", "gold", "gold_plus"}
        # Each tier must contribute at least one section
        for t in tiers:
            assert any(s["tier"] == t for s in secs)


# ---------------------------------------------------------------------------
# 7. Emergency keyword detection
# ---------------------------------------------------------------------------

class TestEmergencySignal:
    def test_positive_bleeding(self):
        assert has_emergency_signal("I'm bleeding badly") is True

    def test_positive_unconscious(self):
        assert has_emergency_signal("my friend is unconscious") is True

    def test_positive_cannot_move(self):
        assert has_emergency_signal("I cannot move my leg") is True

    def test_positive_fire(self):
        assert has_emergency_signal("there's smoke coming from the engine") is True

    def test_negative_benign(self):
        assert has_emergency_signal("the engine just won't start") is False

    def test_negative_empty_string(self):
        assert has_emergency_signal("") is False

    def test_negative_none(self):
        assert has_emergency_signal(None) is False

    def test_case_insensitive(self):
        assert has_emergency_signal("I am INJURED") is True


# ---------------------------------------------------------------------------
# 8. Name plausibility matching
# ---------------------------------------------------------------------------

class TestNamesPlausiblyMatch:
    def test_stt_noise_matches(self):
        # "Sara Michel" vs "Sarah Mitchell" - same 3-char prefix on first and last name
        assert names_plausibly_match("Sara Michel", "Sarah Mitchell") is True

    def test_completely_different_name_rejected(self):
        assert names_plausibly_match("John Rand", "Sarah Mitchell") is False

    def test_empty_provided_always_true(self):
        # Name not yet given - nothing to check
        assert names_plausibly_match("", "Sarah Mitchell") is True
        assert names_plausibly_match(None, "Sarah Mitchell") is True

    def test_exact_match(self):
        assert names_plausibly_match("Sarah Mitchell", "Sarah Mitchell") is True

    def test_first_name_only_matches(self):
        # Customer provides just first name - still plausible
        assert names_plausibly_match("Sarah", "Sarah Mitchell") is True

    def test_different_first_name_rejected(self):
        assert names_plausibly_match("James Carter", "Sarah Mitchell") is False


# ---------------------------------------------------------------------------
# 9. Vehicle mismatch gate - no-leak and attempt cap
# ---------------------------------------------------------------------------

def _run_mismatch_gate(session: dict, claimed_reg: str) -> dict:
    """Replicate the vehicle mismatch gate logic from main.py for unit testing."""
    from coverage import normalize_vehicle_reg
    fields = session["extracted_fields"]
    pending_hydration = session.get("pending_hydration", {})
    policy_reg = normalize_vehicle_reg(pending_hydration.get("vehicle_reg"))
    claimed = normalize_vehicle_reg(claimed_reg)
    extracted = {}
    if policy_reg and claimed and policy_reg != claimed:
        fields["vehicle_reg"] = None
        fields["vehicle_make"] = None
        fields["vehicle_model"] = None
        fields["vehicle_year"] = None
        extracted["intake_complete"] = False
        attempts = session.get("vehicle_mismatch_attempts", 0) + 1
        session["vehicle_mismatch_attempts"] = attempts
        if attempts < 3:
            session["policy_validation_note"] = (
                "IMPORTANT: The vehicle registration the customer provided does NOT match "
                "the vehicle registered on their policy. Do NOT reveal, describe, or hint "
                "at what vehicle is on the policy - no make, model, year, or registration "
                "from our records."
            )
        else:
            session["customer_not_found"] = True
            session["policy_validation_note"] = (
                "After multiple attempts the customer has been unable to confirm vehicle "
                "details that match our records."
            )
            extracted["intake_complete"] = True
    return extracted


class TestVehicleMismatchGate:
    HONDA_HYDRATION = {
        "vehicle_make": "Honda",
        "vehicle_model": "Civic",
        "vehicle_year": 2023,
        "vehicle_reg": "VW23XYZ",
    }

    def _make_session(self):
        from session import create_session, sessions
        sid = "test-mismatch-001"
        sessions.pop(sid, None)
        create_session(sid)
        s = sessions[sid]
        s["pending_hydration"] = self.HONDA_HYDRATION.copy()
        return s

    def test_note_does_not_leak_policy_vehicle(self):
        s = self._make_session()
        _run_mismatch_gate(s, "123ABC")
        note = (s.get("policy_validation_note") or "").lower()
        for secret in ["honda", "civic", "2023", "vw23xyz"]:
            assert secret not in note, f"Gate note leaked policy detail: {secret!r}"

    def test_aborts_after_three_attempts(self):
        s = self._make_session()
        for _ in range(3):
            _run_mismatch_gate(s, "123ABC")
        assert s["customer_not_found"] is True
        assert s["vehicle_mismatch_attempts"] == 3

    def test_no_abort_before_third_attempt(self):
        s = self._make_session()
        _run_mismatch_gate(s, "123ABC")
        _run_mismatch_gate(s, "456DEF")
        assert s.get("customer_not_found") is False
        assert s["vehicle_mismatch_attempts"] == 2
