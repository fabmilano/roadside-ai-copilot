"""
Demo stress test round 2 - the 4 remaining customers.

E. David Wilson   Silver  BMW 3 Series  Bristol       non-drivable breakdown  => tow + none
F. Emma Clark     Gold    Honda Civic   London        accident non-drivable    => tow + hire_car
G. Claire Foster  Silver  Qashqai       Leeds         key locked in car        => mobile_repair + none
H. Tom Bradley    Gold    Tesla Model 3 London (East) EV battery fault         => mobile_repair + none
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


async def send_receive(ws, text, timeout=45):
    await asyncio.sleep(INTER_TURN_PAUSE)
    await ws.send(json.dumps({"type": "user_message", "text": text}))
    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    return msg


async def run_intake(ws, turns):
    fields = {}
    for i, text in enumerate(turns):
        print(f"    turn {i+1}: {text!r}")
        msg = await send_receive(ws, text)
        if msg.get("type") == "error":
            return None, fields, f"LLM error: {msg.get('text','')[:120]}"
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
        "id": "E_wilson_silver_tow",
        "desc": "David Wilson - Silver, non-drivable breakdown in Bristol => tow + none",
        "turns": [
            "Hi, I'm David Wilson, my car has completely broken down and I'm safe",
            "40318",
            "The engine seized up suddenly with a grinding noise, it just stopped",
            "I'm near Temple Meads station in Bristol",
            "BMW 3 Series, 2020",
            "No, it's completely dead and not drivable",
            "QR20 STU",
            "No passengers, just me",
        ],
        "expect_covered": True,
        "expect_recovery": "tow",
        "expect_onward": "none",
    },
    {
        "id": "F_clark_gold_accident",
        "desc": "Emma Clark - Gold, accident => denied (policy excludes collisions), both none",
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
        "desc": "Claire Foster - Silver, lockout => denied (policy excludes lockouts), both none",
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
    {
        "id": "H_bradley_gold_ev",
        "desc": "Tom Bradley - Gold, Tesla EV battery fault in London => mobile_repair + none",
        "turns": [
            "Hi, I'm Tom Bradley, my Tesla's high-voltage battery has gone flat and the car won't drive, I'm safe",
            "80634",
            "The EV battery has discharged completely, battery management fault on the display, the car won't move",
            "I'm near Liverpool Street station in London",
            "Tesla Model 3, 2023",
            "No, it won't move at all",
            "MN23 OPQ",
            "No passengers, just me",
        ],
        "expect_covered": True,
        "expect_recovery": "mobile_repair",
        "expect_onward": "none",
    },
]


async def main():
    results = []

    async with httpx.AsyncClient(timeout=90) as client:
        for i, case in enumerate(CASES):
            if i > 0:
                print(f"\n  [pause {INTER_CASE_PAUSE}s between cases]")
                await asyncio.sleep(INTER_CASE_PAUSE)

            print(f"\n{'='*60}")
            print(f"CASE {i+1}/{len(CASES)}: {case['id']}")
            print(f"  {case['desc']}")
            print(f"{'='*60}")

            result = {"case": case["id"], "pass": False, "failures": []}

            r = await client.post(f"{BASE}/api/session/start")
            sid = r.json()["session_id"]
            print(f"  session: {sid}")

            try:
                async with websockets.connect(f"{WS_BASE}/api/voice/{sid}", open_timeout=10) as ws:
                    _, fields, err = await run_intake(ws, case["turns"])
                    if err:
                        result["failures"].append(f"intake: {err}")
                        results.append(result)
                        continue
            except Exception as e:
                result["failures"].append(f"intake exception: {e}")
                results.append(result)
                continue

            cov, act, sms, err = await full_pipeline(sid, client)
            if err:
                result["failures"].append(f"pipeline: {err}")
                results.append(result)
                continue

            actual_covered = cov.get("covered")
            if bool(actual_covered) != bool(case["expect_covered"]):
                result["failures"].append(
                    f"covered: expected={case['expect_covered']} got={actual_covered}  "
                    f"reason={cov.get('reasoning','')[:80]}"
                )

            actual_recovery = act.get("recovery_action")
            if actual_recovery != case["expect_recovery"]:
                result["failures"].append(
                    f"recovery_action: expected={case['expect_recovery']} got={actual_recovery}"
                )

            actual_onward = act.get("onward_travel")
            if actual_onward != case["expect_onward"]:
                result["failures"].append(
                    f"onward_travel: expected={case['expect_onward']} got={actual_onward}"
                )

            if not sms or not sms.get("sms_text"):
                result["failures"].append("sms_text empty or missing")

            result["pass"] = len(result["failures"]) == 0
            result["coverage_reasoning"] = (cov or {}).get("reasoning", "")[:120]
            result["recovery_action"] = actual_recovery
            result["onward_travel"] = actual_onward
            result["sms_preview"] = ((sms or {}).get("sms_text") or "")[:160]

            status = "PASS" if result["pass"] else "FAIL"
            print(f"\n  [{status}]  recovery={actual_recovery}  onward={actual_onward}")
            if result["failures"]:
                for f in result["failures"]:
                    print(f"    ! {f}")
            print(f"  SMS: {result['sms_preview']}")
            results.append(result)

    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed} pass / {failed} fail out of {len(results)}")
    print("=" * 60)
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"  [{status}] {r['case']}")
        if r["failures"]:
            for f in r["failures"]:
                print(f"         ! {f}")

    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
