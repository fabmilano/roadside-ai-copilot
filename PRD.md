# Alliance Roadside AI Co-Pilot – PRD

## Vision

UK roadside assistance is a large, high-frequency market ripe for AI-driven efficiency gains. [AA and RAC handle ~7M callouts/year between them](https://www.theaa.com/breakdown-cover/compare-breakdown-cover/aa-vs-rac). At peak – extreme weather, holiday weekends – call queues grow while operator headcount stays fixed. [FCA Consumer Duty](https://www.fca.org.uk/publication/policy/ps22-9.pdf) means every coverage decision must be justified and documented, leaving little margin for error. [Smarter triaging alone could unlock tens of millions in annual savings](https://optimapartners.co.uk/insights/cutting-smarter-roadside-triage-unlocks-30m-in-annual-savings/). Alliance Co-Pilot speeds up coverage assessment and dispatch decisions, and automates intake through a pipeline where the AI proposes and a human operator approves or edits at every stage. Every operator edit is a labeled example captured as a byproduct of normal work – a data flywheel that earns the system more autonomy over time.

## Key Features

**Intake agent.** Voice-based conversational intake (STT→LLM→TTS) extracts name, policy, vehicle, location, incident type, and safety status one question at a time. The LLM receives a fixed-size state snapshot each turn rather than the full transcript, keeping prompt cost bounded. Runs on Gemini Flash Lite via litellm.

**Deterministic safety gates.** Emergency keyword detection (prompts 999 call), policy-digit normalization, name/vehicle-reg matching, and required-field enforcement – all plain Python, no LLM dependency.

**Coverage judgment with citations.** Per-tier policy markdown split by section headers and embedded. Cosine similarity retrieves top-4 sections; a synthetic anchor query ensures onward-travel sections are included for non-drivable vehicles. The LLM returns a structured coverage decision via constrained decoding (Pydantic schemas as request-level constraints, including enums for services and event types). Confidence below 0.50 refers to a human even in autopilot mode.

**Dispatch.** Recovery (tow, mobile repair, none) and onward travel (hire car, rail, hotel, none) resolved deterministically from coverage output, incident type, and garage proximity.

**Operator dashboard.** Coverage, dispatch, and SMS each present approve-or-edit. Co-pilot (default) pauses at each stage; autopilot auto-approves above confidence threshold.

## Prioritization

We start with co-pilot coverage because it's the highest-stakes component – a wrong coverage decision cascades through dispatch into real-world action. Human review at each stage prevents errors from compounding (four unchecked steps at 95% accuracy each compound to ~81%). We integrate co-pilot into operator workflows to passively collect edit deltas – the data flywheel – feeding continuous model improvement and regression tests. Intake follows in V1.2: lower decision stakes but customer-facing, requiring careful rollout (employees first, then opted-in customers). As quality data accumulates, we scale both usage (more customers) and agency (removing humans from stabilized components), and potentially enrich intake with photo/video evidence.

## Milestones

**V1.1: Co-pilot Coverage (wk 1-6).** RAG infrastructure (dense + BM25), LLM coverage assessment, Google Maps API integration, SMS service, operator dashboard instrumented to capture edit deltas. *AC:* functional solution validated by SME group, quality sufficient for broader integration (e.g. Cohen's kappa >= 0.7 vs. senior underwriters on blind sample).

**V1.1 Rollout (wk 7-12).** Deploy to gradually larger operator pool (10s → 100s), co-pilot only. *AC:* operator edit rate < 15%, Cohen's kappa >= 0.8 on weekly sample; avg intake-to-dispatch significantly faster than unassisted operators.

**V1.2: Intake (wk 13-36).** Mobile app with conversational AI intake (call and text). Phased: employees → opted-in trial customers → GA. *AC:* >=80% of intakes completed within AI flow vs. escalated to human; quality and ops (API latency, error rate) metrics gate each phase.

**V2: Full Autopilot (wk 37+).** Contingent on AI coverage reaching human parity. Phased rollout starting with employees. *AC:* AI error rate <= human baseline in A/B testing (starting small-scale employees → opted-in customers who signed waiver).

**V3: Photo/Video Evidence (longer term).** Customers upload post-incident photos or video during intake; a multimodal model returns a structured damage assessment via the same constrained decoding pattern, supplementing the verbal description for more confident coverage decisions and stronger regulatory documentation.

## Technical Risks

**Hallucinated coverage.** Retrieval grounding, constrained decoding, and a 0.50 confidence floor reduce but don't eliminate risk. An LLM can cite a real section but misapply it – a fabricated quote is worse than none under regulatory scrutiny. There is a real possibility that full autonomy is never achievable for coverage decisions.

**Non-determinism.** Same claim details can produce different coverage outcomes across calls. Consistency is both a regulatory requirement (FCA expects equivalent treatment) and an operator trust issue. Eval infrastructure must track decision variance, not just accuracy.

**Retrieval doc complexity.** Real policy documents are a mix of PDFs with tables, figures, and legalese. Getting correct semantic chunks out of these requires specialized parsing and potentially source-specific tuning.

**Adversarial intake.** Server-side JSON masking limits injection surface, but adversarial input could still poison extracted fields that feed coverage decisions.

**STT quality in real conditions.** Motorway noise, regional accents, and distressed callers will degrade transcription well below demo conditions. Deterministic gates mostly prevent bad data from reaching coverage, but misrecognized fields trigger re-ask loops that increase call time and frustrate already-stressed customers.

**LLM at scale/cost.** Peak volumes (thousands of concurrent calls) stress LLM APIs, and a single provider is a single point of failure during surges. Litellm abstracts the provider, making failover straightforward in theory but untested under load. At sufficient volume, self-hosted open-weight models may be necessary to manage cost.

**Operator trust.** Rubber-stamping and over-editing are both failure modes. Edit-rate monitoring flags both.
