"""Policy section index with embedding-based retrieval.

On boot, parse each tier policy into named sections and embed them. At request
time, embed a compact query and return the top-k sections as citations.

A lower tier's sections are inherited by higher tiers (Bronze C applies to all,
Silver D applies to Silver/Gold/Gold Plus, etc.).
"""

import hashlib
import json
import math
import re
from pathlib import Path

from llm import EMBEDDING_MODEL, get_embeddings

DATA_DIR = Path(__file__).parent / "data"
CACHE_PATH = Path(__file__).parent / ".embedding_cache.json"

TIER_INHERITANCE = {
    "bronze":    ["bronze"],
    "silver":    ["bronze", "silver"],
    "gold":      ["bronze", "silver", "gold"],
    "gold_plus": ["bronze", "silver", "gold", "gold_plus"],
}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _parse_sections(policy_text: str, tier: str) -> list[dict]:
    """Split a policy file into {section, title, text, tier} chunks.

    Sections are detected by lines matching 'Section X - Title' or
    'NOT COVERED ...'. Preamble is skipped.
    """
    lines = policy_text.splitlines()
    sections: list[dict] = []
    current_key: str | None = None
    current_title: str | None = None
    buffer: list[str] = []

    header_re = re.compile(r"^Section\s+([A-Z])\s*-\s*(.+)$", re.IGNORECASE)
    notcov_re = re.compile(r"^NOT COVERED\b.*$", re.IGNORECASE)

    def flush():
        if current_key and buffer:
            body = "\n".join(buffer).strip()
            if body:
                sections.append({
                    "tier": tier,
                    "section": current_key,
                    "title": current_title or current_key,
                    "text": body,
                })

    for line in lines:
        stripped = line.strip()
        m = header_re.match(stripped)
        if m:
            flush()
            current_key = m.group(1).upper()
            current_title = f"Section {current_key} - {m.group(2).strip()}"
            buffer = [stripped]
            continue
        n = notcov_re.match(stripped)
        if n:
            flush()
            current_key = f"NOT_COVERED_{tier.upper()}"
            current_title = f"NOT COVERED under {tier.upper()}"
            buffer = [stripped]
            continue
        if current_key:
            buffer.append(line)
    flush()
    return sections


def _load_all_sections() -> list[dict]:
    tiers = ["bronze", "silver", "gold", "gold_plus"]
    out: list[dict] = []
    for tier in tiers:
        path = DATA_DIR / f"policy_{tier}.txt"
        if not path.exists():
            continue
        out.extend(_parse_sections(path.read_text(), tier))
    return out


def _cache_key(sections: list[dict]) -> str:
    payload = json.dumps([
        {"tier": s["tier"], "section": s["section"], "text": s["text"]}
        for s in sections
    ], sort_keys=True)
    return hashlib.sha256((EMBEDDING_MODEL + "|" + payload).encode()).hexdigest()


class PolicyIndex:
    def __init__(self):
        self.sections: list[dict] = []
        self.embeddings: list[list[float]] = []
        self.ready = False

    async def build(self):
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

        texts = [f"{s['title']}\n{s['text']}" for s in self.sections]
        self.embeddings = await get_embeddings(texts)
        try:
            CACHE_PATH.write_text(json.dumps({"key": key, "embeddings": self.embeddings}))
        except Exception:
            pass
        self.ready = True

    async def retrieve(
        self,
        query: str,
        tier: str,
        k: int = 2,
        prefer_exclusions: bool = False,
    ) -> list[dict]:
        """Return top-k sections for this tier, each as a citation dict.

        When prefer_exclusions is False (covered claims), NOT_COVERED sections
        are filtered out - the customer cares about their entitlements, not
        generic exclusion lists. When True (denied claims), they're kept so we
        can cite the exclusion that applied.
        """
        if not self.ready or not self.sections:
            return []
        from llm import get_embedding
        try:
            qvec = await get_embedding(query)
        except Exception:
            return []

        allowed_tiers = set(TIER_INHERITANCE.get(tier, [tier]))
        scored: list[tuple[float, dict]] = []
        for sec, vec in zip(self.sections, self.embeddings):
            if sec["tier"] not in allowed_tiers:
                continue
            if not prefer_exclusions and sec["section"].startswith("NOT_COVERED"):
                continue
            score = _cosine(qvec, vec)
            scored.append((score, sec))

        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        for _, sec in scored[:k]:
            snippet = sec["text"].strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197].rstrip() + "..."
            out.append({"section": sec["section"], "snippet": snippet})
        return out


_index: PolicyIndex | None = None


async def get_policy_index() -> PolicyIndex:
    global _index
    if _index is None:
        _index = PolicyIndex()
        await _index.build()
    elif not _index.ready:
        await _index.build()
    return _index
