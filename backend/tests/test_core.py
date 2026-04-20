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
        customer = find_customer("ALC-10042")
        assert customer is not None
        assert customer["name"] == "Sarah Mitchell"
        assert customer["tier"] == "gold"

    def test_find_unknown_customer_returns_none(self):
        assert find_customer("ALC-99999") is None

    def test_find_commercial_customer(self):
        customer = find_customer("ALC-60099")
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
        assert customer["policy_number"] == "ALC-10042"

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
        assert normalize_policy_number("ALC-10042") == "ALC-10042"

    def test_bare_digits_prefixed(self):
        assert normalize_policy_number("10042") == "ALC-10042"

    def test_spaced_digits(self):
        assert normalize_policy_number("1 0 0 4 2") == "ALC-10042"

    def test_lowercase_and_no_dash(self):
        assert normalize_policy_number("alz 10042") == "ALC-10042"

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
        assert "ALLIANCE" in text.upper()

    def test_load_invalid_tier_raises(self):
        from coverage import load_policy
        with pytest.raises(ValueError, match="Unknown policy tier"):
            load_policy("platinum")

    def test_gold_plus_no_longer_valid(self):
        from coverage import load_policy
        with pytest.raises((ValueError, FileNotFoundError)):
            load_policy("gold_plus")


# ---------------------------------------------------------------------------
# 3. Policy section parser (markdown-based)
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock

from embeddings import _parse_markdown_sections, _load_all_sections


class TestSectionParser:
    def test_parses_basic_section(self):
        text = "# Test Policy\n\n### Engine failure\n\nIf your engine fails we will help.\n"
        sections = _parse_markdown_sections(text, "bronze")
        assert len(sections) == 1
        s = sections[0]
        assert s["tier"] == "bronze"
        assert s["section_title"] == "Engine failure"
        assert "engine fails" in s["prose"]

    def test_parses_multiple_sections(self):
        text = "### Section A\n\nProse A.\n\n### Section B\n\nProse B.\n"
        sections = _parse_markdown_sections(text, "gold")
        assert len(sections) == 2
        assert sections[0]["section_title"] == "Section A"
        assert sections[1]["section_title"] == "Section B"

    def test_section_without_prose_excluded(self):
        text = "### Empty section\n\n### Filled section\n\nSome content here.\n"
        sections = _parse_markdown_sections(text, "bronze")
        assert len(sections) == 1
        assert sections[0]["section_title"] == "Filled section"

    def test_load_all_sections_covers_three_tiers(self):
        sections = _load_all_sections()
        tiers = {s["tier"] for s in sections}
        assert tiers == {"bronze", "silver", "gold"}

    def test_bronze_has_covered_and_exclusion_sections(self):
        sections = _load_all_sections()
        bronze_titles = [s["section_title"].lower() for s in sections if s["tier"] == "bronze"]
        assert any("mechanical" in t or "electrical" in t for t in bronze_titles)
        assert any("commercial" in t for t in bronze_titles)
        assert any("accident" in t for t in bronze_titles)

    def test_gold_has_onward_travel_section(self):
        sections = _load_all_sections()
        gold_titles = [s["section_title"].lower() for s in sections if s["tier"] == "gold"]
        assert any("onward travel" in t for t in gold_titles)

    def test_gold_has_misfuelling_section(self):
        sections = _load_all_sections()
        gold_titles = [s["section_title"].lower() for s in sections if s["tier"] == "gold"]
        assert any("misfuel" in t for t in gold_titles)

    def test_silver_has_home_start_section(self):
        sections = _load_all_sections()
        silver_titles = [s["section_title"].lower() for s in sections if s["tier"] == "silver"]
        assert any("home start" in t for t in silver_titles)


# ---------------------------------------------------------------------------
# 4. Coverage decision engine - LLM-assisted (mocked)
# ---------------------------------------------------------------------------

