import json
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from action import find_nearby_garages, load_garages, resolve_location, select_action
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
from embeddings import get_policy_index
from llm import call_llm, call_llm_with_state
from prompts import INTAKE_SYSTEM_PROMPT, SMS_NOT_FOUND_SYSTEM_PROMPT, SMS_SYSTEM_PROMPT
from session import create_session, get_session, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await get_policy_index()
    except Exception as e:
        print(f"[startup] Policy index build failed (citations will be empty): {e}")
    yield


def _assemble_sms(parts: dict) -> str:
    """Join the SMS JSON fields into a single SMS string."""
    order = ["greeting", "status_line", "action_line", "eta_line",
             "services_line", "case_ref_line", "emergency_footer"]
    out: list[str] = []
    for key in order:
        val = (parts.get(key) or "").strip()
        if val:
            out.append(val)
    return " ".join(out)


app = FastAPI(title="Alliance Roadside Co-Pilot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "stage": "unknown"},
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _log_gate(session: dict, gate_type: str, summary: str):
    session["gates_fired"].append({
        "type": gate_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
    })


def _store_proposed(session: dict, stage: str, result: dict) -> bool:
    """Store result as proposed. Returns True if auto-approved (autopilot mode)."""
    session["stage_approvals"][stage]["proposed"] = result
    session["stage_approvals"][stage]["edited"] = None
    session["stage_approvals"][stage]["status"] = "proposed"
    if session.get("mode", "autopilot") == "autopilot":
        session["stage_approvals"][stage]["status"] = "approved"
        return True
    return False


@app.post("/api/session/start")
async def start_session(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    session_id = str(uuid.uuid4())
    create_session(session_id)
    mode = body.get("mode", "autopilot")
    if mode in ("autopilot", "copilot"):
        sessions[session_id]["mode"] = mode
    return {"session_id": session_id}


@app.get("/api/session/{session_id}")
async def get_session_state(session_id: str):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return session


@app.post("/api/session/{session_id}/mode")
async def set_mode(session_id: str, request: Request):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    body = await request.json()
    new_mode = body.get("mode", "autopilot")
    if new_mode not in ("autopilot", "copilot"):
        return JSONResponse(status_code=400, content={"error": "mode must be autopilot or copilot"})
    session["mode"] = new_mode
    auto_approved_stages = []
    if new_mode == "autopilot":
        for stage, data in session["stage_approvals"].items():
            if data["status"] == "proposed":
                data["status"] = "approved"
                auto_approved_stages.append(stage)
    return {"mode": new_mode, "auto_approved_stages": auto_approved_stages}


@app.post("/api/session/{session_id}/approve/{stage}")
async def approve_stage(session_id: str, stage: str, request: Request):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    if stage not in session["stage_approvals"]:
        return JSONResponse(status_code=400, content={"error": f"Unknown stage: {stage}"})
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    edited = body.get("edited")
    approval = session["stage_approvals"][stage]
    approval["status"] = "approved"
    if edited is not None:
        approval["edited"] = edited
        if stage == "coverage":
            session["coverage_result"] = edited
        elif stage == "action":
            session["action_result"] = edited
        elif stage == "notify":
            if session.get("notification_result"):
                session["notification_result"].update(edited)
            session["notification_result"]["sent"] = True
            session["status"] = "complete"
    elif stage == "notify":
        if session.get("notification_result"):
            session["notification_result"]["sent"] = True
        session["status"] = "complete"
    result = approval.get("edited") or approval.get("proposed")
    return {"stage": stage, "status": "approved", "result": result}


@app.patch("/api/session/{session_id}/fields")
async def patch_fields(session_id: str, request: Request):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    updates = await request.json()
    for key, value in updates.items():
        if key in session["extracted_fields"]:
            session["extracted_fields"][key] = value
    return {"updated": list(updates.keys())}


# ---------------------------------------------------------------------------
# Voice / intake WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/api/voice/{session_id}")
async def voice_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "text": "Session not found"})
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "user_message":
                continue

            user_text = data.get("text", "").strip()
            if not user_text:
                continue

            fields = session["extracted_fields"]
            session["conversation_history"].append({"role": "user", "content": user_text})

            # Find the last agent reply for conversational continuity
            last_reply = None
            for msg in reversed(session["conversation_history"][:-1]):
                if msg["role"] == "assistant":
                    try:
                        last_reply = json.loads(msg["content"]).get("reply")
                    except Exception:
                        last_reply = msg["content"]
                    break

            # Emergency detection - deterministic, overrides everything
            emergency_note = None
            if has_emergency_signal(user_text):
                _log_gate(session, "emergency", "Emergency signal detected in customer message")
                emergency_note = (
                    "URGENT: the customer's message suggests a possible medical or safety emergency. "
                    "Your reply MUST begin by urgently telling them to hang up and call 999 immediately. "
                    "Only continue gathering information if they confirm they are safe."
                )

            # Pop and pass any pending validation note from the prior turn
            pending_note = session.pop("policy_validation_note", None)
            session["policy_validation_note"] = None

            # Persistent policy gate: if we've had at least one failed attempt and policy
            # is still unconfirmed, override pending_note every turn until it's resolved.
            # Do not apply once customer_not_found is set (that path has its own note).
            if (
                session.get("policy_validation_attempts", 0) > 0
                and not fields.get("policy_number")
                and not session.get("customer_not_found")
            ):
                pending_note = (
                    "The policy number has NOT been confirmed yet. "
                    "Your ONLY task this turn is to ask for the policy number - nothing else. "
                    "Do not ask for vehicle details, location, or any other information. "
                    "Do NOT set intake_complete."
                )
            # Persistent policy-CONFIRMED signal (counterpart to the unconfirmed gate above).
            # Once the policy has been validated against records, tell the LLM every turn
            # that the policy is confirmed - without this, the LLM sees policy_number
            # populated in state but has no signal it was DB-verified, and follows the
            # STRICT ORDER rule conservatively by re-asking ("Is that correct?" loops).
            elif (
                session.get("hydration_acknowledged")
                and fields.get("policy_number")
                and not session.get("customer_not_found")
                and not session.get("vehicle_mismatch_abort")
            ):
                pending_note = (
                    "The policy number has been CONFIRMED in our records. "
                    "Do NOT re-ask the customer to confirm or repeat the policy number. "
                    "Focus only on any intake details still marked '(not yet provided)', "
                    "one question at a time."
                )

            # Emergency note wins above everything; prepend it so it is seen first
            if emergency_note:
                pending_note = emergency_note + (" " + pending_note if pending_note else "")

            try:
                result = await call_llm_with_state(
                    INTAKE_SYSTEM_PROMPT,
                    session["extracted_fields"],
                    last_reply,
                    user_text,
                    system_note=pending_note,
                )
            except Exception as e:
                await websocket.send_json({"type": "error", "text": f"LLM error: {e}"})
                continue

            reply = result.get("reply", "Sorry, I didn't catch that.")
            extracted = result.get("extracted", {})
            # History appended AFTER gate processing so stored reply reflects any correction.

            # Merge non-null extracted fields into session state.
            # While policy is unconfirmed after failures, only accept name and policy_number -
            # collecting vehicle/location/incident info is pointless without a valid policy.
            policy_unconfirmed = (
                session.get("policy_validation_attempts", 0) > 0
                and not fields.get("policy_number")
                and not session.get("customer_not_found")
            )
            POLICY_GATE_ALLOWED = {"customer_name", "policy_number"}
            for key, value in extracted.items():
                if key == "intake_complete":
                    continue
                if policy_unconfirmed and key not in POLICY_GATE_ALLOWED:
                    continue
                if value is not None and value != "null":
                    fields[key] = value

            # --- Defensive: validate LLM-extracted policy number against user text --
            # The LLM can silently "correct" a wrong number to a known-good one
            # (hallucination / digit-swap). Require the extracted digits to appear
            # in at least one user utterance - either as numerals or spoken words
            # ("six zero zero nine nine" -> "60099").
            _WORD_DIGIT = {
                "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
            }

            def _normalise_spoken_digits(text: str) -> str:
                for word, digit in _WORD_DIGIT.items():
                    text = re.sub(rf"\b{word}\b", digit, text, flags=re.IGNORECASE)
                return text

            extracted_policy = fields.get("policy_number")
            if extracted_policy:
                extracted_digits = re.sub(r"\D", "", str(extracted_policy))
                user_digits_seen = "".join(
                    re.sub(r"\D", "", _normalise_spoken_digits(msg["content"]))
                    for msg in session["conversation_history"]
                    if msg["role"] == "user"
                )
                if extracted_digits and extracted_digits not in user_digits_seen:
                    # LLM invented or altered digits. Drop the extraction.
                    fields["policy_number"] = None

            # --- Normalise / coerce free-form fields --------------------------
            if fields.get("vehicle_reg") is not None:
                fields["vehicle_reg"] = normalize_vehicle_reg(fields["vehicle_reg"])

            # --- Vehicle mismatch gate ----------------------------------------
            # If we know the policy vehicle (from pending_hydration) and the customer
            # has provided a reg that doesn't match, block intake immediately.
            # The policy covers only the registered vehicle - no substitutions allowed.
            pending_hydration = session.get("pending_hydration", {})
            policy_reg = normalize_vehicle_reg(pending_hydration.get("vehicle_reg"))
            claimed_reg = fields.get("vehicle_reg")
            if policy_reg and claimed_reg and policy_reg != claimed_reg:
                fields["vehicle_reg"] = None  # reject the non-policy reg
                fields["vehicle_make"] = None
                fields["vehicle_model"] = None
                fields["vehicle_year"] = None
                extracted["intake_complete"] = False

                mismatch_attempts = session.get("vehicle_mismatch_attempts", 0) + 1
                session["vehicle_mismatch_attempts"] = mismatch_attempts
                _log_gate(session, "vehicle_mismatch", f"Vehicle registration mismatch (attempt {mismatch_attempts} of 3)")

                if mismatch_attempts < 3:
                    session["policy_validation_note"] = (
                        "IMPORTANT: The vehicle registration the customer provided does NOT match "
                        "the vehicle registered on their policy. Do NOT reveal, describe, or hint "
                        "at what vehicle is on the policy - no make, model, year, or registration "
                        "from our records. Tell the customer: 'I'm sorry, the vehicle details "
                        "you've provided don't match our records. This policy only covers the "
                        "registered vehicle. Could you please double-check and confirm the make, "
                        "model, year, and registration of the vehicle you are actually in?' "
                        "Do NOT set intake_complete."
                    )
                else:
                    session["vehicle_mismatch_abort"] = True
                    session["policy_validation_note"] = (
                        "After multiple attempts the customer has been unable to confirm vehicle "
                        "details that match our records. Apologise sincerely, explain that you "
                        "cannot verify the vehicle on their policy, and tell them this call will "
                        "now end but they will receive an SMS shortly with a number they can call "
                        "once they have their correct vehicle details to hand. If there is any "
                        "medical or safety emergency they should call 999. Do NOT reveal any "
                        "details from our records. Set intake_complete to true."
                    )
                    extracted["intake_complete"] = True
            elif policy_reg and claimed_reg and policy_reg == claimed_reg:
                session["vehicle_mismatch_attempts"] = 0
            if fields.get("incident_type") is not None:
                fields["incident_type"] = validate_incident_type(fields["incident_type"])
            if fields.get("vehicle_drivable") is not None:
                fields["vehicle_drivable"] = coerce_bool(fields["vehicle_drivable"])
            if fields.get("is_safe") is not None:
                fields["is_safe"] = coerce_bool(fields["is_safe"])
            if fields.get("passengers") is not None:
                fields["passengers"] = coerce_int(fields["passengers"])

            # --- Validate policy number against customer database -----------
            raw_policy = fields.get("policy_number")
            if raw_policy:
                normalized = normalize_policy_number(raw_policy)
                customer = find_customer(raw_policy) if normalized else None
                if customer:
                    # Cross-check name: a policy number alone isn't enough to verify identity.
                    # If the customer has given a name that doesn't plausibly match the policyholder,
                    # treat it as a failed verification attempt (same 3-attempt abort logic).
                    provided_name = fields.get("customer_name")
                    name_ok = names_plausibly_match(provided_name, customer.get("name", ""))
                    if not name_ok:
                        attempts = session["policy_validation_attempts"] + 1
                        session["policy_validation_attempts"] = attempts
                        fields["policy_number"] = None
                        _log_gate(session, "policy_validation", f"Name mismatch on policy lookup (attempt {attempts} of 3)")
                        if attempts < 3:
                            session["policy_validation_note"] = (
                                f"We found a policy under that number, but the name provided "
                                f"('{provided_name}') does not match the name on the policy. "
                                "Apologise and ask the customer to confirm their full name and "
                                "policy number again. Do NOT set intake_complete."
                            )
                            extracted["intake_complete"] = False
                        else:
                            session["customer_not_found"] = True
                            session["policy_validation_note"] = (
                                "After multiple attempts the name and policy number provided do not "
                                "match our records. Apologise sincerely and explain that you cannot "
                                "verify their identity in our system. Tell them this call will now "
                                "end but they will receive an SMS with a complaints number. "
                                "If there is any medical or safety emergency they should call 999. "
                                "Set intake_complete to true."
                            )
                            extracted["intake_complete"] = True
                    else:
                        fields["policy_number"] = normalized
                        session["policy_validation_attempts"] = 0
                        # Cache full DB vehicle record for the mismatch gate (lines below).
                        vehicle = customer.get("vehicle") or {}
                        session["pending_hydration"] = {
                            "vehicle_make": vehicle.get("make"),
                            "vehicle_model": vehicle.get("model"),
                            "vehicle_year": vehicle.get("year"),
                            "vehicle_reg": vehicle.get("reg"),
                        }
                        # Verify customer-provided fields against the authoritative record.
                        _notes, corrections, missing = hydrate_from_customer_record(fields, customer)
                        if (corrections or missing) and not session.get("hydration_acknowledged"):
                            # corrections = real conflicts (STT-noise) → safe to read back
                            # missing     = fields customer hasn't provided → agent must ASK, never reveal
                            note_parts = ["Policy confirmed."]
                            corrections_no_reg = {k: v for k, v in corrections.items() if k != "vehicle_reg"}
                            if corrections_no_reg:
                                corrections_str = ", ".join(f"{k.replace('vehicle_', '')}={v}" for k, v in corrections_no_reg.items())
                                note_parts.append(
                                    f"Our records show conflicting details: {corrections_str}. "
                                    "Ask the customer to confirm these out loud "
                                    "(e.g. 'Our records show your vehicle as a [year] [make] [model] - is that correct?'). "
                                    "Do NOT write them into extracted fields until the customer confirms."
                                )
                            if "vehicle_reg" in corrections or "vehicle_reg" in missing:
                                note_parts.append(
                                    "Do NOT mention or suggest the vehicle registration number - "
                                    "ask the customer to provide it themselves."
                                )
                            missing_non_reg = [k for k in missing if k != "vehicle_reg"]
                            if missing_non_reg:
                                labels = ", ".join(k.replace("vehicle_", "vehicle ").replace("_", " ") for k in missing_non_reg)
                                note_parts.append(
                                    f"The following details have NOT yet been provided by the customer: {labels}. "
                                    "You MUST ask the customer to state each one themselves. "
                                    "Do NOT reveal, suggest, or hint at any values from our records - "
                                    "the customer must provide them unprompted (identity/fraud safeguard)."
                                )
                            note_parts.append("Do NOT set intake_complete yet.")
                            session["policy_validation_note"] = " ".join(note_parts)
                            session["hydration_acknowledged"] = True
                            extracted["intake_complete"] = False
                else:
                    attempts = session["policy_validation_attempts"] + 1
                    session["policy_validation_attempts"] = attempts
                    _log_gate(session, "policy_validation", f"Policy number not found in records (attempt {attempts} of 3)")
                    if attempts < 3:
                        fields["policy_number"] = None
                        session["policy_validation_note"] = (
                            f"The policy number '{raw_policy}' was not found in our records. "
                            "Apologise, explain that you could not locate that policy, and ask the "
                            "customer to repeat it - the 5-digit number is fine, no need to say 'ALC'. "
                            "Do NOT set intake_complete to true yet."
                        )
                        extracted["intake_complete"] = False
                    else:
                        # 3rd failure - customer genuinely not in DB; abort gracefully
                        session["customer_not_found"] = True
                        session["policy_validation_note"] = (
                            f"We have been unable to locate the policy number '{raw_policy}' after multiple attempts. "
                            "Apologise sincerely and explain that you cannot find their policy in our records. "
                            "Tell them this call will now end, but they will receive an SMS shortly with a "
                            "complaints number they can call if they believe this is a mistake. "
                            "If there is any medical or safety emergency they should call 999. "
                            "Set intake_complete to true."
                        )
                        extracted["intake_complete"] = True

            # Server-side gate: never allow intake_complete if required fields are still null.
            # Exemption: customer_not_found path bypasses this (different abort flow).
            REQUIRED_FOR_INTAKE = [
                "customer_name", "policy_number", "vehicle_make",
                "location_description", "incident_type", "incident_description",
                "vehicle_drivable",
            ]
            if extracted.get("intake_complete") and not session.get("customer_not_found") and not session.get("vehicle_mismatch_abort"):
                missing = [f for f in REQUIRED_FOR_INTAKE if fields.get(f) is None]
                if missing:
                    extracted["intake_complete"] = False
                    missing_note = (
                        "Do NOT set intake_complete yet. Still missing: "
                        + ", ".join(missing)
                        + ". Ask about the first missing item only."
                    )
                    existing_note = session.get("policy_validation_note")
                    session["policy_validation_note"] = (
                        (existing_note + " " + missing_note) if existing_note else missing_note
                    )

            intake_complete = bool(extracted.get("intake_complete", False))
            if intake_complete:
                session["status"] = "coverage"

            # --- Correction call --------------------------------------------------
            # When a gate fires after the LLM call (vehicle mismatch, hydration,
            # name mismatch, policy not found), the LLM's reply may be wrong or
            # incomplete because it hadn't yet learned about the gate result.
            # Instead of waiting for the customer to say something, trigger one
            # additional LLM call immediately with the gate note so the agent
            # asks the right follow-up question now.
            post_gate_note = session.get("policy_validation_note")
            needs_correction = post_gate_note is not None and not intake_complete
            if needs_correction:
                session["policy_validation_note"] = None  # consume now, not next turn
                known = "\n".join(
                    f"- {k}: {v if v is not None else '(not yet provided)'}"
                    for k, v in session["extracted_fields"].items()
                )
                correction_msg = (
                    f"KNOWN SO FAR:\n{known}\n"
                    f"YOUR PREVIOUS REPLY (may need correction): {reply}\n"
                    f"SYSTEM NOTE (critical - act on this now, override previous reply if needed): "
                    f"{post_gate_note}\n"
                    f"CUSTOMER: (still on the line, awaiting your next question)"
                )
                try:
                    correction = await call_llm(
                        INTAKE_SYSTEM_PROMPT, correction_msg, response_format="json"
                    )
                    reply = correction.get("reply", reply)
                    session["conversation_history"].append(
                        {"role": "assistant", "content": json.dumps({**result, "reply": reply})}
                    )
                except Exception:
                    # Fall back: keep original reply, restore note for next turn
                    session["policy_validation_note"] = post_gate_note
                    session["conversation_history"].append(
                        {"role": "assistant", "content": json.dumps(result)}
                    )
            else:
                session["conversation_history"].append(
                    {"role": "assistant", "content": json.dumps(result)}
                )

            await websocket.send_json(
                {
                    "type": "agent_response",
                    "text": reply,
                    "extracted_fields": session["extracted_fields"],
                    "intake_complete": intake_complete,
                    "gates_fired": session["gates_fired"],
                }
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Coverage check
# ---------------------------------------------------------------------------

@app.post("/api/check-coverage/{session_id}")
async def check_coverage(session_id: str):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found", "stage": "coverage"})

    if session.get("vehicle_mismatch_abort"):
        stub = {
            "covered": False,
            "event_type": "vehicle_identity_unverified",
            "applicable_section": "Section A - Eligibility",
            "services_entitled": [],
            "exclusions_flagged": ["Vehicle identity could not be verified after multiple attempts"],
            "reasoning": "The customer was unable to provide vehicle details matching the registered policy vehicle. Cover cannot be extended to an unverified vehicle.",
            "citations": [],
        }
        session["coverage_result"] = stub
        session["status"] = "action"
        auto_approved = _store_proposed(session, "coverage", stub)
        return {**stub, "auto_approved": auto_approved}

    if session.get("customer_not_found"):
        stub = {
            "covered": None,
            "event_type": "unknown_customer",
            "applicable_section": "N/A",
            "services_entitled": [],
            "exclusions_flagged": [],
            "reasoning": "Customer not found in records - coverage check skipped.",
            "citations": [],
        }
        session["coverage_result"] = stub
        session["status"] = "action"
        auto_approved = _store_proposed(session, "coverage", stub)
        return {**stub, "auto_approved": auto_approved}

    fields = session["extracted_fields"]
    policy_number = fields.get("policy_number")

    if not policy_number:
        return JSONResponse(
            status_code=422,
            content={"error": "No policy number extracted yet", "stage": "coverage"},
        )

    customer = find_customer(policy_number)
    if not customer:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Policy number {policy_number} not found in our records",
                "stage": "coverage",
            },
        )

    session["customer_record"] = customer

    # Deterministic vehicle mismatch check - policy only covers the registered vehicle.
    # This is a hard gate: if the reg doesn't match, deny immediately without calling the LLM.
    policy_vehicle = customer.get("vehicle", {})
    policy_reg = normalize_vehicle_reg(policy_vehicle.get("reg"))
    claimed_reg = normalize_vehicle_reg(fields.get("vehicle_reg"))
    if policy_reg and claimed_reg and policy_reg != claimed_reg:
        policy_desc = (
            f"{policy_vehicle.get('year')} {policy_vehicle.get('make')} "
            f"{policy_vehicle.get('model')} (reg {policy_reg})"
        )
        mismatch_result = {
            "covered": False,
            "event_type": "Vehicle not on policy",
            "applicable_section": "Section A - Eligibility",
            "services_entitled": [],
            "exclusions_flagged": [
                f"Vehicle on scene ({claimed_reg}) does not match policy vehicle ({policy_desc})"
            ],
            "reasoning": (
                f"Policy {policy_number} covers {policy_desc} only. "
                f"The vehicle reported at scene ({claimed_reg}) is not registered on this policy. "
                "Cover cannot be provided for an unregistered vehicle."
            ),
            "citations": [],
        }
        session["coverage_result"] = mismatch_result
        session["status"] = "action"
        auto_approved = _store_proposed(session, "coverage", mismatch_result)
        return {**mismatch_result, "auto_approved": auto_approved}

    try:
        index = await get_policy_index()
        result = await index.select_clauses(fields, customer)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "stage": "coverage"})

    session["coverage_result"] = result
    session["status"] = "action"
    auto_approved = _store_proposed(session, "coverage", result)
    return {**result, "auto_approved": auto_approved}


