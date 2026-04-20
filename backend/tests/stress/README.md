# Stress / demo integration tests

These scripts drive full end-to-end sessions over WebSocket + HTTP against a
running backend server. They are **not** part of the regular `pytest` suite
(which uses mocks and runs offline).

## Prerequisites

- Backend server running on `http://localhost:8765`
  (`uvicorn main:app --port 8765` from `backend/`)
- Valid `GEMINI_API_KEY` (or whichever LLM the server is configured for)
- Free-tier API quota: runs are paced with 2.5 s between turns and 30 s
  between cases to stay inside the 5 RPM free-tier limit

## Usage

```bash
cd backend
.venv/bin/python tests/stress/test_demo_cases_1.py   # cases A–D
.venv/bin/python tests/stress/test_demo_cases_2.py   # cases E–H
```

## Cases

| Script | Case | Customer | Tier | Scenario | Expected outcome |
|--------|------|----------|------|----------|-----------------|
| 1 | A | James Carter | Bronze | Non-drivable breakdown | tow + none |
| 1 | B | Laura Barnes | Gold | Motorway breakdown | tow + hire_car |
| 1 | C | Mark Stone | Bronze | Commercial/Uber use | denied |
| 1 | D | Sarah Mitchell | Gold | Flat battery | mobile_repair + none |
| 2 | E | David Wilson | Silver | Non-drivable breakdown | tow + none |
| 2 | F | Emma Clark | Gold | Accident (excluded) | denied |
| 2 | G | Claire Foster | Silver | Key lockout (excluded) | denied |
| 2 | H | Tom Bradley | Gold | Tesla EV battery flat | mobile_repair + none |
