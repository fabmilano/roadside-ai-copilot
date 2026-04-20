"""Policy section index with embedding-based retrieval and LLM-assisted coverage decisions.

On boot, parse each tier policy file (policy_*.md) into sections by ### headers and
embed their prose. At request time the decision engine runs two steps:

  1. RETRIEVAL: filter sections to the customer's tier, rank by cosine similarity
     against the incident description and any customer notes, keep the top-K (4)
     most relevant sections.

  2. LLM DECISION: pass the top-K sections as prose excerpts plus the claim
     details to the LLM. The LLM reads the policy language the way a human
     agent would and returns a structured JSON coverage decision.

Safety net: if the LLM call fails or returns confidence < 0.5, the engine
returns covered=None ("refer to operator") rather than auto-deciding.
"""

import hashlib
import json
import math
from pathlib import Path

from llm import EMBEDDING_MODEL, call_llm, get_embedding, get_embeddings
from prompts import COVERAGE_SYSTEM_PROMPT

DATA_DIR = Path(__file__).parent / "data"
CACHE_PATH = Path(__file__).parent / ".embedding_cache.json"
CONFIDENCE_FLOOR = 0.50
TOP_K = 4


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors; returns 0.0 if either is the zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _parse_markdown_sections(policy_text: str, tier: str) -> list[dict]:
    """Split a tier policy file into sections by ### headers.

    Returns a list of dicts with keys: tier, section_title, prose.
    Sections with no prose content are discarded.
    """
    sections: list[dict] = []
    current_title: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_title:
            prose = "\n".join(current_lines).strip()
            if prose:
                sections.append({
                    "tier": tier,
                    "section_title": current_title,
                    "prose": prose,
                })

    for line in policy_text.splitlines():
        if line.startswith("### "):
            _flush()
            current_title = line[4:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    _flush()
    return sections


def _load_all_sections() -> list[dict]:
    """Parse all policy_*.md files and return a flat list of section objects.

    Tier is inferred from the filename (e.g. policy_gold.md -> 'gold').
    """
    all_sections: list[dict] = []
    for path in sorted(DATA_DIR.glob("policy_*.md")):
        tier = path.stem.replace("policy_", "")
        all_sections.extend(_parse_markdown_sections(path.read_text(), tier))
    return all_sections


def _cache_key(sections: list[dict]) -> str:
    """SHA256 fingerprint of section content + embedding model name.

    Cache is invalidated automatically whenever any policy file changes.
    """
    payload = json.dumps(
        [{"tier": s["tier"], "section_title": s["section_title"], "prose": s.get("prose", "")} for s in sections],
        sort_keys=True,
    )
    return hashlib.sha256((EMBEDDING_MODEL + "|" + payload).encode()).hexdigest()


class PolicyIndex:
    """Holds parsed policy sections and their embedding vectors.

    `sections` and `embeddings` are parallel lists: `embeddings[i]` is the
    vector for `sections[i]`. Built once on startup, then queried per claim.
    """

    def __init__(self):
        self.sections: list[dict] = []
        self.embeddings: list[list[float]] = []
        self.ready = False

    async def build(self):
        """Parse all policy files, embed section prose, and populate the index.

        Loads from `.embedding_cache.json` when the content hash matches,
        avoiding repeat API calls across server restarts.
        """
        self.sections = _load_all_sections()
        key = _cache_key(self.sections)

        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text())
                if cached.get("key") == key:
                    self.embeddings = cached["embeddings"]
                    self.ready = True
                    return
            except Exception:
                pass

        texts = [f"{s['section_title']}\n{s.get('prose', '')}" for s in self.sections]
        self.embeddings = await get_embeddings(texts)
        try:
            CACHE_PATH.write_text(json.dumps({"key": key, "embeddings": self.embeddings}))
        except Exception:
            pass
        self.ready = True

    async def select_clauses(self, fields: dict, customer: dict) -> dict:
        """Coverage decision via embedding retrieval followed by LLM judgment.

        Retrieves the top-K most relevant policy sections for the customer's tier,
        then asks the LLM to read them alongside the claim details and return a
        structured coverage decision. Falls back to refer-to-operator on LLM
        failure or low-confidence results.
        """
        if not self.ready:
            return _refer_to_operator("Policy index not ready")

        tier = (customer.get("tier") or "bronze").lower()
        description = (fields.get("incident_description") or "").strip()
        notes = (customer.get("notes") or "").strip()
        incident_type = fields.get("incident_type") or "not specified"
        drivable = fields.get("vehicle_drivable")

        if not description and not notes:
            return _refer_to_operator("No incident description provided")

        # --- Retrieval: filter to customer's tier, rank by cosine similarity --
        tier_indices = [i for i, s in enumerate(self.sections) if s["tier"] == tier]
        if not tier_indices:
            return _refer_to_operator(f"No policy sections found for tier: {tier}")

        query = " ".join(filter(None, [description, notes]))
        try:
            qvec = await get_embedding(query)
        except Exception:
            return _refer_to_operator("Embedding query failed")

        scored = [
            (_cosine(qvec, self.embeddings[i]), self.sections[i])
            for i in tier_indices
            if i < len(self.embeddings)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_sections = [s for _, s in scored[:TOP_K]]

        # When the vehicle is not drivable, always include the onward-travel
        # section if the tier has one — it ranks poorly against incident-focused
        # queries but is directly relevant and must not be silently omitted.
        if drivable is False:
            onward_title = "Onward travel when the vehicle cannot be driven"
            onward_section = next(
                (s for i, s in enumerate(self.sections)
                 if s["tier"] == tier and s["section_title"] == onward_title),
                None,
            )
            if onward_section and onward_section not in top_sections:
                top_sections.append(onward_section)

        # --- LLM decision: read the policy excerpts and decide ---------------
        excerpts = "\n\n---\n\n".join(
            f"### {s['section_title']}\n\n{s['prose']}"
            for s in top_sections
        )
        drivable_str = {True: "yes", False: "no"}.get(drivable, "not confirmed")
        user_message = (
            f"Customer tier: {tier.upper()}\n\n"
            f"Policy excerpts (most relevant to this claim):\n\n"
            f"{excerpts}\n\n"
            f"---\n\n"
            f"Claim:\n"
            f"- Incident type: {incident_type}\n"
            f"- Incident description: {description or 'not provided'}\n"
            f"- Vehicle drivable: {drivable_str}\n"
            f"- Customer notes on file: {notes or 'none'}\n\n"
            f"Return the JSON coverage decision now."
        )

        try:
            result = await call_llm(COVERAGE_SYSTEM_PROMPT, user_message, response_format="json")
        except Exception as e:
            return _refer_to_operator(f"Coverage LLM call failed: {e}")

        if not isinstance(result, dict):
            return _refer_to_operator("Coverage LLM returned unexpected response format")

        confidence = float(result.get("confidence") or 0.0)
        if confidence < CONFIDENCE_FLOOR:
            return _refer_to_operator(
                f"Low confidence ({confidence:.2f}) - please refer to an operator"
            )

        covered = result.get("covered")
        if covered is not None:
            covered = bool(covered)

        return {
            "covered": covered,
            "event_type": result.get("event_type") or "Breakdown",
            "applicable_section": result.get("applicable_section") or top_sections[0]["section_title"],
            "services_entitled": result.get("services_entitled") or [],
            "exclusions_flagged": result.get("exclusions_flagged") or [],
            "reasoning": result.get("reasoning") or "",
            "citations": result.get("citations") or [],
            "confidence": confidence,
        }


def _snippet(section: dict, max_len: int = 200) -> str:
    """Return a truncated single-line prose snippet for use as a citation."""
    text = section.get("prose", "").strip().replace("\n", " ")
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
