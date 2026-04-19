"""Policy clause index with embedding-based coverage decisions.

On boot, parse each tier policy file into structured clauses and embed their
prose. At request time the decision engine runs three layers:

  1. TRIGGER layer (deterministic): clauses with @trigger_keywords,
     @trigger_incident_type, or @trigger_drivable are matched first.
     Exclusion triggers (outcome=not_covered) deny coverage immediately.
     Add-on triggers (outcome=covered) contribute services on top of the
     primary match.

  2. TIER filter (hard): clauses whose @tiers list does not include the
     customer's tier are excluded before the embedding search.

  3. EMBEDDING layer (semantic): among the remaining non-triggered clauses,
     cosine similarity against the customer's incident_description picks the
     PRIMARY clause. This drives the coverage decision and the citation text.

Confidence floor: if top-1 similarity < 0.5 and no triggers fired, the
engine returns covered=None ("refer to operator") rather than auto-deciding.
"""

import hashlib
import json
import math
import re
from pathlib import Path

from llm import EMBEDDING_MODEL, get_embedding, get_embeddings

DATA_DIR = Path(__file__).parent / "data"
CACHE_PATH = Path(__file__).parent / ".embedding_cache.json"
CONFIDENCE_FLOOR = 0.50


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors; returns 0.0 if either is the zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _parse_clauses(policy_text: str) -> list[dict]:
    """Parse a policy file into clause objects.

    Each clause block has the form:
        Clause <ID> - <Title>
        @tiers: bronze, silver, gold
        @outcome: covered | not_covered
        @event_type: <short label>
        @services: <svc1> | <svc2> | ...
        @trigger_keywords: kw1, kw2         (optional)
        @trigger_incident_type: fuel         (optional)
        @trigger_drivable: false             (optional)

        <prose paragraph that gets embedded>
    """
    lines = policy_text.splitlines()
    clauses: list[dict] = []
    current: dict | None = None
    meta_done = False

    clause_re = re.compile(r"^Clause\s+(\S+)\s*-\s*(.+)$", re.IGNORECASE)

    def _flush():
        if current:
            prose = "\n".join(current.pop("_lines", [])).strip()
            if prose and current.get("tiers"):
                current["prose"] = prose
                clauses.append(current)

    for line in lines:
        stripped = line.strip()
        m = clause_re.match(stripped)
        if m:
            _flush()
            current = {
                "id": m.group(1).upper(),
                "title": m.group(2).strip(),
                "tiers": [],
                "outcome": "covered",
                "event_type": None,
                "services": [],
                "trigger_keywords": None,
                "trigger_incident_type": None,
                "trigger_drivable": None,
                "_lines": [],
            }
            meta_done = False
            continue

        if current is None:
            continue

        if not meta_done and stripped.startswith("@"):
            key, _, val = stripped[1:].partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "tiers":
                current["tiers"] = [t.strip().lower() for t in val.split(",")]
            elif key == "outcome":
                current["outcome"] = val.lower()
            elif key == "event_type":
                current["event_type"] = val
            elif key == "services":
                current["services"] = [s.strip() for s in val.split("|") if s.strip()]
            elif key == "trigger_keywords":
                current["trigger_keywords"] = [k.strip().lower() for k in val.split(",") if k.strip()]
            elif key == "trigger_incident_type":
                current["trigger_incident_type"] = val.lower()
            elif key == "trigger_drivable":
                current["trigger_drivable"] = val.lower() == "true"
        elif stripped:
            meta_done = True
            current["_lines"].append(line)
        else:
            if meta_done:
                current["_lines"].append(line)

    _flush()
    return clauses


def _load_all_clauses() -> list[dict]:
    """Parse all policy_*.txt files and return a single flat list of clause objects."""
    all_clauses: list[dict] = []
    for path in sorted(DATA_DIR.glob("policy_*.txt")):
        all_clauses.extend(_parse_clauses(path.read_text()))
    return all_clauses


def _cache_key(clauses: list[dict]) -> str:
    """SHA256 fingerprint of clause content + embedding model name. Cache is invalidated on any change."""
    payload = json.dumps(
        [{"id": c["id"], "tiers": c["tiers"], "prose": c.get("prose", "")} for c in clauses],
        sort_keys=True,
    )
    return hashlib.sha256((EMBEDDING_MODEL + "|" + payload).encode()).hexdigest()


