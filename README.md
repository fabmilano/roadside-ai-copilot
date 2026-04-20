# Roadside Assistance AI Co-Pilot

A full-stack prototype of an AI co-pilot for call-centre agents handling motor breakdown claims. The system conducts the intake conversation, evaluates coverage against policy documents, selects a garage, and drafts the customer SMS - surfacing each output to an operator who can approve, edit, or retry before anything reaches the customer.

---

## What "co-pilot" means here

The system is designed for a **human-in-the-loop** workflow, not autonomous operation. A call-centre agent runs the tool alongside a live customer call.

The distinction matters: observation alone isn't leverage. This prototype goes further than a monitoring dashboard - the operator console lets agents edit AI outputs before they take effect. That matters because:

- Coverage entitlements affect the customer directly. A missed hire-car entitlement means a stranded customer.
- SMS wording can be legally sensitive. The agent should own what gets sent.
- An agent who notices an error mid-flow should be able to fix it, not just flag it.

**Autopilot mode** runs the same pipeline end-to-end with all stages auto-approved. It's available for low-risk cases or demos, and can be toggled mid-session (which auto-approves any pending stage).

---

## Architecture

```
Customer (via phone / Web Speech API simulation)
        │
        ▼
┌───────────────────────┐
│  WebSocket /voice     │  ← LLM conducts intake conversation
│  intake conversation  │    Safety gates run as deterministic Python checks
└──────────┬────────────┘
           │ intake_complete
           ▼
┌───────────────────────┐
│  POST /check-coverage │  ← Embeddings retrieve relevant policy sections
│                       │    LLM reads the prose and returns a JSON decision
└──────────┬────────────┘
           │ coverage_result
           ▼
┌───────────────────────┐
│  POST /next-action    │  ← Pure algorithm: Haversine distance + capability filter
│                       │    No LLM
└──────────┬────────────┘
           │ action_result
           ▼
┌───────────────────────┐
│  POST /notify         │  ← LLM drafts SMS as a JSON object
│                       │    Server assembles fields into final text
└───────────────────────┘

Each stage: proposed → [operator approve / edit / retry] → approved
Switching to Autopilot mid-session auto-approves all pending stages.
```

---

## Where the LLM is used - and where it isn't

This is the most important design decision in the system. LLMs are good at language and judgment; they are unreliable at exact computation, rule enforcement, and deterministic checks. The split here tries to respect that.

| Stage | Approach | Why |
|---|---|---|
| Intake conversation | **LLM** (Gemini Flash Lite) | Customer speech is open-ended and noisy. The LLM needs to extract structured fields from natural language, handle STT transcription errors, and ask sensible follow-up questions. A rule-based parser can't do this. |
| Safety gates | **Deterministic Python** | Emergency detection, policy digit verification, name plausibility, and vehicle mismatch must be right on adversarial inputs. An LLM instruction like "detect emergencies" can be confused by unusual phrasing or multi-topic utterances. Hard code these. |
| Coverage decision | **Embeddings + LLM** | Coverage requires reading policy prose and reasoning about the claim. The policy documents are written in natural language and the set of possible claim descriptions is open-ended. An LLM that reads the relevant policy sections is the right tool here. |
| Garage selection | **Deterministic algorithm** | Nearest eligible garage with the right capability is an unambiguous calculation. There is no judgment involved - the answer is a function of distance and a capability list. LLM adds cost and non-determinism for no benefit. |
| SMS drafting | **LLM** (Gemini Flash Lite) | The SMS needs to be warm and readable, not just a data dump. The LLM provides natural language variation while a JSON output schema keeps the structure verifiable. |

### Intake safety gates in more detail

Five gates run as Python logic inside the WebSocket handler, not as LLM instructions. Any gate that fires overrides the LLM's reply:

| Gate | Trigger | Behaviour |
|---|---|---|
| Emergency scan | Keyword match on user text | Override agent reply: tell customer to call 999 immediately |
| Policy digit verification | LLM-extracted digits not present in user utterances | Drop the extraction - the LLM invented or corrected digits |
| Name plausibility | 3-char prefix match of name tokens | Fail if zero token overlap; 3-strike abort |
| Vehicle mismatch | Extracted reg doesn't match policy record | Reject mismatch, never leak the DB reg; 3-strike abort |
| Required fields gate | Server-side check before `intake_complete` | LLM cannot close intake with missing required fields |