class TestCoverageDecisionLLM:
    """Coverage decision tests with mocked embeddings and LLM calls."""

    LLM_COVERED = {
        "covered": True,
        "event_type": "Roadside breakdown",
        "applicable_section": "Mechanical or electrical failure at the roadside",
        "services_entitled": ["Roadside repair attempt (up to 60 minutes)", "National recovery"],
        "exclusions_flagged": [],
        "reasoning": "Alternator failure is a covered mechanical fault under Gold.",
        "citations": [{"section": "Mechanical or electrical failure at the roadside", "snippet": "..."}],
        "confidence": 0.92,
    }

    def _make_index(self):
        from embeddings import PolicyIndex, _load_all_sections
        idx = PolicyIndex()
        idx.sections = _load_all_sections()
        idx.embeddings = [[0.0] * 768 for _ in idx.sections]
        idx.ready = True
        return idx

    def _run(self, fields, customer, llm_response=None):
        idx = self._make_index()
        resp = llm_response if llm_response is not None else self.LLM_COVERED
        with patch("embeddings.call_llm", new=AsyncMock(return_value=resp)):
            with patch("embeddings.get_embedding", new=AsyncMock(return_value=[0.0] * 768)):
                return asyncio.get_event_loop().run_until_complete(
                    idx.select_clauses(fields, customer)
                )

    def test_llm_covered_decision_propagates(self):
        fields = {"incident_type": "breakdown", "incident_description": "alternator died", "vehicle_drivable": True}
        r = self._run(fields, {"tier": "gold", "notes": ""})
        assert r["covered"] is True
        assert r["confidence"] == 0.92
        assert "National recovery" in r["services_entitled"]

    def test_llm_denied_decision_propagates(self):
        llm_resp = {
            "covered": False,
            "event_type": "Commercial use exclusion",
            "applicable_section": "Commercial use and hire-or-reward",
            "services_entitled": [],
            "exclusions_flagged": ["Commercial use - vehicle used for Uber"],
            "reasoning": "Customer notes indicate Uber use; excluded by policy.",
            "citations": [],
            "confidence": 0.95,
        }
        fields = {"incident_type": "breakdown", "incident_description": "broke down", "vehicle_drivable": True}
        r = self._run(fields, {"tier": "bronze", "notes": "Uber driver"}, llm_resp)
        assert r["covered"] is False
        assert r["exclusions_flagged"] == ["Commercial use - vehicle used for Uber"]

    def test_safety_net_on_low_confidence(self):
        llm_resp = {**self.LLM_COVERED, "covered": True, "confidence": 0.3}
        fields = {"incident_type": "other", "incident_description": "something unusual", "vehicle_drivable": None}
        r = self._run(fields, {"tier": "gold", "notes": ""}, llm_resp)
        assert r["covered"] is None
        assert "0.30" in r["reasoning"] or "refer to" in r["reasoning"].lower()

    def test_safety_net_on_llm_failure(self):
        idx = self._make_index()
        fields = {"incident_type": "breakdown", "incident_description": "engine died", "vehicle_drivable": False}
        with patch("embeddings.call_llm", new=AsyncMock(side_effect=Exception("API error"))):
            with patch("embeddings.get_embedding", new=AsyncMock(return_value=[0.0] * 768)):
                r = asyncio.get_event_loop().run_until_complete(
                    idx.select_clauses(fields, {"tier": "gold", "notes": ""})
                )
        assert r["covered"] is None
        assert "failed" in r["reasoning"].lower()


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
        assert r["recovery_action"] == "tow"
        assert r["garage"]["has_tow_truck"] is True

    def test_drivable_flat_tyre_picks_nearest_tyre_capable(self):
        r = select_action(self._sorted(), "flat_tyre", True, [], "silver")
        assert r["recovery_action"] == "mobile_repair"
        assert "tyre" in r["garage"]["capabilities"]

    def test_eta_scales_with_distance(self):
        r = select_action(self._sorted(), "breakdown", False, [], "gold")
        assert r["estimated_response_minutes"] == 19

    def test_gold_nondrivable_with_hire_car_entitlement(self):
        services = [
            "Roadside repair attempt (up to 60 minutes labour)",
            "National recovery to any single UK destination",
            "Group A hire car (up to 24 hours)",
            "Standard class rail fare to destination (up to GBP 75 per person)",
        ]
        r = select_action(self._sorted(), "breakdown", False, services, "gold")
        assert r["recovery_action"] == "tow"
        assert r["onward_travel"] == "hire_car"
        assert "hire_car" in r["onward_travel_options"]
        assert "rail" in r["onward_travel_options"]

    def test_bronze_nondrivable_no_onward_services(self):
        r = select_action(self._sorted(), "breakdown", False, [], "bronze")
        assert r["recovery_action"] == "tow"
        assert r["onward_travel"] == "none"
        assert r["onward_travel_options"] == []

    def test_drivable_has_no_onward_travel(self):
        services = ["Group A hire car (up to 24 hours)"]
        r = select_action(self._sorted(), "breakdown", True, services, "gold")
        assert r["recovery_action"] == "mobile_repair"
        assert r["onward_travel"] == "none"
        assert r["onward_travel_options"] == []

    def test_empty_garages_returns_none_action(self):
        r = select_action([], "breakdown", False, [], "bronze")
        assert r["recovery_action"] == "none"
        assert r["onward_travel"] == "none"
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