class PolicyIndex:
    """Holds the parsed clause objects and their embedding vectors.

    `clauses` and `embeddings` are parallel lists: `embeddings[i]` is the
    vector for `clauses[i]`. Built once on startup, then queried per claim.
    """

    def __init__(self):
        self.clauses: list[dict] = []
        self.embeddings: list[list[float]] = []
        self.ready = False

    async def build(self):
        """Parse all policy files, embed clause prose, and populate the index.

        Loads from `.embedding_cache.json` when the content hash matches,
        avoiding repeat API calls across server restarts.
        """
        self.clauses = _load_all_clauses()
        key = _cache_key(self.clauses)

        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text())
                if cached.get("key") == key:
                    self.embeddings = cached["embeddings"]
                    self.ready = True
                    return
            except Exception:
                pass

        texts = [f"{c['id']} - {c['title']}\n{c.get('prose', '')}" for c in self.clauses]
        self.embeddings = await get_embeddings(texts)
        try:
            CACHE_PATH.write_text(json.dumps({"key": key, "embeddings": self.embeddings}))
        except Exception:
            pass
        self.ready = True

    async def select_clauses(self, fields: dict, customer: dict) -> dict:
        """Three-layer coverage decision: triggers → tier filter → embedding.

        Returns a dict matching the existing coverage_result schema:
          covered, event_type, applicable_section, services_entitled,
          exclusions_flagged, reasoning, citations, confidence
        """
        if not self.ready:
            return _refer_to_operator("Policy index not ready")

        tier = (customer.get("tier") or "bronze").lower()
        notes = (customer.get("notes") or "").lower()
        incident_type = (fields.get("incident_type") or "").lower()
        drivable = fields.get("vehicle_drivable")
        description = (fields.get("incident_description") or "").strip()

        # ---- Layer 1: trigger scan (deterministic) --------------------------
        # Three trigger roles:
        #   exclusion: outcome=not_covered → deny immediately when fired
        #   incident_primary: trigger_incident_type matches → this IS the primary event
        #     (e.g. G1 misfuelling; the whole claim is a misfuelling incident)
        #   drivable_addon: trigger_drivable matches → add-on services on top of embedding primary
        #     (e.g. F1 onward travel; the claim is a breakdown, F1 just adds entitlements)
        exclusion_triggered: dict | None = None
        incident_primary: dict | None = None
        drivable_addons: list[dict] = []
        non_triggered: list[dict] = []

        for clause in self.clauses:
            if tier not in clause["tiers"]:
                continue  # layer 2: tier filter

            kws = clause.get("trigger_keywords")
            it = clause.get("trigger_incident_type")
            td = clause.get("trigger_drivable")
            has_any_trigger = bool(kws or it or td is not None)

            triggered = False
            if kws and any(kw in notes for kw in kws):
                triggered = True
            if it and incident_type == it:
                triggered = True
            if td is not None and drivable is not None and drivable == td:
                triggered = True

            if triggered:
                if clause["outcome"] == "not_covered":
                    if exclusion_triggered is None:
                        exclusion_triggered = clause
                elif it and incident_type == it:
                    # Incident-type match: this clause describes the primary event
                    if incident_primary is None:
                        incident_primary = clause
                else:
                    # Drivable or keyword match on a covered clause: add-on only
                    drivable_addons.append(clause)
            elif not has_any_trigger:
                non_triggered.append(clause)

        # Exclusion wins immediately - no embedding search needed
        if exclusion_triggered:
            c = exclusion_triggered
            return {
                "covered": False,
                "event_type": c.get("event_type") or "Excluded",
                "applicable_section": f"Clause {c['id']}",
                "services_entitled": [],
                "exclusions_flagged": [c["title"]],
                "reasoning": (
                    f"Clause {c['id']} ({c['title']}) applies: "
                    f"cover cannot be provided."
                ),
                "citations": [{"section": c["id"], "snippet": _snippet(c)}],
                "confidence": 1.0,
            }

        # ---- Layer 3: embedding (semantic) ----------------------------------
        top_score = 1.0  # default confidence for trigger-decided primaries

        if incident_primary:
            # Incident-type trigger identified the primary clause directly
            primary = incident_primary
            addons = drivable_addons
        elif not description or not non_triggered:
            if drivable_addons:
                primary = drivable_addons[0]
                addons = drivable_addons[1:]
            else:
                return _refer_to_operator("No incident description and no eligible clauses")
        else:
            try:
                qvec = await get_embedding(description)
            except Exception:
                return _refer_to_operator("Embedding query failed")

            # Build parallel (score, clause) list using id() to index into self.embeddings
            clause_index = {id(c): i for i, c in enumerate(self.clauses)}
            scored: list[tuple[float, dict]] = []
            for c in non_triggered:
                idx = clause_index.get(id(c))
                if idx is not None and idx < len(self.embeddings):
                    scored.append((_cosine(qvec, self.embeddings[idx]), c))

            if not scored:
                return _refer_to_operator("No eligible clauses after tier filter")

            scored.sort(key=lambda x: x[0], reverse=True)
            top_score, primary = scored[0]

            if top_score < CONFIDENCE_FLOOR and not drivable_addons:
                return _refer_to_operator(
                    f"Low confidence match ({top_score:.2f}) - please refer to an operator"
                )

            addons = drivable_addons

        # ---- Compose result -------------------------------------------------
        services = list(primary.get("services") or [])
        for addon in addons:
            services.extend(addon.get("services") or [])

        citations = [{"section": primary["id"], "snippet": _snippet(primary)}]
        for addon in addons[:2]:
            citations.append({"section": addon["id"], "snippet": _snippet(addon)})

        addon_note = ""
        if addons:
            addon_note = f" Additional: {', '.join(a['title'] for a in addons)}."

        return {
            "covered": primary["outcome"] == "covered",
            "event_type": primary.get("event_type") or "Breakdown",
            "applicable_section": f"Clause {primary['id']}",
            "services_entitled": services,
            "exclusions_flagged": [],
            "reasoning": (
                f"Matched {primary['title']} "
                f"(confidence {top_score:.2f}).{addon_note}"
            ),
            "citations": citations,
            "confidence": top_score,
        }


def _snippet(clause: dict, max_len: int = 200) -> str:
    """Return a truncated single-line prose snippet for use as a citation."""
    text = clause.get("prose", "").strip().replace("\n", " ")
    return text[:max_len - 3].rstrip() + "..." if len(text) > max_len else text


def _refer_to_operator(reason: str) -> dict:
    """Coverage result indicating the engine could not decide; human review required."""
    return {
        "covered": None,
        "event_type": "unknown",
        "applicable_section": "N/A",
        "services_entitled": [],
        "exclusions_flagged": [],
        "reasoning": reason,
        "citations": [],
        "confidence": 0.0,
    }


_index: PolicyIndex | None = None


async def get_policy_index() -> PolicyIndex:
    """Return the singleton PolicyIndex, building it on first call."""
    global _index
    if _index is None:
        _index = PolicyIndex()
        await _index.build()
    elif not _index.ready:
        await _index.build()
    return _index
