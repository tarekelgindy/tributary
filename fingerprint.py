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
import httpx

from models import (
    AmplifierRole,
    AttestedInstance,
    ConceptualLayer,
    Domain,
    ElementOrigin,
    EvidenceLandscape,
    EventAnalysis,
    ExtractedClaim,
    FramePrimitive,
    FramingCarrier,
    FramingOmission,
    GenealogyLayer,
    GenealogyStatus,
    InformationSource,
    LexicalLayer,
    LineageRecord,
    Mutation,
    NarrativeFingerprint,
    NarrativeFraming,
    Provenance,
    ReviewStatus,
    RhetoricalLayer,
    Scope,
    SharedFact,
    SharedFoundation,
    SocialAttestedInstance,
    SourceAnalysis,
    SourceDirection,
    SourceStatus,
    SourceStrength,
    SourceType,
    SourceVenue,
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
  originator              the chronologically earliest credible articulation
                          of the framing in this chain — EXACTLY ONE INSTANCE
                          may have this role, the oldest one. Subsequent
                          instances that originate a new variant or
                          re-articulate in new vocabulary are early-amplifiers
                          or mass-amplifiers, NOT additional originators.
  early-amplifier         spread the framing before mainstream uptake
  mass-amplifier          drove broad public adoption (e.g. viral campaign,
                          national TV speech, viral op-ed)
  institutional-adoption  adopted into an official platform — party platform,
                          government document, major newspaper editorial,
                          flagship academic journal
  critic                  pushed back, fact-checked, or rebutted the framing
  mention                 used the framing in passing without driving spread

Assign exactly one role per instance. Use evidence-based judgement, not
inference from author identity alone. Only the chronologically earliest
instance in your returned chain may be tagged as originator.

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

If no earlier instances are found, return an empty list AND populate
verification_notes with a substantive audit trail describing what you
actually searched. A clean negative result is valuable — but ONLY if
documented. An empty earlier_instances list with an empty
verification_notes field is useless to the reader.

MANDATORY when earlier_instances is empty: verification_notes must
explain (a) which n-grams or variants you searched for, (b) which
specific older sources / traditions / time periods you checked, (c) any
near-misses you considered and why you ruled them out, and (d) the
limitations of the search (e.g. paywalled archives, undigitized text).
At minimum 3-5 sentences.

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
                    institutional-adoption, critic, mention.

                    CRITICAL: ONLY ONE instance in the returned chain may
                    be tagged "originator" — the chronologically earliest
                    one. Subsequent foundational figures (Smith, Marx,
                    etc.) who articulate the claim in their own
                    vocabulary are early-amplifiers or mass-amplifiers,
                    NOT additional originators. Do not assign "originator"
                    to Marx's Capital if an older ancestor (e.g. Smith,
                    Aristotle, Ibn Khaldun) is also in your chain.

                    Role definitions:
                      originator              = chronologically earliest;
                                                exactly one
                      early-amplifier         = built the intellectual
                                                tradition
                      mass-amplifier          = popularized to a broad
                                                audience (bestseller,
                                                viral essay, documentary)
                      institutional-adoption  = official platform / major
                                                party / govt / flagship
                                                academic journal
                      critic                  = pushed back
                      mention                 = passing reference
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

If nothing earlier is found, return an empty list AND populate
verification_notes with a substantive audit trail describing what you
actually searched. A clean negative result is valuable — but ONLY if
documented. An empty earlier_ancestors list with an empty
verification_notes field is useless to the reader.

