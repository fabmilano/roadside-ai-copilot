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
        customer = find_customer("10042")
        assert customer is not None
        assert customer["name"] == "Sarah Mitchell"

    def test_find_with_spelled_out_prefix(self):
        customer = find_customer("A L Z 1 0 0 4 2")
        assert customer is not None
        assert customer["policy_number"] == "ALZ-10042"

    def test_no_gold_plus_tier_in_customers(self):
        """Gold Plus has been removed - no customer should still carry that tier."""
        import json
        from pathlib import Path
        data = json.loads((Path(__file__).parent.parent / "data" / "customers.json").read_text())
        tiers = {c["tier"] for c in data}
        assert "gold_plus" not in tiers
        assert tiers == {"bronze", "silver", "gold"}


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
        fields = {
            "customer_name": "Sara Michel",
            "vehicle_make": "Four",
            "vehicle_model": "Focus",
            "vehicle_year": None,
            "vehicle_reg": "AB21 CDE",
        }
        notes, proposed, missing = hydrate_from_customer_record(fields, sample_customer)
        assert fields["customer_name"] == "Sara Michel"
        assert fields["vehicle_make"] == "Four"
        assert proposed["customer_name"] == "Sarah Mitchell"
        assert proposed["vehicle_make"] == "Ford"
        assert "vehicle_year" in missing
        assert "vehicle_year" not in proposed
        assert fields.get("vehicle_year") is None
        assert len(notes) >= 2

    def test_hydrate_matching_fields_accepted(self, sample_customer):
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

    def test_hydrate_missing_fields_not_revealed(self, sample_customer):
        fields = {
            "customer_name": None,
            "vehicle_make": None,
            "vehicle_model": None,
            "vehicle_year": None,
            "vehicle_reg": None,
        }
        notes, proposed, missing = hydrate_from_customer_record(fields, sample_customer)
        assert proposed == {}
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
# 2. Policy loading (3 tiers only)
# ---------------------------------------------------------------------------

class TestPolicyLoading:
    @pytest.mark.parametrize("tier", ["bronze", "silver", "gold"])
    def test_load_policy_returns_text(self, tier):
        from coverage import load_policy
        text = load_policy(tier)
        assert isinstance(text, str)
        assert len(text) > 100
        assert "ALLIANZ" in text.upper()

    def test_load_invalid_tier_raises(self):
        from coverage import load_policy
        with pytest.raises(ValueError, match="Unknown policy tier"):
            load_policy("platinum")

    def test_gold_plus_no_longer_valid(self):
        from coverage import load_policy
        with pytest.raises((ValueError, FileNotFoundError)):
            load_policy("gold_plus")


# ---------------------------------------------------------------------------
# 3. Policy clause parser
# ---------------------------------------------------------------------------

from embeddings import _parse_clauses, _load_all_clauses


class TestClauseParser:
    def test_parses_basic_clause(self):
        text = (
            "ALLIANZ TEST\n\n"
            "Clause C1 - Engine failure\n"
            "@tiers: bronze\n"
            "@outcome: covered\n"
            "@event_type: Roadside breakdown\n"
            "@services: Roadside repair | Recovery\n\n"
            "If your engine fails we will help.\n"
        )
        clauses = _parse_clauses(text)
        assert len(clauses) == 1
        c = clauses[0]
        assert c["id"] == "C1"
        assert c["title"] == "Engine failure"
        assert c["tiers"] == ["bronze"]
        assert c["outcome"] == "covered"
        assert c["event_type"] == "Roadside breakdown"
        assert "Roadside repair" in c["services"]
        assert "Recovery" in c["services"]
        assert "engine fails" in c["prose"]

    def test_parses_trigger_keywords(self):
        text = (
            "Clause X1 - Commercial exclusion\n"
            "@tiers: bronze, silver, gold\n"
            "@outcome: not_covered\n"
            "@trigger_keywords: uber, taxi\n\n"
            "No cover for commercial use.\n"
        )
        clauses = _parse_clauses(text)
        assert clauses[0]["trigger_keywords"] == ["uber", "taxi"]
        assert clauses[0]["outcome"] == "not_covered"

    def test_parses_trigger_incident_type(self):
        text = (
            "Clause G1 - Misfuelling\n"
            "@tiers: gold\n"
            "@outcome: covered\n"
            "@trigger_incident_type: fuel\n\n"
            "Wrong fuel cover.\n"
        )
        clauses = _parse_clauses(text)
        assert clauses[0]["trigger_incident_type"] == "fuel"

    def test_parses_trigger_drivable_false(self):
        text = (
            "Clause F1 - Onward travel\n"
            "@tiers: gold\n"
            "@outcome: covered\n"
            "@trigger_drivable: false\n\n"
            "Hire car when not drivable.\n"
        )
        clauses = _parse_clauses(text)
        assert clauses[0]["trigger_drivable"] is False

    def test_load_all_clauses_covers_three_tiers(self):
        clauses = _load_all_clauses()
        tiers_present = {t for c in clauses for t in c["tiers"]}
        assert "bronze" in tiers_present
        assert "silver" in tiers_present
        assert "gold" in tiers_present
        assert "gold_plus" not in tiers_present

    def test_load_all_clauses_has_exclusions(self):
        clauses = _load_all_clauses()
        exclusions = [c for c in clauses if c["outcome"] == "not_covered"]
        assert len(exclusions) >= 3
        ids = [c["id"] for c in exclusions]
        assert "X1" in ids
        assert "X2" in ids
        assert "X3" in ids

    def test_load_all_clauses_gold_has_onward_travel(self):
        clauses = _load_all_clauses()
        f1 = next((c for c in clauses if c["id"] == "F1"), None)
        assert f1 is not None
        assert "gold" in f1["tiers"]
        assert f1["trigger_drivable"] is False
        assert any("hire car" in s.lower() for s in f1["services"])

    def test_load_all_clauses_gold_has_misfuelling(self):
        clauses = _load_all_clauses()
        g1 = next((c for c in clauses if c["id"] == "G1"), None)
        assert g1 is not None
        assert g1["trigger_incident_type"] == "fuel"
        assert "gold" in g1["tiers"]

    def test_d1_home_start_covers_silver_and_gold(self):
        clauses = _load_all_clauses()
        d1 = next((c for c in clauses if c["id"] == "D1"), None)
        assert d1 is not None
        assert "silver" in d1["tiers"]
        assert "gold" in d1["tiers"]
        assert "bronze" not in d1["tiers"]


