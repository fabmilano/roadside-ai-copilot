"""
Stress test round 3 - JSON schema enforcement verification.

Tests 4 cases across different coverage outcomes to verify that
the Gemini response_schema constraint produces well-formed sms_parts
with all 7 expected keys.

Cases:
  B. Laura Barnes   Gold    Toyota Yaris   Motorway M6     non-drivable  => tow + hire_car
  C. Mark Stone     Bronze  Fiat 500       London          commercial    => denied (none + none)
  F. Emma Clark     Gold    Honda Civic    London          collision     => denied (none + none)
  G. Claire Foster  Silver  Nissan Qashqai Leeds           key lockout   => denied (none + none)
"""

import asyncio
import json
import sys
import httpx
import websockets

BASE = "http://localhost:8765"
WS_BASE = "ws://localhost:8765"

INTER_TURN_PAUSE = 2.5
INTER_CASE_PAUSE = 120

EXPECTED_SMS_KEYS = {
    "greeting", "status_line", "action_line", "eta_line",
    "services_line", "case_ref_line", "emergency_footer",
}


async def send_receive(ws, text, timeout=45):
    await asyncio.sleep(INTER_TURN_PAUSE)
    await ws.send(json.dumps({"type": "user_message", "text": text}))
    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    return msg


async def run_intake(ws, turns):
    """Run intake turns. On LLM errors, retry by resending the message."""
    fields = {}
    MAX_RETRIES = 3
    for i, text in enumerate(turns):
        print(f"    turn {i+1}: {text!r}")
        msg = await send_receive(ws, text)

        # Retry on LLM errors (malformed JSON or rate limits from Gemini free tier)
        retries = 0
        while msg.get("type") == "error" and retries < MAX_RETRIES:
            retries += 1
            err_text = msg.get("text", "")[:100]
            is_rate_limit = "RateLimit" in err_text or "429" in err_text
            wait = 45 if is_rate_limit else 20
            print(f"           !! LLM error (retry {retries}/{MAX_RETRIES}, wait {wait}s): {err_text}")
            await asyncio.sleep(wait)
            msg = await send_receive(ws, text)

        if msg.get("type") == "error":
            return None, fields, f"LLM error after {MAX_RETRIES} retries: {msg.get('text','')[:120]}"
        fields = msg.get("extracted_fields", fields)
        agent_reply = msg.get("text", "")
        print(f"           => {agent_reply[:90]}")
        if msg.get("intake_complete"):
            return agent_reply, fields, None
    return None, fields, "intake_complete never set after all turns"


async def full_pipeline(sid, client):
    r = await client.post(f"{BASE}/api/check-coverage/{sid}")
    cov = r.json()
    if not r.is_success:
        return None, None, None, f"coverage HTTP {r.status_code}"
    if not cov.get("auto_approved"):
        await client.post(f"{BASE}/api/session/{sid}/approve/coverage", json={"edited": cov})

    r = await client.post(f"{BASE}/api/next-action/{sid}")
    act = r.json()
    if not r.is_success:
        return cov, None, None, f"action HTTP {r.status_code}: {act}"
    if not act.get("auto_approved"):
        await client.post(f"{BASE}/api/session/{sid}/approve/action", json={"edited": act})

    r = await client.post(f"{BASE}/api/notify/{sid}")
    sms = r.json()
    if not r.is_success:
        return cov, act, None, f"notify HTTP {r.status_code}"
    if not sms.get("auto_approved"):
        await client.post(
            f"{BASE}/api/session/{sid}/approve/notify",
            json={"edited": {"sms_text": sms.get("sms_text", "")}},
        )

    return cov, act, sms, None


