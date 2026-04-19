# Roadside Assistance AI Co-Pilot

A full-stack prototype demonstrating an AI co-pilot for human call-centre agents handling motor breakdown claims. The system combines a conversational intake agent, deterministic rule-based coverage evaluation, embedding-assisted policy citation retrieval, and an LLM-drafted SMS notification - all surfaced through an operator console with per-stage approve / edit / retry controls.

---

## What "co-pilot" means here

The system is designed for a **human-in-the-loop** workflow, not autonomous operation. A call-centre agent runs the tool alongside a live customer call. The AI:

- Conducts the structured intake conversation (collects name, policy, vehicle, location, incident, drivability)
- Evaluates coverage against policy rules
- Selects the nearest eligible garage
- Drafts the customer SMS

At each stage, the operator can **approve as-is, edit the AI's proposal, or retry**. The AI proposes; the human decides. An Autopilot mode is also available (all stages auto-approved, SMS auto-sent) for low-risk cases or demonstration purposes.

---

## Architecture

```
Customer (via phone / Web Speech API simulation)
        │
        ▼
┌───────────────────────┐
│  WebSocket /voice     │  ← Intake agent (Gemini LLM, O(1) prompt)
│  intake conversation  │    Safety gates run deterministically here
└──────────┬────────────┘
           │ intake_complete
           ▼
┌───────────────────────┐
│  POST /check-coverage │  ← Rule table (coverage_rules.py)
│                       │    + embedding citations (embeddings.py)
└──────────┬────────────┘
           │ coverage_result
           ▼
┌───────────────────────┐
│  POST /next-action    │  ← Deterministic selector (action.py)
│                       │    Haversine distance, capability match
└──────────┬────────────┘
           │ action_result
           ▼
┌───────────────────────┐
│  POST /notify         │  ← Gemini LLM drafts JSON-structured SMS
│                       │    Server assembles fields into final text
└───────────────────────┘

All stages: proposed → [operator approval] → approved
Mode switch copilot → autopilot auto-approves pending stages.
```

---

## Design decisions

### 1. Co-pilot over autonomous agent

The goal is "a UI to observe the agent for humans" - but observation alone is not leverage. This prototype goes further: the operator console lets agents edit AI outputs before they reach the customer. This matters because:

- Coverage entitlements affect customer outcomes directly (missed hire car = stranded customer)
- SMS wording can be legally sensitive
- An agent noticing an error mid-flow should be able to fix it, not just observe it

Autopilot mode is retained as an alternative - it runs the same pipeline end-to-end and surfaces all outputs, so supervisors can review even when no intervention was made. The mode can be toggled mid-session; switching to autopilot auto-approves any pending stage and cascades.

### 2. LLM only where needed

| Stage | Approach | Reason |
|---|---|---|
| Intake | Gemini LLM (Flash Lite) | Conversational flexibility, STT noise handling, open-ended incident descriptions |
| Coverage | Rule table + embeddings | Fully deterministic, auditable, zero hallucination risk on entitlements |
| Action | Haversine + capability filter | Pure algorithm - nearest eligible garage is unambiguous |
| SMS | Gemini LLM (JSON template) | Benefits from natural language variation; JSON schema prevents structural hallucinations |

This reduces LLM calls from 4 per claim to 2 (intake turns + one SMS call), with one cheap embedding call for policy citations.

### 3. Coverage: rule table + embedding citations (hybrid)

A pure LLM coverage check risks hallucinating entitlements or misquoting limits. A pure rule table can't surface the policy text the customer cares about.

The hybrid approach:
- **Rule table** (`coverage_rules.py`) encodes all tier/incident combinations deterministically. Outputs `covered`, `services_entitled`, `exclusions_flagged`, `reasoning`.
- **Embedding retrieval** (`embeddings.py`) embeds all 11 policy sections at startup (cached to disk after first run), then runs one query embedding per claim to surface the 2 most relevant verbatim policy snippets as citations.

Citations are steered by the rule table's own output (event type + applicable section + services list) rather than raw user text, so they align with what was actually decided. Covered claims exclude NOT_COVERED sections from retrieval; denied claims prefer them.

### 4. Deterministic action selection

Garage selection is pure algorithm: filter by `has_tow_truck` if non-drivable, match incident type to required capability, pick nearest, compute `ETA = 15 + distance_miles * 3`. No LLM call. The top 5 garages are returned to the frontend so the operator can override to a different one.

### 5. Safety gates (deterministic, not LLM-driven)

All safety rules run as Python logic in the WebSocket handler, not as LLM instructions:

| Gate | Trigger | Behaviour |
|---|---|---|
| Emergency scan | Keyword match on user text | Override agent reply: tell customer to call 999 |
| Policy digit verification | LLM-extracted digits vs user utterances | Drop extraction if LLM invented or altered digits |
| Name plausibility | 3-char prefix match of name tokens | Fail if zero token overlap; 3-strike abort |
| Vehicle mismatch | Extracted reg vs policy record | Reject mismatch, never leak DB reg; 3-strike abort |
| Required fields gate | Server-side check before intake_complete | LLM cannot mark intake complete with missing fields |

Gate firings are surfaced to the operator console (GateBanner) with opaque summaries - no policy specifics, no PII from the database.

