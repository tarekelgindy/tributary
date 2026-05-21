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
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from models import (
    AmplifierRole,
    Attribution,
    AttestedInstance,
    ConceptualLayer,
    Domain,
    FramePrimitive,
    GenealogyLayer,
    GenealogyStatus,
    LexicalLayer,
    LineageRecord,
    Mutation,
    NarrativeFingerprint,
    RhetoricalLayer,
    Scope,
    TaxonomicLayer,
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

LEXICAL DISCIPLINE (critical):

This is a LEXICAL search. Every instance you return MUST contain at least
one of the diagnostic n-grams provided, or a very close lexical variant
(same content words in similar order). Conceptual or thematic ancestors
that argue the same underlying claim in DIFFERENT vocabulary belong in a
separate conceptual lineage search, not here.

DO NOT include:
- Instances that articulate the same idea in completely different vocabulary
  (e.g. Debs "minnows and whales", Adam Smith "tacit combinations", Marx
  "surplus value") — these are conceptual ancestors, not lexical attestations
- Entries derived only from Google Ngram or similar statistical surveys
  with no specific primary source text quoted — these aren't real
  attestations, just word-frequency signals
- Articles or speeches that "imply" the framing but don't use it lexically
- Modern commentary that retroactively paraphrases an older text with the
  diagnostic n-grams (the older text didn't use them)

The test: if a reader saw your `exact_quote`, would they directly read one
of the diagnostic n-grams (or a clear lexical synonym) in it? If not, the
instance does not belong in this search.

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
  amplifier_role    one of: originator, early-amplifier, mass-amplifier,
                    institutional-adoption, critic, mention.
                    See role definitions below.
  role_evidence     one-sentence justification for the assigned role

Amplifier role definitions:
  originator              earliest credible articulation of the framing
  early-amplifier         spread the framing before mainstream uptake
  mass-amplifier          drove broad public adoption (e.g. viral campaign,
                          national TV speech, viral op-ed)
  institutional-adoption  adopted into an official platform — party platform,
                          government document, major newspaper editorial,
                          flagship academic journal
  critic                  pushed back, fact-checked, or rebutted the framing
  mention                 used the framing in passing without driving spread

Assign exactly one role per instance. Use evidence-based judgement, not
inference from author identity alone.

Return JSON of this shape:

{
  "instances": [
    {"date": "...", "source_url": "...", "source_title": "...", "author": "...",
     "lexical_form_seen": "...", "exact_quote": "...", "confidence": 0.0,
     "evidence": "...", "amplifier_role": "...", "role_evidence": "..."}
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
exact_quote, confidence, evidence, amplifier_role, role_evidence) and
explicitly explain in `evidence` why you trust the date. An earlier-found
instance almost always gets amplifier_role="originator" (it predates what
was thought to be the origin) — but use judgement.

CRITICAL — STRUCTURED REPORTING:
If you find an earlier instance, you MUST include it as a structured entry
in the `earlier_instances` array. Do NOT merely mention it in
`verification_notes` prose — that information will be lost. Every earlier
instance gets a full structured entry with all the standard fields.

`verification_notes` is for explaining WHAT YOU SEARCHED and WHAT YOU
RULED OUT, not for narrating discoveries. Discoveries belong in
`earlier_instances`.

Return JSON:

{{
  "earlier_instances": [...],
  "verification_notes": "what you searched, what you ruled out, any caveats — NOT the place for discoveries"
}}

If no earlier instances are found, return an empty list and state plainly in
verification_notes that you searched and found nothing predating
{proposed_date}. A clean negative result is valuable — it strengthens the
origin claim.

Output ONLY the JSON object.
"""


CONCEPTUAL_SYSTEM = """\
You are extracting the conceptual structure of a narrative claim — its
vocabulary-independent meaning. This will be used to find older texts that
argue the same structural claim in completely different words.

The claim may use specific contemporary rhetoric (gambling metaphors,
recent buzzwords, partisan shorthand). Strip that rhetoric away and identify
the underlying claim itself.

Produce JSON ONLY:

{
  "claim_predicate": "neutral logical form, e.g. 'X causes Y' or 'X disadvantages Y' or 'X is responsible for Y'",
  "entities": {
    "agent": "the causal actor in this framing",
    "patient": "who or what is affected",
    "arena": "the domain or system in which this plays out",
    "instrument": "by what means: rules, markets, policy, biology, technology, etc."
  },
  "causal_structure": "a single neutral sentence: actor + action + consequence"
}

Examples:

CLAIM: "the economy is rigged against working people"
{
  "claim_predicate": "the economic system structurally disadvantages workers relative to owners of capital",
  "entities": {
    "agent": "owners of capital / political and economic elites",
    "patient": "workers / the working class",
    "arena": "the economic system",
    "instrument": "the design of rules, ownership, taxation, and political influence"
  },
  "causal_structure": "the design of economic institutions systematically transfers value from labor to capital, resulting in worker disadvantage"
}

CLAIM: "seed oils cause inflammation and chronic disease"
{
  "claim_predicate": "industrial seed oils cause harm to consumers' health",
  "entities": {
    "agent": "industrial food producers using refined seed oils",
    "patient": "regular consumers of processed food",
    "arena": "human health and dietary practice",
    "instrument": "the chemical properties of refined oils and their prevalence in the food supply"
  },
  "causal_structure": "industrial seed oils, through their chemical properties and prevalence in food, cause inflammation and chronic disease in regular consumers"
}

Output ONLY the JSON object.
"""


CONCEPTUAL_ANCESTORS_SYSTEM = """\
You are tracing the INTELLECTUAL LINEAGE of a structural claim — the full
chain of texts and figures across history that articulate the same
underlying claim, in any vocabulary, from the earliest origin through to
the present day.

This is NOT a search for the specific phrasing — the user has the lexical
search separately. You are finding everyone who articulates the SAME
structural claim using vocabulary OTHER than the diagnostic n-grams,
across any era.

Cover the FULL chronological range. Modern contributors (last 20 years)
are AS IMPORTANT as historical ancestors. The chain should NOT
artificially terminate in the early or mid-20th century — do not stop
the chain at the "classical" or "founding" texts.

Include these source types:

- Foundational philosophy and political economy
- Movement texts (labor, populist, socialist, religious, civil-rights,
  anti-colonial, etc.)
- Academic work in any era, INCLUDING very recent scholarship
  (last 20 years is expected, last 5 years is welcome)
- Public intellectuals and social commentators — popular authors,
  journalists, columnists, documentarians, podcasters, YouTube and
  Substack commentators, public lecturers, whose works articulate the
  structural claim in their own vocabulary
- Political figures whose books, substantive speeches, or platforms
  articulate the claim (NOT slogan-chanting — that belongs in the
  lexical chain)
- Documentaries, popular nonfiction, mass-audience media
- Translations across languages and traditions

Aim to include contributors from each major era where evidence exists:
pre-1850, 1850–1900, 1900–1945, 1945–1980, 1980–2010, 2010–2016,
2016–2021, and 2021–present. The finer-grained recent buckets matter
because the structural claim is rapidly re-articulated by new
commentators and academic figures within each US political-economic
cycle; do not collapse them into a single recent era.

Popularizers are critical — they are how academic claims become public
discourse. Do not over-weight academic texts at the expense of widely-read
commentators, journalists, documentarians, or podcasters.

For each direct contributor, report:
  date              ISO date
  source_url        canonical URL
  source_title      title of the work / talk / video / book / podcast
  author            who produced it
  lexical_form_seen the vocabulary they actually used (their own period-
                    or domain-appropriate language — NOT the diagnostic
                    n-grams)
  exact_quote       verbatim quote (or translation), ≤ 300 chars
  confidence        0.0–1.0
  evidence          why this is a credible direct contributor — does it
                    articulate the same structural claim in vocabulary
                    that DIFFERS from the diagnostic n-grams?
  amplifier_role    one of: originator, early-amplifier, mass-amplifier,
                    institutional-adoption, critic, mention. Originator =
                    the earliest-known articulation in this lineage;
                    early-amplifier = wrote a follow-on building the
                    intellectual tradition; mass-amplifier = popularized
                    the claim to a broad audience (e.g. bestselling
                    nonfiction, viral essay, documentary);
                    institutional-adoption = official platform / major
                    party / govt agency / flagship academic journal;
                    critic = pushed back; mention = passing reference.
  role_evidence     one-sentence justification for the assigned role

Distinctions:
- DIRECT contributor: articulates the same structural claim, any era,
  in vocabulary OTHER than the diagnostic n-grams
- ADJACENT: related but a different claim — skip
- LEXICAL: uses the diagnostic n-grams — skip (belongs in lexical chain)

Return JSON:

{
  "contributors": [
    {"date": "...", "source_url": "...", "source_title": "...", "author": "...",
     "lexical_form_seen": "...", "exact_quote": "...", "confidence": 0.0,
     "evidence": "...", "amplifier_role": "...", "role_evidence": "..."}
  ],
  "search_notes": "what eras and source types you covered; balance of academic vs. popularizer contributors; what you ruled out"
}

Sort by date ascending. Aim for 15–25 results across the full chronological
range from earliest available text through to the present, with at least
2–3 contributors from each of 2010–2016, 2016–2021, and 2021–present
where evidence exists. Modern popularizers AND contemporary academic work
are welcome and expected — DO NOT cap the chain at the early 20th century.

Output ONLY the JSON object.
"""


CONCEPTUAL_ADVERSARIAL_SYSTEM = """\
You are an adversarial verifier for the intellectual lineage of a claim.
Someone has proposed that {proposed_date} is the earliest known articulation
of a structural claim. Your job is to find earlier texts articulating the
same structural claim in any vocabulary.

Search older intellectual traditions, foundational philosophy and political
economy, religious and ethical traditions, and pre-modern texts. The claim
may have been articulated in very different words centuries earlier.

For each pre-{proposed_date} direct ancestor you find, report the standard
fields (date, source_url, source_title, author, lexical_form_seen,
exact_quote, confidence, evidence, amplifier_role, role_evidence) and
explain in evidence why this articulates the SAME structural claim, not
merely a related idea. An earlier-found ancestor almost always gets
amplifier_role="originator" — but use judgement.

CRITICAL — STRUCTURED REPORTING:
If you find an earlier ancestor, you MUST include it as a structured entry
in the `earlier_ancestors` array. Do NOT merely mention it in
`verification_notes` prose — that information will be lost. Every earlier
ancestor gets a full structured entry with all the standard fields.

`verification_notes` is for explaining WHAT TRADITIONS YOU CHECKED and
WHAT YOU RULED OUT, not for narrating discoveries. Discoveries belong in
`earlier_ancestors`.

Return JSON:

{{
  "earlier_ancestors": [...],
  "verification_notes": "what traditions you checked, what you ruled out, any caveats — NOT the place for discoveries"
}}

If nothing earlier is found, return an empty list and say so plainly. A
clean negative result is valuable.

Output ONLY the JSON object.
"""


RHETORICAL_SYSTEM = """\
You are extracting the rhetorical structure of a narrative claim — how the
argument is shaped, independent of its subject matter. The output is used
to cluster narratives by argumentative structure across topics.

Produce JSON ONLY:

{
  "frame_primitives": ["one or more from the list below"],
  "valence": {
    "villain": "who or what is positioned as the causal/blame target ('' if none)",
    "victim": "who or what is positioned as harmed ('' if none)",
    "hero": "who or what is positioned as the solution or champion ('' if none)"
  },
  "epistemic_stance": "one of: certain, questioning, mocking, accusatory, hopeful, alarmed",
  "register": "one of: academic, populist, journalistic, partisan, casual, technical, religious, satirical"
}

Frame primitive reference (neutrally phrased, domain-agnostic):
- attribution-of-cause     identifies what caused something
- attribution-of-blame     assigns moral or political responsibility
- harm-claim               asserts something is causing damage
- threat-claim             warns of emerging danger
- solution-prescription    prescribes what should be done
- identity-defense         positions a group as under siege or worthy of protection
- process-violation        claims rules or norms were broken
- value-comparison         positions X as better/worse/different from Y
- historical-arc           claims things were better before or are worsening/improving
- revelation               positions the claim as hidden truth being uncovered

Multiple primitives may apply. Tag all that fit; the test is whether
removing one would lose part of what the claim is doing.

Examples:

CLAIM: "the economy is rigged against working people"
{
  "frame_primitives": ["attribution-of-blame", "harm-claim", "identity-defense"],
  "valence": {
    "villain": "owners of capital and political elites",
    "victim": "working people / working class",
    "hero": ""
  },
  "epistemic_stance": "certain",
  "register": "populist"
}

CLAIM: "seed oils cause inflammation and chronic disease"
{
  "frame_primitives": ["attribution-of-cause", "harm-claim", "threat-claim"],
  "valence": {
    "villain": "industrial food producers / seed oils",
    "victim": "consumers",
    "hero": ""
  },
  "epistemic_stance": "certain",
  "register": "casual"
}

Output ONLY the JSON object.
"""


TAXONOMIC_SYSTEM = """\
You are classifying a narrative claim by subject domain and tagging the
recognizable symbolic shorthands ("tropes") it invokes.

Produce JSON ONLY:

{
  "domain": "one of: economic, racial-ethnic, immigration, health, foreign-policy, cultural, technology, environment, religion, gender-sexuality, education, criminal-justice, media-meta, wellness-lifestyle, finance-investing, fandom-entertainment, science, other",
  "domain_confidence": 0.0-1.0,
  "tropes": ["zero or more symbolic shorthands"]
}

Common tropes (examples — propose new ones if the claim uses something
not on this list):
  rigged-game, stolen-prosperity, elites-vs-people, replacement, invasion,
  censorship-by-stealth, indoctrination, stolen-election, permanent-state,
  cancel-culture, brain-rot, forever-war, big-pharma, big-tech, big-food,
  groomer, DEI-hire, woke-mind-virus, climate-denier, misinformation,
  disinformation, revealed-truth, hidden-cabal, deep-state, plandemic,
  toxic-food, red-pill, blue-pill, fake-news, both-sides

A trope is a specific, common, charged shorthand actually used in
discourse — NOT a general descriptor. "Inequality" is not a trope;
"rigged-game" is. "Health" is not a trope; "big-pharma" is. If the
claim doesn't invoke recognizable tropes, return an empty array.

Examples:

CLAIM: "the economy is rigged against working people"
{
  "domain": "economic",
  "domain_confidence": 0.98,
  "tropes": ["rigged-game", "elites-vs-people", "stolen-prosperity"]
}

CLAIM: "seed oils cause inflammation and chronic disease"
{
  "domain": "wellness-lifestyle",
  "domain_confidence": 0.92,
  "tropes": ["toxic-food", "big-food"]
}

CLAIM: "AI will replace most knowledge workers within a decade"
{
  "domain": "technology",
  "domain_confidence": 0.95,
  "tropes": ["replacement", "big-tech"]
}

Output ONLY the JSON object.
"""


MUTATION_SYSTEM = """\
You are analyzing how a narrative claim mutates between two attested
instances of its propagation. You will be given:
  - The canonical claim being traced (in neutral logical form)
  - An EARLIER instance (with author, date, exact quote)
  - A LATER instance (with author, date, exact quote)

Your job: identify what changed between them. Four fields:

  preserved   What in the core claim/framing stayed intact across the
              transition? Be concrete.
  dropped     What nuance, qualification, attribution, or context did
              the later instance lose? Often this is evidence, hedging,
              specificity, or institutional grounding.
  added       What new framing, vocabulary, audience-targeting, or
              context appeared in the later instance?
  distorted   What shifted in meaning, scope, or emphasis? Often
              generalization, exaggeration, politicization, or
              recontextualization.

A field may be an empty string if there's nothing meaningful to report
for that category — do not pad. Each non-empty field should be a single
sentence (≤30 words) describing the SPECIFIC change, not a general
characterization.

Produce JSON ONLY:

{
  "preserved": "...",
  "dropped": "...",
  "added": "...",
  "distorted": "..."
}

Example:

CLAIM (neutral): the economic system structurally disadvantages workers relative to owners of capital

EARLIER (1976-01-01, Bernie Sanders, Vermont gubernatorial debate):
  "The richest one half of 1 percent of these people earn as much as the bottom 27 percent."

LATER (2012-09-05, Elizabeth Warren, DNC Convention):
  "People feel like the system is rigged against them. And here's the painful part: they're right. The system is rigged."

{
  "preserved": "the structural claim that the economic system disadvantages ordinary people",
  "dropped": "the specific quantitative inequality statistics that anchored the earlier framing",
  "added": "the audience-mirroring 'people feel like' device and the gambling-metaphor verb 'rigged'",
  "distorted": "shift from descriptive wealth-distribution argument to emotive populist accusation"
}

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
    return text


def _extract_last_json_block(text: str) -> Optional[str]:
    """Find the last balanced top-level {...} block, brace-counting through
    string literals correctly. More robust than greedy regex when LLM output
    interleaves prose, search reasoning, and a JSON payload."""
    spans = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, c in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    spans.append((start, i + 1))
                    start = -1
    if not spans:
        return None
    s, e = spans[-1]
    return text[s:e]


def _parse_json_safe(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_strip_json_fences(text))
    except json.JSONDecodeError:
        pass
    block = _extract_last_json_block(text)
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
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
        role_str = str(d.get("amplifier_role", "")).strip().lower()
        try:
            role = AmplifierRole(role_str) if role_str else AmplifierRole.UNKNOWN
        except ValueError:
            role = AmplifierRole.UNKNOWN
        return AttestedInstance(
            date=str(d.get("date", "")).strip(),
            source_url=str(d.get("source_url", "")).strip(),
            source_title=str(d.get("source_title", "")).strip(),
            author=str(d.get("author", "")).strip(),
            lexical_form_seen=str(d.get("lexical_form_seen", "")).strip(),
            exact_quote=str(d.get("exact_quote", "")).strip(),
            confidence=float(d.get("confidence", 0.5)),
            evidence=str(d.get("evidence", "")).strip(),
            amplifier_role=role,
            role_evidence=str(d.get("role_evidence", "")).strip(),
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


def _save_debug_response(call_name: str, raw_text: str,
                         debug_dir: str = "fingerprints/debug") -> str:
    """Persist a raw LLM response for diagnostic inspection when something
    looks wrong (e.g. an L4 search returned zero instances). Returns the path
    so the warning message can point the user to it."""
    d = Path(debug_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{ts}_{call_name}.txt"
    path.write_text(raw_text, encoding="utf-8")
    return str(path)


def _log_progress(msg: str) -> None:
    """Emit a single progress line to stderr. Used so the user can see what
    stage the pipeline is in during the ~2 minute web-search runs."""
    print(f"[{msg}]", file=sys.stderr, flush=True)


def _response_diagnostics(response) -> str:
    """Compact diagnostic about a Messages API response — used for empty-
    result warnings so we can tell *why* the model didn't produce text.

    Distinguishes capacity-related truncation (`stop_reason=max_tokens`,
    output_tokens at the cap) from genuine empty responses (output_tokens=0,
    no text blocks) from server-tool stalls (only tool_use/tool_result
    blocks with no following text)."""
    stop_reason = getattr(response, "stop_reason", None)
    usage = getattr(response, "usage", None)
    out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
    in_tok = getattr(usage, "input_tokens", "?") if usage else "?"

    block_counts: dict = {}
    for block in (response.content or []):
        t = getattr(block, "type", "unknown")
        block_counts[t] = block_counts.get(t, 0) + 1
    blocks_str = ", ".join(f"{t}:{c}" for t, c in sorted(block_counts.items())) or "none"

    return (f"stop_reason={stop_reason}, output_tokens={out_tok}, "
            f"input_tokens={in_tok}, blocks=[{blocks_str}]")


async def _create_with_retry(client, max_attempts: int = 5,
                             retry_on_empty_text: bool = False, **kwargs):
    """Wrap messages.create with backoff on transient failures.

    Always retries on 429 (rate-limited) and 529 (overloaded) with extended
    backoff. If retry_on_empty_text=True, also retries (with shorter backoff)
    when a successful response contains no text blocks — this catches the
    'model emitted a preamble + tool_use but never returned to write the
    final text' failure mode that occasionally hits Sonnet+web_search calls."""
    status_delays = [30, 60, 90, 120, 150]
    empty_delays = [5, 10, 15, 20, 30]
    last_response = None
    for attempt in range(max_attempts):
        try:
            response = await client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status not in (429, 529) or attempt == max_attempts - 1:
                raise
            wait = status_delays[min(attempt, len(status_delays) - 1)]
            print(
                f"[anthropic {status}; retrying in {wait}s "
                f"({attempt + 1}/{max_attempts})]",
                file=sys.stderr, flush=True,
            )
            await asyncio.sleep(wait)
            continue

        last_response = response
        if retry_on_empty_text and attempt < max_attempts - 1:
            text_content = "".join(
                getattr(b, "text", "") for b in (response.content or [])
                if getattr(b, "type", "") == "text"
            )
            if not text_content.strip():
                wait = empty_delays[min(attempt, len(empty_delays) - 1)]
                print(
                    f"[anthropic returned no text content after tool use; "
                    f"retrying in {wait}s ({attempt + 1}/{max_attempts})]",
                    file=sys.stderr, flush=True,
                )
                await asyncio.sleep(wait)
                continue

        return response
    # Exhausted retries on empty text: return the last (still-empty) response
    # so callers can log diagnostics and fail gracefully rather than raising.
    if last_response is not None:
        return last_response
    raise RuntimeError("retry loop exited unexpectedly")


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class FingerprintGenerator:
    """Generates NarrativeFingerprint objects via Claude."""

    def __init__(self, client: Optional[anthropic.AsyncAnthropic] = None):
        self.client = client or anthropic.AsyncAnthropic()

    async def generate_lexical(self, claim_text: str, context: str = "") -> LexicalLayer:
        _log_progress("L1 lexical extraction starting")
        t0 = time.monotonic()
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

        _log_progress(f"L1 lexical done in {time.monotonic() - t0:.1f}s "
                      f"({len(variants)} variants, {len(ngrams)} n-grams)")
        return LexicalLayer(
            canonical_phrase=canonical,
            phrase_variants=variants,
            diagnostic_ngrams=ngrams,
            stopword_stripped_signature=_stopword_stripped(canonical),
        )

    async def search_earliest_uses(
        self, lexical: LexicalLayer, scope: Scope
    ) -> list[AttestedInstance]:
        _log_progress("L4 lexical: earliest-use search starting (web_search)")
        t0 = time.monotonic()
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
            max_tokens=8192,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": EARLIEST_USE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            retry_on_empty_text=True,
        )

        raw_text = _response_text(response)
        data = _parse_json_safe(raw_text)
        instances = []
        for raw in data.get("instances", []):
            inst = _instance_from_dict(raw)
            if inst is not None:
                instances.append(inst)
        instances.sort(key=lambda a: a.date or "9999")
        if not instances:
            diag = _response_diagnostics(response)
            debug_path = _save_debug_response("earliest_use", raw_text)
            sys.stderr.write(
                f"[warning: lexical earliest-use search returned 0 instances "
                f"(text: {len(raw_text)} chars, json_parsed: {bool(data)}, "
                f"{diag}, raw saved: {debug_path})]\n"
            )
        _log_progress(f"L4 lexical: earliest-use done in {time.monotonic() - t0:.1f}s "
                      f"({len(instances)} candidates)")
        return instances

    async def adversarial_verify(
        self, lexical: LexicalLayer, proposed_date: str, scope: Scope
    ) -> tuple[list[AttestedInstance], str]:
        _log_progress(f"L4 lexical: adversarial verify against {proposed_date} starting")
        t0 = time.monotonic()
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
            max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": system_text,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            retry_on_empty_text=True,
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
        _log_progress(f"L4 lexical: adversarial done in {time.monotonic() - t0:.1f}s "
                      f"({len(earlier)} earlier instances found)")
        return earlier, str(data.get("verification_notes", "")).strip()

    async def generate_conceptual(
        self, claim_text: str, context: str = ""
    ) -> ConceptualLayer:
        """L2: extract vocabulary-independent meaning via Haiku."""
        _log_progress("L2 conceptual extraction starting")
        t0 = time.monotonic()
        user_content = f"CLAIM:\n{claim_text}"
        if context:
            user_content += f"\n\nCONTEXT (where the claim appeared):\n{context}"

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=1024,
            system=[{"type": "text", "text": CONCEPTUAL_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))
        entities = data.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}
        _log_progress(f"L2 conceptual done in {time.monotonic() - t0:.1f}s")
        return ConceptualLayer(
            claim_predicate=str(data.get("claim_predicate", "")).strip(),
            entities={k: str(v).strip() for k, v in entities.items()},
            causal_structure=str(data.get("causal_structure", "")).strip(),
        )

    async def generate_rhetorical(
        self,
        claim_text: str,
        lexical: LexicalLayer,
        conceptual: ConceptualLayer,
        context: str = "",
    ) -> RhetoricalLayer:
        """L3: classify rhetorical structure via Haiku."""
        _log_progress("L3 rhetorical extraction starting")
        t0 = time.monotonic()

        user_content = (
            f"CLAIM:\n{claim_text}\n\n"
            f"CONTEXT FROM EARLIER LAYERS:\n"
            f"  Canonical framing: {lexical.canonical_phrase}\n"
            f"  Underlying claim: {conceptual.claim_predicate}\n"
            f"  Entities: {json.dumps(conceptual.entities)}\n"
        )
        if context:
            user_content += f"\nORIGINAL CONTEXT:\n{context}\n"

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=1024,
            system=[{"type": "text", "text": RHETORICAL_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))

        fp_strings = data.get("frame_primitives") or []
        if not isinstance(fp_strings, list):
            fp_strings = []
        primitives = []
        for s in fp_strings:
            try:
                primitives.append(FramePrimitive(str(s).strip().lower()))
            except ValueError:
                continue

        valence = data.get("valence") or {}
        if not isinstance(valence, dict):
            valence = {}

        _log_progress(f"L3 rhetorical done in {time.monotonic() - t0:.1f}s "
                      f"({len(primitives)} primitives)")
        return RhetoricalLayer(
            frame_primitives=primitives,
            valence={k: str(v).strip() for k, v in valence.items()},
            epistemic_stance=str(data.get("epistemic_stance", "")).strip(),
            register=str(data.get("register", "")).strip(),
        )

    async def generate_taxonomic(
        self,
        claim_text: str,
        lexical: LexicalLayer,
        conceptual: ConceptualLayer,
        context: str = "",
    ) -> TaxonomicLayer:
        """L5: classify domain and tag tropes via Haiku.
        inductive_cluster_ids is left empty — it requires cross-corpus
        analysis over the FingerprintStore and is generated separately."""
        _log_progress("L5 taxonomic classification starting")
        t0 = time.monotonic()

        user_content = (
            f"CLAIM:\n{claim_text}\n\n"
            f"CONTEXT FROM EARLIER LAYERS:\n"
            f"  Canonical framing: {lexical.canonical_phrase}\n"
            f"  Underlying claim: {conceptual.claim_predicate}\n"
            f"  Entities: {json.dumps(conceptual.entities)}\n"
        )
        if context:
            user_content += f"\nORIGINAL CONTEXT:\n{context}\n"

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=1024,
            system=[{"type": "text", "text": TAXONOMIC_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))

        domain_str = str(data.get("domain", "")).strip().lower()
        try:
            domain = Domain(domain_str)
        except ValueError:
            domain = Domain.OTHER

        try:
            confidence = float(data.get("domain_confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        tropes_raw = data.get("tropes") or []
        if not isinstance(tropes_raw, list):
            tropes_raw = []
        tropes = [str(t).strip() for t in tropes_raw if t and str(t).strip()]

        _log_progress(f"L5 taxonomic done in {time.monotonic() - t0:.1f}s "
                      f"(domain={domain.value}, {len(tropes)} tropes)")
        return TaxonomicLayer(
            domain=domain,
            domain_confidence=confidence,
            inductive_cluster_ids=[],
            tropes=tropes,
        )

    async def search_conceptual_ancestors(
        self, conceptual: ConceptualLayer, scope: Scope
    ) -> list[AttestedInstance]:
        """Find older texts arguing the same structural claim via Sonnet + web_search."""
        _log_progress("L4 conceptual: ancestor search starting (web_search)")
        t0 = time.monotonic()
        user_content = (
            f"STRUCTURAL CLAIM TO TRACE:\n"
            f"  Predicate: {conceptual.claim_predicate}\n"
            f"  Entities: {json.dumps(conceptual.entities)}\n"
            f"  Causal structure: {conceptual.causal_structure}\n\n"
            f"SCOPE: {_scope_clause(scope)}\n\n"
            "Find direct intellectual ancestors of this claim. Cast a wide net "
            "across philosophy, political economy, movement texts, and older "
            "traditions. Look for texts that argue the SAME structural claim "
            "in the vocabulary of their own time."
        )

        response = await _create_with_retry(
            self.client,
            model=SONNET,
            max_tokens=16384,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": CONCEPTUAL_ANCESTORS_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            retry_on_empty_text=True,
        )

        raw_text = _response_text(response)
        data = _parse_json_safe(raw_text)
        # "contributors" is the new field; "ancestors" is accepted for
        # backward compatibility with earlier prompt versions.
        items = data.get("contributors") or data.get("ancestors") or []
        ancestors = []
        for raw in items:
            inst = _instance_from_dict(raw)
            if inst is not None:
                ancestors.append(inst)
        ancestors.sort(key=lambda a: a.date or "9999")
        if not ancestors:
            diag = _response_diagnostics(response)
            debug_path = _save_debug_response("conceptual_ancestors", raw_text)
            sys.stderr.write(
                f"[warning: conceptual ancestor search returned 0 instances "
                f"(text: {len(raw_text)} chars, json_parsed: {bool(data)}, "
                f"{diag}, raw saved: {debug_path})]\n"
            )
        _log_progress(f"L4 conceptual: ancestors done in {time.monotonic() - t0:.1f}s "
                      f"({len(ancestors)} ancestors)")
        return ancestors

    async def adversarial_verify_conceptual(
        self, conceptual: ConceptualLayer, proposed_date: str, scope: Scope
    ) -> tuple[list[AttestedInstance], str]:
        _log_progress(f"L4 conceptual: adversarial verify against {proposed_date} starting")
        t0 = time.monotonic()
        system_text = CONCEPTUAL_ADVERSARIAL_SYSTEM.format(proposed_date=proposed_date)

        user_content = (
            f"STRUCTURAL CLAIM:\n"
            f"  Predicate: {conceptual.claim_predicate}\n"
            f"  Entities: {json.dumps(conceptual.entities)}\n"
            f"  Causal structure: {conceptual.causal_structure}\n\n"
            f"PROPOSED EARLIEST DATE: {proposed_date}\n\n"
            f"SCOPE: {_scope_clause(scope)}\n\n"
            "Search older intellectual traditions for any earlier articulation "
            "of this same structural claim. A clean negative result strengthens "
            "the claim; say so plainly if you find nothing."
        )

        response = await _create_with_retry(
            self.client,
            model=SONNET,
            max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=[{"type": "text", "text": system_text,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            retry_on_empty_text=True,
        )

        data = _parse_json_safe(_response_text(response))
        earlier = []
        for raw in data.get("earlier_ancestors", []):
            inst = _instance_from_dict(raw)
            if inst is None:
                continue
            if inst.date and inst.date < proposed_date:
                earlier.append(inst)
        earlier.sort(key=lambda a: a.date)
        _log_progress(f"L4 conceptual: adversarial done in {time.monotonic() - t0:.1f}s "
                      f"({len(earlier)} earlier instances found)")
        return earlier, str(data.get("verification_notes", "")).strip()

    async def generate_lineage_lexical(
        self,
        lexical: LexicalLayer,
        scope: Scope,
        polygenesis_window_days: int = 7,
    ) -> LineageRecord:
        """L4 lexical lineage: where did the PHRASING come from?"""
        instances = await self.search_earliest_uses(lexical, scope)
        if not instances:
            return LineageRecord(
                lineage_type="lexical",
                status=GenealogyStatus.UNKNOWN,
            )

        earliest = instances[0]
        earlier_instances, adv_notes = await self.adversarial_verify(
            lexical, earliest.date or "9999-12-31", scope
        )
        if earlier_instances:
            instances = earlier_instances + instances
            earliest = instances[0]

        # Polygenesis applies to lexical lineage only — same phrase emerging
        # in independent places within a short window of a real-world event.
        parallel = []
        try:
            earliest_d = date.fromisoformat(earliest.date)
            for inst in instances:
                if inst.instance_id == earliest.instance_id:
                    continue
                try:
                    d = date.fromisoformat(inst.date)
                    if abs((d - earliest_d).days) <= polygenesis_window_days:
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

        return LineageRecord(
            lineage_type="lexical",
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

    async def generate_lineage_conceptual(
        self,
        conceptual: ConceptualLayer,
        scope: Scope,
    ) -> LineageRecord:
        """L4 conceptual lineage: where did the underlying CLAIM come from?"""
        if not conceptual.claim_predicate:
            return LineageRecord(
                lineage_type="conceptual",
                status=GenealogyStatus.UNKNOWN,
            )

        ancestors = await self.search_conceptual_ancestors(conceptual, scope)
        if not ancestors:
            return LineageRecord(
                lineage_type="conceptual",
                status=GenealogyStatus.UNKNOWN,
            )

        earliest = ancestors[0]
        earlier_ancestors, adv_notes = await self.adversarial_verify_conceptual(
            conceptual, earliest.date or "9999-12-31", scope
        )
        if earlier_ancestors:
            ancestors = earlier_ancestors + ancestors
            earliest = ancestors[0]

        # Conceptual lineage skips polygenesis detection: structural claims
        # evolve across decades and centuries, not days. A near-coincident
        # second articulation does not imply independent emergence.
        if earliest.confidence < 0.4:
            status = GenealogyStatus.DIFFUSE
        else:
            status = GenealogyStatus.SINGLE_ORIGIN

        return LineageRecord(
            lineage_type="conceptual",
            status=status,
            first_attested_date=earliest.date,
            first_attested_source=earliest.source_url,
            attestation_confidence=earliest.confidence,
            primary_origin_id=earliest.instance_id,
            attestation_log=ancestors,
            adversarial_check_performed=True,
            adversarial_notes=adv_notes,
        )

    async def _analyze_single_mutation(
        self,
        claim_predicate: str,
        prev_inst: AttestedInstance,
        curr_inst: AttestedInstance,
    ) -> Optional[Mutation]:
        """Analyze how the framing changed between two attested instances."""
        user_content = (
            f"CLAIM (neutral):\n{claim_predicate}\n\n"
            f"EARLIER ({prev_inst.date}, {prev_inst.author}, "
            f"{prev_inst.source_title}):\n"
            f"  \"{prev_inst.exact_quote}\"\n\n"
            f"LATER ({curr_inst.date}, {curr_inst.author}, "
            f"{curr_inst.source_title}):\n"
            f"  \"{curr_inst.exact_quote}\""
        )

        try:
            response = await _create_with_retry(
                self.client,
                model=SONNET,
                max_tokens=1024,
                system=[{"type": "text", "text": MUTATION_SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.APIStatusError:
            return None

        data = _parse_json_safe(_response_text(response))
        if not data:
            return None

        return Mutation(
            from_source=prev_inst.source_url,
            to_source=curr_inst.source_url,
            preserved=str(data.get("preserved", "")).strip(),
            dropped=str(data.get("dropped", "")).strip(),
            added=str(data.get("added", "")).strip(),
            distorted=str(data.get("distorted", "")).strip(),
            attribution=Attribution(source="ai", model=SONNET),
        )

    async def analyze_lineage_mutations(
        self,
        lineage: LineageRecord,
        claim_predicate: str,
    ) -> list[Mutation]:
        """Identify the meaningful mutations across a lineage's chain.

        Filters to significant amplifier roles only — mention/unknown
        instances are typically echoes that don't mutate the framing in
        meaningful ways. Adjacent significant instances in chronological
        order are pair-analyzed in parallel."""
        SIGNIFICANT = {
            AmplifierRole.ORIGINATOR,
            AmplifierRole.EARLY_AMPLIFIER,
            AmplifierRole.MASS_AMPLIFIER,
            AmplifierRole.INSTITUTIONAL_ADOPTION,
            AmplifierRole.CRITIC,
        }
        significant = [
            i for i in lineage.attestation_log
            if i.amplifier_role in SIGNIFICANT and i.exact_quote and i.exact_quote.strip()
        ]
        if len(significant) < 2:
            return []

        # Defensive sort — chains should already be chronological
        significant.sort(key=lambda a: a.date or "9999")

        pairs = list(zip(significant[:-1], significant[1:]))
        _log_progress(
            f"L4 {lineage.lineage_type}: analyzing {len(pairs)} mutation transitions"
        )
        t0 = time.monotonic()

        results = await asyncio.gather(*[
            self._analyze_single_mutation(claim_predicate, prev, curr)
            for prev, curr in pairs
        ])
        mutations = [m for m in results if m is not None]

        _log_progress(
            f"L4 {lineage.lineage_type}: mutations done in "
            f"{time.monotonic() - t0:.1f}s ({len(mutations)} transitions analyzed)"
        )
        return mutations

    async def generate_genealogy(
        self,
        lexical: LexicalLayer,
        conceptual: ConceptualLayer,
        scope: Scope,
        skip_conceptual: bool = False,
        skip_mutations: bool = False,
    ) -> GenealogyLayer:
        """Build both lexical and conceptual lineages in parallel, then
        post-process each with mutation analysis (unless skipped)."""
        if skip_conceptual:
            lex_record = await self.generate_lineage_lexical(lexical, scope)
            con_record = LineageRecord(
                lineage_type="conceptual",
                status=GenealogyStatus.UNKNOWN,
            )
        else:
            lex_record, con_record = await asyncio.gather(
                self.generate_lineage_lexical(lexical, scope),
                self.generate_lineage_conceptual(conceptual, scope),
            )

        # Mutation analysis post-pass: walks each chain's significant
        # transitions in parallel. Uses the conceptual claim_predicate as
        # the canonical reference because it's the vocabulary-independent
        # statement of what's being traced.
        if not skip_mutations:
            lex_muts, con_muts = await asyncio.gather(
                self.analyze_lineage_mutations(lex_record, conceptual.claim_predicate),
                self.analyze_lineage_mutations(con_record, conceptual.claim_predicate),
            )
            lex_record.mutations = lex_muts
            con_record.mutations = con_muts

        return GenealogyLayer(lexical=lex_record, conceptual=con_record)

    async def generate_fingerprint(
        self,
        claim_text: str,
        scope: Optional[Scope] = None,
        context: str = "",
        skip_conceptual: bool = False,
        skip_mutations: bool = False,
        lexical: Optional[LexicalLayer] = None,
    ) -> NarrativeFingerprint:
        scope = scope or Scope()
        # Phase 1: L1 + L2 (Haiku, parallel) — needed as input to L3/L5.
        # If the caller pre-generated L1 (e.g. for early dedup in the CLI),
        # reuse it and only run L2 here.
        if lexical is None:
            lexical, conceptual = await asyncio.gather(
                self.generate_lexical(claim_text, context=context),
                self.generate_conceptual(claim_text, context=context),
            )
        else:
            conceptual = await self.generate_conceptual(claim_text, context=context)
        # Phase 2: L3 + L5 (Haiku, fast) and L4 (Sonnet + web_search, slow)
        # all in parallel. L3/L5 finish in seconds while L4 grinds on.
        rhetorical, taxonomic, genealogy = await asyncio.gather(
            self.generate_rhetorical(claim_text, lexical, conceptual, context=context),
            self.generate_taxonomic(claim_text, lexical, conceptual, context=context),
            self.generate_genealogy(
                lexical, conceptual, scope,
                skip_conceptual=skip_conceptual,
                skip_mutations=skip_mutations,
            ),
        )
        return NarrativeFingerprint(
            scope=scope,
            lexical=lexical,
            conceptual=conceptual,
            rhetorical=rhetorical,
            taxonomic=taxonomic,
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
            "lexical_first_attested_date": fp.genealogy.lexical.first_attested_date,
            "lexical_first_attested_source": fp.genealogy.lexical.first_attested_source,
            "lexical_status": fp.genealogy.lexical.status.value,
            "conceptual_first_attested_date": fp.genealogy.conceptual.first_attested_date,
            "conceptual_first_attested_source": fp.genealogy.conceptual.first_attested_source,
            "conceptual_status": fp.genealogy.conceptual.status.value,
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

    # Generate L1 first; this is cheap (one Haiku call). The lexical signature
    # is the only thing dedup needs, so we can short-circuit before paying for
    # L2 and L4 if the user re-runs without --force.
    context = args.context or ""
    lexical = await gen.generate_lexical(args.claim, context=context)

    store = None
    if args.save:
        store = FingerprintStore(args.store_dir)
        existing = store.find_matching(lexical)
        if existing and not args.force:
            print(json.dumps(lexical.to_dict(), indent=2))
            print(
                f"[matched existing fingerprint: {existing} — "
                f"L2 and L4 skipped to avoid cost. Pass --force to regenerate.]"
            )
            return

    # No dedup hit (or --force): run the full pipeline (L2 + L3 + L5 + L4),
    # reusing the L1 we already generated to avoid a duplicate Haiku call.
    fp = await gen.generate_fingerprint(
        args.claim,
        scope=scope,
        context=context,
        skip_conceptual=args.no_conceptual,
        skip_mutations=args.no_mutations,
        lexical=lexical,
    )

    print(fp.to_json())

    if store is not None:
        try:
            fp_id = store.save(fp)
            print(f"[saved fingerprint: {fp_id}]")
        except Exception as e:
            print(f"[save failed: {type(e).__name__}: {e}]")


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
                        help="Generate only the L1 lexical layer (no L2, no web search)")
    parser.add_argument("--no-conceptual", action="store_true",
                        help="Skip the L2 conceptual lineage pass (cheaper; matches v1 behavior)")
    parser.add_argument("--no-mutations", action="store_true",
                        help="Skip the mutation analysis post-pass over the lineages "
                             "(saves ~$0.10–0.25 per fingerprint)")
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