CASES = [
    {
        "id": "B_barnes_gold_tow_hire",
        "desc": "Laura Barnes - Gold, non-drivable motorway => tow + hire_car",
        "turns": [
            "Hello, I'm Laura Barnes, I've broken down on the motorway, I'm safe on the hard shoulder",
            "30295",
            "The engine completely failed on the M6",
            "Near junction 15 northbound just outside Stoke-on-Trent",
            "Toyota Yaris, 2022",
            "No, it's completely undrivable",
            "LM22 NOP",
            "No passengers, just me",
        ],
        "expect_covered": True,
        "expect_recovery": "tow",
        "expect_onward": "hire_car",
    },
    {
        "id": "C_stone_bronze_denied",
        "desc": "Mark Stone - Bronze, commercial/Uber => denied (none + none)",
        "turns": [
            "Hi, I'm Mark Stone, my car has broken down, I'm safe",
            "60099",
            "Engine failure, it just cut out while driving",
            "I'm on Commercial Road in London near Aldgate",
            "Fiat 500, 2018",
            "No, it won't start",
            "AB18 CDF",
            "No passengers, just me",
        ],
        "expect_covered": False,
        "expect_recovery": "none",
        "expect_onward": "none",
    },
    {
        "id": "F_clark_gold_accident",
        "desc": "Emma Clark - Gold, collision => denied (none + none)",
        "turns": [
            "Hello, I'm Emma Clark, I've been in a collision and my car is damaged, I'm safe",
            "50421",
            "I was rear-ended at a junction, the car has body damage and it's not safe to drive",
            "I'm near Victoria station in London",
            "Honda Civic, 2023",
            "No, it's not safe to drive after the accident",
            "VW23 XYZ",
            "No passengers, just me",
        ],
        "expect_covered": False,
        "expect_recovery": "none",
        "expect_onward": "none",
    },
    {
        "id": "G_foster_silver_keys",
        "desc": "Claire Foster - Silver, key lockout => denied (none + none)",
        "turns": [
            "Hi, I'm Claire Foster, I've locked my keys inside the car and I'm safe",
            "70512",
            "My keys are locked inside the car and I can't get in at all",
            "I'm on Park Row in Leeds city centre",
            "Nissan Qashqai, 2021",
            "The car is locked so I can't drive it right now",
            "GH21 JKL",
            "No passengers, just me",
        ],
        "expect_covered": False,
        "expect_recovery": "none",
        "expect_onward": "none",
    },
]

# Pauses between cases: 120s between B-C, 120s between C-F, 0 between F-G
# (but F and G are in the second batch so we wait 120s after C before starting F)
PAUSE_AFTER = {
    0: 120,  # after case B (index 0), wait 120s before C
    1: 120,  # after case C (index 1), wait 120s before F
    2: 120,  # after case F (index 2), wait 120s before G
}


