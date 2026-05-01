# Demo script - all 8 stress-test cases

Start backend and frontend, then run through these cases in the UI. Each case lists the customer record details and the exact intake turns used by the stress tests.

Policy number format: the customer says the digits only (e.g. "10042"), not the full "ALC-10042".

## Running the stress tests (automated)

```bash
cd backend
.venv/bin/python tests/stress/test_demo_cases_1.py   # cases A-D
.venv/bin/python tests/stress/test_demo_cases_2.py   # cases E-H
```

Paced at 2.5s per turn, 120s between cases (free-tier Gemini rate limits).

---

## Case A - James Carter (Bronze, tow only)

**Expected outcome:** covered, `tow` + `none`

| Field | Value |
|---|---|
| Name | James Carter |
| Policy | ALC-20187 (say "20187") |
| Tier | Bronze |
| Vehicle | 2019 Vauxhall Corsa |
| Reg | FG19 HJK |
| Incident | Engine died, breakdown |
| Location | New Street, Birmingham, near train station |
| Drivable | No |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hi, I'm James Carter, my car has broken down completely and I'm safe"
2. "20187"
3. "The engine just died and won't start at all"
4. "I'm on New Street in Birmingham city centre near the train station"
5. "It's a Vauxhall Corsa, 2019"
6. "No, not drivable at all"
7. "FG19 HJK"
8. "No passengers, just me"

**Why this outcome:** Bronze covers roadside + local recovery. Vehicle not drivable so tow is dispatched. Bronze has no onward travel entitlement.

---

## Case B - Laura Barnes (Gold, tow + hire car)

**Expected outcome:** covered, `tow` + `hire_car`

| Field | Value |
|---|---|
| Name | Laura Barnes |
| Policy | ALC-30295 (say "30295") |
| Tier | Gold |
| Vehicle | 2022 Toyota Yaris |
| Reg | LM22 NOP |
| Incident | Engine failure on motorway |
| Location | M6 junction 15 northbound, near Stoke-on-Trent |
| Drivable | No |
| Safe | Yes (hard shoulder) |
| Passengers | None |

**Intake turns:**
1. "Hello, I'm Laura Barnes, I've broken down on the motorway, I'm safe on the hard shoulder"
2. "30295"
3. "The engine completely failed on the M6"
4. "Near junction 15 northbound just outside Stoke-on-Trent"
5. "Toyota Yaris, 2022"
6. "No, it's completely undrivable"
7. "LM22 NOP"
8. "No passengers, just me"

**Why this outcome:** Gold covers national recovery + onward travel. Vehicle not drivable so tow is dispatched. Gold entitles hire car for onward travel.

---

## Case C - Mark Stone (Bronze, denied - commercial use)

**Expected outcome:** denied, `none` + `none`

| Field | Value |
|---|---|
| Name | Mark Stone |
| Policy | ALC-60099 (say "60099") |
| Tier | Bronze |
| Vehicle | 2018 Fiat 500 |
| Reg | AB18 CDF |
| Incident | Engine failure |
| Location | Commercial Road, London, near Aldgate |
| Drivable | No |
| Safe | Yes |
| Passengers | None |
| Customer notes | "Vehicle registered as Uber driver - commercial use" |

**Intake turns:**
1. "Hi, I'm Mark Stone, my car has broken down, I'm safe"
2. "60099"
3. "Engine failure, it just cut out while driving"
4. "I'm on Commercial Road in London near Aldgate"
5. "Fiat 500, 2018"
6. "No, it won't start"
7. "AB18 CDF"
8. "No passengers, just me"

**Why this outcome:** Customer notes flag commercial/Uber use. Policy excludes commercial vehicles. LLM reads the exclusion prose + customer notes and denies coverage.

---

## Case D - Sarah Mitchell (Gold, mobile repair)

**Expected outcome:** covered, `mobile_repair` + `none`