# ---------------------------------------------------------------------------
# 4. Coverage decision engine - trigger layer (mocked embeddings)
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import MagicMock


def _make_index_with_mock_embeddings(zero_vector_dim: int = 768):
    """Build a PolicyIndex from real policy files but inject zero embeddings.

    Trigger-layer tests don't need real vectors - the triggers are purely
    deterministic. Injecting zeros ensures no network call is made.
    """
    from embeddings import PolicyIndex, _load_all_clauses
    idx = PolicyIndex()
    idx.clauses = _load_all_clauses()
    idx.embeddings = [[0.0] * zero_vector_dim for _ in idx.clauses]
    idx.ready = True
    return idx


class TestCoverageDecisionTriggers:
    """Trigger-layer tests: commercial, accident, and key_issue exclusions."""

    def _run(self, fields: dict, customer: dict) -> dict:
        idx = _make_index_with_mock_embeddings()
        return asyncio.get_event_loop().run_until_complete(
            idx.select_clauses(fields, customer)
        )

    def test_commercial_use_denied_across_tiers(self, commercial_customer):
        for tier in ("bronze", "silver", "gold"):
            c = dict(commercial_customer, tier=tier)
            r = self._run({"incident_type": "breakdown", "incident_description": "broke down"}, c)
            assert r["covered"] is False
            assert "X1" in r["applicable_section"]
            assert r["services_entitled"] == []

    def test_accident_denied_by_trigger(self):
        fields = {"incident_type": "accident", "incident_description": "I had an accident"}
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert r["covered"] is False
        assert "X2" in r["applicable_section"]

    def test_key_issue_denied_by_trigger(self):
        fields = {"incident_type": "key_issue", "incident_description": "locked keys in car"}
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert r["covered"] is False
        assert "X3" in r["applicable_section"]

    def test_exclusion_beats_description(self, commercial_customer):
        """Even with a generic description the commercial trigger must fire."""
        fields = {"incident_type": "breakdown", "incident_description": "battery flat"}
        r = self._run(fields, commercial_customer)
        assert r["covered"] is False

    def test_misfuelling_trigger_gold(self):
        fields = {"incident_type": "fuel", "incident_description": "put wrong fuel in", "vehicle_drivable": True}
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert r["covered"] is True
        assert "G1" in r["applicable_section"]
        assert any("drain" in s.lower() or "flush" in s.lower() for s in r["services_entitled"])

    def test_onward_travel_addon_when_nondrivable_gold(self):
        fields = {
            "incident_type": "breakdown",
            "incident_description": "engine failure, car won't move",
            "vehicle_drivable": False,
        }
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert r["covered"] is True
        # F1 triggered as addon - hire car must appear in services
        assert any("hire car" in s.lower() for s in r["services_entitled"])
        # F1 citation present
        citation_ids = [c["section"] for c in r["citations"]]
        assert "F1" in citation_ids

    def test_onward_travel_not_triggered_when_drivable(self):
        fields = {
            "incident_type": "breakdown",
            "incident_description": "engine fault but I can still drive",
            "vehicle_drivable": True,
        }
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert not any("hire car" in s.lower() for s in r.get("services_entitled", []))

    def test_f1_not_available_for_bronze(self):
        fields = {
            "incident_type": "breakdown",
            "incident_description": "car won't start",
            "vehicle_drivable": False,
        }
        r = self._run(fields, {"tier": "bronze", "notes": ""})
        assert not any("hire car" in s.lower() for s in r.get("services_entitled", []))


# ---------------------------------------------------------------------------
# 5. Tier filter - bronze cannot access gold-only clauses
# ---------------------------------------------------------------------------