# ---------------------------------------------------------------------------
# Next action
# ---------------------------------------------------------------------------

@app.post("/api/next-action/{session_id}")
async def next_action(session_id: str):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found", "stage": "action"})

    def _no_dispatch(reason: str) -> dict:
        """Return and persist a no-dispatch result, always auto-approved.

        There is nothing for the operator to decide when no action is taken -
        forcing copilot pause here would deadlock the pipeline (no Approve button
        is shown for action=none). Always mark approved so the frontend proceeds
        straight to the SMS stage.
        """
        result = {
            "recovery_action": "none",
            "garage": None,
            "top_garages": [],
            "onward_travel": "none",
            "onward_travel_options": [],
            "estimated_response_minutes": 0,
            "reasoning": reason,
        }
        session["action_result"] = result
        session["status"] = "notify"
        session["stage_approvals"]["action"]["proposed"] = result
        session["stage_approvals"]["action"]["edited"] = None
        session["stage_approvals"]["action"]["status"] = "approved"
        return {**result, "auto_approved": True}

    if session.get("vehicle_mismatch_abort"):
        return JSONResponse(_no_dispatch("No action - vehicle identity could not be verified. See SMS for next steps."))

    if session.get("customer_not_found"):
        return JSONResponse(_no_dispatch("No action - customer not found in records. See SMS for next steps."))

    fields = session["extracted_fields"]
    coverage = session.get("coverage_result")
    customer = session.get("customer_record")

    if not coverage:
        return JSONResponse(status_code=422, content={"error": "Coverage check not yet completed", "stage": "action"})

    # Short-circuit: no garage search needed when coverage is denied.
    if coverage.get("covered") is False:
        return JSONResponse(_no_dispatch("No dispatch - incident not covered under policy. See SMS for next steps."))

    # Resolve location and find garages only for covered cases.
    location_desc = fields.get("location_description") or ""
    lat, lng = resolve_location(location_desc)
    fields["location_lat"] = lat
    fields["location_lng"] = lng

    nearby = find_nearby_garages(lat, lng)
    top_garages = nearby[:5]

    if not top_garages:
        return JSONResponse(status_code=500, content={"error": "No garages found within range", "stage": "action"})

    tier = customer["tier"] if customer else "unknown"

    decision = select_action(
        garages=top_garages,
        incident_type=fields.get("incident_type"),
        drivable=fields.get("vehicle_drivable"),
        coverage_services=coverage.get("services_entitled") or [],
        tier=tier,
    )
    selected = decision["garage"] or top_garages[0]

    response = {
        "recovery_action": decision["recovery_action"],
        "garage": {
            "name": selected["name"],
            "distance_miles": selected["distance_miles"],
            "lat": selected["lat"],
            "lng": selected["lng"],
            "capabilities": selected["capabilities"],
            "hours": selected["hours"],
        },
        "top_garages": [
            {
                "name": g["name"],
                "distance_miles": g["distance_miles"],
                "lat": g["lat"],
                "lng": g["lng"],
                "capabilities": g["capabilities"],
                "hours": g["hours"],
            }
            for g in top_garages
        ],
        "onward_travel": decision["onward_travel"],
        "onward_travel_options": decision["onward_travel_options"],
        "estimated_response_minutes": decision["estimated_response_minutes"],
        "reasoning": decision["reasoning"],
    }

    session["action_result"] = response
    session["status"] = "notify"
    auto_approved = _store_proposed(session, "action", response)
    return {**response, "auto_approved": auto_approved}