Gate firings are surfaced to the operator console with opaque summaries - no policy specifics, no PII from the database.

---

## Coverage: how the policy decision works

### Why not a rule table

The naive approach to coverage is a rule table: `if tier == "gold" and incident_type == "fuel": covered = True, services = [...]`. This works until the policy changes, the rule table doesn't get updated, and entitlements drift from what the document says. The table is also brittle to edge cases (what is a "fuel incident" exactly?) and silent about *why* a claim was decided the way it was.

### Why not pure LLM without retrieval

Sending the whole policy to an LLM on every claim is expensive, and a general-purpose LLM can hallucinate entitlements that aren't in the policy or misquote the terms of coverage.

### The approach: RAG + grounded LLM judgment

The policy documents (`backend/data/policy_*.md`) are plain markdown - section headers and prose paragraphs, written the way a product team would write them. No machine-readable metadata, no trigger tables.

**Step 1 - Embedding retrieval**: on startup, each `### Section` and its prose is embedded with `gemini-embedding-001`. At claim time, the customer's incident description and customer notes are embedded as a query, and the top-4 most relevant sections from their tier's policy file are retrieved by cosine similarity.

**Step 2 - LLM decision**: the retrieved sections are passed to the LLM as verbatim policy prose, alongside the claim details. The LLM reads them and returns a structured JSON decision: `covered`, `services_entitled`, the applicable section title, reasoning, and verbatim citations. The prompt instructs the LLM to use only the provided excerpts and not invent services.

**What the embeddings actually do**: they narrow the policy down to the handful of sections relevant to this specific claim before the LLM reads anything. At demo scale (24 sections across 3 small files) you could skip embeddings and send the whole tier file to the LLM - the pattern earns its keep at production scale, where the policy is a 200-page document.

**What the LLM adds over embeddings alone**: judgment. Two sections might be semantically similar to the incident description, but only one actually applies. More importantly, the LLM catches cross-field reasoning that embeddings can't: a claim description that says "my car broke down" ranks low against the "commercial use exclusion" section by cosine similarity, but the LLM reads the customer notes field ("Uber driver - commercial use") alongside that exclusion prose and denies coverage correctly.

**Confidence floor**: if the LLM returns `confidence < 0.5`, the engine returns `covered=None` with a "refer to operator" message rather than auto-deciding.

---

## O(1) intake prompt size

The intake LLM receives a state snapshot per turn, not the full conversation history:

```
KNOWN SO FAR:
- customer_name: Sarah Mitchell
- policy_number: ALC-10042
...
YOUR PREVIOUS REPLY: ...
SYSTEM NOTE: ...
CUSTOMER: <latest utterance>
```

Prompt size is bounded regardless of call length. The LLM sees only extracted state and its own last reply. This works because intake is structured: once a field is extracted and validated, it doesn't need to be re-derived from the transcript.

---

## SMS JSON template

The SMS LLM receives structured input and returns a fixed JSON schema:

```json
{
  "greeting": "Hi Sarah,",
  "status_line": "Your Gold policy covers this breakdown.",
  "action_line": "We are arranging a tow to Manchester Recovery Centre.",
  "eta_line": "ETA ~15 mins.",
  "services_line": "Replacement vehicle: Group A hire car for up to 24 hours; ...",
  "case_ref_line": "Case: RA-2026-1234",
  "emergency_footer": "For any medical or safety emergency call 999."
}
```

The server assembles these fields into the final SMS string. The operator sees and can edit each field individually before sending.

---

## Session state machine

```
intake → coverage → action → notify → complete
```

Per stage:
```python
{"status": "idle" | "proposed" | "approved", "proposed": {...}, "edited": {...}}
```

Downstream stages read `edited` if present, else `proposed`. Operator overrides are meaningful end-to-end: changing the dispatch on the action card changes what appears in the SMS draft.

## Two dispatch slots per action

Each claim has two parallel dispatch outputs, both shown on the action card and editable by the operator:

