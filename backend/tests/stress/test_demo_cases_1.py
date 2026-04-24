"""
Demo stress test - runs the 4 key dispatch-outcome scenarios sequentially.
Adds a small inter-turn pause to stay inside the free-tier 5 RPM Gemini quota.

Cases:
  A. James Carter  (Bronze, non-drivable)          => tow + none
  B. Laura Barnes  (Gold, non-drivable, motorway)  => tow + hire_car
  C. Mark Stone    (Bronze, Uber/commercial)        => none + none  (denied)
  D. Sarah Mitchell (Gold, drivable flat battery)   => mobile_repair + none
"""

import asyncio
import json
import sys
import time
import httpx
import websockets

BASE = "http://localhost:8765"
WS_BASE = "ws://localhost:8765"

INTER_TURN_PAUSE = 2.5   # seconds between each WebSocket send (respects 5 RPM)
INTER_CASE_PAUSE = 120   # seconds between cases (free-tier 5 RPM limit)


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
    # coverage
    r = await client.post(f"{BASE}/api/check-coverage/{sid}")
    cov = r.json()
    if not r.is_success:
        return None, None, None, f"coverage HTTP {r.status_code}"

    if not cov.get("auto_approved"):
        await client.post(f"{BASE}/api/session/{sid}/approve/coverage", json={"edited": cov})

    # action
    r = await client.post(f"{BASE}/api/next-action/{sid}")
    act = r.json()
    if not r.is_success:
        return cov, None, None, f"action HTTP {r.status_code}"

    if not act.get("auto_approved"):
        await client.post(f"{BASE}/api/session/{sid}/approve/action", json={"edited": act})

    # notify
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
        "id": "A_carter_bronze_tow",
        "desc": "James Carter - Bronze, non-drivable => tow + none",
        "turns": [
            "Hi, I'm James Carter, my car has broken down completely and I'm safe",
            "20187",
            "The engine just died and won't start at all",
            "I'm on New Street in Birmingham city centre near the train station",
            "It's a Vauxhall Corsa, 2019",
            "No, not drivable at all",
            "FG19 HJK",
            "No passengers, just me",
        ],
        "expect_covered": True,
        "expect_recovery": "tow",
        "expect_onward": "none",
    },
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
        "desc": "Mark Stone - Bronze, commercial/Uber => denied, both none",
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
        "id": "D_mitchell_gold_mobile",
        "desc": "Sarah Mitchell - Gold, drivable flat battery => mobile_repair + none",
        "turns": [
            "Hi, I'm Sarah Mitchell, my battery has gone flat and I can't start the car, I'm safe",
            "10042",
            "Flat battery, the car just won't start",
            "I'm in a car park in Bristol city centre near Cabot Circus",
            "Ford Focus, 2021",
            "The car won't start so no, it's not drivable",
            "AB21 CDE",
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
                print(f"\n  [pause {INTER_CASE_PAUSE}s between cases to respect rate limit]")
                await asyncio.sleep(INTER_CASE_PAUSE)

            print(f"\n{'='*60}")
            print(f"CASE {i+1}/{len(CASES)}: {case['id']}")
            print(f"  {case['desc']}")
            print(f"{'='*60}")

            result = {"case": case["id"], "pass": False, "failures": []}

            # Start session
            r = await client.post(f"{BASE}/api/session/start")
            sid = r.json()["session_id"]
            print(f"  session: {sid}")

            # Intake
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

            # Pipeline
            cov, act, sms, err = await full_pipeline(sid, client)
            if err:
                result["failures"].append(f"pipeline: {err}")
                results.append(result)
                continue

            # Assertions
            actual_covered = cov.get("covered")
            if bool(actual_covered) != bool(case["expect_covered"]):
                result["failures"].append(
                    f"covered: expected={case['expect_covered']} got={actual_covered}"
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

    # Summary
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