MANDATORY when earlier_ancestors is empty: verification_notes must
explain (a) which intellectual traditions / philosophical schools /
language traditions you checked, (b) which named earlier figures or
texts you considered and why each was ruled out (e.g. "Plato Republic —
addresses inequality but not the specific structural mechanism"), and
(c) any limitations of the search (e.g. untranslated pre-modern texts,
oral traditions). At minimum 4-6 sentences.

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


SOCIAL_ROLE_SYSTEM = """\
You are classifying social-media posts by their role in spreading a
narrative on a single platform.

You will receive:
  - A narrative claim (the canonical framing being traced)
  - A batch of posts mentioning or using the framing, each with index,
    author handle, date, text, and engagement (likes, reposts)

For each post, assign exactly one of these amplifier_role values:

  mention            uses the framing in passing; most posts will be this
  mass-amplifier     drove visible spread — high engagement, or a notable
                     account that crystallized the framing
  early-amplifier    posted earlier than most in the batch and contributed
                     to spread before mainstream uptake
  critic             explicitly disagrees with, rebuts, or mocks the framing
  institutional-adoption  posted by a recognizable institution (news
                          outlet, political org, government agency, brand)
                          using the framing officially
  originator         genuinely the originating use on this platform —
                     RARE; only assign if confident

Bias toward "mention." Use engagement as a SIGNAL but not the only one —
post text matters more than counts for distinguishing critic from
amplifier. Sarcastic, dismissive, or rebuttal posts about the framing
are critic, not mention.

Output ONLY JSON with one classification per input post:

{
  "classifications": [
    {"idx": 0, "role": "mention", "role_evidence": "passing use of the phrase, no signal of intent to spread"},
    {"idx": 1, "role": "mass-amplifier", "role_evidence": "47 reposts, account with large following, restates canonical framing as call-to-action"},
    {"idx": 2, "role": "critic", "role_evidence": "post explicitly argues the framing is misleading and links a rebuttal"}
  ]
}

The `idx` field MUST match the index from the input batch. Provide a
classification for EVERY post in the batch.
"""


EVIDENCE_LANDSCAPE_SYSTEM = """\
You are mapping the information landscape around a narrative claim.

Goal: give the reader a curated list of 8-15 pieces of information they
should know when evaluating this claim — supporting, disputing,
suggesting alternative conclusions, or providing shared background. You
do NOT take a side on whether the claim is true. You map the epistemic
terrain so the reader can judge.

DIRECTION TAGS:

  supports          provides evidence backing the claim
  disputes          provides evidence against the claim
  redirects         addresses the same starting material but suggests a
                    different conclusion — alternative causal hypothesis,
                    alternative interpretation, different question
  shared-context    accepted by all sides — common factual ground,
                    background, definitional context. Often the most
                    clarifying entries because they're uncontested.

PER-SOURCE METADATA:

  strength          strong / moderate / weak
                      strong   = compelling: large sample, robust
                                 methodology, replicated, or widely
                                 accepted (for shared-context)
                      moderate = decent evidence with caveats
                      weak     = underpowered, contested methodology,
                                 anecdotal, or supports/disputes only a
                                 weaker version of the claim

  source_venue      peer-reviewed / institutional / news-outlet /
                    opinion-venue / aggregator / preprint-server /
                    self-published / other
                      peer-reviewed   = peer-reviewed scientific journal
                      institutional   = govt agency, intergovernmental
                                        org, major NGO
                      news-outlet     = mainstream journalism
                      opinion-venue   = editorial, op-ed, blog, Substack
                      aggregator      = Wikipedia, encyclopedia
                      preprint-server = arXiv, bioRxiv, SSRN, etc.
                      self-published  = personal blog, YouTube, podcast
                      other

  source_type       primary / secondary / tertiary
                      primary    = original research, raw data, official
                                   statement, direct observation,
                                   original document
                      secondary  = analysis, interpretation, review,
                                   journalism about events
                      tertiary   = compilation of secondary sources,
                                   encyclopedia, textbook

  source_url        canonical URL when locatable

  date              ISO date (YYYY-MM-DD); YYYY-01-01 if only year known

  status            current / retracted / methodology-disputed /
                    superseded / correction-issued.
                    ALWAYS flag retractions, methodology disputes, and
                    supersessions when known.

  status_notes      one sentence if status != current
                    (e.g. "retracted 2010 for data falsification and
                    ethics violations")

  notes             one sentence justifying the direction tag

CRITICAL INSTRUCTIONS:

- SOURCE TYPE PREFERENCE: when a primary source exists and is citable,
  PREFER it over secondary coverage. If a news article references a
  study, locate and cite the study itself. Cite the meta-analysis
  rather than the journalism about the meta-analysis.

- Flag venue/type mismatches in `notes` (e.g. "aggregator entry treats
  this as primary but the underlying source is a secondary summary").

- BALANCE DIRECTIONS where evidence exists in multiple directions. Do
  not return 12 supports and 0 disputes if disputes exist in the
  literature.

- Include 2-4 shared-context entries when relevant — they're often the
  most clarifying because they're the uncontested factual ground.

- Prefer concrete citable items (specific studies, datasets, official
  documents, dated events) over abstract concepts.

- Target 8-15 sources total. Fewer is fine for claims with limited
  literature; more is fine for rich evidence bases.

ALSO PROVIDE:

  summary           one paragraph (3-5 sentences) on the overall shape
                    of the landscape. Example:
                    "Strong institutional consensus disputes the claim,
                    with four peer-reviewed meta-analyses. Supporting
                    evidence is primarily a single retracted study
                    (Wakefield 1998) and parental observation. The
                    common ground is the rising autism prevalence data,
                    which both sides explain differently — supporters
                    point to vaccine schedule changes; mainstream
                    medicine cites diagnostic criteria changes and
                    genetic factors."

  search_notes      1-2 sentences on what categories of evidence you
                    searched and any you may have under-covered.

OUTPUT FORMAT — JSON ONLY:

{
  "sources": [
    {
      "title": "Wakefield 1998 Lancet Study",
      "description": "Case-series claiming a link between MMR and autism in 12 children",
      "direction": "supports",
      "strength": "weak",
      "source_venue": "peer-reviewed",
      "source_type": "primary",
      "source_url": "https://...",
      "date": "1998-02-28",
      "status": "retracted",
      "status_notes": "Retracted by The Lancet in 2010 for data falsification and ethics violations",
      "notes": "Original supporting evidence cited by the claim; methodology and conclusions thoroughly discredited"
    },
    ...
  ],
  "summary": "...",
  "search_notes": "..."
}

Output ONLY the JSON object.
"""


CLAIM_EXTRACTION_SYSTEM = """\
You are auditing the claims in a piece of content (an article, blog post,
podcast transcript, or social thread) — producing a complete inventory of
its significant claims, each classified by type.

Classify each significant claim with ONE of FIVE labels:

  fact          specific verifiable assertion (names, numbers, dates, events)
  study         reference to specific research, data, or findings
  narrative     interpretive framing with a traceable origin (e.g. "the
                economy is rigged", "X is the new Y", "the system is broken")
  opinion       genuinely personal preference or value judgment ("I think
                jazz is boring", "this policy is wrong")
  unverifiable  sounds factual but too vague or subjective to trace
                ("most people feel uneasy", "things are getting worse")

The first three (fact / study / narrative) are TRACEABLE — their origin
can be investigated. The last two (opinion / unverifiable) are NOT
traceable, but they are still part of what the content is made of and
MUST be included in the inventory. Do not drop them — they tell the
reader what kind of content this is (reporting vs. commentary vs. opinion).

Apply the traceability test for the fact/narrative boundary: "Could I find
a primary source to confirm or deny this?" If yes and it's a discrete
assertion → fact. If it's an interpretive framing with an identifiable
origin → narrative. If it's a value judgment → opinion.

Select the {max_claims} MOST SIGNIFICANT claims across ALL FIVE types —
the ones most central to the piece. Rank by significance. Return a
representative mix reflecting the actual content (an op-ed will be
opinion/narrative-heavy; a news report will be fact-heavy).

For each claim:
  claim_text     a clean, self-contained restatement. Preserve meaning and
                 any framing/spin. Should stand alone without the article.
  claim_type     fact / study / narrative / opinion / unverifiable
  significance   one sentence on why it matters in the piece
  context        a short phrase locating it ("central thesis", "supporting
                 statistic", "aside", "closing appeal")

ALSO provide a one-sentence characterization of the content's overall
makeup — e.g. "predominantly narrative framing with limited factual
grounding; reads as opinion-led commentary" or "fact-dense reporting with
minimal interpretation."

Output ONLY JSON:

{
  "claims": [
    {"claim_text": "...", "claim_type": "...", "significance": "...", "context": "..."}
  ],
  "characterization": "..."
}
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


def _recover_json_array_objects(text: str, key: str) -> list:
    """Recover the COMPLETE {...} objects from a possibly-truncated JSON
    array value `"key": [ ... ]`. Tolerant of a mid-array cutoff (e.g. the
    response hit max_tokens before closing the JSON): returns every object
    that fully parsed before the truncation point, skipping the final
    incomplete one. String/escape aware so braces inside strings don't
    fool the brace counter."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\[', text)
    if not m:
        return []
    i = m.end()
    objs, depth, start = [], 0, -1
    in_str = escape = False
    while i < len(text):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif c == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        try:
                            objs.append(json.loads(text[start:i + 1]))
                        except json.JSONDecodeError:
                            pass
                        start = -1
            elif c == "]" and depth == 0:
                break
        i += 1
    return objs


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


def _instance_from_dict(d: dict, model: str = SONNET) -> Optional[AttestedInstance]:
    try:
        role_str = str(d.get("amplifier_role", "")).strip().lower()
        try:
            role = AmplifierRole(role_str) if role_str else AmplifierRole.UNKNOWN
        except ValueError:
            role = AmplifierRole.UNKNOWN
        conf = float(d.get("confidence", 0.5))
        return AttestedInstance(
            date=_clean_text_field(d.get("date")),
            source_url=_clean_text_field(d.get("source_url")),
            source_title=_clean_text_field(d.get("source_title")),
            author=_clean_text_field(d.get("author")),
            lexical_form_seen=_clean_text_field(d.get("lexical_form_seen")),
            exact_quote=_clean_text_field(d.get("exact_quote")),
            confidence=conf,
            evidence=_clean_text_field(d.get("evidence")),
            amplifier_role=role,
            role_evidence=_clean_text_field(d.get("role_evidence")),
            provenance=Provenance.ai(model=model, confidence=conf),
        )
    except (TypeError, ValueError):
        return None


_EMPTY_SENTINELS = {"none", "n/a", "null", "nil", "not applicable", "not specified"}


def _estimate_fingerprint_cost(fingerprint_kwargs: dict) -> float:
    """Rough per-fingerprint cost estimate (USD) from which layers are enabled.
    Deliberately approximate — for a 'you're about to spend ~$X' heads-up."""
    cost = 0.15  # lean base: L1/L2/L3/L5 (Haiku) + lexical lineage + verify
    if not fingerprint_kwargs.get("skip_conceptual", True):
        cost += 0.30
    if not fingerprint_kwargs.get("skip_evidence", True):
        cost += 0.20
    if not fingerprint_kwargs.get("skip_mutations", True):
        cost += 0.10
    if fingerprint_kwargs.get("include_social", False):
        cost += 0.03
    return cost


def _save_analysis(analysis, directory: str) -> str:
    """Write a SourceAnalysis manifest to <directory>/<analysis_id>.json.
    Called incrementally (after each claim) so a mid-run crash never loses
    completed work. Returns the path."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{analysis.analysis_id}.json"
    path.write_text(analysis.to_json(), encoding="utf-8")
    return str(path)


def _compute_breakdown(claims: list, characterization: str = "") -> dict:
    """Content profile over a list of ExtractedClaim: counts and percentages
    by claim_type, plus a traceable count and the model's one-line
    characterization. Pure-Python."""
    counts: dict = {}
    for c in claims:
        t = getattr(c, "claim_type", None) or "unknown"
        counts[t] = counts.get(t, 0) + 1
    total = len(claims)
    percentages = (
        {t: round(100 * n / total, 1) for t, n in counts.items()}
        if total else {}
    )
    traceable_count = sum(1 for c in claims if getattr(c, "traceable", False))
    return {
        "total": total,
        "counts": counts,
        "percentages": percentages,
        "traceable_count": traceable_count,
        "characterization": characterization,
    }


def _clean_text_field(value) -> str:
    """Strip and treat 'None' / 'N/A' / 'null' / etc. as empty string.

    Models sometimes fill optional text fields with the literal string 'None'
    instead of leaving them empty. This normalizes those to true empty
    strings so the viewer and downstream consumers don't display
    placeholder text as if it were real content."""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.lower() in _EMPTY_SENTINELS:
        return ""
    return s


def _information_source_from_dict(d: dict, model: str = SONNET) -> InformationSource:
    """Parse an InformationSource from a model-emitted dict, tolerating
    invalid enum values by falling back to safe defaults."""
    def _safe_enum(value, enum_cls, default):
        try:
            return enum_cls(str(value or "").strip())
        except ValueError:
            return default
    return InformationSource(
        title=_clean_text_field(d.get("title")),
        description=_clean_text_field(d.get("description")),
        direction=_safe_enum(d.get("direction"), SourceDirection,
                             SourceDirection.SHARED_CONTEXT),
        strength=_safe_enum(d.get("strength"), SourceStrength,
                            SourceStrength.MODERATE),
        source_venue=_safe_enum(d.get("source_venue"), SourceVenue,
                                SourceVenue.OTHER),
        source_type=_safe_enum(d.get("source_type"), SourceType,
                               SourceType.SECONDARY),
        source_url=_clean_text_field(d.get("source_url")),
        date=_clean_text_field(d.get("date")),
        status=_safe_enum(d.get("status"), SourceStatus,
                          SourceStatus.CURRENT),
        status_notes=_clean_text_field(d.get("status_notes")),
        notes=_clean_text_field(d.get("notes")),
        provenance=Provenance.ai(model=model),
    )


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


# ---------------------------------------------------------------------------
# Source verification (URL existence + quote-in-page + Wayback fallback)
# ---------------------------------------------------------------------------

_VERIFY_USER_AGENT = "TributaryFingerprintVerifier/0.1 (+https://github.com/tarekelgindy/tributary)"
_VERIFY_TIMEOUT = 12.0


async def _verify_url_exists(http: httpx.AsyncClient, url: str) -> tuple:
    """HEAD-then-GET a URL, return (status_code or None, error_message)."""
    if not url:
        return None, "no url"
    try:
        resp = await http.head(url, follow_redirects=True, timeout=_VERIFY_TIMEOUT)
        # Some servers reject HEAD (405) or return 5xx on HEAD but work on GET
        if resp.status_code in (403, 405) or resp.status_code >= 500:
            resp = await http.get(url, follow_redirects=True, timeout=_VERIFY_TIMEOUT)
        return resp.status_code, ""
    except httpx.TimeoutException:
        return None, "timeout"
    except (httpx.NetworkError, httpx.HTTPError) as e:
        return None, f"{type(e).__name__}"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"


async def _verify_quote_at_url(
    http: httpx.AsyncClient, url: str, quote: str
) -> tuple:
    """Check whether a fuzzy version of `quote` appears at `url`.
    Returns (matched: bool, note: str)."""
    if not url or not quote:
        return False, "no url or quote"
    quote = quote.strip()
    if len(quote) < 12:
        return False, "quote too short to verify"
    try:
        resp = await http.get(url, follow_redirects=True, timeout=_VERIFY_TIMEOUT)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code} fetching content"
    except httpx.TimeoutException:
        return False, "timeout fetching content"
    except Exception as e:
        return False, f"{type(e).__name__} fetching content"

    # Strip HTML and normalize whitespace; lowercase for fuzzy match
    text_only = re.sub(r"<[^>]+>", " ", resp.text)
    text_norm = re.sub(r"\s+", " ", text_only.lower())

    quote_words = re.findall(r"\w+", quote.lower())
    if len(quote_words) < 4:
        return False, "too few words to verify"

    # Try several distinctive windows from the quote; if any appears in
    # the page content, count as matched. This handles minor wording drift,
    # boilerplate framing, paraphrase pull-quotes, etc.
    window_size = min(6, max(3, len(quote_words) // 3))
    step = max(1, window_size // 2)
    for i in range(0, len(quote_words) - window_size + 1, step):
        chunk = " ".join(quote_words[i:i + window_size])
        if chunk in text_norm:
            return True, "matched fuzzy quote chunk"
    return False, "no fuzzy chunk of quote found in page"


async def _find_wayback_snapshot(http: httpx.AsyncClient, url: str) -> str:
    """Look up the closest Wayback Machine snapshot for `url`. Returns
    the snapshot URL or empty string."""
    if not url:
        return ""
    try:
        api = "https://archive.org/wayback/available"
        resp = await http.get(api, params={"url": url}, timeout=_VERIFY_TIMEOUT)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        closest = (data.get("archived_snapshots") or {}).get("closest") or {}
        if closest.get("available"):
            return str(closest.get("url", ""))
    except Exception:
        pass
    return ""


async def _verify_information_source(http: httpx.AsyncClient, src) -> None:
    """Verify an InformationSource's URL exists. Description is a summary,
    not a quote, so we don't try to match it against page content."""
    if not src.source_url:
        src.verification_status = "unchecked"
        src.verification_notes = "no URL to verify"
        return
    status, err = await _verify_url_exists(http, src.source_url)
    if status is None:
        archive = await _find_wayback_snapshot(http, src.source_url)
        src.verified = False
        src.verification_status = "fetch-error"
        src.verification_notes = err
        src.archive_url = archive
        return
    if status >= 400:
        archive = await _find_wayback_snapshot(http, src.source_url)
        src.verified = False
        src.verification_status = "url-error"
        src.verification_notes = f"HTTP {status}"
        src.archive_url = archive
        return
    src.verified = True
    src.verification_status = "verified"
    src.verification_notes = f"URL reachable (HTTP {status})"


async def _verify_carrier(http: httpx.AsyncClient, carrier) -> None:
    """Verify a FramingCarrier's URL exists (URL-only — the excerpt is a
    representative headline, not necessarily a verbatim on-page quote)."""
    if not carrier.url:
        carrier.verification_status = "unchecked"
        carrier.verification_notes = "no URL to verify"
        return
    status, err = await _verify_url_exists(http, carrier.url)
    if status is None:
        carrier.archive_url = await _find_wayback_snapshot(http, carrier.url)
        carrier.verified = False
        carrier.verification_status = "fetch-error"
        carrier.verification_notes = err
        return
    if status >= 400:
        carrier.archive_url = await _find_wayback_snapshot(http, carrier.url)
        carrier.verified = False
        carrier.verification_status = "url-error"
        carrier.verification_notes = f"HTTP {status}"
        return
    carrier.verified = True
    carrier.verification_status = "verified"
    carrier.verification_notes = f"URL reachable (HTTP {status})"


async def _verify_attested(http: httpx.AsyncClient, inst, check_quote: bool) -> None:
    """Verify an AttestedInstance's URL and (optionally) its exact_quote."""
    if not inst.source_url:
        inst.verification_status = "unchecked"
        inst.verification_notes = "no URL to verify"
        return
    status, err = await _verify_url_exists(http, inst.source_url)
    if status is None:
        archive = await _find_wayback_snapshot(http, inst.source_url)
        inst.verified = False
        inst.verification_status = "fetch-error"
        inst.verification_notes = err
        inst.archive_url = archive
        return
    if status >= 400:
        archive = await _find_wayback_snapshot(http, inst.source_url)
        inst.verified = False
        inst.verification_status = "url-error"
        inst.verification_notes = f"HTTP {status}"
        inst.archive_url = archive
        return
    if check_quote and inst.exact_quote:
        matched, note = await _verify_quote_at_url(http, inst.source_url, inst.exact_quote)
        if matched:
            inst.verified = True
            inst.verification_status = "verified"
            inst.verification_notes = "URL + quote verified"
        else:
            inst.verified = False
            inst.verification_status = "quote-not-found"
            inst.verification_notes = note
    else:
        inst.verified = True
        inst.verification_status = "verified"
        inst.verification_notes = "URL reachable (quote not checked)"


async def _verify_social(http: httpx.AsyncClient, post) -> None:
    """Verify a social post URL exists. Post text is rarely directly
    scrapable from the rendered page (JS rendering), so URL-only here."""
    if not post.post_url:
        post.verification_status = "unchecked"
        post.verification_notes = "no URL to verify"
        return
    status, err = await _verify_url_exists(http, post.post_url)
    if status is None:
        post.verified = False
        post.verification_status = "fetch-error"
        post.verification_notes = err
        return
    if status >= 400:
        post.verified = False
        post.verification_status = "url-error"
        post.verification_notes = f"HTTP {status}"
        return
    post.verified = True
    post.verification_status = "verified"
    post.verification_notes = f"URL reachable (HTTP {status})"


def _parse_iso_date(s):
    """Parse the leading YYYY-MM-DD portion of an ISO date string into a
    `date`, or return None if unparseable."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _role_str(inst) -> str:
    """Extract `amplifier_role` as a plain string, tolerant of both live
    enum instances and string values that may appear after deserialization."""
    r = getattr(inst, "amplifier_role", None)
    if r is None:
        return "unknown"
    if hasattr(r, "value"):
        return r.value
    return str(r)


def _sample_social_posts(posts: list, target: int) -> list:
    """Pick a representative subset of social posts: half top-engaged,
    half spread evenly across time. Cheaper and simpler than the strategy
    in social_search.py's _sample_representative, but adequate for
    amplifier-role classification."""
    if len(posts) <= target:
        return posts
    by_engagement = sorted(
        posts, key=lambda p: (p.likes or 0) + (p.reposts or 0), reverse=True
    )
    half = max(1, target // 2)
    top = by_engagement[:half]
    selected_urls = {p.url for p in top}
    remaining = [p for p in by_engagement[half:] if p.url not in selected_urls]
    remaining.sort(key=lambda p: p.posted_at or "")
    step = max(1, len(remaining) // max(1, target - half))
    spread = remaining[::step][:target - half]
    return top + spread


def compute_timeline_stats(lineage: "LineageRecord") -> dict:
    """Pure-Python time-series stats over a lineage's attestation_log.

    Idempotent and free to run. Captures the active span, year-by-year
    instance distribution (both flat and broken out by amplifier role),
    peak year, and milestone latencies (originator -> first mass amplifier
    / first institutional adoption / first critic). Empty dict if the
    chain has no parseable dates."""
    log = lineage.attestation_log or []
    if not log:
        return {}

    dated = []
    for inst in log:
        d = _parse_iso_date(getattr(inst, "date", ""))
        if d is not None:
            dated.append((d, inst))
    if not dated:
        return {}

    dated.sort(key=lambda x: x[0])
    earliest_date, _ = dated[0]
    latest_date, _ = dated[-1]

    per_year: dict = {}
    per_year_by_role: dict = {}
    for d, inst in dated:
        y = str(d.year)
        per_year[y] = per_year.get(y, 0) + 1
        role = _role_str(inst)
        per_year_by_role.setdefault(y, {})
        per_year_by_role[y][role] = per_year_by_role[y].get(role, 0) + 1

    peak_year = max(per_year, key=per_year.get)
    peak_year_count = per_year[peak_year]

    def first_with_role(target: str):
        for d, inst in dated:
            if _role_str(inst) == target:
                return d
        return None

    originator_d = first_with_role("originator")
    first_mass_d = first_with_role("mass-amplifier")
    first_inst_d = first_with_role("institutional-adoption")
    first_critic_d = first_with_role("critic")

    def days_between(a, b):
        return (b - a).days if (a is not None and b is not None and b >= a) else None

    today = datetime.now(timezone.utc).date()

    return {
        "active_from": earliest_date.isoformat(),
        "active_through": latest_date.isoformat(),
        "active_span_days": (latest_date - earliest_date).days,
        "years_active": latest_date.year - earliest_date.year,
        "years_with_activity": len(per_year),
        "instances_per_year": per_year,
        "instances_per_year_by_role": per_year_by_role,
        "peak_year": peak_year,
        "peak_year_count": peak_year_count,
        "originator_to_mass_amplifier_days": days_between(originator_d, first_mass_d),
        "originator_to_institutional_adoption_days": days_between(originator_d, first_inst_d),
        "originator_to_critic_days": days_between(originator_d, first_critic_d),
        "days_since_latest": (today - latest_date).days,
    }


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
    final text' failure mode that occasionally hits Sonnet+web_search calls.

    Defaults temperature to 0 for run-to-run consistency: the same claim
    queried twice should produce as similar a fingerprint as the model and
    web_search allow. Callers can still override by passing temperature."""
    kwargs.setdefault("temperature", 0)
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
# Batch API execution (50% off tokens, async up to 24h)
# ---------------------------------------------------------------------------

def run_message_batch(requests: list, poll_seconds: int = 20,
                      max_wait_seconds: int = 24 * 3600, label: str = "batch") -> dict:
    """Submit a list of {custom_id, params} Messages requests to the Anthropic
    Message Batches API (~50% off tokens), poll to completion, and return
    {custom_id: response_text or None}. Synchronous (mirrors the proven
    batch_probe.py); call it off the event loop via run_in_executor.

    web_search is supported in batch (confirmed by batch_probe.py). A single
    item's dependent stages can't share a batch — this batches ONE stage
    across many items, where the per-stage wait is amortized."""
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)
    _log_progress(f"{label}: submitted {len(requests)} requests "
                  f"(batch {batch.id}); polling...")
    waited = 0
    while batch.processing_status != "ended":
        if waited >= max_wait_seconds:
            _log_progress(f"{label}: still processing after {waited//60} min; "
                          f"giving up the wait (batch {batch.id} continues server-side)")
            return {}
        time.sleep(poll_seconds)
        waited += poll_seconds
        batch = client.messages.batches.retrieve(batch.id)
        _log_progress(f"{label}: {batch.processing_status} ({waited}s)")

    out = {}
    for r in client.messages.batches.results(batch.id):
        rtype = getattr(r.result, "type", None)
        if rtype == "succeeded":
            out[r.custom_id] = _response_text(r.result.message)
        else:
            err = getattr(r.result, "error", rtype)
            _log_progress(f"{label}: request {r.custom_id} {rtype}: {err}")
            out[r.custom_id] = None
    return out