| Slot | Values | Derived from |
|---|---|---|
| `recovery_action` | `tow` / `mobile_repair` / `none` | `vehicle_drivable` flag + coverage outcome |
| `onward_travel` | `hire_car` / `rail` / `hotel` / `none` | Coverage `services_entitled` when vehicle is not drivable |

This maps directly to the four outcomes in the brief:

| Outcome | Example | `recovery_action` | `onward_travel` |
|---|---|---|---|
| Repair / tow only | James Carter, Bronze, non-drivable | `tow` | `none` |
| Transport only | Operator edit: vehicle handled separately | `none` | `hire_car` |
| Both | Laura Barnes, Gold, non-drivable | `tow` | `hire_car` |
| Neither | Mark Stone, Bronze, Uber - denied | `none` | `none` |

The AI proposes both slots based on coverage entitlements. In co-pilot mode the operator can override either via a dropdown before approving. The "transport only" case is reached by the operator setting `recovery_action` to `none` - for example, when the customer says their vehicle is already being recovered by a third party and they just need to get home.

---

## Repo structure

```
.
├── backend/
│   ├── main.py              # FastAPI app - all HTTP + WebSocket endpoints; safety gates
│   ├── session.py           # In-memory session store and factory
│   ├── coverage.py          # Field validators, normalizers, and safety-gate helpers
│   ├── embeddings.py        # Policy section parser + embedding index + retrieval + LLM decision
│   ├── action.py            # Haversine distance, garage finder, deterministic action selector
│   ├── llm.py               # LLM client (fallback chain) + embedding client
│   ├── prompts.py           # INTAKE_SYSTEM_PROMPT, COVERAGE_SYSTEM_PROMPT, SMS_SYSTEM_PROMPT
│   ├── data/
│   │   ├── customers.json   # 8 synthetic customers across 3 tiers
│   │   ├── garages.json     # 10 garages across UK cities
│   │   ├── policy_bronze.md # Natural-prose policy files (markdown sections)
│   │   ├── policy_silver.md
│   │   └── policy_gold.md
│   └── tests/
│       ├── conftest.py
│       ├── test_core.py     # 70 unit tests (LLM and embedding calls mocked)
│       └── stress/          # End-to-end demo scripts (require live server + API key)
│           ├── test_demo_cases_1.py  # Cases A-D (Carter, Barnes, Stone, Mitchell)
│           └── test_demo_cases_2.py  # Cases E-H (Wilson, Clark, Foster, Bradley)
│
└── frontend/
    └── src/
        ├── App.jsx          # Orchestrator: state machine, mode toggle, stage handlers
        ├── index.css        # All styles (no CSS framework)
        ├── components/
        │   ├── VoiceChat.jsx        # Customer simulator (Web Speech API)
        │   ├── Dashboard.jsx        # Right-pane operator console
        │   ├── ModeToggle.jsx       # Autopilot / Co-pilot switch
        │   ├── GateBanner.jsx       # Colour-coded safety gate log
        │   ├── PipelineStatus.jsx   # Stage progress bar
        │   ├── ClaimFields.jsx      # Extracted fields with inline edit
        │   ├── CoverageResult.jsx   # Coverage card with citations + approve/edit/retry
        │   ├── ActionResult.jsx     # Action card with Leaflet map + garage picker
        │   └── SmsPreview.jsx       # SMS draft with phone mockup + send button
        └── ErrorBoundary.jsx
```

---

## Synthetic data

**Customers** - 8 records across 3 policy tiers:

| Name | Policy | Tier | Demo notes |
|---|---|---|---|
| Sarah Mitchell | ALC-10042 | Gold | Flat battery - mobile_repair dispatch |
| James Carter | ALC-20187 | Bronze | Breakdown - tow only (Bronze has no onward travel) |
| Laura Barnes | ALC-30295 | Gold | Motorway breakdown - tow + hire_car (Gold onward travel) |
| David Wilson | ALC-40318 | Silver | Breakdown - tow, no onward travel (Silver excludes it) |
| Emma Clark | ALC-50421 | Gold | Accident - coverage denied (policy excludes collisions) |
| Mark Stone | ALC-60099 | Bronze | Commercial/Uber use - denied via customer notes flag |
| Claire Foster | ALC-70512 | Silver | Key lockout - denied (policy excludes lockouts) |
| Tom Bradley | ALC-80634 | Gold | Tesla EV battery flat - mobile_repair, EV-capable garage |

