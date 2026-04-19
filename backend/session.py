sessions = {}


def create_session(session_id: str):
    sessions[session_id] = {
        "id": session_id,
        "status": "intake",
        "conversation_history": [],
        "extracted_fields": {
            "customer_name": None,
            "policy_number": None,
            "vehicle_make": None,
            "vehicle_model": None,
            "vehicle_year": None,
            "vehicle_reg": None,
            "location_description": None,
            "location_lat": None,
            "location_lng": None,
            "incident_type": None,
            "incident_description": None,
            "vehicle_drivable": None,
            "is_safe": None,
            "passengers": None,
            "notes": None,
        },
        "customer_record": None,
        "coverage_result": None,
        "action_result": None,
        "notification_result": None,
        "policy_validation_attempts": 0,
        "policy_validation_note": None,
        "hydration_acknowledged": False,
        "pending_hydration": {},
        "customer_not_found": False,
        "vehicle_mismatch_attempts": 0,
        "mode": "autopilot",
        "stage_approvals": {
            "coverage": {"status": "idle", "proposed": None, "edited": None},
            "action":   {"status": "idle", "proposed": None, "edited": None},
            "notify":   {"status": "idle", "proposed": None, "edited": None},
        },
        "gates_fired": [],
    }


def get_session(session_id: str) -> dict | None:
    return sessions.get(session_id)
