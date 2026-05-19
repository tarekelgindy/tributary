"""
Tributary Narrative Fingerprint Generator
==========================================
Codifies narratives into structured, searchable fingerprints. v1 generates
the lexical (L1) and genealogical (L4) layers — enough to power earliest-use
search and adversarial origin verification.

L2 (conceptual), L3 (rhetorical), and L5 (taxonomic) layer schemas exist in
models.py but are not yet generated here.

Pipeline:
    FingerprintGenerator
        generate_lexical        L1 (Haiku)             — phrase, variants, ngrams
        search_earliest_uses    L4 candidates (Sonnet + web_search)
        adversarial_verify      L4 predecessor check (Sonnet + web_search)
        generate_genealogy      L4 assembly + polygenesis detection
        generate_fingerprint    full v1 pipeline

    FingerprintStore   JSON-backed persistence + dedup by lexical signature
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from models import (
    Attribution,
    AttestedInstance,
    GenealogyLayer,
    GenealogyStatus,
    LexicalLayer,
    NarrativeFingerprint,
    Scope,
)


HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

# Minimal English stopword set for the deterministic signature. Intentionally
# small so content words survive.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "it", "its", "i", "we", "you", "they", "he", "she", "them", "us",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "should", "could", "can", "may", "might",
    "not", "no", "so", "such",
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

LEXICAL_SYSTEM = """\
You are extracting the lexical signature of a narrative framing for archival
fingerprinting. Your output will be used to search for the earliest documented
uses of this specific framing across the internet.

Distinguish framing from topic:
- Topic: WHAT is discussed (the economy, vaccines, immigration)
- Framing: HOW it is interpreted (rigged, dangerous, invasion)

A fingerprint captures the FRAMING, not the topic.

Produce a JSON object with these fields ONLY:

{
  "canonical_phrase": "5–15 words. The most distilled version of THIS framing. Reflect the actual phrasing as it circulates — do not water it down or neutralize it.",
  "phrase_variants": ["3 to 8 short reformulations as they actually appear on the internet — the surface forms users would search or read"],
  "diagnostic_ngrams": ["3 to 6 short 2-to-4 word collocations that are SPECIFIC to this framing, not the topic. Test: searching this n-gram should predominantly surface THIS framing, not other framings of the same topic."]
}

Examples:

CLAIM: "The economy is rigged against working people"
{
  "canonical_phrase": "the economy is rigged against working people",
  "phrase_variants": ["rigged economy", "system is rigged", "rigged against workers", "the deck is stacked", "rigged against the working class"],
  "diagnostic_ngrams": ["rigged economy", "rigged against working", "deck is stacked", "system is rigged"]
}

CLAIM: "Seed oils are inflammatory and harmful"
{
  "canonical_phrase": "seed oils cause inflammation and chronic disease",
  "phrase_variants": ["seed oils are toxic", "vegetable oils are killing us", "the seed oil scam", "avoid industrial seed oils"],
  "diagnostic_ngrams": ["toxic seed oils", "seed oils inflammation", "vegetable oil scam", "industrial seed oils"]
}

Output ONLY the JSON object. No commentary, no markdown fences.
"""


EARLIEST_USE_SYSTEM = """\
You are a research assistant finding the EARLIEST documented uses of a
specific narrative framing online. This is not a relevance search — you want
the oldest credible appearances, not today's most authoritative analysis.

Search strategy:
- Use the diagnostic phrases provided, especially in archives and older content
- Prioritize results by date ascending — older is better
- A 2008 tweet or 1995 Usenet post may matter more than a 2024 NYT article here
- Cross-check claimed dates against published metadata when possible
- Cast a wide net: books, archived news, social posts, congressional records,
  forum/Usenet posts, academic papers, blogs

For each instance you find, report:
  date              ISO YYYY-MM-DD; if only year is known, use YYYY-01-01 and
                    note this in evidence
  source_url        canonical URL of the source
  source_title      title of the article/post/book
  author            if known, else ""
  lexical_form_seen the actual wording used at this source
  exact_quote       verbatim quote of the framing in context, ≤ 300 chars
  confidence        0.0–1.0 — your confidence in the date and attribution
  evidence          how you dated this; whether it is the originator or
                    an early echo

Return JSON of this shape:

{
  "instances": [
    {"date": "...", "source_url": "...", "source_title": "...", "author": "...",
     "lexical_form_seen": "...", "exact_quote": "...", "confidence": 0.0, "evidence": "..."}
  ],
  "search_notes": "brief notes on what you searched, what archives you checked, where signal was weak"
}

