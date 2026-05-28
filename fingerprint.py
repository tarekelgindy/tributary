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
    Attribution,
    AttestedInstance,
    ConceptualLayer,
    Domain,
    EvidenceLandscape,
    FramePrimitive,
    GenealogyLayer,
    GenealogyStatus,
    InformationSource,
    LexicalLayer,
    LineageRecord,
    Mutation,
    NarrativeFingerprint,
    RhetoricalLayer,
    Scope,
    SocialAttestedInstance,
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
            date=_clean_text_field(d.get("date")),
            source_url=_clean_text_field(d.get("source_url")),
            source_title=_clean_text_field(d.get("source_title")),
            author=_clean_text_field(d.get("author")),
            lexical_form_seen=_clean_text_field(d.get("lexical_form_seen")),
            exact_quote=_clean_text_field(d.get("exact_quote")),
            confidence=float(d.get("confidence", 0.5)),
            evidence=_clean_text_field(d.get("evidence")),
            amplifier_role=role,
            role_evidence=_clean_text_field(d.get("role_evidence")),
        )
    except (TypeError, ValueError):
        return None


_EMPTY_SENTINELS = {"none", "n/a", "null", "nil", "not applicable", "not specified"}


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


def _information_source_from_dict(d: dict) -> InformationSource:
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
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
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
        skip_conceptual: bool = False,
        skip_mutations: bool = False,
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
        skip_conceptual: bool = False,
        skip_mutations: bool = False,
        include_social: bool = False,
        skip_evidence: bool = False,
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
            attribution=Attribution(source="ai", model=SONNET),
        )

        # Post-pass: URL existence + quote-in-page verification for every
        # cited source. Pure HTTP, no API cost. Parallel-fetches all URLs.
        if not skip_verification:
            fp = await self.verify_fingerprint(fp)

        return fp


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
        include_social=args.social,
        skip_evidence=args.no_evidence,
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
    parser.add_argument("--social", action="store_true",
                        help="Search Bluesky for platform-native uses of the diagnostic "
                             "n-grams and attach to the lexical lineage. Requires "
                             "BLUESKY_HANDLE + BLUESKY_APP_PASSWORD in env; gracefully "
                             "skipped if missing. Adds ~$0.01–0.05 per fingerprint.")
    parser.add_argument("--no-evidence", action="store_true",
                        help="Skip the evidence-landscape generation (saves "
                             "~$0.15–0.25 per fingerprint). Default behavior includes "
                             "it as a core part of the output.")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the post-generation URL + quote verification pass "
                             "(URLs and quotes won't be checked against live pages; "
                             "Wayback fallback won't be populated). Saves ~30s "
                             "wallclock but no API cost.")
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