# ---------------------------------------------------------------------------
# Notify (SMS)
# ---------------------------------------------------------------------------

@app.post("/api/notify/{session_id}")
async def notify(session_id: str):
    session = get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found", "stage": "notify"})

    fields = session["extracted_fields"]
    coverage = session.get("coverage_result", {})
    action = session.get("action_result", {})
    customer = session.get("customer_record", {})
    case_ref = f"RA-2026-{random.randint(1000, 9999)}"

    if session.get("vehicle_mismatch_abort"):
        user_message = (
            f"Customer name: {fields.get('customer_name') or 'Unknown'}\n"
            f"case_ref: {case_ref}\n\n"
            "Return the JSON SMS object. status_line: we were unable to verify the vehicle on the policy after multiple attempts so cover cannot be provided at this time. "
            "action_line: advise them to call Alliance Customer Relations on 0800 555 0199 with their vehicle registration to hand. "
            "eta_line and services_line should be empty strings."
        )
        try:
            parts = await call_llm(SMS_NOT_FOUND_SYSTEM_PROMPT, user_message, response_format="json")
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e), "stage": "notify"})
        sms_text = _assemble_sms(parts if isinstance(parts, dict) else {})
        auto_approved = session.get("mode", "autopilot") == "autopilot"
        result = {
            "sms_text": sms_text,
            "sms_parts": parts if isinstance(parts, dict) else {},
            "case_ref": case_ref,
            "sent": auto_approved,
        }
        session["notification_result"] = result
        if auto_approved:
            session["status"] = "complete"
        _store_proposed(session, "notify", result)
        return {**result, "auto_approved": auto_approved}

    if session.get("customer_not_found"):
        user_message = (
            f"Customer name: {fields.get('customer_name') or 'Unknown'}\n"
            f"case_ref: {case_ref}\n\n"
            "Return the JSON SMS object for a not-found case."
        )
        try:
            parts = await call_llm(SMS_NOT_FOUND_SYSTEM_PROMPT, user_message, response_format="json")
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e), "stage": "notify"})
        sms_text = _assemble_sms(parts if isinstance(parts, dict) else {})
        auto_approved = session.get("mode", "autopilot") == "autopilot"
        result = {
            "sms_text": sms_text,
            "sms_parts": parts if isinstance(parts, dict) else {},
            "case_ref": case_ref,
            "sent": auto_approved,
        }
        session["notification_result"] = result
        if auto_approved:
            session["status"] = "complete"
        _store_proposed(session, "notify", result)
        return {**result, "auto_approved": auto_approved}

    garage = action.get("garage") or {}
    onward_travel = action.get("onward_travel", "none")
    onward_label = {
        "hire_car": "Group A hire car (up to 24 hours)",
        "rail": "Standard-class rail fare to destination",
        "hotel": "Hotel accommodation (up to GBP 150 per night)",
    }.get(onward_travel, "none")

    user_message = f"""Case inputs:

customer_name: {fields.get("customer_name")}
policy_tier: {customer.get("tier", "").upper() if customer else ""}
coverage_covered: {coverage.get("covered")}
coverage_event_type: {coverage.get("event_type", "")}
coverage_reasoning: {coverage.get("reasoning", "")}
recovery_action: {action.get("recovery_action", "none")}
garage_name: {garage.get("name", "")}
eta_minutes: {action.get("estimated_response_minutes", 0)}
onward_travel: {onward_label}
case_ref: {case_ref}

Return the JSON SMS object now."""

    try:
        parts = await call_llm(SMS_SYSTEM_PROMPT, user_message, response_format="json")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "stage": "notify"})

    sms_text = _assemble_sms(parts if isinstance(parts, dict) else {})
    auto_approved = session.get("mode", "autopilot") == "autopilot"
    result = {
        "sms_text": sms_text,
        "sms_parts": parts if isinstance(parts, dict) else {},
        "case_ref": case_ref,
        "sent": auto_approved,
    }
    session["notification_result"] = result
    if auto_approved:
        session["status"] = "complete"
    _store_proposed(session, "notify", result)
    return {**result, "auto_approved": auto_approved}