Sort instances by date ascending. Aim for 5–15 results. If you cannot find
anything credible, return an empty list and explain in search_notes.

Output ONLY the JSON object.
"""


ADVERSARIAL_SYSTEM = """\
You are an adversarial verifier. Someone has proposed that {proposed_date} is
the earliest documented use of a specific narrative framing. Your sole job is
to FIND EVIDENCE THAT THIS IS WRONG — that the framing existed before that date.

Do not return instances from after {proposed_date}. We are not interested in
echoes; we are interested in PREDECESSORS.

Search strategy:
- Target older archives specifically (books, congressional records, newspaper
  archives, Google Books, Internet Archive, Usenet/forum archives, early blogs)
- Try variant phrasings — the same framing in slightly different words
- Try translations if relevant
- Be skeptical: if you find something dated before {proposed_date}, verify the
  date independently before reporting

For each pre-{proposed_date} instance you find, report the same fields as a
normal attestation (date, source_url, source_title, author, lexical_form_seen,
exact_quote, confidence, evidence) and explicitly explain in `evidence` why
you trust the date.

Return JSON:

{{
  "earlier_instances": [...],
  "verification_notes": "what you searched, what you tried that did not pan out, any caveats"
}}

If no earlier instances are found, return an empty list and state plainly in
verification_notes that you searched and found nothing predating
{proposed_date}. A clean negative result is valuable — it strengthens the
origin claim.

Output ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def _parse_json_safe(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_strip_json_fences(text))
    except json.JSONDecodeError:
        return {}