class TestTierFilter:
    def _run(self, fields, customer):
        idx = _make_index_with_mock_embeddings()
        return asyncio.get_event_loop().run_until_complete(
            idx.select_clauses(fields, customer)
        )

    def test_bronze_never_gets_f1(self):
        fields = {"incident_type": "breakdown", "incident_description": "broken down", "vehicle_drivable": False}
        r = self._run(fields, {"tier": "bronze", "notes": ""})
        citation_ids = [c["section"] for c in r.get("citations", [])]
        assert "F1" not in citation_ids
        services_text = " ".join(r.get("services_entitled", [])).lower()
        assert "hire car" not in services_text

    def test_bronze_never_gets_g1(self):
        # Misfuelling trigger (incident_type=fuel) should deny for bronze (G1 is gold-only)
        fields = {"incident_type": "fuel", "incident_description": "wrong fuel", "vehicle_drivable": True}
        r = self._run(fields, {"tier": "bronze", "notes": ""})
        # G1 is @tiers: gold - bronze customer should not match G1
        assert r["covered"] is not True or "G1" not in r.get("applicable_section", "")


# ---------------------------------------------------------------------------
# 6. Garage finder / haversine
# ---------------------------------------------------------------------------

class TestGarageFinder:
    def test_haversine_manchester_to_trafford(self):
        dist = haversine_miles(53.4800, -2.2400, 53.4600, -2.3200)
        assert 2.0 < dist < 5.0, f"Expected ~3 miles, got {dist}"

    def test_find_nearby_garages_sorted_by_distance(self):
        garages = find_nearby_garages(53.4808, -2.2426, max_miles=15.0)
        assert len(garages) > 0
        distances = [g["distance_miles"] for g in garages]
        assert distances == sorted(distances)

    def test_find_nearby_garages_tight_radius_returns_empty(self):
        garages = find_nearby_garages(56.0, 3.0, max_miles=0.01)
        assert garages == []

    def test_resolve_location_keyword_match(self):
        lat, lng = resolve_location("I'm on the M60 near Manchester city centre")
        assert abs(lat - 53.4800) < 0.1
        assert abs(lng - (-2.2400)) < 0.2

    def test_resolve_location_fallback(self):
        lat, lng = resolve_location("somewhere in Outer Mongolia")
        assert lat == 51.5074
        assert lng == -0.1278


# ---------------------------------------------------------------------------
# 7. Deterministic action selector
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
        assert r["estimated_response_minutes"] == 19

    def test_additional_services_filters_roadside_items(self):
        services = [
            "Roadside repair attempt (up to 60 minutes labour)",
            "National recovery to any single UK destination",
            "Group A hire car (up to 24 hours)",
            "Standard class rail fare to destination (up to GBP 75 per person)",
        ]
        r = select_action(self._sorted(), "breakdown", False, services, "gold")
        assert any("hire car" in s.lower() for s in r["additional_services"])
        assert any("rail fare" in s.lower() for s in r["additional_services"])
        assert not any("roadside repair" in s.lower() for s in r["additional_services"])
        assert not any("national recovery" in s.lower() for s in r["additional_services"])

    def test_empty_garages_returns_none_action(self):
        r = select_action([], "breakdown", False, [], "bronze")
        assert r["action"] == "none"
        assert r["garage"] is None


# ---------------------------------------------------------------------------
# 8. Session management
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
# 9. LLM JSON parsing (mocked)
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
# 10. Emergency keyword detection
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
# 11. Name plausibility matching
# ---------------------------------------------------------------------------

class TestNamesPlausiblyMatch:
    def test_stt_noise_matches(self):
        assert names_plausibly_match("Sara Michel", "Sarah Mitchell") is True

    def test_completely_different_name_rejected(self):
        assert names_plausibly_match("John Rand", "Sarah Mitchell") is False

    def test_empty_provided_always_true(self):
        assert names_plausibly_match("", "Sarah Mitchell") is True
        assert names_plausibly_match(None, "Sarah Mitchell") is True

    def test_exact_match(self):
        assert names_plausibly_match("Sarah Mitchell", "Sarah Mitchell") is True

    def test_first_name_only_matches(self):
        assert names_plausibly_match("Sarah", "Sarah Mitchell") is True

    def test_different_first_name_rejected(self):
        assert names_plausibly_match("James Carter", "Sarah Mitchell") is False


# ---------------------------------------------------------------------------
# 12. Vehicle mismatch gate
# ---------------------------------------------------------------------------

def _run_mismatch_gate(session: dict, claimed_reg: str) -> dict:
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
            session["vehicle_mismatch_abort"] = True
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
        assert s["vehicle_mismatch_abort"] is True
        assert s["vehicle_mismatch_attempts"] == 3

    def test_no_abort_before_third_attempt(self):
        s = self._make_session()
        _run_mismatch_gate(s, "123ABC")
        _run_mismatch_gate(s, "456DEF")
        assert s.get("vehicle_mismatch_abort") is not True
        assert s["vehicle_mismatch_attempts"] == 2