async def run_message_batch_async(requests: list, **kwargs) -> dict:
    """Await wrapper — runs the synchronous batch executor in a thread so it
    doesn't block the event loop."""
    return await asyncio.to_thread(run_message_batch, requests, **kwargs)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class FingerprintGenerator:
    """Generates NarrativeFingerprint objects via Claude."""

    def __init__(self, client: Optional[anthropic.AsyncAnthropic] = None,
                 max_searches: int = 10):
        self.client = client or anthropic.AsyncAnthropic()
        # Cap on web searches per Sonnet+web_search call. The model tends to
        # over-search (15-22 searches in one call observed), and each search
        # carries a fee. Capping trades peripheral depth for lower cost,
        # faster runs, and better run-to-run consistency. 0 or None = uncapped.
        self.max_searches = max_searches

    def _web_search_tool(self) -> dict:
        """Build the web_search tool config, applying the search cap."""
        tool = {"type": "web_search_20250305", "name": "web_search"}
        if self.max_searches and self.max_searches > 0:
            tool["max_uses"] = self.max_searches
        return tool

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
            tools=[self._web_search_tool()],
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
            tools=[self._web_search_tool()],
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
            tools=[self._web_search_tool()],
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
            tools=[self._web_search_tool()],
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
            provenance=Provenance.ai(model=SONNET),
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

    async def search_social_spread(
        self,
        lexical: LexicalLayer,
        scope: Scope,
        target_sample_size: int = 40,
    ) -> tuple[list[SocialAttestedInstance], str]:
        """Search Bluesky for instances of the diagnostic n-grams, sample a
        representative subset, classify each post's amplifier_role via Haiku,
        and return as structured entries.

        Gracefully returns ([], explanation) if Bluesky is unavailable
        (missing creds, atproto not installed, API down, etc.) so the
        pipeline never fails on social being absent."""
        try:
            from social_search import BlueskySearch
        except ImportError as e:
            return [], f"social_search module unavailable: {e}"

        _log_progress("Social spread: starting Bluesky search")
        t0 = time.monotonic()

        bsky = BlueskySearch()
        try:
            # social_search.py prints status to stdout; redirect to stderr
            # so it doesn't pollute the fingerprint JSON output channel.
            import contextlib
            with contextlib.redirect_stdout(sys.stderr):
                await bsky.login()

                queries: list[str] = []
                if lexical.canonical_phrase:
                    queries.append(f'"{lexical.canonical_phrase}"')
                for n in (lexical.diagnostic_ngrams or []):
                    if n and n not in queries:
                        queries.append(n)
                queries = queries[:6]   # cap to limit API load

                since = scope.time_window_start or None
                until = scope.time_window_end or None

                all_posts: list = []
                seen_urls: set[str] = set()
                url_to_query: dict[str, str] = {}
                for query in queries:
                    for sort_mode in ("top", "latest"):
                        posts = await bsky.search_posts(
                            query=query,
                            sort=sort_mode,
                            since=since,
                            until=until,
                            limit=100,
                        )
                        for post in posts:
                            if post.url not in seen_urls:
                                seen_urls.add(post.url)
                                url_to_query[post.url] = query
                                all_posts.append(post)

                await bsky.close()

            if not all_posts:
                notes = (
                    f"Searched {len(queries)} queries on Bluesky "
                    f"(canonical phrase + diagnostic n-grams). No matching "
                    f"posts found within the requested time window."
                )
                _log_progress(f"Social spread done in {time.monotonic() - t0:.1f}s "
                              f"(0 posts found)")
                return [], notes

            sample = (_sample_social_posts(all_posts, target_sample_size)
                      if len(all_posts) > target_sample_size else all_posts)

            instances: list[SocialAttestedInstance] = []
            for p in sample:
                instances.append(SocialAttestedInstance(
                    platform=getattr(p, "platform", "bluesky"),
                    post_url=p.url,
                    author_handle=p.author,
                    post_date=p.posted_at or "",
                    post_text=p.text or "",
                    matched_ngram=url_to_query.get(p.url, ""),
                    likes=int(p.likes or 0),
                    reposts=int(p.reposts or 0),
                    replies=int(getattr(p, "replies", 0) or 0),
                    provenance=Provenance.ai(model=HAIKU),
                ))

            if instances:
                instances = await self._classify_social_roles(instances, lexical)

            notes = (
                f"Searched {len(queries)} queries on Bluesky "
                f"(canonical phrase + diagnostic n-grams). Found "
                f"{len(all_posts)} unique posts; sampled "
                f"{len(sample)} for role classification."
            )
            _log_progress(
                f"Social spread done in {time.monotonic() - t0:.1f}s "
                f"({len(all_posts)} found, {len(instances)} classified)"
            )
            return instances, notes

        except Exception as e:
            try:
                await bsky.close()
            except Exception:
                pass
            msg = f"Social search skipped: {type(e).__name__}: {e}"
            _log_progress(msg)
            return [], msg

    async def _classify_social_roles(
        self,
        instances: list[SocialAttestedInstance],
        lexical: LexicalLayer,
    ) -> list[SocialAttestedInstance]:
        """One Haiku call classifies the whole batch. Cheap and avoids
        per-post round-trips."""
        posts_data = [
            {
                "idx": i,
                "author": inst.author_handle,
                "date": (inst.post_date or "")[:10],
                "text": (inst.post_text or "")[:300],
                "likes": inst.likes,
                "reposts": inst.reposts,
            }
            for i, inst in enumerate(instances)
        ]

        user_content = (
            f"NARRATIVE: {lexical.canonical_phrase}\n\n"
            f"POSTS:\n{json.dumps(posts_data, indent=2)}"
        )

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=4096,
            system=[{"type": "text", "text": SOCIAL_ROLE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )

        data = _parse_json_safe(_response_text(response))
        raw = data.get("classifications") or []
        by_idx: dict = {}
        for c in raw:
            if isinstance(c, dict) and "idx" in c:
                try:
                    by_idx[int(c["idx"])] = c
                except (TypeError, ValueError):
                    continue

        for i, inst in enumerate(instances):
            c = by_idx.get(i, {})
            role_str = str(c.get("role", "")).strip().lower()
            try:
                inst.amplifier_role = (
                    AmplifierRole(role_str) if role_str else AmplifierRole.MENTION
                )
            except ValueError:
                inst.amplifier_role = AmplifierRole.MENTION
            inst.role_evidence = str(c.get("role_evidence", "")).strip()

        return instances

    async def generate_evidence_landscape(
        self,
        claim_text: str,
        lexical: LexicalLayer,
        conceptual: ConceptualLayer,
        scope: Scope,
    ) -> EvidenceLandscape:
        """Map the information landscape around the claim via Sonnet +
        web_search. Returns a curated list of 8-15 sources tagged by
        direction (supports/disputes/redirects/shared-context) plus
        venue / type / strength / status metadata."""
        _log_progress("Evidence landscape: search starting (web_search)")
        t0 = time.monotonic()

        user_content = (
            f"CLAIM:\n{claim_text}\n\n"
            f"CANONICAL FRAMING:\n{lexical.canonical_phrase}\n\n"
            f"NEUTRAL CLAIM (vocabulary-independent):\n"
            f"{conceptual.claim_predicate}\n\n"
            f"ENTITIES:\n{json.dumps(conceptual.entities, indent=2)}\n\n"
            f"SCOPE: {_scope_clause(scope)}\n\n"
            "Map the information landscape around this claim. Find 8-15 "
            "concrete citable sources spanning supporting evidence, disputing "
            "evidence, alternative interpretations, and shared common ground. "
            "Prefer primary sources when available."
        )

        response = await _create_with_retry(
            self.client,
            model=SONNET,
            max_tokens=8192,
            tools=[self._web_search_tool()],
            system=[{"type": "text", "text": EVIDENCE_LANDSCAPE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            retry_on_empty_text=True,
        )

        raw_text = _response_text(response)
        data = _parse_json_safe(raw_text)

        sources: list[InformationSource] = []
        for raw in (data.get("sources") or []):
            if not isinstance(raw, dict):
                continue
            sources.append(_information_source_from_dict(raw))

        summary = str(data.get("summary", "")).strip()
        search_notes = str(data.get("search_notes", "")).strip()

        if not sources:
            debug_path = _save_debug_response("evidence_landscape", raw_text)
            diag = _response_diagnostics(response)
            sys.stderr.write(
                f"[warning: evidence landscape returned 0 sources "
                f"(text: {len(raw_text)} chars, {diag}, raw saved: {debug_path})]\n"
            )

        _log_progress(
            f"Evidence landscape done in {time.monotonic() - t0:.1f}s "
            f"({len(sources)} sources, summary: {len(summary)} chars)"
        )

        return EvidenceLandscape(
            sources=sources,
            summary=summary,
            search_notes=search_notes,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    async def generate_genealogy(
        self,
        lexical: LexicalLayer,
        conceptual: ConceptualLayer,
        scope: Scope,
        skip_conceptual: bool = True,
        skip_mutations: bool = True,
        include_social: bool = False,
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

        # Social spread: lexical only, opt-in. Gracefully skipped if Bluesky
        # creds missing or atproto unavailable — the call itself reports its
        # own failure mode via social_search_notes.
        if include_social:
            social_posts, social_notes = await self.search_social_spread(
                lexical, scope
            )
            lex_record.social_spread = social_posts
            lex_record.social_search_performed = True
            lex_record.social_search_notes = social_notes

        # Timeline stats: pure-Python post-process, always runs (free).
        lex_record.timeline_stats = compute_timeline_stats(lex_record)
        con_record.timeline_stats = compute_timeline_stats(con_record)

        return GenealogyLayer(lexical=lex_record, conceptual=con_record)

    async def verify_fingerprint(
        self, fp: NarrativeFingerprint
    ) -> NarrativeFingerprint:
        """Post-generation pass that checks URL existence and quote-in-page
        for every cited source. Mutates and returns the same fingerprint.

        - Evidence landscape: URL-only (descriptions are summaries, not quotes)
        - Lexical attestation: URL + fuzzy quote match
        - Conceptual attestation: URL-only (quotes are translations/paraphrases
          of pre-modern texts; fuzzy match against modern transcriptions would
          fail more often than not)
        - Social spread: URL-only (post text rarely scrapable from rendered
          page; auth-walled JS rendering)

        On URL failure, attempts to find a Wayback Machine snapshot and
        populates archive_url for graceful fallback in the viewer."""
        _log_progress("Verification: starting URL + quote checks")
        t0 = time.monotonic()

        headers = {"User-Agent": _VERIFY_USER_AGENT}
        async with httpx.AsyncClient(headers=headers, timeout=_VERIFY_TIMEOUT) as http:
            tasks = []
            for src in fp.evidence_landscape.sources:
                tasks.append(_verify_information_source(http, src))
            for inst in fp.genealogy.lexical.attestation_log:
                tasks.append(_verify_attested(http, inst, check_quote=True))
            for inst in fp.genealogy.conceptual.attestation_log:
                tasks.append(_verify_attested(http, inst, check_quote=False))
            for post in fp.genealogy.lexical.social_spread:
                tasks.append(_verify_social(http, post))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate stats for the progress log
        ev_total = len(fp.evidence_landscape.sources)
        ev_ok = sum(1 for s in fp.evidence_landscape.sources if s.verified)
        lex_total = len(fp.genealogy.lexical.attestation_log)
        lex_ok = sum(1 for i in fp.genealogy.lexical.attestation_log if i.verified)
        con_total = len(fp.genealogy.conceptual.attestation_log)
        con_ok = sum(1 for i in fp.genealogy.conceptual.attestation_log if i.verified)
        soc_total = len(fp.genealogy.lexical.social_spread)
        soc_ok = sum(1 for p in fp.genealogy.lexical.social_spread if p.verified)

        _log_progress(
            f"Verification done in {time.monotonic() - t0:.1f}s "
            f"(evidence {ev_ok}/{ev_total}, lex {lex_ok}/{lex_total}, "
            f"con {con_ok}/{con_total}, social {soc_ok}/{soc_total})"
        )
        return fp

    async def generate_fingerprint(
        self,
        claim_text: str,
        scope: Optional[Scope] = None,
        context: str = "",
        # Lean by default: the expensive layers (conceptual lineage, mutations,
        # evidence landscape) are skipped unless explicitly enabled. A bare
        # call does L1/L2/L3/L5 + lexical lineage + verification (~$0.15-0.25).
        # Verification is free (HTTP only), so it stays on by default.
        skip_conceptual: bool = True,
        skip_mutations: bool = True,
        include_social: bool = False,
        skip_evidence: bool = True,
        skip_verification: bool = False,
        lexical: Optional[LexicalLayer] = None,
    ) -> NarrativeFingerprint:
        scope = scope or Scope()
        # Phase 1: L1 + L2 (Haiku, parallel) — needed as input to L3/L5
        # and the evidence landscape. If the caller pre-generated L1
        # (e.g. for early dedup in the CLI), reuse it and only run L2.
        if lexical is None:
            lexical, conceptual = await asyncio.gather(
                self.generate_lexical(claim_text, context=context),
                self.generate_conceptual(claim_text, context=context),
            )
        else:
            conceptual = await self.generate_conceptual(claim_text, context=context)

        # Phase 2: independent tasks run concurrently. L3/L5 are Haiku and
        # finish in seconds; L4 lineages and evidence landscape are
        # Sonnet+web_search and take longer. The whole phase wallclock is
        # max(L3, L5, L4, evidence) which is essentially max(L4, evidence).
        tasks = [
            self.generate_rhetorical(claim_text, lexical, conceptual, context=context),
            self.generate_taxonomic(claim_text, lexical, conceptual, context=context),
            self.generate_genealogy(
                lexical, conceptual, scope,
                skip_conceptual=skip_conceptual,
                skip_mutations=skip_mutations,
                include_social=include_social,
            ),
        ]
        if not skip_evidence:
            tasks.append(self.generate_evidence_landscape(
                claim_text, lexical, conceptual, scope,
            ))
        results = await asyncio.gather(*tasks)

        rhetorical = results[0]
        taxonomic = results[1]
        genealogy = results[2]
        evidence = results[3] if not skip_evidence else EvidenceLandscape()

        fp = NarrativeFingerprint(
            scope=scope,
            lexical=lexical,
            conceptual=conceptual,
            rhetorical=rhetorical,
            taxonomic=taxonomic,
            genealogy=genealogy,
            evidence_landscape=evidence,
            provenance=Provenance.ai(model=SONNET),
        )

        # Post-pass: URL existence + quote-in-page verification for every
        # cited source. Pure HTTP, no API cost. Parallel-fetches all URLs.
        if not skip_verification:
            fp = await self.verify_fingerprint(fp)

        return fp

    # -- Multi-claim source analysis ----------------------------------

    async def extract_claims_from_content(
        self, content_text: str, max_claims: int = 5
    ) -> tuple[list[ExtractedClaim], str]:
        """Inventory the significant claims in content, classified across the
        full five-way scheme (fact/study/narrative/opinion/unverifiable).
        One Haiku call; content capped to control cost. Returns
        (claims, characterization)."""
        _log_progress(
            f"Claim extraction: parsing content ({len(content_text)} chars)"
        )
        system_text = CLAIM_EXTRACTION_SYSTEM.replace("{max_claims}", str(max_claims))
        excerpt = content_text[:12000]

        response = await _create_with_retry(
            self.client,
            model=HAIKU,
            max_tokens=2048,
            system=[{"type": "text", "text": system_text,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"CONTENT:\n\n{excerpt}"}],
        )

        data = _parse_json_safe(_response_text(response))
        claims: list[ExtractedClaim] = []
        for raw in (data.get("claims") or [])[:max_claims]:
            if not isinstance(raw, dict):
                continue
            text = _clean_text_field(raw.get("claim_text"))
            if not text:
                continue
            ctype = (_clean_text_field(raw.get("claim_type")) or "").lower()
            traceable = ctype in ("fact", "study", "narrative")
            claims.append(ExtractedClaim(
                claim_text=text,
                claim_type=ctype,
                significance=_clean_text_field(raw.get("significance")),
                context=_clean_text_field(raw.get("context")),
                traceable=traceable,
            ))
        characterization = _clean_text_field(data.get("characterization"))
        n_trace = sum(1 for c in claims if c.traceable)
        _log_progress(
            f"Claim extraction done ({len(claims)} claims, {n_trace} traceable)"
        )
        return claims, characterization

    async def _fetch_content(self, url: str):
        """Fetch content from a URL via ingestors.ContentExtractor.
        Returns a ContentItem or None on failure. ContentExtractor prints
        status to stdout, so we redirect it to stderr."""
        import contextlib
        try:
            from ingestors import ContentExtractor
        except ImportError as e:
            _log_progress(f"Content fetch failed: ingestors unavailable ({e})")
            return None

        extractor = ContentExtractor()
        try:
            with contextlib.redirect_stdout(sys.stderr):
                item = await extractor.extract(url)
            return item
        except Exception as e:
            _log_progress(f"Content fetch failed: {type(e).__name__}: {e}")
            return None
        finally:
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    await extractor.close()
            except Exception:
                pass

    async def analyze_source(
        self,
        url: Optional[str] = None,
        text: Optional[str] = None,
        scope: Optional[Scope] = None,
        max_claims: int = 5,
        extract_only: bool = False,
        store=None,
        analysis_dir: Optional[str] = None,
        **fingerprint_kwargs,
    ) -> SourceAnalysis:
        """Multi-claim analysis: extract significant claims from a URL or
        raw text, then fingerprint each. Returns a SourceAnalysis. Claims
        are fingerprinted sequentially (not parallel) to avoid overwhelming
        the API — each fingerprint is already a heavy concurrent operation.

        If extract_only is True, claims are extracted and listed but not
        fingerprinted (cheap preview, one Haiku call)."""
        scope = scope or Scope()

        if url:
            item = await self._fetch_content(url)
            if item is None:
                return SourceAnalysis(source_url=url, source_title="(fetch failed)")
            content_text = item.text or ""
            platform = getattr(item.platform, "value", str(item.platform))
            analysis = SourceAnalysis(
                source_url=url,
                source_title=item.title or "",
                source_platform=platform,
                source_author=item.author or "",
                source_published_at=item.published_at or "",
                content_excerpt=content_text[:500],
            )
        else:
            content_text = text or ""
            analysis = SourceAnalysis(
                source_title="(text input)",
                content_excerpt=content_text[:500],
            )

        if not content_text.strip():
            _log_progress("No content extracted; nothing to analyze")
            return analysis

        claims, characterization = await self.extract_claims_from_content(
            content_text, max_claims=max_claims
        )
        analysis.claims = claims
        analysis.breakdown = _compute_breakdown(claims, characterization)

        # Persist the manifest right after extraction so an --extract-only
        # preview is saved and a later crash still leaves the claim list.
        if analysis_dir:
            _save_analysis(analysis, analysis_dir)

        if extract_only:
            return analysis

        # Only traceable claims (fact/study/narrative) get fingerprinted.
        # Opinions and unverifiable statements stay in the inventory but
        # incur no fingerprint cost.
        traceable = [c for c in claims if c.traceable]

        # Heads-up cost estimate before we start spending.
        per = _estimate_fingerprint_cost(fingerprint_kwargs)
        _log_progress(
            f"About to fingerprint {len(traceable)} traceable claims at "
            f"~${per:.2f} each — estimated ~${per * len(traceable):.2f} total. "
            f"(Ctrl-C now to abort.)"
        )

        for i, claim in enumerate(traceable):
            _log_progress(
                f"Fingerprinting traceable claim {i + 1}/{len(traceable)}: "
                f"{claim.claim_text[:60]}"
            )
            fp = await self.generate_fingerprint(
                claim.claim_text,
                scope=scope,
                context=claim.context,
                **fingerprint_kwargs,
            )
            claim.fingerprint_id = fp.fingerprint_id
            analysis.fingerprints.append(fp)
            if store is not None:
                try:
                    store.save(fp)
                except Exception as e:
                    _log_progress(f"Could not save fingerprint {fp.fingerprint_id}: {e}")
            # Incremental manifest save after EACH claim — a crash (e.g.
            # credit exhaustion) leaves all completed work on disk.
            if analysis_dir:
                _save_analysis(analysis, analysis_dir)

        return analysis


# ---------------------------------------------------------------------------
# Downstream: EventAnalysis generation (the mission's second direction)
# ---------------------------------------------------------------------------

EVENT_DESCRIBE_SYSTEM = """\
You are neutralizing an event or statement for analysis. Given a claim,
headline, or description of an event, produce a single neutral, factual
one-sentence description of WHAT HAPPENED — stripped of any framing,
spin, or interpretation. Name the concrete who/what/when/where; avoid
characterizations, blame, and loaded words.

Output ONLY JSON:
{ "event": "neutral one-sentence description", "event_date": "ISO date if determinable, else \\"\\"" }
"""


EVENT_FRAMINGS_SYSTEM = """\
You are a framing analyst for Tributary. Given a neutral event, search the
information ecosystem and identify the distinct NARRATIVE FRAMINGS forming
around it — and for each, WHO creates and amplifies it.

A framing is a lens defined by the QUESTION it asks about the event, not by
a political side. The same facts get narrated through different questions:
"Was this lawful?" (accountability) vs "Were procedures followed?" (use of
force) vs "Who was harmed?" (human cost) vs "How is this being used
politically?" (political fallout). Aim for 4-8 genuinely distinct framings.

BE AGGRESSIVELY NEUTRAL AND BALANCED. Include framings from across the
political and ideological spectrum. Do NOT privilege one. Do NOT judge any
framing's correctness. Your job is to map the terrain, not to referee it.

For each framing identify the carriers — the outlets, authors, accounts,
or institutions that create or amplify it — and assign each an
amplifier_role:
  originator              coined or first prominently used this framing
  early-amplifier         spread it before it went mainstream
  mass-amplifier          drove broad adoption (viral post, major outlet, big platform)
  institutional-adoption  a party / govt / major institution adopting it officially
  critic                  pushes back on or fact-checks this framing
  mention                 uses it in passing

Use web search to ground carriers in real, datable sources. Prefer concrete
outlets/accounts with a representative headline or quote.

Output ONLY JSON:
{
  "framings": [
    {
      "name": "Accountability",
      "question": "the underlying question this lens asks",
      "key_claim": "the narrative statement this framing asserts (one sentence, fingerprintable)",
      "description": "1-2 sentences on what this framing is",
      "emphasizes": "what this framing foregrounds",
      "downplays": "what this framing minimizes or omits",
      "tone": "measured | populist | alarmed | accusatory | celebratory | dismissive | ...",
      "sample_headlines": ["representative headline 1", "headline 2"],
      "carriers": [
        {"name": "outlet/author/handle", "url": "...", "carrier_type": "news|social|political|institutional|academic|other",
         "amplifier_role": "originator|early-amplifier|mass-amplifier|institutional-adoption|critic|mention",
         "excerpt": "a representative headline or quote", "date": "ISO date if known",
         "role_evidence": "one-line justification for the role"}
      ]
    }
  ],
  "search_notes": "what you searched and any coverage gaps"
}

IMPORTANT: After searching, output ONLY the JSON object. Do NOT write any
preamble, explanation, or summary before it — begin your final response
with the opening brace `{`. Preamble wastes output budget and can truncate
the JSON.
"""


EVENT_SHARED_FOUNDATION_SYSTEM = """\
You are a shared-ground analyst for Tributary. Given a neutral event and the
distinct framings forming around it, identify the SHARED FACTUAL GROUND —
the specific, concrete facts that (almost) all framings accept, regardless
of how they interpret them. This common ground is often the most clarifying
part of an analysis because it's what is NOT contested.

Include only SPECIFIC, ATOMIC, AGREED facts (names, dates, places, concrete
actions, numbers, outcomes). EXCLUDE interpretations, value judgments,
predictions, and anything one framing disputes. Mark a fact verified=true
only if it is the kind of concrete primary-record fact that could be
confirmed against an authoritative source; otherwise verified=false.

Also list the genuine points of disagreement (what the framings actually
argue about).

Output ONLY JSON:
{
  "verified_facts": [{"statement": "...", "source_url": "...", "note": ""}],
  "unverified_shared_claims": [{"statement": "...", "note": "why unverified"}],
  "points_of_disagreement": ["..."],
  "summary": "1-2 sentences on the shared foundation beneath the disagreement"
}
"""


EVENT_OMISSIONS_SYSTEM = """\
You are an omissions analyst for Tributary. Given the distinct framings of
an event, identify for EACH framing what it leaves out that OTHER framings
cover — the mechanism by which echo chambers work. A reader who only saw
this framing would not know X.

For each omission name: what is missing, which other framing surfaces it,
the impact of not knowing it, and the type (factual / perspective / context).

Output ONLY JSON keyed by framing name:
{
  "omissions_by_framing": {
    "Accountability": [
      {"what_is_missing": "...", "found_in_framing": "Use of Force", "impact": "...", "omission_type": "perspective"}
    ]
  }
}
"""


EVENT_GAPS_SYSTEM = """\
You are a completeness critic for Tributary. Given a neutral event and the
framings identified so far, assess what framings or perspectives might still
be MISSING from the map — angles, communities, or questions not yet
represented. Be concrete. Aggressively neutral: name missing framings from
any part of the spectrum.

Output ONLY JSON:
{ "gap_analysis": "2-4 sentences on what framings or perspectives may still be missing" }
"""


class EventAnalyzer:
    """Downstream generator: given an event/statement, map the shared factual
    ground and the distinct narrative framings forming around it (and who
    carries each). The dual of FingerprintGenerator. Every step is
    model-configurable — the on-ramp to per-step provider routing."""

    # Per-step model defaults. Override via the `models` dict (or a blanket
    # model). The web-search step (framings) needs a search-capable model.
    DEFAULT_MODELS = {
        "event_desc": HAIKU,
        "framings": SONNET,            # web_search — ecosystem sweep, quality-critical
        "shared_foundation": HAIKU,
        "omissions": HAIKU,
        "gaps": HAIKU,
    }

    def __init__(self, client: Optional[anthropic.AsyncAnthropic] = None,
                 max_searches: int = 10, models: Optional[dict] = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.max_searches = max_searches
        self.models = dict(self.DEFAULT_MODELS)
        if models:
            self.models.update(models)

    def _web_search_tool(self) -> dict:
        tool = {"type": "web_search_20250305", "name": "web_search"}
        if self.max_searches and self.max_searches > 0:
            tool["max_uses"] = self.max_searches
        return tool

    async def describe_event(self, raw: str) -> tuple:
        """Neutralize the input into a factual event description + date."""
        _log_progress("Event: neutral description (step 1/5)")
        resp = await _create_with_retry(
            self.client, model=self.models["event_desc"], max_tokens=512,
            system=[{"type": "text", "text": EVENT_DESCRIBE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": raw[:8000]}],
        )
        d = _parse_json_safe(_response_text(resp))
        return (_clean_text_field(d.get("event")) or raw.strip(),
                _clean_text_field(d.get("event_date")))

    def framings_request_params(self, event: str, scope: Scope) -> dict:
        """The Messages-API params for the framing search, so the call can run
        either live (search_framings) or batched (the Batch-API corpus path)."""
        user = (f"EVENT:\n{event}\n\nSCOPE: {_scope_clause(scope)}\n\n"
                "Map the distinct narrative framings forming around this event "
                "and who creates/amplifies each. Be balanced across the spectrum.")
        return {
            "model": self.models["framings"],
            "max_tokens": 16384,
            "tools": [self._web_search_tool()],
            "system": [{"type": "text", "text": EVENT_FRAMINGS_SYSTEM,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }

    def parse_framings_text(self, raw_text: str) -> tuple:
        """Parse a framing-search response (live or batched) into framings +
        notes, with the truncation-salvage fallback."""
        data = _parse_json_safe(raw_text)
        raw_framings = data.get("framings") or []
        # Salvage: tool-using models often wrap the JSON in a chatty preamble
        # and can hit the token cap mid-array. If the clean parse found
        # nothing, recover the complete framing objects that DID finish.
        if not raw_framings:
            raw_framings = _recover_json_array_objects(raw_text, "framings")
            if raw_framings:
                _log_progress(f"Event: recovered {len(raw_framings)} framings "
                              "from truncated/wrapped output")
        framings = self._parse_framings(raw_framings)
        if not framings:
            _save_debug_response("event_framings", raw_text)
        return framings, _clean_text_field(data.get("search_notes"))

    async def search_framings(self, event: str, scope: Scope) -> tuple:
        """Find the distinct framings + carriers across the ecosystem (live)."""
        _log_progress("Event: framing search (step 2/5, web_search)")
        t0 = time.monotonic()
        resp = await _create_with_retry(
            self.client, retry_on_empty_text=True,
            **self.framings_request_params(event, scope),
        )
        framings, notes = self.parse_framings_text(_response_text(resp))
        _log_progress(f"Event: {len(framings)} framings found in "
                      f"{time.monotonic() - t0:.1f}s")
        return framings, notes

    async def verify_event(self, analysis: EventAnalysis) -> EventAnalysis:
        """Post-pass: HTTP-check every framing carrier's URL (with Wayback
        fallback). Free (no API), flags dead/hallucinated carrier links so
        the corpus is trustworthy."""
        carriers = [c for fr in analysis.framings for c in fr.carriers if c.url]
        if not carriers:
            return analysis
        _log_progress(f"Event: verifying {len(carriers)} carrier URLs")
        headers = {"User-Agent": _VERIFY_USER_AGENT}
        async with httpx.AsyncClient(headers=headers, timeout=_VERIFY_TIMEOUT) as http:
            await asyncio.gather(
                *[_verify_carrier(http, c) for c in carriers],
                return_exceptions=True,
            )
        ok = sum(1 for c in carriers if c.verified)
        _log_progress(f"Event: carriers verified {ok}/{len(carriers)}")
        return analysis

    async def build_event_from_framings(
        self, event: str, event_date: str, framings: list,
        source_urls: Optional[list] = None, framings_only: bool = False,
        verify: bool = True,
    ) -> EventAnalysis:
        """Given already-found framings (e.g. from a batch), run the cheap
        live Haiku steps (shared foundation / omissions / gaps), verify the
        carrier URLs, and assemble the EventAnalysis. Shared by the live and
        batch paths."""
        if not framings:
            return EventAnalysis(
                event=event, event_date=event_date, source_urls=source_urls or [],
                framings=[],
                gap_analysis="No narrative framings were identified for this event.",
                provenance=Provenance.ai(model=self.models["framings"]),
            )
        if framings_only:
            analysis = EventAnalysis(
                event=event, event_date=event_date, source_urls=source_urls or [],
                framings=framings,
                gap_analysis="(framings-only — shared foundation, omissions, and "
                             "gap analysis were skipped)",
                provenance=Provenance.ai(model=self.models["framings"]),
            )
            return await self.verify_event(analysis) if verify else analysis
        shared, _omit = await asyncio.gather(
            self.extract_shared_foundation(event, framings),
            self.analyze_omissions(framings),
        )
        gap_analysis = await self.detect_gaps(event, framings)
        analysis = EventAnalysis(
            event=event, event_date=event_date, source_urls=source_urls or [],
            shared_foundation=shared, framings=framings, gap_analysis=gap_analysis,
            provenance=Provenance.ai(model=self.models["framings"]),
        )
        return await self.verify_event(analysis) if verify else analysis

    def _parse_framings(self, raw_list) -> list:
        framings = []
        for fr in raw_list:
            if not isinstance(fr, dict):
                continue
            carriers = []
            for c in (fr.get("carriers") or []):
                if not isinstance(c, dict):
                    continue
                role_str = str(c.get("amplifier_role", "")).strip().lower()
                try:
                    role = AmplifierRole(role_str) if role_str else AmplifierRole.UNKNOWN
                except ValueError:
                    role = AmplifierRole.UNKNOWN
                carriers.append(FramingCarrier(
                    name=_clean_text_field(c.get("name")),
                    url=_clean_text_field(c.get("url")),
                    carrier_type=_clean_text_field(c.get("carrier_type")),
                    amplifier_role=role,
                    excerpt=_clean_text_field(c.get("excerpt")),
                    date=_clean_text_field(c.get("date")),
                    role_evidence=_clean_text_field(c.get("role_evidence")),
                    provenance=Provenance.ai(model=self.models["framings"]),
                ))
            headlines = [h for h in (fr.get("sample_headlines") or []) if h]
            framings.append(NarrativeFraming(
                name=_clean_text_field(fr.get("name")),
                question=_clean_text_field(fr.get("question")),
                key_claim=_clean_text_field(fr.get("key_claim")),
                description=_clean_text_field(fr.get("description")),
                emphasizes=_clean_text_field(fr.get("emphasizes")),
                downplays=_clean_text_field(fr.get("downplays")),
                tone=_clean_text_field(fr.get("tone")),
                sample_headlines=[str(h).strip() for h in headlines],
                carriers=carriers,
                provenance=Provenance.ai(model=self.models["framings"]),
            ))
        return framings

    async def extract_shared_foundation(self, event: str, framings: list) -> SharedFoundation:
        _log_progress("Event: shared foundation (step 3/5)")
        framing_brief = [{"name": f.name, "key_claim": f.key_claim,
                          "emphasizes": f.emphasizes} for f in framings]
        user = (f"EVENT:\n{event}\n\nFRAMINGS:\n{json.dumps(framing_brief, indent=2)}")
        resp = await _create_with_retry(
            self.client, model=self.models["shared_foundation"], max_tokens=2048,
            system=[{"type": "text", "text": EVENT_SHARED_FOUNDATION_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        d = _parse_json_safe(_response_text(resp))
        m = self.models["shared_foundation"]

        def _facts(items, verified):
            out = []
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                out.append(SharedFact(
                    statement=_clean_text_field(it.get("statement")),
                    verified=verified,
                    source_url=_clean_text_field(it.get("source_url")),
                    note=_clean_text_field(it.get("note")),
                    provenance=Provenance.ai(model=m),
                ))
            return [f for f in out if f.statement]

        return SharedFoundation(
            verified_facts=_facts(d.get("verified_facts"), True),
            unverified_shared_claims=_facts(d.get("unverified_shared_claims"), False),
            points_of_disagreement=[str(p).strip() for p in (d.get("points_of_disagreement") or []) if p],
            summary=_clean_text_field(d.get("summary")),
        )

    async def analyze_omissions(self, framings: list) -> None:
        """Populate each framing's omissions in place (comparative, no search)."""
        if len(framings) < 2:
            return
        _log_progress("Event: omissions per framing (step 4/5)")
        brief = [{"name": f.name, "key_claim": f.key_claim,
                  "emphasizes": f.emphasizes, "downplays": f.downplays} for f in framings]
        resp = await _create_with_retry(
            self.client, model=self.models["omissions"], max_tokens=3072,
            system=[{"type": "text", "text": EVENT_OMISSIONS_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": json.dumps(brief, indent=2)}],
        )
        d = _parse_json_safe(_response_text(resp))
        by_framing = d.get("omissions_by_framing") or {}
        m = self.models["omissions"]
        for f in framings:
            for o in (by_framing.get(f.name) or []):
                if not isinstance(o, dict):
                    continue
                f.omissions.append(FramingOmission(
                    what_is_missing=_clean_text_field(o.get("what_is_missing")),
                    found_in_framing=_clean_text_field(o.get("found_in_framing")),
                    impact=_clean_text_field(o.get("impact")),
                    omission_type=_clean_text_field(o.get("omission_type")),
                    provenance=Provenance.ai(model=m),
                ))

    async def detect_gaps(self, event: str, framings: list) -> str:
        _log_progress("Event: gap analysis (step 5/5)")
        names = [f.name for f in framings]
        user = f"EVENT:\n{event}\n\nFRAMINGS IDENTIFIED: {', '.join(names)}"
        resp = await _create_with_retry(
            self.client, model=self.models["gaps"], max_tokens=512,
            system=[{"type": "text", "text": EVENT_GAPS_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        d = _parse_json_safe(_response_text(resp))
        return _clean_text_field(d.get("gap_analysis"))

    async def analyze_event(
        self,
        raw: str,
        scope: Optional[Scope] = None,
        source_urls: Optional[list] = None,
        framings_only: bool = False,
        verify: bool = True,
    ) -> EventAnalysis:
        """Full downstream pipeline: neutralize -> framings+carriers ->
        shared foundation -> omissions -> gaps -> verify carriers. Framing
        key_claims are left un-fingerprinted (decoupled); trace on demand."""
        scope = scope or Scope()
        event, event_date = await self.describe_event(raw)
        framings, notes = await self.search_framings(event, scope)

        # If the framing search came back empty (transient API empty-response,
        # or a very recent/niche event), build_event_from_framings returns a
        # clear empty result instead of burning the downstream calls.
        if not framings:
            _log_progress("Event: framing search returned no framings — "
                          "skipping shared-foundation / omissions / gap steps")
        elif framings_only:
            _log_progress("Event: framings-only — skipping shared-foundation "
                          "/ omissions / gap steps")
        return await self.build_event_from_framings(
            event, event_date, framings,
            source_urls=source_urls, framings_only=framings_only, verify=verify,
        )

    async def analyze_events_batch(
        self,
        raws: list,
        scope: Optional[Scope] = None,
        framings_only: bool = False,
        verify: bool = True,
    ) -> list:
        """Batch many events through the Batch API: the expensive framing
        searches (the ~90% cost) go as ONE batch (~50% off tokens, async),
        while the cheap Haiku stages (describe, foundation, omissions, gaps)
        run live. Returns an EventAnalysis per input, order-aligned.

        Also the engine behind a single --batch event (call with one raw)."""
        scope = scope or Scope()
        # Stage 1 (live Haiku): neutral descriptions.
        descs = await asyncio.gather(*[self.describe_event(r) for r in raws])
        events = [d[0] for d in descs]
        dates = [d[1] for d in descs]

        # Stage 2 (BATCH, web_search): all framing searches in one job.
        requests = [
            {"custom_id": f"fr-{i}",
             "params": self.framings_request_params(events[i], scope)}
            for i in range(len(events))
        ]
        _log_progress(f"Event batch: submitting {len(requests)} framing "
                      "searches to the Batch API (~50% off, async)")
        results = await run_message_batch_async(requests, label="framings")

        # Stage 3 (live Haiku per event): foundation/omissions/gaps + assemble.
        analyses = []
        for i, ev in enumerate(events):
            text = results.get(f"fr-{i}")
            framings, _notes = self.parse_framings_text(text) if text else ([], "")
            if not framings:
                _log_progress(f"Event batch [{i+1}]: no framings "
                              "(empty/failed batch item)")
            analyses.append(await self.build_event_from_framings(
                ev, dates[i], framings, framings_only=framings_only, verify=verify))
        return analyses


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
    gen = FingerprintGenerator(max_searches=args.max_searches)
    scope = Scope(
        language="en",
        region=args.region,
        time_window_start=args.from_date or "",
        time_window_end=args.to_date or "",
    )

    # Lean by default; expensive layers are opt-in. --full bundles them all.
    full = args.full
    skip_conceptual = not (full or args.conceptual)
    skip_mutations = not (full or args.mutations)
    skip_evidence = not (full or args.evidence)

    # ---- Downstream mode: --event ------------------------------------
    # Map the narrative framings forming around an event (the mission's
    # second direction), instead of tracing one narrative upstream.
    if args.event:
        raw = args.claim
        if args.url:
            item = await gen._fetch_content(args.url)
            raw = (item.text if item else "") or args.url
        elif args.file:
            try:
                with open(args.file, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError as e:
                print(f"[error reading {args.file}: {e}]", file=sys.stderr)
                return
        if not raw:
            print("[error: --event needs an event statement, --url, or --file]",
                  file=sys.stderr)
            return

        models = {k: args.event_model for k in EventAnalyzer.DEFAULT_MODELS} if args.event_model else None
        analyzer = EventAnalyzer(max_searches=args.max_searches, models=models)
        if args.batch:
            # Route the expensive framing search through the Batch API (~50%
            # off, async — slower, for when you don't need it instantly).
            print("[--batch: the framing search goes through the async Batch "
                  "API; this can take minutes. Ctrl-C is safe.]", file=sys.stderr)
            analyses = await analyzer.analyze_events_batch(
                [raw], scope=scope, framings_only=args.framings_only,
                verify=not args.no_verify)
            analysis = analyses[0]
            analysis.source_urls = [args.url] if args.url else []
        else:
            analysis = await analyzer.analyze_event(
                raw, scope=scope,
                source_urls=[args.url] if args.url else [],
                framings_only=args.framings_only,
                verify=not args.no_verify,
            )

        # Decoupled framing→fingerprint: only trace framings on demand.
        if args.trace_framings and analysis.framings:
            fp_store = FingerprintStore(args.store_dir) if args.save else None
            for fr in analysis.framings:
                if not fr.key_claim:
                    continue
                _log_progress(f"Tracing framing '{fr.name}': {fr.key_claim[:50]}")
                fp = await gen.generate_fingerprint(
                    fr.key_claim, scope=scope,
                    skip_conceptual=skip_conceptual, skip_mutations=skip_mutations,
                    include_social=args.social, skip_evidence=skip_evidence,
                    skip_verification=args.no_verify,
                )
                fr.fingerprint_id = fp.fingerprint_id
                analysis.fingerprints.append(fp)
                if fp_store is not None:
                    try:
                        fp_store.save(fp)
                    except Exception as e:
                        _log_progress(f"Could not save fingerprint {fp.fingerprint_id}: {e}")

        print(analysis.to_json())
        if args.save:
            from pathlib import Path
            edir = Path(args.store_dir).parent / "events"
            edir.mkdir(parents=True, exist_ok=True)
            epath = edir / f"{analysis.analysis_id}.json"
            epath.write_text(analysis.to_json(), encoding="utf-8")
            print(f"[saved event analysis: {epath} "
                  f"({len(analysis.framings)} framings)]")
        return

    # ---- Multi-claim mode: --url / --file ----------------------------
    if args.url or args.file:
        text = None
        if args.file:
            try:
                with open(args.file, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as e:
                print(f"[error reading {args.file}: {e}]", file=sys.stderr)
                return

        store = FingerprintStore(args.store_dir) if args.save else None
        analysis_dir = None
        if args.save:
            from pathlib import Path
            analysis_dir = str(Path(args.store_dir).parent / "analyses")

        analysis = await gen.analyze_source(
            url=args.url,
            text=text,
            scope=scope,
            max_claims=args.max_claims,
            extract_only=args.extract_only,
            store=store,
            analysis_dir=analysis_dir,   # incremental manifest save after each claim
            skip_conceptual=skip_conceptual,
            skip_mutations=skip_mutations,
            include_social=args.social,
            skip_evidence=skip_evidence,
            skip_verification=args.no_verify,
        )

        print(analysis.to_json())

        # The manifest was already saved incrementally inside analyze_source.
        if analysis_dir:
            print(f"[saved analysis: {analysis_dir}/{analysis.analysis_id}.json "
                  f"({len(analysis.fingerprints)} fingerprints)]")
        return

    if not args.claim:
        print("[error: provide a claim, or use --url / --file]", file=sys.stderr)
        return

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

    # No dedup hit (or --force): run the pipeline (lean by default; opt-in
    # layers per the flags), reusing the L1 we already generated.
    fp = await gen.generate_fingerprint(
        args.claim,
        scope=scope,
        context=context,
        skip_conceptual=skip_conceptual,
        skip_mutations=skip_mutations,
        include_social=args.social,
        skip_evidence=skip_evidence,
        skip_verification=args.no_verify,
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
    parser.add_argument("claim", nargs="?", default=None,
                        help="A single claim or narrative to fingerprint. "
                             "Omit when using --url or --file.")
    parser.add_argument("--url",
                        help="Multi-claim mode: extract the significant claims from a "
                             "URL (web article, Bluesky, YouTube, X) and fingerprint each.")
    parser.add_argument("--file",
                        help="Multi-claim mode: extract claims from a local text file "
                             "(e.g. a pasted transcript) and fingerprint each.")
    parser.add_argument("--max-claims", type=int, default=5,
                        help="Max claims to extract in multi-claim mode (default 5). "
                             "Each claim is a full fingerprint, so cost scales with this.")
    parser.add_argument("--extract-only", action="store_true",
                        help="Multi-claim mode: extract and list the claims only, without "
                             "fingerprinting them (cheap preview — one Haiku call).")
    parser.add_argument("--event", action="store_true",
                        help="DOWNSTREAM mode: map the narrative framings forming around an "
                             "event (the claim, --url, or --file), and who creates/amplifies "
                             "each. Produces an EventAnalysis instead of a fingerprint.")
    parser.add_argument("--event-model", default="",
                        help="Blanket model override for all EventAnalysis steps "
                             "(otherwise per-step defaults: Haiku for cheap steps, Sonnet "
                             "for the framing search). Per-step control is available "
                             "programmatically via EventAnalyzer(models=...).")
    parser.add_argument("--trace-framings", action="store_true",
                        help="In --event mode, also fingerprint each framing's key_claim "
                             "(decoupled by default — off, since each is a full fingerprint).")
    parser.add_argument("--framings-only", action="store_true",
                        help="In --event mode, stop after the framing search and skip the "
                             "shared-foundation / omissions / gap steps (~$0.20 vs ~$0.50) "
                             "— a cheap preview for iterating on events.")
    parser.add_argument("--batch", action="store_true",
                        help="In --event mode, route the expensive framing search through "
                             "the async Batch API (~50%% off tokens). Slower (minutes, no "
                             "SLA) — use when you don't need the result instantly.")
    parser.add_argument("--context", help="Optional context where the claim appeared")
    parser.add_argument("--region", default="US",
                        help='Regional focus for scope (default: "US")')
    parser.add_argument("--from-date", default="",
                        help="Time window start (ISO date)")
    parser.add_argument("--to-date", default="",
                        help="Time window end (ISO date)")
    parser.add_argument("--lexical-only", action="store_true",
                        help="Generate only the L1 lexical layer (no L2, no web search)")
    parser.add_argument("--full", action="store_true",
                        help="Run the full deep pipeline: conceptual lineage + "
                             "mutations + evidence landscape (~$0.75–1.00/claim). "
                             "Default is LEAN — lexical lineage only (~$0.15–0.25).")
    parser.add_argument("--conceptual", action="store_true",
                        help="Add the L4 conceptual lineage pass (~+$0.30). "
                             "Opt-in; included by --full.")
    parser.add_argument("--evidence", action="store_true",
                        help="Add the evidence-landscape generation (~+$0.20). "
                             "Opt-in; included by --full.")
    parser.add_argument("--mutations", action="store_true",
                        help="Add the mutation-analysis post-pass over the lineages "
                             "(~+$0.10). Opt-in; included by --full.")
    parser.add_argument("--social", action="store_true",
                        help="Search Bluesky for platform-native uses of the diagnostic "
                             "n-grams and attach to the lexical lineage. Requires "
                             "BLUESKY_HANDLE + BLUESKY_APP_PASSWORD in env; gracefully "
                             "skipped if missing. Adds ~$0.01–0.05 per fingerprint.")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the post-generation URL + quote verification pass "
                             "(URLs and quotes won't be checked against live pages; "
                             "Wayback fallback won't be populated). Saves ~30s "
                             "wallclock but no API cost.")
    parser.add_argument("--max-searches", type=int, default=10,
                        help="Cap web searches per Sonnet call (default 10). Lower = "
                             "cheaper and faster with less peripheral depth and "
                             "adversarial thoroughness; also improves run-to-run "
                             "consistency. Set 0 to remove the cap.")
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