async def main():
    results = []

    async with httpx.AsyncClient(timeout=90) as client:
        for i, case in enumerate(CASES):
            if i > 0:
                pause = PAUSE_AFTER.get(i - 1, 0)
                if pause > 0:
                    print(f"\n  [pause {pause}s between cases to respect rate limit]")
                    await asyncio.sleep(pause)

            print(f"\n{'='*70}")
            print(f"CASE {i+1}/{len(CASES)}: {case['id']}")
            print(f"  {case['desc']}")
            print(f"{'='*70}")

            result = {"case": case["id"], "pass": False, "failures": []}

            r = await client.post(f"{BASE}/api/session/start")
            sid = r.json()["session_id"]
            print(f"  session: {sid}")

            # -- INTAKE --
            try:
                async with websockets.connect(f"{WS_BASE}/api/voice/{sid}", open_timeout=10) as ws:
                    _, fields, err = await run_intake(ws, case["turns"])
                    if err:
                        result["failures"].append(f"intake: {err}")
                        results.append(result)
                        continue
            except Exception as e:
                result["failures"].append(f"intake exception ({type(e).__name__}): {e}")
                results.append(result)
                continue

            print(f"\n  --- Intake complete, extracted fields ---")
            for k, v in fields.items():
                if v is not None:
                    print(f"    {k}: {v}")

            # -- PIPELINE (coverage -> action -> sms) --
            cov, act, sms, err = await full_pipeline(sid, client)
            if err:
                result["failures"].append(f"pipeline: {err}")
                results.append(result)
                continue

            # -- COVERAGE ASSERTIONS --
            actual_covered = cov.get("covered")
            cov_confidence = cov.get("confidence", "N/A")
            cov_services = cov.get("services_entitled", [])
            print(f"\n  --- Coverage result ---")
            print(f"    covered: {actual_covered}")
            print(f"    confidence: {cov_confidence}")
            print(f"    services_entitled: {cov_services}")
            print(f"    reasoning: {cov.get('reasoning', '')[:120]}")

            if bool(actual_covered) != bool(case["expect_covered"]):
                result["failures"].append(
                    f"covered: expected={case['expect_covered']} got={actual_covered}  "
                    f"reason={cov.get('reasoning','')[:80]}"
                )

            # -- ACTION ASSERTIONS --
            actual_recovery = act.get("recovery_action")
            actual_onward = act.get("onward_travel")
            print(f"\n  --- Action result ---")
            print(f"    recovery_action: {actual_recovery}")
            print(f"    onward_travel: {actual_onward}")
            print(f"    reasoning: {act.get('reasoning', '')[:120]}")

            if actual_recovery != case["expect_recovery"]:
                result["failures"].append(
                    f"recovery_action: expected={case['expect_recovery']} got={actual_recovery}"
                )
            if actual_onward != case["expect_onward"]:
                result["failures"].append(
                    f"onward_travel: expected={case['expect_onward']} got={actual_onward}"
                )

            # -- SMS ASSERTIONS --
            sms_text = (sms or {}).get("sms_text", "")
            sms_parts = (sms or {}).get("sms_parts", {})
            print(f"\n  --- SMS result ---")
            print(f"    sms_text: {sms_text[:200]}")
            print(f"    sms_parts keys: {sorted(sms_parts.keys()) if sms_parts else 'MISSING'}")

            if not sms_text:
                result["failures"].append("sms_text empty or missing")

            # Validate sms_parts contains all 7 expected keys
            if not sms_parts:
                result["failures"].append("sms_parts missing entirely")
            else:
                actual_keys = set(sms_parts.keys())
                missing_keys = EXPECTED_SMS_KEYS - actual_keys
                if missing_keys:
                    result["failures"].append(f"sms_parts missing keys: {sorted(missing_keys)}")
                for key in EXPECTED_SMS_KEYS:
                    val = sms_parts.get(key)
                    if val is not None and not isinstance(val, str):
                        result["failures"].append(f"sms_parts[{key}] is {type(val).__name__}, expected str")

            # -- VERDICT --
            result["pass"] = len(result["failures"]) == 0
            result["coverage_reasoning"] = (cov or {}).get("reasoning", "")[:120]
            result["recovery_action"] = actual_recovery
            result["onward_travel"] = actual_onward
            result["sms_preview"] = sms_text[:160]
            result["sms_keys_ok"] = not bool(EXPECTED_SMS_KEYS - set((sms_parts or {}).keys()))

            status = "PASS" if result["pass"] else "FAIL"
            print(f"\n  [{status}]  covered={actual_covered}  recovery={actual_recovery}  onward={actual_onward}")
            if result["failures"]:
                for f in result["failures"]:
                    print(f"    ! {f}")
            results.append(result)

    # -- SUMMARY TABLE --
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    print(f"\n\n{'='*70}")
    print(f"SUMMARY: {passed} pass / {failed} fail out of {len(results)}")
    print("=" * 70)
    print(f"  {'Case':<30} {'Recovery':<16} {'Onward':<12} {'SMS Keys':<10} {'Result'}")
    print(f"  {'-'*30} {'-'*16} {'-'*12} {'-'*10} {'-'*6}")
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        sms_ok = "OK" if r.get("sms_keys_ok") else "MISSING"
        recovery = r.get("recovery_action", "?")
        onward = r.get("onward_travel", "?")
        print(f"  {r['case']:<30} {recovery:<16} {onward:<12} {sms_ok:<10} {status}")
        if r["failures"]:
            for f in r["failures"]:
                print(f"    ! {f}")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