def _stopword_stripped(phrase: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", phrase.lower())
    return " ".join(t for t in tokens if t not in _STOPWORDS)


def _scope_clause(scope: Scope) -> str:
    parts = [f"language: {scope.language}", f"region focus: {scope.region}"]
    if scope.time_window_start or scope.time_window_end:
        start = scope.time_window_start or "earliest available"
        end = scope.time_window_end or "present"
        parts.append(f"time window: {start} to {end}")
    else:
        parts.append("time window: all available history")
    return "; ".join(parts)


def _instance_from_dict(d: dict) -> Optional[AttestedInstance]:
    try:
        return AttestedInstance(
            date=str(d.get("date", "")).strip(),
            source_url=str(d.get("source_url", "")).strip(),
            source_title=str(d.get("source_title", "")).strip(),
            author=str(d.get("author", "")).strip(),
            lexical_form_seen=str(d.get("lexical_form_seen", "")).strip(),
            exact_quote=str(d.get("exact_quote", "")).strip(),
            confidence=float(d.get("confidence", 0.5)),
            evidence=str(d.get("evidence", "")).strip(),
        )
    except (TypeError, ValueError):
        return None


def _response_text(response) -> str:
    """Concatenate all text blocks from a Messages API response."""
    out = []
    for block in response.content:
        if getattr(block, "type", "") == "text":
            out.append(block.text)
    return "".join(out)


async def _create_with_retry(client, max_attempts: int = 5, **kwargs):
    """Wrap messages.create with backoff on 429 (rate-limited) and 529 (overloaded).
    Matches the retry posture used in agent.py."""
    delays = [30, 60, 90, 120, 150]
    for attempt in range(max_attempts):
        try:
            return await client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status not in (429, 529) or attempt == max_attempts - 1:
                raise
            wait = delays[min(attempt, len(delays) - 1)]
            print(
                f"[anthropic {status}; retrying in {wait}s "
                f"({attempt + 1}/{max_attempts})]",
                flush=True,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("retry loop exited unexpectedly")


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class FingerprintGenerator:
    """Generates NarrativeFingerprint objects via Claude."""

    def __init__(self, client: Optional[anthropic.AsyncAnthropic] = None):
        self.client = client or anthropic.AsyncAnthropic()

    async def generate_lexical(self, claim_text: str, context: str = "") -> LexicalLayer:
        user_content = f"CLAIM:\n{claim_text}"
        if context:
            user_content += f"\n\nCONTEXT (where the claim appeared):\n{context}"

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=1024,
            system=[{"type": "text", "text": LEXICAL_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))
        canonical = (data.get("canonical_phrase") or "").strip() or claim_text.strip()
        variants = [v.strip() for v in (data.get("phrase_variants") or []) if v and v.strip()]
        ngrams = [n.strip() for n in (data.get("diagnostic_ngrams") or []) if n and n.strip()]

        return LexicalLayer(
            canonical_phrase=canonical,
            phrase_variants=variants,
            diagnostic_ngrams=ngrams,
            stopword_stripped_signature=_stopword_stripped(canonical),
        )

    async def search_earliest_uses(
        self, lexical: LexicalLayer, scope: Scope
    ) -> list[AttestedInstance]:
        user_content = (
            f"NARRATIVE FRAMING TO TRACE:\n"
            f"  Canonical: {lexical.canonical_phrase}\n"
            f"  Variants: {', '.join(lexical.phrase_variants)}\n"
            f"  Diagnostic n-grams: {', '.join(lexical.diagnostic_ngrams)}\n\n"
            f"SCOPE: {_scope_clause(scope)}\n\n"
            "Find the earliest documented uses across as much of the internet "
            "and as far back as you can. Prioritize older results."
        )

        response = await _create_with_retry(
            self.client,
            model=SONNET,
            max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": EARLIEST_USE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))
        instances = []
        for raw in data.get("instances", []):
            inst = _instance_from_dict(raw)
            if inst is not None:
                instances.append(inst)
        instances.sort(key=lambda a: a.date or "9999")
        return instances

    async def adversarial_verify(
        self, lexical: LexicalLayer, proposed_date: str, scope: Scope
    ) -> tuple[list[AttestedInstance], str]:
        system_text = ADVERSARIAL_SYSTEM.format(proposed_date=proposed_date)

        user_content = (
            f"NARRATIVE FRAMING:\n"
            f"  Canonical: {lexical.canonical_phrase}\n"
            f"  Variants: {', '.join(lexical.phrase_variants)}\n"
            f"  Diagnostic n-grams: {', '.join(lexical.diagnostic_ngrams)}\n\n"
            f"PROPOSED EARLIEST DATE: {proposed_date}\n\n"
            f"SCOPE: {_scope_clause(scope)}\n\n"
            "Your job: find anything from BEFORE the proposed date. If you "
            "find nothing credible, say so plainly — a clean negative result "
            "is itself valuable."
        )

        response = await _create_with_retry(
            self.client,
            model=SONNET,
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": system_text,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))
        earlier = []
        for raw in data.get("earlier_instances", []):
            inst = _instance_from_dict(raw)
            if inst is None:
                continue
            # Only keep instances that are genuinely before the proposed date
            if inst.date and inst.date < proposed_date:
                earlier.append(inst)
        earlier.sort(key=lambda a: a.date)
        return earlier, str(data.get("verification_notes", "")).strip()

    async def generate_genealogy(
        self,
        lexical: LexicalLayer,
        scope: Scope,
        polygenesis_window_days: int = 7,
    ) -> GenealogyLayer:
        instances = await self.search_earliest_uses(lexical, scope)

        if not instances:
            return GenealogyLayer(
                status=GenealogyStatus.UNKNOWN,
                adversarial_check_performed=False,
            )

        earliest = instances[0]
        earlier_instances, adv_notes = await self.adversarial_verify(
            lexical, earliest.date or "9999-12-31", scope
        )

        if earlier_instances:
            # The adversarial pass found predecessors — they become the new
            # earliest. Existing instances are preserved as later echoes.
            instances = earlier_instances + instances
            earliest = instances[0]

        # Polygenesis: are there other instances within a small window of the
        # earliest? That suggests parallel independent emergence rather than a
        # single origin (e.g., reactions to a real-world event).
        parallel = []
        try:
            earliest_d = date.fromisoformat(earliest.date)
            for inst in instances:
                if inst.instance_id == earliest.instance_id:
                    continue
                try:
                    d = date.fromisoformat(inst.date)
                    delta_days = abs((d - earliest_d).days)
                    if delta_days <= polygenesis_window_days:
                        parallel.append(inst)
                except ValueError:
                    continue
        except ValueError:
            pass

        if parallel:
            status = GenealogyStatus.MULTIPLE_INDEPENDENT
            parallel_ids = [earliest.instance_id] + [p.instance_id for p in parallel]
        elif earliest.confidence < 0.4:
            status = GenealogyStatus.DIFFUSE
            parallel_ids = []
        else:
            status = GenealogyStatus.SINGLE_ORIGIN
            parallel_ids = []

        return GenealogyLayer(
            status=status,
            first_attested_date=earliest.date,
            first_attested_source=earliest.source_url,
            attestation_confidence=earliest.confidence,
            primary_origin_id=earliest.instance_id,
            parallel_origin_ids=parallel_ids if status == GenealogyStatus.MULTIPLE_INDEPENDENT else [],
            attestation_log=instances,
            adversarial_check_performed=True,
            adversarial_notes=adv_notes,
        )

    async def generate_fingerprint(
        self,
        claim_text: str,
        scope: Optional[Scope] = None,
        context: str = "",
    ) -> NarrativeFingerprint:
        scope = scope or Scope()
        lexical = await self.generate_lexical(claim_text, context=context)
        genealogy = await self.generate_genealogy(lexical, scope)
        return NarrativeFingerprint(
            scope=scope,
            lexical=lexical,
            genealogy=genealogy,
            attribution=Attribution(source="ai", model=SONNET),
        )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class FingerprintStore:
    """JSON-backed fingerprint persistence + lexical-signature dedup.

    Layout:
        <base_dir>/index.json            id → searchable metadata
        <base_dir>/<fingerprint_id>.json full fingerprint payload
    """

    def __init__(self, base_dir: str = "fingerprints"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self.index = self._load_index()

    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_index(self):
        self.index_path.write_text(
            json.dumps(self.index, indent=2, default=str), encoding="utf-8"
        )

    def save(self, fp: NarrativeFingerprint) -> str:
        path = self.base_dir / f"{fp.fingerprint_id}.json"
        path.write_text(fp.to_json(), encoding="utf-8")

        self.index[fp.fingerprint_id] = {
            "canonical_phrase": fp.lexical.canonical_phrase,
            "diagnostic_ngrams": fp.lexical.diagnostic_ngrams,
            "stopword_stripped_signature": fp.lexical.stopword_stripped_signature,
            "first_attested_date": fp.genealogy.first_attested_date,
            "first_attested_source": fp.genealogy.first_attested_source,
            "genealogy_status": fp.genealogy.status.value,
            "created_at": fp.created_at,
            "last_updated": fp.last_updated,
            "scope": fp.scope.to_dict(),
        }
        self._save_index()
        return fp.fingerprint_id

    def load_raw(self, fingerprint_id: str) -> Optional[dict]:
        # v1: return the raw payload. Full from_dict reconstruction with
        # enum/dataclass round-trip is deferred until something needs it.
        path = self.base_dir / f"{fingerprint_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def find_matching(self, lexical: LexicalLayer) -> Optional[str]:
        """Return an existing fingerprint_id if its lexical signature matches.
        v1 matching: exact stopword_stripped_signature match, or ≥2 shared
        diagnostic n-grams (case-insensitive)."""
        sig = lexical.stopword_stripped_signature
        new_ngrams = {n.lower() for n in lexical.diagnostic_ngrams}

        for fp_id, meta in self.index.items():
            if sig and meta.get("stopword_stripped_signature") == sig:
                return fp_id
            existing = {n.lower() for n in meta.get("diagnostic_ngrams", [])}
            if len(new_ngrams & existing) >= 2:
                return fp_id
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _cli(args):
    gen = FingerprintGenerator()
    scope = Scope(
        language="en",
        region=args.region,
        time_window_start=args.from_date or "",
        time_window_end=args.to_date or "",
    )

    if args.lexical_only:
        lex = await gen.generate_lexical(args.claim, context=args.context or "")
        print(json.dumps(lex.to_dict(), indent=2))
        return

    fp = await gen.generate_fingerprint(
        args.claim, scope=scope, context=args.context or ""
    )

    if args.save:
        store = FingerprintStore(args.store_dir)
        existing = store.find_matching(fp.lexical)
        if existing and not args.force:
            print(f"[matched existing fingerprint: {existing} — pass --force to write anyway]")
            return
        fp_id = store.save(fp)
        print(f"[saved fingerprint: {fp_id}]")

    print(fp.to_json())


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate a NarrativeFingerprint for a claim."
    )
    parser.add_argument("claim", help="The claim or narrative to fingerprint")
    parser.add_argument("--context", help="Optional context where the claim appeared")
    parser.add_argument("--region", default="US",
                        help='Regional focus for scope (default: "US")')
    parser.add_argument("--from-date", default="",
                        help="Time window start (ISO date)")
    parser.add_argument("--to-date", default="",
                        help="Time window end (ISO date)")
    parser.add_argument("--lexical-only", action="store_true",
                        help="Generate only the L1 lexical layer (no web search)")
    parser.add_argument("--save", action="store_true",
                        help="Save the fingerprint to the store")
    parser.add_argument("--store-dir", default="fingerprints",
                        help="Directory for the fingerprint store")
    parser.add_argument("--force", action="store_true",
                        help="Save even if an existing match is found")
    args = parser.parse_args()
    asyncio.run(_cli(args))


if __name__ == "__main__":
    main()