| Field | Value |
|---|---|
| Name | Sarah Mitchell |
| Policy | ALC-10042 (say "10042") |
| Tier | Gold |
| Vehicle | 2021 Ford Focus |
| Reg | AB21 CDE |
| Incident | Flat battery |
| Location | Car park, Bristol city centre, near Cabot Circus |
| Drivable | No (won't start) |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hi, I'm Sarah Mitchell, my battery has gone flat and I can't start the car, I'm safe"
2. "10042"
3. "Flat battery, the car just won't start"
4. "I'm in a car park in Bristol city centre near Cabot Circus"
5. "Ford Focus, 2021"
6. "The car won't start so no, it's not drivable"
7. "AB21 CDE"
8. "No passengers, just me"

**Why this outcome:** Gold covers everything. Flat battery is a roadside-fixable incident type, so mobile repair is dispatched instead of tow. No onward travel needed because the vehicle can be fixed in place.

---

## Case E - David Wilson (Silver, tow only)

**Expected outcome:** covered, `tow` + `none`

| Field | Value |
|---|---|
| Name | David Wilson |
| Policy | ALC-40318 (say "40318") |
| Tier | Silver |
| Vehicle | 2020 BMW 3 Series |
| Reg | QR20 STU |
| Incident | Engine seized |
| Location | Near Temple Meads station, Bristol |
| Drivable | No |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hi, I'm David Wilson, my car has completely broken down and I'm safe"
2. "40318"
3. "The engine seized up suddenly with a grinding noise, it just stopped"
4. "I'm near Temple Meads station in Bristol"
5. "BMW 3 Series, 2020"
6. "No, it's completely dead and not drivable"
7. "QR20 STU"
8. "No passengers, just me"

**Why this outcome:** Silver covers roadside + Home Start + local recovery. Vehicle not drivable so tow is dispatched. Silver excludes onward travel.

---

## Case F - Emma Clark (Gold, denied - accident/collision)

**Expected outcome:** denied, `none` + `none`

| Field | Value |
|---|---|
| Name | Emma Clark |
| Policy | ALC-50421 (say "50421") |
| Tier | Gold |
| Vehicle | 2023 Honda Civic |
| Reg | VW23 XYZ |
| Incident | Rear-ended at junction, collision |
| Location | Near Victoria station, London |
| Drivable | No |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hello, I'm Emma Clark, I've been in a collision and my car is damaged, I'm safe"
2. "50421"
3. "I was rear-ended at a junction, the car has body damage and it's not safe to drive"
4. "I'm near Victoria station in London"
5. "Honda Civic, 2023"
6. "No, it's not safe to drive after the accident"
7. "VW23 XYZ"
8. "No passengers, just me"

**Why this outcome:** All tiers exclude accidents and collisions. Even though Gold has the broadest coverage, the policy explicitly does not cover collision damage.

---

## Case G - Claire Foster (Silver, denied - key lockout)

**Expected outcome:** denied, `none` + `none`

| Field | Value |
|---|---|
| Name | Claire Foster |
| Policy | ALC-70512 (say "70512") |
| Tier | Silver |
| Vehicle | 2021 Nissan Qashqai |
| Reg | GH21 JKL |
| Incident | Keys locked in car |
| Location | Park Row, Leeds city centre |
| Drivable | No (locked out) |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hi, I'm Claire Foster, I've locked my keys inside the car and I'm safe"
2. "70512"
3. "My keys are locked inside the car and I can't get in at all"
4. "I'm on Park Row in Leeds city centre"
5. "Nissan Qashqai, 2021"
6. "The car is locked so I can't drive it right now"
7. "GH21 JKL"
8. "No passengers, just me"

**Why this outcome:** Silver (and all tiers) exclude key lockouts from coverage.

---

## Case H - Tom Bradley (Gold, EV mobile repair)

**Expected outcome:** covered, `mobile_repair` + `none`

| Field | Value |
|---|---|
| Name | Tom Bradley |
| Policy | ALC-80634 (say "80634") |
| Tier | Gold |
| Vehicle | 2023 Tesla Model 3 |
| Reg | MN23 OPQ |
| Incident | EV high-voltage battery flat |
| Location | Near Liverpool Street station, London |
| Drivable | No |
| Safe | Yes |
| Passengers | None |

**Intake turns:**
1. "Hi, I'm Tom Bradley, my Tesla's high-voltage battery has gone flat and the car won't drive, I'm safe"
2. "80634"
3. "The EV battery has discharged completely, battery management fault on the display, the car won't move"
4. "I'm near Liverpool Street station in London"
5. "Tesla Model 3, 2023"
6. "No, it won't move at all"
7. "MN23 OPQ"
8. "No passengers, just me"

**Why this outcome:** Gold covers EV-specific breakdowns. Battery fault is roadside-fixable, so mobile repair with an EV-capable garage is dispatched instead of tow.

---

## Quick reference

| Case | Customer | Tier | Incident | Covered | Recovery | Onward |
|---|---|---|---|---|---|---|
| A | James Carter | Bronze | Breakdown | Yes | tow | none |
| B | Laura Barnes | Gold | Motorway breakdown | Yes | tow | hire_car |
| C | Mark Stone | Bronze | Breakdown (Uber) | No | none | none |
| D | Sarah Mitchell | Gold | Flat battery | Yes | mobile_repair | none |
| E | David Wilson | Silver | Engine seized | Yes | tow | none |
| F | Emma Clark | Gold | Collision | No | none | none |
| G | Claire Foster | Silver | Key lockout | No | none | none |
| H | Tom Bradley | Gold | EV battery flat | Yes | mobile_repair | none |