### 6. O(1) prompt size for intake

The intake LLM receives a state snapshot per turn rather than the full conversation history:

```
KNOWN SO FAR:
- customer_name: Sarah Mitchell
- policy_number: ALZ-10042
...
YOUR PREVIOUS REPLY: ...
SYSTEM NOTE: ...
CUSTOMER: <latest utterance>
```

Prompt size is bounded regardless of call length. The LLM doesn't see verbatim conversation history - only extracted state and its own last reply. This is acceptable because intake is structured: once a field is extracted and validated, it doesn't need to be re-derived.

### 7. JSON-template SMS

The SMS LLM receives structured input and returns a JSON object with fixed fields:

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

The server assembles these into the final SMS string. This keeps the LLM's natural language benefits (warm, adaptive wording) while making the structure verifiable and operator-editable field by field.

### 8. LLM model fallback chain

```
gemini-2.5-flash-lite  →  gemini-2.5-flash  →  gemini-3.1-flash-lite-preview  →  gemini-3-flash-preview
```

Tried in order on any failure. Embedding model (`gemini-embedding-001`) has no fallback - it is a fixed dependency for the policy section index.

### 9. Session state machine

```
intake → coverage → action → notify → complete
```

Per stage:
```python
{"status": "idle" | "proposed" | "approved", "proposed": {...}, "edited": {...}}
```

Downstream stages read `edited` if present, else `proposed`. This makes operator overrides meaningful end-to-end: changing the garage on the action card affects what appears in the SMS draft.

---

## Repo structure

```
.
├── backend/
│   ├── main.py              # FastAPI app - all HTTP + WebSocket endpoints
│   ├── session.py           # In-memory session store and factory
│   ├── coverage.py          # Validators, normalizers, safety-gate helpers
│   ├── coverage_rules.py    # Deterministic coverage rule table (no LLM)
│   ├── embeddings.py        # Policy section parser + embedding index + retrieval
│   ├── action.py            # Haversine, garage finder, deterministic action selector
│   ├── llm.py               # LLM client (fallback chain) + embedding client
│   ├── prompts.py           # INTAKE_SYSTEM_PROMPT, SMS_SYSTEM_PROMPT, SMS_NOT_FOUND_SYSTEM_PROMPT
│   ├── data/
│   │   ├── customers.json   # 8 synthetic customers across 4 tiers
│   │   ├── garages.json     # 10 garages across UK cities
│   │   ├── policy_bronze.txt
│   │   ├── policy_silver.txt
│   │   ├── policy_gold.txt
│   │   └── policy_gold_plus.txt
│   └── tests/
│       ├── conftest.py
│       └── test_core.py     # 65 unit tests (no LLM calls, all mocked)
│
└── frontend/
    └── src/
        ├── App.jsx          # Orchestrator: state machine, mode toggle, handlers
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

**Customers** - 8 records across 4 policy tiers:

| Name | Policy | Tier | Notes |
|---|---|---|---|
| Sarah Mitchell | ALZ-10042 | Gold | Standard golden-path customer |
| James Carter | ALZ-20187 | Bronze | Minimal cover - local recovery only |
| Laura Barnes | ALZ-30295 | Gold Plus | European cover available |
| David Wilson | ALZ-40318 | Silver | Home Start included |
| Emma Clark | ALZ-50421 | Gold | Good for vehicle mismatch demo |
| Mark Stone | ALZ-60099 | Bronze | Uber driver - triggers commercial exclusion |
| Claire Foster | ALZ-70512 | Silver | |
| Tom Bradley | ALZ-80634 | Gold Plus | Tesla Model 3 - routes to EV-capable garages |

**Garages** - 10 records across UK cities (Manchester, Birmingham, Edinburgh, Leeds, Bristol, London x2, Glasgow, Cardiff). Each has a `capabilities` list (`mechanical`, `electrical`, `tyre`, `battery`, `ev`, `bodywork`) and `has_tow_truck` flag.

**Policy tiers**: Bronze (roadside only) → Silver (+ home start) → Gold (+ national recovery + onward travel + misfuelling) → Gold Plus (+ European cover).

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

**Tests**

```bash
cd backend
pytest
# 65 tests, all unit tests, no external calls
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | Yes | - | Gemini API key |
| `LLM_MODEL_CHAIN` | No | `gemini/gemini-2.5-flash-lite,...` | Comma-separated fallback chain |
| `EMBEDDING_MODEL_NAME` | No | `gemini/gemini-embedding-001` | Fixed embedding model (no fallback) |

---

## Known simplifications

Acknowledged shortcuts for a prototype:

- **Voice**: uses Web Speech API (browser STT) and browser TTS. In production: WebRTC + streaming STT/TTS provider.
- **Session storage**: in-memory dict. In production: Redis or a database with TTL.
- **Location resolution**: keyword lookup table mapping city/road names to static lat/lng. In production: Google Maps Geocoding API.
- **Policy index**: embeds 11 short policy sections. In production: proper RAG over full policy documents with a chunking and overlap strategy.
- **Authentication**: none. In production: agent login, session ownership, full audit log.
- **Garage availability**: `open_now_override` boolean per record. In production: real-time availability feed from a dispatch system.