**Garages** - 10 records across UK cities (Manchester, Birmingham, Edinburgh, Leeds, Bristol, London x2, Glasgow, Cardiff). Each has a `capabilities` list (`mechanical`, `electrical`, `tyre`, `battery`, `ev`, `bodywork`) and a `has_tow_truck` flag.

**Policy tiers**: Bronze (roadside + local recovery, no home) → Silver (+ Home Start) → Gold (+ national recovery + onward travel + misfuelling).

**Policy format**: plain markdown files. Each tier has a `## What is covered` section and a `## What is not covered` section, each split into `### Named subsections` that become individual embedding chunks.

---

## Running locally

**Backend**

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set LLM_API_KEY in .env to your Gemini API key
uvicorn main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Backend must be running on port 8000.

**Unit tests**

```bash
cd backend
pytest
# 70 tests - LLM and embedding calls are mocked throughout
```

**End-to-end stress tests** (require live server and API key)

```bash
cd backend
.venv/bin/python tests/stress/test_demo_cases_1.py   # cases A-D
.venv/bin/python tests/stress/test_demo_cases_2.py   # cases E-H
```

These drive full WebSocket + HTTP sessions and assert against actual LLM responses. Paced at 2.5 s per turn and 30 s between cases to stay within free-tier API limits.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | Yes | - | Gemini API key |
| `LLM_MODEL_CHAIN` | No | `gemini/gemini-2.5-flash-lite,...` | Comma-separated fallback chain for LLM calls |
| `EMBEDDING_MODEL_NAME` | No | `gemini/gemini-embedding-001` | Embedding model (no fallback - fixed dependency) |

---

## Known limitations

These are intentional shortcuts for a prototype, not oversights.

**Coverage decisions are non-deterministic.** The LLM coverage call can return different results for the same claim on different runs, or be swayed by how the incident is phrased. A rule table is fully deterministic; this design trades that for flexibility and policy-grounding. The operator approval step is the human check on this.

**LLM hallucination is mitigated but not eliminated.** The coverage prompt instructs the LLM to use only the provided policy excerpts and not invent services. In practice, a capable model follows this reliably - but it is an instruction, not a hard constraint. An operator reviewing the coverage card before approval is the backstop.

**The confidence score is self-reported.** The `confidence` field in the coverage JSON is returned by the LLM itself, not computed from an external signal. It is a useful heuristic for the refer-to-operator floor, but it is not calibrated.

**Commercial exclusion is now soft, not hard.** The previous design used a deterministic keyword scan on `customer.notes` to deny commercial-use claims. Now the LLM reads the notes and the exclusion prose and decides. This is more natural and handles novel phrasings, but it is probabilistic. For a production system handling fraud risk, this rule should probably be re-hardened.

**Embeddings are underused at this scale.** With 24 short sections across 3 small policy files, sending the full tier policy to the LLM would work fine and skip the retrieval step entirely. The embedding pattern is included because it is the right approach at production scale (a full policy document), and because it demonstrates the RAG pattern - but it is not strictly necessary here.

**Voice is simulated.** Web Speech API (browser STT) and browser TTS. In production: WebRTC + a streaming STT/TTS provider.

**Session storage is in-memory.** A Python dict. In production: Redis with TTL.

**Location resolution is a keyword table.** City and road names map to static lat/lng coordinates. In production: Google Maps Geocoding API.

**No authentication.** Any request can access any session. In production: agent login, session ownership, full audit log.

**Garage availability is a static flag.** `open_now_override` boolean per record. In production: real-time availability feed from a dispatch system.

**Onward-travel-only dispatch requires operator intervention.** The system defaults `recovery_action` to `tow` or `mobile_repair` based on the `vehicle_drivable` flag. There is no intake signal for "vehicle already handled elsewhere - customer only needs transport." The operator can set `recovery_action` to `none` on the action card to produce this outcome, but the system won't reach it automatically. Adding a dedicated intake field for this case was considered and left out of scope: the case is edge-rare and classifying it from noisy speech adds a new failure mode.
