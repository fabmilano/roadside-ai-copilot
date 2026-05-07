# Alliance Roadside AI Co-Pilot - PRD

## Vision

UK breakdown providers handle millions of callouts per year. At peak, call queues grow while operator headcount stays fixed, and FCA Consumer Duty requires firms to evidence good outcomes. Alliance Co-Pilot automates intake, coverage judgment, dispatch, and SMS across a four-stage pipeline where the AI proposes and the operator approves or edits at every stage. Each human checkpoint resets compounding error downstream - at 95% per-step accuracy, four unchecked steps compound to ~81%; human review resets to 100%. Autopilot is earned once confidence thresholds stabilize. Target: call-to-dispatch under 4 min (vs. ~10 today).

## Key Features

**Intake agent.** Voice-based conversational intake (STT/LLM/TTS) extracts name, policy, vehicle, location, incident type, and safety status one question at a time. The LLM receives a fixed-size state snapshot each turn (fields so far, last reply, system note) rather than the full transcript, keeping prompt cost O(1) in conversation length. Runs on Gemini Flash Lite via litellm.

**Deterministic safety gates.** Emergency keyword detection, policy-digit normalization, name matching, vehicle-reg matching, and required-field enforcement - all plain Python with no LLM in the critical path. There is no model-dependent path to a 999 prompt.

**Coverage judgment with citations.** Per-tier policy markdown is split by section headers and embedded. Cosine similarity retrieves top-4 sections for the customer's tier; a semantic pinning pass ensures onward-travel sections (whose language doesn't match incident descriptions) are included for non-drivable vehicles. The LLM returns a structured decision via constrained decoding - Pydantic schemas compiled into server-side token masks, not prompt-only JSON. Confidence below 0.50 refers to a human.

**Two-slot dispatch.** Recovery (tow, mobile repair, none) and onward travel (hire car, rail, hotel, none) resolved deterministically from coverage output, incident type, and garage proximity.

**Operator dashboard.** Coverage, dispatch, and SMS each present approve-or-edit. Co-pilot (default) pauses at each stage; autopilot auto-approves above tier-specific confidence thresholds.

## Prioritization

Coverage ships with the most engineering rigor because it drives dispatch - an irreversible action once a truck rolls. Safety gates ship first in every stage. Co-pilot precedes autopilot for two reasons: it prevents costly errors while thresholds are uncalibrated, and every operator edit is a labeled training example. The delta between the AI's proposal and the operator's correction is captured as a byproduct of normal work - no separate annotation effort. This data flywheel is why co-pilot ships first: it generates the data that makes autopilot possible.

## Milestones

**PoC (complete).** Four-stage pipeline end-to-end, 8 regression cases, 82 unit tests, constrained decoding on coverage and SMS calls.
**Pilot - week 1-6.** 5-8 operators, co-pilot only. Exit criteria: Cohen's kappa >= 0.7 vs. senior underwriters on monthly blind sample (50 cases), zero safety-gate misses, wrongful-denial rate < 2%. Kill if kappa < 0.7 at week 6 or any safety-gate miss occurs.
**Eval infrastructure - week 4-8.** Retrieval eval harness (Recall@4 >= 90% on ~50 labeled cases), continuous production sampling (5% holdout for human review). Gates autopilot rollout.
**MVP - week 6-10.** Operator auth, persistent audit log, carrier SMS integration. Exit: end-to-end flow on production infrastructure with audit trail passing compliance review.
**Autopilot - week 10+.** Bronze/Silver first (fewer edge cases), enabled only when pilot edit rate < 15% sustained over 2 weeks. Gold stays co-pilot until volume is sufficient to stabilize thresholds.

## Technical Risks

**Hallucinated coverage.** Retrieval grounding, constrained decoding, and a 0.50 confidence floor reduce but don't eliminate risk. Residual: LLM cites a real section but misapplies it. Operator review in co-pilot is the detection layer; an expert-labeled eval set is the long-term fix.
**Regulatory exposure.** Citations are prompted but not source-verified in the PoC. A fabricated quote is worse than none in an FCA audit. Source-text verification ships before any autonomous path.
**Adversarial intake.** Raw transcript never reaches the coverage LLM - only extracted field values. Limits prompt injection surface.
**Retrieval at scale.** Dense-only retrieval works for ~30 policy sections. Hybrid (dense + BM25) is the path as corpus grows.
**Operator trust.** Rubber-stamping and over-editing are both failure modes. Edit-rate metrics flag both.

## AI Integration: Damage Assessment

Customers upload post-incident photos; a vision-language model returns structured JSON - damaged areas, severity, drivability estimate - via the same constrained decoding pattern used for coverage. This supplements the verbal description and gives operators visual evidence alongside the AI assessment. Failure modes (blur, multi-vehicle scenes, prompt injection via image text) trigger refusal and an operator flag rather than a wrong answer. Co-pilot only at launch; operator corrections build the eval set for eventual automation.
