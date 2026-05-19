"""
Tributary Source Tracer Agent
=============================
Multi-model AI agent that traces factual claims back to their primary sources.

Architecture:
  - Haiku 4.5: Fast classification and orchestration (claim extraction, search planning)
  - Sonnet 4.6: Deep analysis (source evaluation, chain following, synthesis)

Pipeline:
  1. Extract claims from content and classify them
  2. Generate targeted search queries for each traceable claim
  3. Retrieve and analyze candidate sources
  4. Follow citation chains toward primary sources
  5. Synthesize a provenance chain with confidence scores
"""

import json
import hashlib
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

import anthropic
import httpx


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class ContentLabel(str, Enum):
    UNPROVEN = "unproven"          # Default — no classification yet
    OPINION = "opinion"            # Pure personal preference, not traceable
    NARRATIVE = "narrative"        # Interpretive framing with a traceable origin
    STUDY_ANALYSIS = "study"       # References facts, studies, or data
    UNVERIFIABLE = "unverifiable"  # Unclear sources / can't confirm
    FACT = "fact"                  # Claims to state a verifiable fact


class SourceTier(str, Enum):
    PRIMARY = "primary"           # Original data, study, official report
    SECONDARY = "secondary"       # News article citing primary source
    TERTIARY = "tertiary"         # Blog/social post citing secondary
    ORIGINATOR = "originator"     # Origin point of a narrative/framing
    AMPLIFIER = "amplifier"       # Amplified/spread a narrative
    CRITIC = "critic"             # Critiques or fact-checks a narrative
    UNKNOWN = "unknown"


@dataclass
class Claim:
    text: str
    label: ContentLabel
    context: str                          # Surrounding text for disambiguation
    claim_id: str = ""

    def __post_init__(self):
        if not self.claim_id:
            self.claim_id = hashlib.sha256(
                self.text.lower().strip().encode()
            ).hexdigest()[:12]


@dataclass
class Source:
    url: str
    title: str
    tier: SourceTier
    relevant_excerpt: str
    referenced_url: Optional[str] = None   # URL of source this one references
    flow_type: Optional[str] = None        # cites|co_reports|amplifies|responds_to|etc.
    flow_evidence: Optional[str] = None    # Why this flow type was assigned
    published_date: Optional[str] = None
    author: Optional[str] = None
    confidence: float = 0.0
    retrieved_at: str = ""

    # Legacy alias
    @property
    def cites(self) -> Optional[str]:
        return self.referenced_url

    def __post_init__(self):
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ProvenanceChain:
    claim: Claim
    sources: list[Source] = field(default_factory=list)
    primary_source: Optional[Source] = None
    chain_complete: bool = False
    confidence: float = 0.0
    summary: str = ""
    traced_at: str = ""
    hops: int = 0

    def __post_init__(self):
        if not self.traced_at:
            self.traced_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return asdict(self)


# -- Tree-trace data structures --

@dataclass
class TraceNode:
    """A node in a provenance tree."""
    node_id: str                              # short hash of url
    url: str
    title: str = ""
    outlet: str = ""                          # publisher domain
    author: str = ""
    published_date: Optional[str] = None
    tier: str = "unknown"                     # primary|originator|secondary|amplifier|critic|social
    exact_phrasing: str = ""                  # the claim as it appears in this source
    attribution_text: str = ""                # "according to X" language
    attributed_to: list[str] = field(default_factory=list)
    confidence: float = 0.0
    is_origin: bool = False
    parents: list[dict] = field(default_factory=list)
    # parents: [{"node_id": str, "flow_type": str, "evidence": str}]
    children: list[str] = field(default_factory=list)  # node_ids
    on_main_thread: bool = False
    # Multi-framing support: which framings this source carries, with strength
    framings: dict = field(default_factory=dict)
    # framings: {"framing_id": strength_0_to_1, ...}

    def to_dict(self):
        return asdict(self)


@dataclass
class Framing:
    """A distinct framing of the claim."""
    framing_id: str
    label: str
    framing_text: str
    ideological_tradition: str = ""
    characteristic_phrases: list[str] = field(default_factory=list)
    is_neutral: bool = False
    is_original_input: bool = False  # was this what the user submitted?

    def to_dict(self):
        return asdict(self)


@dataclass
class TraceTree:
    """A complete provenance tree for a single claim, with optional multi-framing overlay."""
    claim_text: str
    neutral_phrasing: str = ""
    nodes: list[TraceNode] = field(default_factory=list)
    main_thread: list[str] = field(default_factory=list)  # node_ids in order
    origins: list[str] = field(default_factory=list)       # node_ids
    traced_at: str = ""
    depth: str = "standard"
    days_back: Optional[int] = None
    cost_usd: float = 0.0
    # Multi-framing
    framings: list[Framing] = field(default_factory=list)
    framing_mode: str = "single"  # single|multi-frame|discover-framings

    def __post_init__(self):
        if not self.traced_at:
            self.traced_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "claim_text": self.claim_text,
            "neutral_phrasing": self.neutral_phrasing,
            "nodes": [n.to_dict() for n in self.nodes],
            "main_thread": self.main_thread,
            "origins": self.origins,
            "traced_at": self.traced_at,
            "depth": self.depth,
            "days_back": self.days_back,
            "cost_usd": self.cost_usd,
            "framings": [f.to_dict() for f in self.framings],
            "framing_mode": self.framing_mode,
            "stats": {
                "total_nodes": len(self.nodes),
                "origins": len(self.origins),
                "main_thread_length": len(self.main_thread),
                "by_tier": {
                    tier: sum(1 for n in self.nodes if n.tier == tier)
                    for tier in {n.tier for n in self.nodes}
                },
                "framings_count": len(self.framings),
                "nodes_per_framing": {
                    f.framing_id: sum(
                        1 for n in self.nodes
                        if n.framings.get(f.framing_id, 0) >= 0.5
                    )
                    for f in self.framings
                },
            },
        }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

MAX_SEARCH_RESULTS = 5
MAX_CHAIN_HOPS = 4
MIN_CONFIDENCE_THRESHOLD = 0.3

# System prompts — cached across calls for cost savings
CLAIM_EXTRACTOR_PROMPT = """You are a claim extraction engine for Tributary, an information 
provenance tool. Given a piece of content, extract every discrete claim and classify each one.

CONTENT FORMAT AWARENESS:
- For SHORT content (tweets, social posts, <500 chars): The entire post is usually one claim 
  or a small cluster of claims. Extract the FULL statement as a claim — do NOT break it into 
  sentence fragments. A tweet is already bite-sized; preserve it whole.
- For LONG content (articles, transcripts): Extract individual claims from the text. Each 
  claim should be a complete, self-contained assertion.

For EACH claim, apply TWO tests:

TEST 1 — TRACEABILITY: "Could this be confirmed or denied by pointing to a specific 
primary data source (dataset, report, study, government filing)?"

TEST 2 — NARRATIVE ORIGIN: "Even if this isn't a verifiable fact, does it use a specific 
framing, talking point, or interpretive frame that likely originated somewhere — a political 
speech, think tank, media figure, viral post, or coordinated messaging?"

Classifications:

- "fact": Traceable to DATA. Contains specific, verifiable details — numbers, dates, named 
  sources, measurable outcomes — that point to a findable primary source.
  SIGNALS: specific numbers ("added 256,000 jobs"), named institutions ("the BLS reported"), 
  dates ("in December 2024"), measurable events ("unemployment fell to 4.1%").
  TRACE TARGET: The primary data source.

- "study": Traceable to RESEARCH. References a specific study, dataset, analysis, or 
  research finding that can be located.
  SIGNALS: "a study found", "research shows", specific percentages tied to research 
  ("35% more productive"), named researchers or institutions.
  TRACE TARGET: The specific study or paper.

- "narrative": Traceable to an ORIGIN POINT. This is an interpretive claim, framing, or 
  talking point — not a raw fact, but not a pure personal preference either. It makes an 
  assertion about how to understand or interpret events, and that framing likely originated 
  somewhere specific and spread through information channels.
  SIGNALS: evaluative framings presented as reality ("the economy is failing", "the border 
  is open", "AI will replace all jobs"), causal claims without data ("X caused Y"), 
  characterizations ("this is the worst crisis in decades"), loaded framing ("radical left", 
  "far right extremists"), talking points that multiple commentators repeat, claims that 
  attribute motives ("they want to destroy...").
  TRACE TARGET: The earliest or most prominent source of this specific framing.

  KEY DISTINCTION from opinion: A narrative is SHARED and SPREADABLE. It's not just one 
  person's view — it's a framing you'd expect to find other people using with similar 
  wording. "I prefer chocolate ice cream" is opinion. "The woke mob is destroying America" 
  is narrative — it has an origin, it spread, and it can be traced.

- "opinion": NOT traceable. A genuinely personal, subjective judgment that doesn't come 
  from an external source. Individual taste, personal experience, unique perspective.
  SIGNALS: "I think", "I prefer", "in my experience", personal recommendations, aesthetic 
  preferences, statements that are uniquely personal and wouldn't be repeated by others 
  in the same wording.
  NOTE: This category should be SMALL. Most things that look like "opinions" in political 
  or cultural commentary are actually NARRATIVES with traceable origins.

- "unverifiable": Sounds factual but practically untraceable. Vague sourcing, unnamed 
  experts, unspecified data.
  SIGNALS: "experts say" (which?), "studies show" (which?), "many people believe", 
  unnamed sources, vague quantities.

- "unproven": Default when uncertain.

IMPORTANT EXAMPLES:
  "GDP grew 3.1% in Q3" → FACT (specific number, traceable to BEA)
  "A Harvard study found X" → STUDY (named institution, findable)
  "The economy is failing" → NARRATIVE (evaluative framing with traceable origin)
  "Biden's border crisis" → NARRATIVE (political framing, traceable to specific usage)
  "AI will take everyone's jobs" → NARRATIVE (widespread claim with traceable origin)
  "I prefer working from home" → OPINION (genuinely personal)
  "Sources say the deal is close" → UNVERIFIABLE (unnamed sources)

Respond with ONLY a JSON array. No markdown fences. Each element:
{
  "text": "the exact claim as stated — preserve full sentences, never fragments",
  "label": "fact|study|narrative|opinion|unverifiable|unproven",
  "traceable_to": "for fact/study: what data source. for narrative: what type of origin (political speech, think tank, media figure, viral post, etc). null if not traceable",
  "context": "1-2 sentences of surrounding context, or empty if the content is a single post"
}"""

SEARCH_PLANNER_PROMPT = """You are a search query planner. Given a factual claim, generate 
2-4 targeted search queries designed to find the ORIGINAL source of this information.

Strategy:
- Query 1: Search for the specific data point or statistic mentioned
- Query 2: Search for the institution or entity most likely to have published this
- Query 3: Search for the claim in a news context to find who reported it first
- Query 4 (optional): Search for the underlying dataset or study

Keep queries short (3-6 words). Be specific. Include years, names, or numbers from the claim.

Respond with ONLY a JSON array of query strings. No markdown fences."""

NARRATIVE_SEARCH_PLANNER_PROMPT = """You are a search query planner for NARRATIVE TRACING. 
Given a narrative claim or talking point, generate 3-5 targeted search queries designed to 
find WHERE THIS FRAMING ORIGINATED — not whether it's true, but who said it first and how 
it spread.

Strategy:
- Query 1: Search for the exact or near-exact phrasing in quotes to find early usage
- Query 2: Search for the framing + political figures, pundits, or organizations who 
  might have originated it
- Query 3: Search for the framing + "talking point" or "narrative" or "messaging"
- Query 4: Search for the topic + think tanks, PACs, or media organizations
- Query 5 (optional): Search for the framing in press releases, speeches, or memos

The goal is to find the EARLIEST or MOST PROMINENT usage of this specific framing, 
not to fact-check the claim.

Keep queries short (3-6 words). Include distinctive phrases from the claim.

Respond with ONLY a JSON array of query strings. No markdown fences."""

NARRATIVE_ANALYZER_PROMPT = """You are a narrative origin analyst for Tributary, an 
information provenance tool. You will be given a NARRATIVE CLAIM (a talking point, framing, 
or interpretive assertion) and the content of a web page. 

Your job is NOT to fact-check. Your job is to determine whether this page is part of the 
ORIGIN or SPREAD of this narrative.

Determine:
1. Does this page use the same or very similar framing as the claim?
2. What role does this page play in the narrative's lifecycle?
   - "originator": This appears to be where the framing was coined or first used 
     prominently (a speech, op-ed, think tank report, viral post)
   - "amplifier": This repeats or amplifies the framing (news coverage of the originator, 
     commentators adopting the framing, social media spread)
   - "critic": This discusses or critiques the framing (fact-checks, opposing commentary)
   - "unrelated": Not relevant to this narrative
3. When was this framing used? (Date if available)
4. Who is using it? (Person, organization, outlet)
5. Does this page reference an earlier source of the framing? If so, what is the 
   RELATIONSHIP?
   - "cites": Explicitly attributes the framing to another source
   - "co_reports": Uses the same framing independently (no evidence of having read the other)
   - "amplifies": Spreads the framing without attribution
   - "responds_to": Reacts to or critiques the other source's use of the framing
   - "distorts": Takes the framing and changes its meaning
   - "paraphrases": Restates the framing in different words

Respond with ONLY JSON. No markdown fences:
{
  "relevant": true/false,
  "role": "originator|amplifier|critic|unrelated",
  "who": "person or organization using the framing",
  "date": "date of usage if available, or null",
  "relevant_excerpt": "the most relevant sentence showing the framing",
  "referenced_url": "URL of earlier source or null",
  "flow_type": "cites|co_reports|amplifies|responds_to|distorts|paraphrases|null",
  "flow_evidence": "why you classified this relationship type, or null",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}"""

NARRATIVE_SYNTHESIS_PROMPT = """You are a narrative provenance engine. Given a narrative 
claim and a set of analyzed sources, produce a report on where this framing originated 
and how it spread.

Determine:
1. Who appears to have originated or popularized this framing?
2. When did it first appear (approximately)?
3. How did it spread? (e.g., speech → news coverage → social media → commentators)
4. Is this an organic narrative or does it appear to be coordinated messaging?
5. Overall confidence (0.0-1.0) that we've found the origin

Respond with ONLY JSON. No markdown fences:
{
  "originator_url": "url of earliest/most prominent source, or null",
  "originator_who": "person or org who originated it, or null",
  "originator_date": "approximate date of origin, or null",
  "spread_pattern": "brief description of how it spread",
  "chain_complete": true/false,
  "confidence": 0.0-1.0,
  "summary": "2-3 sentence human-readable summary of where this narrative comes from and how it spread"
}"""

# -- Competing narratives (same data, different stories) --

COMPETING_NARRATIVES_SEARCH_PROMPT = """You are a search query planner for finding 
COMPETING NARRATIVES about the same topic or data point.

Given a factual claim and its primary source, generate 8-10 search queries designed to 
find the WIDEST POSSIBLE RANGE of framings. You MUST actively seek out perspectives that 
OPPOSE each other.

CRITICAL: Search engines tend to surface mainstream and progressive sources first. You 
must DELIBERATELY construct queries that will find conservative, libertarian, populist, 
establishment, and contrarian perspectives too — not just the dominant narrative.

Strategy:
- Queries 1-2: Search for the topic in MAINSTREAM framing
- Queries 3-4: Search for the topic from a DEFENDING/SUPPORTING perspective of the 
  party being criticized. Ask yourself: "Who would defend the other side? What language 
  would they use?" For law enforcement stories, search for "justified" or "self defense". 
  For economic policy, search for the opposing party's framing.
- Queries 5-6: Search for the topic on SPECIFIC OUTLETS known to represent the 
  underrepresented perspective. For right-leaning views: try adding "Daily Wire", 
  "Fox News", "National Review", "Breitbart", "Daily Caller". For left-leaning views: 
  try "Jacobin", "The Intercept", "Democracy Now". For libertarian: "Reason magazine".
- Queries 7-8: Search for the topic + SOCIAL MEDIA REACTION from the opposing side, 
  e.g., "topic + conservatives react" or "topic + liberals slam"
- Queries 9-10: Search for THINK TANK or ADVOCACY GROUP responses from both sides

The goal is to find the FULL SPECTRUM of framings — especially perspectives that directly 
CONTRADICT each other. If you only find one side, you have failed.

Keep queries short (3-6 words).

Respond with ONLY a JSON array of query strings. No markdown fences."""

COMPETING_NARRATIVES_ANALYZER_PROMPT = """You are a framing analyst for Tributary, an 
information provenance tool. You are given a FACTUAL CLAIM with its verified primary 
source, plus the content of a web page that discusses the same topic.

Your job is to identify HOW this page FRAMES the same underlying data. Different outlets 
and commentators take the same facts and construct different stories around them.

Determine:
1. Does this page reference or discuss the same underlying data/event as the claim?
2. What FRAMING does this page apply? How does it interpret or contextualize the data?
3. What is the ideological or institutional perspective? (Not a partisan label — describe 
   the actual perspective: pro-business, labor-focused, libertarian, progressive, 
   establishment, populist, etc.)
4. Who benefits from this framing? Who is the implied audience?
5. What does this framing EMPHASIZE vs what does it DOWNPLAY compared to the raw data?

Respond with ONLY JSON. No markdown fences:
{
  "relevant": true/false,
  "same_data": true/false,
  "framing_headline": "one sentence capturing how this source frames the data",
  "perspective": "brief description of the ideological/institutional perspective",
  "emphasizes": "what this framing highlights or focuses on",
  "downplays": "what this framing minimizes or ignores",
  "who_benefits": "who benefits from this interpretation",
  "source_type": "news|think_tank|government|advocacy|academic|commentary|social",
  "excerpt": "most relevant sentence showing the framing",
  "confidence": 0.0-1.0
}"""

SOURCE_ANALYZER_PROMPT = """You are a source credibility analyst for Tributary, an information 
provenance tool. You will be given a factual claim and the content of a web page. Determine:

1. Does this page contain information relevant to the claim?
2. What tier is this source?
   - "primary": This IS the original source (government data, research paper, official report, 
     the organization that produced the data)
   - "secondary": This reports on or cites a primary source (news articles, press coverage)
   - "tertiary": This repeats the claim without clear attribution (blogs, social posts, 
     aggregators)
   - "unknown": Can't determine
3. Does this page reference a deeper source? If so, what URL and what is the RELATIONSHIP?
   This is CRITICAL — you must distinguish between:
   - "cites": The page EXPLICITLY attributes information to the other source 
     (uses phrases like "according to", "as reported by", "the report states")
   - "co_reports": The page covers the SAME primary data but shows NO evidence of having 
     read the other source. Two news outlets both covering a government report on the same 
     day are co-reporting, NOT citing each other.
   - "amplifies": The page uses the same framing or talking points without attribution
   - "responds_to": The page was clearly written in reaction to the other source
   
   KEY TEST for cites vs co_reports: Is there explicit attribution language pointing to the 
   other source? If not, and both are covering the same underlying data, it's co_reports.
4. How confident are you (0.0-1.0) that this page is relevant to tracing this claim?

Respond with ONLY JSON. No markdown fences:
{
  "relevant": true/false,
  "tier": "primary|secondary|tertiary|unknown",
  "relevant_excerpt": "the most relevant sentence or passage from the page",
  "referenced_url": "URL of referenced source or null",
  "flow_type": "cites|co_reports|amplifies|responds_to|null",
  "flow_evidence": "why you classified the relationship this way, or null",
  "published_date": "publication date if found, or null",
  "author": "author name if found, or null",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of your assessment"
}"""

SYNTHESIS_PROMPT = """You are a provenance synthesis engine. Given a claim and a set of 
analyzed sources, produce a final provenance report.

Determine:
1. Was a primary source found? If so, which one?
2. What is the full chain from the content to the primary source?
3. Overall confidence (0.0-1.0) that we've found the true origin
4. A human-readable summary of the provenance chain

Respond with ONLY JSON. No markdown fences:
{
  "primary_source_url": "url or null",
  "chain_complete": true/false,
  "confidence": 0.0-1.0,
  "summary": "2-3 sentence human-readable summary of where this information comes from"
}"""


# -- Mutation tracking --

MUTATION_ANALYSIS_PROMPT = """You are a narrative mutation analyst for Tributary, an 
information provenance tool. You are given a CHAIN OF SOURCES that all discuss the same 
claim or narrative, ordered from earliest (closest to origin) to latest (furthest from 
origin).

Your job is to identify HOW THE CLAIM CHANGED as it passed through the chain. 
Information doesn't just spread — it MUTATES. Details get added, dropped, exaggerated, 
or recontextualized at each step.

For each transition in the chain, identify:
1. What was PRESERVED (carried forward accurately)
2. What was DROPPED (present in the earlier source but missing in the later one)
3. What was ADDED (not in the earlier source but appears in the later one)
4. What was DISTORTED (present in both but changed — numbers rounded, qualifiers 
   removed, certainty increased, context stripped)

Then provide an overall mutation summary:
- How much did the claim change from origin to final version?
- What type of mutation pattern is this? (simplification, exaggeration, 
  politicization, decontextualization, sensationalization)
- At which step did the biggest mutation occur?

Respond with ONLY JSON. No markdown fences:
{
  "transitions": [
    {
      "from_source": "source name/url",
      "to_source": "source name/url",
      "preserved": "what stayed the same",
      "dropped": "what was removed",
      "added": "what was inserted",
      "distorted": "what was changed and how"
    }
  ],
  "overall": {
    "mutation_severity": "minimal|moderate|significant|severe",
    "mutation_pattern": "simplification|exaggeration|politicization|decontextualization|sensationalization|mixed",
    "biggest_mutation_step": "from X to Y",
    "original_vs_final": "brief comparison of the claim at its origin vs its most mutated form",
    "summary": "2-3 sentence human-readable summary of how this claim transformed as it spread"
  }
}"""

# -- Missing information analysis --

MISSING_INFO_PROMPT = """You are an omission analyst for Tributary, an information 
provenance tool. You are given a piece of content AND a set of competing perspectives 
(views) on the same event from other sources.

Your job is to identify what IMPORTANT INFORMATION this content LEAVES OUT that sources 
in other views consider significant. Echo chambers work not through lies but through 
SELECTIVE EMPHASIS — showing you true things while hiding other true things.

For the given content, identify:
1. FACTUAL OMISSIONS: Specific facts, data points, or events that other views discuss 
   but this content does not mention at all
2. PERSPECTIVE OMISSIONS: Viewpoints or interpretations that exist in other views but 
   are completely absent from this content
3. CONTEXT OMISSIONS: Historical context, comparison data, or qualifying information 
   that would change how the reader interprets the content

For each omission, note:
- What is missing
- Which view/source includes it
- How knowing this would change the reader's understanding

Also assess:
- COMPLETENESS SCORE (0.0-1.0): How much of the full picture does this single source 
  provide? 1.0 = comprehensive, 0.0 = extremely narrow
- BIAS DIRECTION: Does the pattern of omissions consistently favor one perspective?

Respond with ONLY JSON. No markdown fences:
{
  "factual_omissions": [
    {
      "what_is_missing": "description of the missing fact",
      "found_in_view": "which view includes this",
      "impact": "how this changes understanding"
    }
  ],
  "perspective_omissions": [
    {
      "missing_viewpoint": "description of the absent perspective",
      "found_in_view": "which view includes this",
      "impact": "how this changes understanding"
    }
  ],
  "context_omissions": [
    {
      "missing_context": "what context is absent",
      "found_in_view": "which view includes this",
      "impact": "how this changes understanding"
    }
  ],
  "completeness_score": 0.0-1.0,
  "bias_direction": "description of any systematic bias in what's omitted",
  "summary": "2-3 sentence summary of what a reader of only this source would miss"
}"""


# -- Shared foundation analysis --

SHARED_FACTS_EXTRACTOR_PROMPT = """You are a shared-ground analyst for Tributary. Given 
multiple views (perspectives) on the same event, your job is to identify the specific 
factual claims that ALL views agree on — the shared reality underneath the disagreement.

Look for SPECIFIC FACTS that appear across the views:
- Specific names of people, organizations, places
- Specific dates and times
- Specific events that occurred (not interpretations of those events)
- Specific actions taken by named actors
- Specific numbers, quantities, outcomes

DO NOT include:
- Interpretations or framings (those are what views disagree about)
- Value judgments or characterizations
- Predictions or implications
- Quotes that one side uses but another disputes

Each fact must be:
- SPECIFIC: Concrete enough to be verified, not vague
- ATOMIC: One distinct fact per item, not a compound statement  
- AGREED: Supported by ALL views, not just some

Also identify the points of disagreement — what the views actually argue about.

Respond with ONLY JSON. No markdown fences:
{
  "shared_facts": [
    {
      "fact": "specific concrete factual statement",
      "category": "who|when|where|what|how_many|outcome",
      "appears_in_views": ["view 1 name", "view 2 name", ...]
    }
  ],
  "points_of_disagreement": [
    {
      "topic": "what the views disagree about",
      "view_positions": [
        {"view": "view name", "position": "what this view argues"}
      ]
    }
  ],
  "summary": "1-2 sentence summary of the shared foundation underneath the disagreement"
}"""

SHARED_FACTS_VERIFIER_PROMPT = """You are a fact verifier for Tributary. Given a list of 
candidate "shared facts" extracted from multiple views, and the content of a primary 
source, determine which facts are explicitly supported by the primary source.

A fact is VERIFIED if the primary source explicitly states it (or trivially implies it).
A fact is UNVERIFIED if it doesn't appear in the primary source — even if all views 
agree, it's secondary information that hasn't been confirmed at the source level.

Be strict. If the primary source doesn't directly support the claim, mark it unverified.
This isn't about whether the fact is TRUE — it's about whether it's documented in the 
primary source we have access to.

Respond with ONLY JSON. No markdown fences:
{
  "verified_facts": [
    {
      "fact": "the original fact",
      "supporting_excerpt": "the specific passage from the primary source that confirms this"
    }
  ],
  "unverified_facts": [
    {
      "fact": "the original fact",
      "reason": "why this couldn't be verified from the primary source"
    }
  ]
}"""


# -- Tree-trace prompts (--trace mode) --

TRACE_QUERY_PLANNER_PROMPT = """You are a search query planner for tracing the origin and 
spread of a claim. Given a claim, generate diverse search queries that would help find:

1. The ORIGIN — the earliest authoritative source where this claim first appeared
2. SECONDARY REPORTING — outlets that initially covered or cited the origin
3. AMPLIFICATIONS — commentators, social posts, blogs, or partisan outlets that spread it
4. CRITICS — fact-checks, rebuttals, or pushback against the claim
5. EVOLVED VERSIONS — later forms of the claim that may have shifted in meaning

Generate queries that span these categories. Use exact-phrase queries for distinctive 
language, broad keyword queries for the underlying event, and quote-search queries for 
attribution like "according to" or "report finds."

Be aggressive about finding sources at different points in the information chain.

Respond with ONLY JSON. No markdown fences:
{
  "origin_queries": ["query 1", "query 2", ...],
  "reporting_queries": ["query 1", "query 2", ...],
  "amplification_queries": ["query 1", "query 2", ...],
  "critic_queries": ["query 1", "query 2", ...],
  "evolution_queries": ["query 1", "query 2", ...]
}"""

CLAIM_PHRASING_EXTRACTOR_PROMPT = """You are an analyst for Tributary. Given the content 
of a web page and the claim being traced, extract:

1. The EXACT PHRASING this source uses for the claim (quote it directly if possible)
2. The ATTRIBUTION the source gives — who they say said it (e.g., "according to Reuters",
   "the BLS reported", "in a study by Stanford") — by name, even if no URL is provided
3. The CONFIDENCE level: how directly does this source carry the claim?

If the source doesn't actually carry this claim, mark it irrelevant.

Respond with ONLY JSON. No markdown fences:
{
  "carries_claim": true/false,
  "exact_phrasing": "how this source phrases the claim, in their words",
  "attribution_text": "what the source says about where it got the claim, or null",
  "attributed_to": ["name 1", "name 2", ...],
  "published_date": "ISO date if found, else null",
  "author": "byline if found, else null",
  "confidence": 0.0-1.0
}"""

CHAIN_RECONSTRUCTION_PROMPT = """You are an information-flow analyst for Tributary. Given a
corpus of sources that all carry the same claim, identify the PARENT-CHILD relationships
between them.

For each source, determine:
1. What other source(s) it most likely DERIVED FROM (its "parent" — could be multiple if it
   drew from several)
2. The relationship type: cites (explicit attribution), amplifies (spreads framing without 
   attribution), distorts (changes meaning), rebuts (argues against), responds_to (reacts 
   to), paraphrases (restates)
3. Whether it likely is an ORIGIN (no parents — earliest source for this claim)

Use these signals:
- Publication date: parents must be earlier than children
- Attribution: explicit "according to X" naming a known source means cites
- Phrasing similarity: very similar wording without attribution suggests amplification
- New language: deviation from earlier sources suggests mutation/distortion
- Tier: primary sources are typically origins; commentary is typically derivative

Multiple sources can be co-equal origins (e.g., two outlets independently reporting on the
same primary event). Mark these as origins with no parents.

Respond with ONLY JSON. No markdown fences:
{
  "nodes": [
    {
      "id": "source identifier matching input id",
      "is_origin": true/false,
      "parents": [
        {"id": "parent_source_id", "flow_type": "cites|amplifies|distorts|rebuts|responds_to|paraphrases", "evidence": "why this relationship was assigned"}
      ]
    }
  ],
  "main_thread_ids": ["origin_id", "next_id", "next_id", ...]
}

The main_thread is the path from an origin to the most-amplified version of the claim — 
i.e., the path most readers would have encountered."""


# -- Multi-framing prompts (--multi-frame and --discover-framings) --

FRAMING_GENERATOR_PROMPT = """You are a framing analyst for Tributary. Given a neutrally 
phrased factual claim, generate 3-5 distinct ideological framings that this same claim 
typically receives in public discourse.

Each framing should:
- Cover the same underlying facts, but with different emphasis or interpretation
- Reflect a coherent ideological worldview (progressive, conservative, libertarian,
  populist, technocratic, internationalist, etc.) — not just rhetorical variations
- Use language characteristic of how that worldview actually talks about this issue
- Be specific enough to search for in news/social media, not generic

For each framing, identify which ideological tradition typically uses it. Be balanced — 
include framings from across the political spectrum if applicable.

Respond with ONLY JSON. No markdown fences:
{
  "framings": [
    {
      "id": "short_slug",
      "label": "Short descriptive name (e.g., 'Rigged System', 'Regulatory Capture')",
      "framing_text": "How this framing typically phrases the claim, in characteristic language",
      "ideological_tradition": "Brief description of the worldview this represents",
      "search_phrases": ["distinctive phrase 1", "distinctive phrase 2", "distinctive phrase 3"]
    }
  ]
}"""

FRAMING_DISCOVERER_PROMPT = """You are a framing analyst for Tributary. Given a corpus 
of sources that all discuss the same underlying event/claim, identify the dominant 
distinct framings present in the corpus.

A "framing" is a recurring pattern of how multiple sources phrase, emphasize, and interpret 
the underlying facts. Look for:
- Repeated phrases or terminology across multiple sources
- Common interpretive frames (who is responsible, what's at stake, what should be done)
- Distinct ideological signatures in word choice, source selection, and emphasis

Group sources into framings. A single source may carry multiple framings if it engages 
with several. Aim for 3-6 distinct framings — enough to capture meaningful variety, not 
so many that they become indistinct.

Respond with ONLY JSON. No markdown fences:
{
  "framings": [
    {
      "id": "short_slug",
      "label": "Short descriptive name",
      "framing_text": "Representative phrasing of this framing as it appears in the corpus",
      "ideological_tradition": "Brief description of the worldview",
      "characteristic_phrases": ["phrase 1", "phrase 2", "phrase 3"],
      "source_ids": ["id of source 1", "id of source 2", ...]
    }
  ]
}"""

FRAMING_CLASSIFIER_PROMPT = """You are a framing classifier for Tributary. Given a source's 
exact phrasing of a claim AND a set of candidate framings, determine which framing(s) the
source carries and how strongly.

A source carries a framing if it uses language, emphasis, or interpretation characteristic
of that framing. A source can carry multiple framings (e.g., a news article that quotes 
both sides). A source might carry NO framings if it's purely factual reporting without 
interpretive framing.

For each framing, provide a strength score:
- 0.0 = does not carry this framing
- 0.3 = mentions or alludes to this framing but doesn't adopt it
- 0.6 = uses some characteristic language or emphasis of this framing
- 1.0 = clearly adopts and amplifies this framing

Respond with ONLY JSON. No markdown fences:
{
  "framing_strengths": [
    {"framing_id": "id_from_input", "strength": 0.0-1.0, "evidence": "brief justification"}
  ]
}"""


DIVERSE_SEED_QUERY_PROMPT = """You are a search strategist for Tributary. Given a claim, 
generate search queries designed to surface IDEOLOGICALLY DIVERSE coverage of it — not 
just neutral news, but commentary, op-eds, partisan outlets, think tanks, and social 
discussion across the political spectrum.

The goal is to seed a corpus that contains genuinely different framings. So intentionally 
vary your queries to hit different parts of the discourse:

- Use language characteristic of progressive populism (e.g., "rigged", "billionaire class")
- Use language characteristic of conservative populism (e.g., "swamp", "elite establishment")  
- Use language characteristic of libertarian critique (e.g., "regulatory capture", "cronyism")
- Use language characteristic of technocratic centrism (e.g., "policy reform", "structural")
- Add site-restricted queries to surface partisan coverage:
  - site:nationalreview.com OR site:reason.com OR site:thefederalist.com (right-leaning)
  - site:jacobin.com OR site:thenation.com OR site:motherjones.com (left-leaning)
  - site:reddit.com (broad public discussion)
  - site:substack.com (commentary writers)

Generate a balanced mix that does NOT favor any one ideological camp.

Respond with ONLY JSON. No markdown fences:
{
  "diverse_queries": ["query 1", "query 2", ..., "query 10"]
}"""


# ---------------------------------------------------------------------------
# Web search adapter (plug in your preferred search API)
# ---------------------------------------------------------------------------

class WebSearchAdapter:
    """
    Adapter for web search. Replace the internals with your preferred API
    (Google Custom Search, Bing, Brave, SerpAPI, etc.)
    """

    def __init__(self, api_key: str, search_engine: str = "google"):
        self.api_key = api_key
        self.search_engine = search_engine
        self.http = httpx.AsyncClient(timeout=15.0)

    async def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Returns list of {"url": ..., "title": ..., "snippet": ...}
        
        TODO: Replace with your actual search API implementation.
        This is a placeholder showing the expected interface.
        """
        # --- Google Custom Search example ---
        # resp = await self.http.get(
        #     "https://www.googleapis.com/customsearch/v1",
        #     params={
        #         "key": self.api_key,
        #         "cx": self.search_engine_id,
        #         "q": query,
        #         "num": num_results,
        #     }
        # )
        # items = resp.json().get("items", [])
        # return [{"url": i["link"], "title": i["title"], "snippet": i.get("snippet", "")} 
        #         for i in items]

        raise NotImplementedError("Wire up your search API here")

    async def fetch_page(self, url: str, max_chars: int = 15000) -> str:
        """Fetch and extract text content from a URL."""
        try:
            resp = await self.http.get(url, follow_redirects=True)
            resp.raise_for_status()
            # In production, use a proper HTML-to-text extractor
            # (trafilatura, readability, etc.)
            text = resp.text
            # Naive strip — replace with real extraction
            return text[:max_chars]
        except Exception as e:
            return f"[Error fetching {url}: {e}]"

    async def close(self):
        await self.http.aclose()


# ---------------------------------------------------------------------------
# Cache layer (plug in Redis, Neo4j, or any KV store)
# ---------------------------------------------------------------------------

class TraceCache:
    """
    Simple in-memory cache for development. In production, back this with
    Neo4j (matching your existing Tributary graph) or Redis.
    
    Key insight: claims are hashed by normalized text, so the same claim
    from different sources hits the same cache entry.
    """

    def __init__(self):
        self._store: dict[str, ProvenanceChain] = {}

    def _key(self, claim_text: str) -> str:
        return hashlib.sha256(claim_text.lower().strip().encode()).hexdigest()[:16]

    async def get(self, claim_text: str) -> Optional[ProvenanceChain]:
        return self._store.get(self._key(claim_text))

    async def put(self, chain: ProvenanceChain):
        self._store[self._key(chain.claim.text)] = chain

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------

class SourceTracer:
    """
    The main tracing agent. Orchestrates the multi-model pipeline.
    
    Usage:
        tracer = SourceTracer(search_adapter=my_search)
        
        # Trace all claims in a piece of content
        results = await tracer.trace_content(article_text)
        
        # Trace a single claim
        chain = await tracer.trace_claim(claim)
    """

    def __init__(
        self,
        search_adapter: WebSearchAdapter,
        cache: Optional[TraceCache] = None,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
    ):
        self.search = search_adapter
        self.cache = cache or TraceCache()
        self.client = anthropic_client or anthropic.AsyncAnthropic()
        self._token_usage = {"input": 0, "output": 0, "cost_usd": 0.0}

    # -- Token tracking --

    def _track_tokens(self, response, model: str):
        """Track token usage and estimated cost."""
        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        self._token_usage["input"] += inp
        self._token_usage["output"] += out

        if model == HAIKU:
            cost = (inp / 1_000_000 * 1.0) + (out / 1_000_000 * 5.0)
        else:  # SONNET
            cost = (inp / 1_000_000 * 3.0) + (out / 1_000_000 * 15.0)
        self._token_usage["cost_usd"] += cost

    @property
    def usage(self) -> dict:
        return {**self._token_usage, "cached_claims": self.cache.size}

    # -- LLM calls --

    async def _call_llm(
        self, model: str, system: str, user_msg: str, max_tokens: int = 2048
    ) -> str:
        """Make an LLM call with prompt caching. Retries on transient errors."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = await self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=[{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user_msg}],
                )
                self._track_tokens(response, model)
                return response.content[0].text
            except Exception as e:
                error_str = str(e).lower()
                is_transient = ("rate_limit" in error_str or "429" in str(e)
                                or "529" in str(e) or "overloaded" in error_str)
                if is_transient and attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    print(f"  [retry] {e.__class__.__name__}, waiting {wait}s "
                          f"(attempt {attempt+1}/{max_retries})...")
                    await asyncio.sleep(wait)
                elif is_transient:
                    print(f"  [skip] Still overloaded after {max_retries} attempts, skipping this call")
                    return "{}"  # Return empty JSON so caller can handle gracefully
                else:
                    raise

    def _parse_json(self, text: str):
        """Safely parse JSON from LLM output, handling common quirks."""
        cleaned = text.strip()

        # Strip markdown fences (```json ... ``` or ``` ... ```)
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        # Try direct parse first
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Find the JSON array or object boundary and extract just that
        # Handles cases where LLM adds text before/after the JSON
        start = -1
        bracket = None
        for i, ch in enumerate(cleaned):
            if ch == '[':
                start = i
                bracket = ']'
                break
            if ch == '{':
                start = i
                bracket = '}'
                break

        if start >= 0 and bracket:
            # Walk forward to find the matching closing bracket
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] in ('[', '{'):
                    depth += 1
                elif cleaned[i] in (']', '}'):
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            break

        # Last resort: try to find any valid JSON array in the text
        import re
        array_match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass

        # Give up — raise so caller can handle it
        raise json.JSONDecodeError("Could not extract valid JSON", cleaned, 0)

    # -- Pipeline stages --

    async def extract_claims(self, content: str) -> list[Claim]:
        """
        Stage 1: Extract and classify claims from content.
        Uses Haiku for speed and cost efficiency.
        Retries once with a stricter prompt if parsing fails.
        """
        for attempt in range(2):
            if attempt == 0:
                user_msg = f"Extract claims from this content:\n\n{content[:8000]}"
            else:
                print("  [retry] Retrying with stricter JSON instructions...")
                user_msg = (
                    f"Extract claims from this content. "
                    f"Return ONLY a valid JSON array, nothing else. "
                    f"No explanation, no text before or after the array.\n\n"
                    f"{content[:8000]}"
                )

            raw = await self._call_llm(
                model=HAIKU,
                system=CLAIM_EXTRACTOR_PROMPT,
                user_msg=user_msg,
            )
            try:
                claims_data = self._parse_json(raw)
                if not isinstance(claims_data, list):
                    claims_data = [claims_data]
                return [
                    Claim(
                        text=c["text"],
                        label=ContentLabel(c["label"]),
                        context=c.get("context", ""),
                    )
                    for c in claims_data
                ]
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                if attempt == 0:
                    print(f"[extract_claims] Parse error (will retry): {e}")
                else:
                    print(f"[extract_claims] Parse error (giving up): {e}")
                    return []

    async def plan_searches(self, claim: Claim) -> list[str]:
        """
        Stage 2: Generate search queries for a claim.
        Uses Haiku — this is a simple planning task.
        Uses different prompts for facts vs narratives.
        """
        is_narrative = claim.label == ContentLabel.NARRATIVE
        prompt = NARRATIVE_SEARCH_PLANNER_PROMPT if is_narrative else SEARCH_PLANNER_PROMPT

        if is_narrative:
            user_msg = f"Generate search queries to find where this narrative or framing originated:\n\n\"{claim.text}\""
        else:
            user_msg = f"Generate search queries to find the original source of this claim:\n\n\"{claim.text}\""

        raw = await self._call_llm(
            model=HAIKU,
            system=prompt,
            user_msg=user_msg,
            max_tokens=512,
        )
        try:
            queries = self._parse_json(raw)
            return queries[:4]  # Cap at 4 queries
        except (json.JSONDecodeError, ValueError):
            # Fallback: use the claim text itself as a query
            return [claim.text[:100]]

    async def analyze_source(self, claim: Claim, url: str, page_content: str) -> Optional[Source]:
        """
        Stage 3: Analyze a single source page against a claim.
        Uses Sonnet — this requires deep comprehension.
        Uses different prompts for facts vs narratives.
        """
        is_narrative = claim.label == ContentLabel.NARRATIVE
        prompt = NARRATIVE_ANALYZER_PROMPT if is_narrative else SOURCE_ANALYZER_PROMPT

        raw = await self._call_llm(
            model=SONNET,
            system=prompt,
            user_msg=(
                f"{'Narrative claim' if is_narrative else 'Claim'}: \"{claim.text}\"\n\n"
                f"Source URL: {url}\n\n"
                f"Page content:\n{page_content[:12000]}"
            ),
        )
        try:
            analysis = self._parse_json(raw)
            if not analysis.get("relevant", False):
                return None
            if analysis.get("confidence", 0) < MIN_CONFIDENCE_THRESHOLD:
                return None

            # Map tier based on claim type
            if is_narrative:
                role = analysis.get("role", "unrelated")
                tier_map = {
                    "originator": SourceTier.ORIGINATOR,
                    "amplifier": SourceTier.AMPLIFIER,
                    "critic": SourceTier.CRITIC,
                    "unrelated": SourceTier.UNKNOWN,
                }
                tier = tier_map.get(role, SourceTier.UNKNOWN)
            else:
                tier = SourceTier(analysis.get("tier", "unknown"))

            return Source(
                url=url,
                title=analysis.get("who", url) if is_narrative else (analysis.get("author", "") or url),
                tier=tier,
                relevant_excerpt=analysis.get("relevant_excerpt", ""),
                referenced_url=analysis.get("referenced_url", analysis.get("cites_url")),
                flow_type=analysis.get("flow_type"),
                flow_evidence=analysis.get("flow_evidence"),
                published_date=analysis.get("published_date", analysis.get("date")),
                author=analysis.get("author", analysis.get("who")),
                confidence=analysis.get("confidence", 0.0),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[analyze_source] Parse error: {e}")
            return None

    async def follow_chain(self, claim: Claim, sources: list[Source], seen_urls: set[str] = None) -> list[Source]:
        """
        Stage 4: Follow citation chains deeper toward primary sources.
        Recursively fetches and analyzes cited URLs, up to MAX_CHAIN_HOPS.
        """
        all_sources = list(sources)
        visited_urls = seen_urls or set()
        visited_urls.update(s.url for s in sources)
        frontier = [s for s in sources if s.cites and s.cites not in visited_urls]
        hops = 0

        while frontier and hops < MAX_CHAIN_HOPS:
            hops += 1
            next_frontier = []

            for source in frontier:
                if source.cites and source.cites not in visited_urls:
                    visited_urls.add(source.cites)
                    page_content = await self.search.fetch_page(source.cites)

                    if page_content.startswith("[Error"):
                        continue

                    new_source = await self.analyze_source(claim, source.cites, page_content)
                    if new_source:
                        all_sources.append(new_source)
                        if new_source.cites and new_source.cites not in visited_urls:
                            next_frontier.append(new_source)

                        # If we found a primary source or narrative originator, stop
                        if new_source.tier in (SourceTier.PRIMARY, SourceTier.ORIGINATOR):
                            return all_sources

            frontier = next_frontier

        return all_sources

    async def synthesize(self, claim: Claim, sources: list[Source]) -> ProvenanceChain:
        """
        Stage 5: Produce the final provenance chain.
        Uses Sonnet for quality synthesis.
        Uses different prompts for facts vs narratives.
        """
        is_narrative = claim.label == ContentLabel.NARRATIVE

        sources_summary = json.dumps(
            [{"url": s.url, "tier": s.tier.value, "confidence": s.confidence,
              "excerpt": s.relevant_excerpt, "referenced_url": s.referenced_url,
              "flow_type": s.flow_type, "published_date": s.published_date,
              **({"title": s.title, "author": s.author} if is_narrative else {})}
             for s in sources],
            indent=2,
        )

        prompt = NARRATIVE_SYNTHESIS_PROMPT if is_narrative else SYNTHESIS_PROMPT

        raw = await self._call_llm(
            model=SONNET,
            system=prompt,
            user_msg=(
                f"{'Narrative claim' if is_narrative else 'Claim'}: \"{claim.text}\"\n\n"
                f"Analyzed sources:\n{sources_summary}"
            ),
        )

        try:
            result = self._parse_json(raw)

            # Find the key source — primary for facts, originator for narratives
            origin = None
            origin_url = result.get(
                "originator_url" if is_narrative else "primary_source_url"
            )
            if origin_url:
                origin = next(
                    (s for s in sources if s.url == origin_url), None
                )

            # Build summary — include narrative-specific info if available
            summary = result.get("summary", "")
            if is_narrative and result.get("originator_who"):
                who = result["originator_who"]
                when = result.get("originator_date", "unknown date")
                spread = result.get("spread_pattern", "")
                if spread and spread not in summary:
                    summary = f"Originated from {who} ({when}). {spread}. {summary}"

            return ProvenanceChain(
                claim=claim,
                sources=sources,
                primary_source=origin,
                chain_complete=result.get("chain_complete", False),
                confidence=result.get("confidence", 0.0),
                summary=summary,
                hops=len(sources),
            )
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[synthesize] Parse error: {e}")
            return ProvenanceChain(
                claim=claim, sources=sources, summary="Synthesis failed", hops=len(sources)
            )

    # -- Main entry points --

    async def trace_claim(self, claim: Claim) -> ProvenanceChain:
        """
        Trace a single claim through the full pipeline.
        Returns cached results if available.
        """
        # Check cache first
        cached = await self.cache.get(claim.text)
        if cached:
            print(f"  [cache hit] {claim.text[:60]}...")
            return cached

        print(f"  [tracing] {claim.text[:60]}...")

        # Stage 2: Plan searches
        queries = await self.plan_searches(claim)
        print(f"  [search] {len(queries)} queries: {queries}")

        # Stage 3: Search and analyze sources
        sources: list[Source] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                results = await self.search.search(query, num_results=MAX_SEARCH_RESULTS)
            except NotImplementedError:
                print("  [search] Search adapter not implemented — skipping")
                break
            except Exception as e:
                print(f"  [search] Error: {e}")
                continue

            # Filter out URLs we've already analyzed
            new_results = [r for r in results if r["url"] not in seen_urls]
            if len(new_results) < len(results):
                print(f"  [dedup] Skipped {len(results) - len(new_results)} already-seen URLs")
            for r in new_results:
                seen_urls.add(r["url"])

            # Fetch all pages in parallel (HTTP only, no API calls)
            fetch_tasks = [self.search.fetch_page(r["url"]) for r in new_results]
            pages = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            # Haiku pre-filter: cheaply reject irrelevant pages
            for result, page in zip(new_results, pages):
                if isinstance(page, Exception) or str(page).startswith("[Error"):
                    continue
                page_text = str(page)

                # Quick relevance check with Haiku before expensive Sonnet analysis
                try:
                    filter_raw = await self._call_llm(
                        model=HAIKU,
                        system=(
                            "You are a relevance filter. Given a claim and a web page excerpt, "
                            "determine if this page is relevant. "
                            "Respond with ONLY JSON: {\"relevant\": true/false}"
                        ),
                        user_msg=(
                            f"Claim: \"{claim.text}\"\n"
                            f"URL: {result['url']}\n"
                            f"Page excerpt: {page_text[:3000]}"
                        ),
                        max_tokens=64,
                    )
                    filter_result = self._parse_json(filter_raw)
                    if not filter_result.get("relevant", True):
                        continue
                except Exception:
                    pass  # If filter fails, proceed with analysis

                source = await self.analyze_source(claim, result["url"], page_text)
                if source:
                    sources.append(source)

            # If we already found a primary source or originator, skip remaining queries
            if any(s.tier in (SourceTier.PRIMARY, SourceTier.ORIGINATOR) for s in sources):
                break

        # Stage 4: Follow citation chains (pass seen_urls to avoid re-analyzing)
        if sources:
            sources = await self.follow_chain(claim, sources, seen_urls=seen_urls)

        # Stage 5: Synthesize
        chain = await self.synthesize(claim, sources)

        # Cache the result
        await self.cache.put(chain)

        return chain

    async def trace_content(self, content: str) -> list[ProvenanceChain]:
        """
        Full pipeline: extract claims from content, then trace each
        traceable claim (facts and studies only).
        
        Returns a list of provenance chains.
        """
        # Stage 1: Extract claims
        print("[stage 1] Extracting claims...")
        claims = await self.extract_claims(content)
        print(f"  Found {len(claims)} claims")

        for c in claims:
            print(f"  [{c.label.value}] {c.text[:80]}")

        # Filter to traceable claims only (facts, studies, AND narratives)
        traceable = [
            c for c in claims
            if c.label in (ContentLabel.FACT, ContentLabel.STUDY_ANALYSIS, ContentLabel.NARRATIVE)
        ]
        print(f"\n[stage 2-5] Tracing {len(traceable)} claims...")

        # Trace each claim (could parallelize, but sequential is safer for rate limits)
        chains = []
        for claim in traceable:
            chain = await self.trace_claim(claim)
            chains.append(chain)
            print(f"  [result] confidence={chain.confidence:.2f} "
                  f"primary={'YES' if chain.primary_source else 'NO'} "
                  f"hops={chain.hops}")

        print(f"\n[done] Traced {len(chains)} claims. Usage: {self.usage}")
        return chains

    async def find_competing_narratives(
        self, fact_text: str, primary_source_url: str = "",
    ) -> dict:
        """
        Given a factual claim (and optionally its primary source),
        find competing narratives built on the same underlying data.
        
        Does two passes:
          1. Broad search for diverse framings
          2. Gap detection + targeted search for missing perspectives
        """
        print(f"\n[perspectives] Finding competing framings...")
        print(f"  Fact: {fact_text[:80]}")
        if primary_source_url:
            print(f"  Primary source: {primary_source_url}")

        # Step 1: Generate searches for competing framings
        raw = await self._call_llm(
            model=HAIKU,
            system=COMPETING_NARRATIVES_SEARCH_PROMPT,
            user_msg=(
                f"Factual claim: \"{fact_text}\"\n"
                f"Primary source: {primary_source_url or 'unknown'}\n\n"
                f"Generate search queries to find the WIDEST RANGE of framings "
                f"of this data point, including perspectives that DEFEND and "
                f"OPPOSE the dominant narrative."
            ),
            max_tokens=512,
        )
        try:
            queries = self._parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            queries = [fact_text[:80]]

        print(f"  [pass 1] {len(queries)} queries: {queries}")

        # Step 2: Search and analyze — first pass
        framings = []
        seen_urls = set()
        if primary_source_url:
            seen_urls.add(primary_source_url)

        framings, seen_urls = await self._search_and_analyze_framings(
            queries[:10], fact_text, primary_source_url, framings, seen_urls
        )

        # Step 3: Gap detection — what perspectives are missing?
        print(f"\n  [gap check] Analyzing {len(framings)} framings for missing perspectives...")
        perspectives_found = [f.get("perspective", "") for f in framings]

        gap_raw = await self._call_llm(
            model=HAIKU,
            system="""You are a perspective gap analyzer. Given a factual claim/event 
and a list of perspectives already found, identify what major ANALYTICAL LENSES 
are missing. 

Different people ask different QUESTIONS about the same event. These questions 
come from different analytical views, not just different political positions:

POLITICAL/IDEOLOGICAL LENSES (the partisan axis):
- Progressive / left-wing framing
- Conservative / right-wing framing
- Libertarian framing
- Populist framing (left or right)

INSTITUTIONAL LENSES (who has authority/responsibility):
- Law enforcement / security perspective
- Legal / constitutional analysis
- Jurisdictional (federal vs state vs local)
- Regulatory / policy design

HUMAN LENSES (who is affected):
- Victims / affected community
- Civil liberties / rights
- International / how the world sees it
- Historical / comparative (what pattern does this fit)

ANALYTICAL LENSES (how to understand it):
- Academic / research
- Economic / business impact
- Data / polling / empirical
- Procedural / what protocols exist

For CONTROVERSIAL events, the partisan axis matters — but it's only ONE axis. 
If all found perspectives are on the same political side, that gap matters. 
But ALSO check whether non-political views are missing.

Respond with ONLY JSON. No markdown fences:
{
  "missing_views": ["description of missing view 1", ...],
  "search_queries": ["targeted query 1", "targeted query 2", ...],
  "gap_analysis": "brief explanation of what's missing and why it matters"
}""",
            user_msg=(
                f"Event/claim: \"{fact_text}\"\n\n"
                f"Perspectives already found:\n"
                + "\n".join(f"  - {p}" for p in perspectives_found)
                + "\n\nWhat major perspectives are MISSING? Generate 4-6 targeted "
                f"search queries to find them."
            ),
            max_tokens=512,
        )

        try:
            gap_analysis = self._parse_json(gap_raw)
            gap_queries = gap_analysis.get("search_queries", [])
            missing = gap_analysis.get("missing_views", gap_analysis.get("missing_perspectives", []))
            gap_explanation = gap_analysis.get("gap_analysis", "")

            if gap_queries:
                print(f"  [gap check] Missing: {', '.join(missing[:3])}")
                print(f"  [pass 2] {len(gap_queries)} targeted queries: {gap_queries}")

                # Step 4: Second pass — targeted search for missing perspectives
                framings, seen_urls = await self._search_and_analyze_framings(
                    gap_queries[:6], fact_text, primary_source_url, framings, seen_urls
                )
            else:
                gap_explanation = "All major perspectives appear to be represented."
                print(f"  [gap check] No significant gaps detected.")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [gap check] Parse error on first attempt: {e}")
            print(f"  [gap check] Retrying with stricter instructions...")

            # Retry with explicit JSON-only instruction
            try:
                gap_raw = await self._call_llm(
                    model=HAIKU,
                    system="You are a gap analyzer. Respond with ONLY valid JSON, no other text.",
                    user_msg=(
                        f"Event: \"{fact_text}\"\n\n"
                        f"Perspectives already found:\n"
                        + "\n".join(f"  - {p}" for p in perspectives_found)
                        + "\n\nReturn JSON with these fields:\n"
                        f"- missing_views: array of missing perspective descriptions\n"
                        f"- search_queries: array of 4-6 search queries to find them\n"
                        f"- gap_analysis: string explaining what's missing"
                    ),
                    max_tokens=512,
                )
                gap_analysis = self._parse_json(gap_raw)
                gap_queries = gap_analysis.get("search_queries", [])
                missing = gap_analysis.get("missing_views", [])
                gap_explanation = gap_analysis.get("gap_analysis", "")

                if gap_queries:
                    print(f"  [gap check] Missing: {', '.join(missing[:3])}")
                    print(f"  [pass 2] {len(gap_queries)} targeted queries: {gap_queries}")
                    framings, seen_urls = await self._search_and_analyze_framings(
                        gap_queries[:6], fact_text, primary_source_url, framings, seen_urls
                    )

            except Exception as e2:
                print(f"  [gap check] Retry also failed: {e2}")
                gap_explanation = "Gap analysis failed — second pass was skipped."

        # Step 5: Consolidate — group raw framings into distinct views
        print(f"\n  [consolidate] Grouping {len(framings)} raw framings into distinct views...")

        consolidated = await self._consolidate_framings(fact_text, framings)

        return {
            "fact": fact_text,
            "primary_source": primary_source_url,
            "views": consolidated,
            "raw_framings_count": len(framings),
            "total_sources_checked": len(seen_urls),
            "gap_analysis": gap_explanation,
        }

    async def _consolidate_framings(self, fact_text: str, framings: list[dict]) -> list[dict]:
        """
        Use Sonnet to consolidate raw framings into distinct analytical views.
        Each view groups sources that are asking the same fundamental question.
        """
        if not framings:
            return []

        # Build a summary of all raw framings for Sonnet
        framing_summaries = []
        for i, f in enumerate(framings):
            framing_summaries.append({
                "id": i,
                "headline": f.get("framing_headline", ""),
                "perspective": f.get("perspective", ""),
                "emphasizes": f.get("emphasizes", ""),
                "url": f.get("url", ""),
                "source_type": f.get("source_type", ""),
            })

        raw = await self._call_llm(
            model=SONNET,
            system="""You are a framing consolidation engine. Given a list of raw framings 
about the same event, group them into DISTINCT ANALYTICAL LENSES.

A "view" is a fundamental question or angle through which people view the event. 
Multiple sources may share the same view even if their specific wording differs.

For example, for a police shooting:
- Lens: "Accountability" — Was this lawful? Who is responsible?
- Lens: "Use of Force" — Were procedures followed? Was force proportionate?
- Lens: "Political Weapon" — How is this being used by left/right for political gain?
- Lens: "Community Impact" — What does this mean for affected communities?
- Lens: "Federal vs State" — Who has jurisdiction? Is this federal overreach?
- Lens: "Law & Order" — Was the officer justified? What were the risks?

Rules:
- Aim for 4-8 distinct views (not 20+ overlapping ones)
- Each view should represent a genuinely DIFFERENT question, not just a different wording
- Group sources under the view they best fit
- A source can only belong to ONE view (pick the best fit)
- Include the partisan axis (left vs right framing) but as specific views alongside 
  non-partisan ones, not as the organizing principle
- Give each view a short, clear name and a one-sentence description of the question it asks

Respond with ONLY JSON. No markdown fences:
[
  {
    "view_name": "short name (2-4 words)",
    "question": "the fundamental question this view asks",
    "description": "1-2 sentence description of this perspective",
    "source_ids": [0, 3, 7],
    "key_claim": "the most representative claim from sources in this view"
  }
]""",
            user_msg=(
                f"Event: \"{fact_text}\"\n\n"
                f"Raw framings to consolidate:\n"
                f"{json.dumps(framing_summaries, indent=2)}"
            ),
            max_tokens=4096,
        )

        try:
            views = self._parse_json(raw)
            if not isinstance(views, list):
                views = [views]  # Wrap single object in list
        except (json.JSONDecodeError, ValueError):
            # Fallback: return framings ungrouped
            return [{
                "view_name": "All Framings",
                "question": "Various perspectives on this event",
                "description": "Consolidation failed — showing all framings ungrouped",
                "sources": framings,
                "key_claim": framings[0].get("framing_headline", "") if framings else "",
            }]

        # Attach the actual source data to each view
        for view_item in views:
            source_ids = view_item.get("source_ids", [])
            view_item["sources"] = []
            for sid in source_ids:
                if 0 <= sid < len(framings):
                    view_item["sources"].append(framings[sid])
            view_item.pop("source_ids", None)

        print(f"  [consolidate] Grouped into {len(views)} distinct views")
        return views

    async def _search_and_analyze_framings(
        self, queries, fact_text, primary_source_url, framings, seen_urls,
    ):
        """
        Shared search+analyze loop for perspective finding.
        Fully sequential to respect API rate limits.
        Haiku pre-filter rejects irrelevant pages before expensive Sonnet calls.
        """
        import time
        t0 = time.time()

        PRE_FILTER_PROMPT = (
            "You are a relevance filter. Given a factual claim and a web page excerpt, "
            "determine if this page discusses the SAME event/data. "
            "Respond with ONLY JSON: {\"relevant\": true/false}"
        )

        new_framings = 0
        total_pages = 0
        filtered_out = 0

        for query in queries:
            # Search
            try:
                results = await self.search.search(query, num_results=5)
            except Exception as e:
                print(f"  [search] Error: {e}")
                continue

            # Dedup
            new_results = [r for r in results if r["url"] not in seen_urls]
            for r in new_results:
                seen_urls.add(r["url"])

            # Process each result sequentially
            for result in new_results:
                total_pages += 1

                # Fetch page
                page_text = await self.search.fetch_page(result["url"])
                if page_text.startswith("[Error"):
                    continue

                # Haiku pre-filter
                try:
                    filter_raw = await self._call_llm(
                        model=HAIKU,
                        system=PRE_FILTER_PROMPT,
                        user_msg=(
                            f"Claim: \"{fact_text}\"\n"
                            f"URL: {result['url']}\n"
                            f"Page excerpt: {page_text[:3000]}"
                        ),
                        max_tokens=64,
                    )
                    filter_result = self._parse_json(filter_raw)
                    if not filter_result.get("relevant", True):
                        filtered_out += 1
                        continue
                except Exception:
                    pass  # If filter fails, proceed with analysis

                # Sonnet deep analysis
                print(f"  [analyze] {result['url'][:70]}...")
                try:
                    raw = await self._call_llm(
                        model=SONNET,
                        system=COMPETING_NARRATIVES_ANALYZER_PROMPT,
                        user_msg=(
                            f"Factual claim: \"{fact_text}\"\n"
                            f"Primary source: {primary_source_url or 'unknown'}\n\n"
                            f"Source URL: {result['url']}\n\n"
                            f"Page content:\n{page_text[:12000]}"
                        ),
                    )
                    analysis = self._parse_json(raw)
                    if analysis.get("relevant") and analysis.get("same_data"):
                        analysis["url"] = result["url"]
                        framings.append(analysis)
                        new_framings += 1
                except (json.JSONDecodeError, ValueError):
                    continue

        elapsed = time.time() - t0
        if filtered_out:
            print(f"  [filter] {filtered_out} irrelevant pages skipped")
        print(f"  [done] {new_framings} framings from {total_pages} pages ({elapsed:.0f}s)")

        return framings, seen_urls

    def _perspectives_similar(self, a: str, b: str) -> bool:
        """Quick check if two perspective descriptions are roughly the same."""
        if not a or not b:
            return False
        # Check word overlap
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap > 0.6

    async def analyze_mutations(
        self, claim_text: str, sources: list[dict],
    ) -> dict:
        """
        Track how a claim or narrative mutated as it passed through the 
        information chain.
        
        Takes sources from a trace or perspectives analysis and analyzes
        what changed at each step — what got dropped, added, distorted.
        """
        if len(sources) < 2:
            return {
                "transitions": [],
                "overall": {
                    "mutation_severity": "minimal",
                    "mutation_pattern": "none",
                    "summary": "Not enough sources to track mutations.",
                },
            }

        print(f"\n[mutations] Analyzing how claim mutated across {len(sources)} sources...")

        chain_summary = []
        for i, s in enumerate(sources):
            chain_summary.append({
                "position": i + 1,
                "url": s.get("url", ""),
                "tier": s.get("tier", "unknown"),
                "title": s.get("title", s.get("who", "")),
                "excerpt": s.get("excerpt", s.get("relevant_excerpt", ""))[:300],
            })

        raw = await self._call_llm(
            model=SONNET,
            system=MUTATION_ANALYSIS_PROMPT,
            user_msg=(
                f"Original claim: \"{claim_text}\"\n\n"
                f"Source chain (ordered from closest to origin to furthest):\n"
                f"{json.dumps(chain_summary, indent=2)}"
            ),
            max_tokens=4096,
        )

        try:
            result = self._parse_json(raw)

            # Handle various formats Sonnet might return
            if isinstance(result, list):
                # Sonnet returned just the transitions array
                result = {"transitions": result, "overall": {
                    "mutation_severity": "unknown",
                    "mutation_pattern": "unknown",
                    "summary": "Transitions detected but overall summary was not generated.",
                }}

            # Ensure overall is a dict
            overall = result.get("overall", {})
            if not isinstance(overall, dict):
                overall = {"summary": str(overall)}
                result["overall"] = overall

            # Ensure transitions is a list
            transitions = result.get("transitions", [])
            if not isinstance(transitions, list):
                transitions = []
                result["transitions"] = transitions

            severity = overall.get("mutation_severity", "unknown")
            print(f"  [mutations] Severity: {severity}")
            return result
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            print(f"  [mutations] Parse error: {e}")
            print(f"  [mutations] Raw response: {raw[:300]}")
            return {
                "transitions": [],
                "overall": {"summary": "Mutation analysis failed to parse."},
            }

    async def analyze_missing_info(
        self, content_text: str, content_url: str, views: list[dict],
    ) -> dict:
        """
        Analyze what important information a single source omits compared 
        to the full set of perspectives.
        
        Shows how echo chambers work — not through lies, but through 
        selective emphasis and omission.
        """
        if not views:
            return {
                "factual_omissions": [],
                "perspective_omissions": [],
                "context_omissions": [],
                "completeness_score": 0.5,
                "summary": "No views available for comparison.",
            }

        print(f"\n[omissions] Analyzing what this source leaves out...")

        view_summaries = []
        for view_item in views:
            sources_in_view = view_item.get("sources", [])
            key_points = []
            for s in sources_in_view[:3]:
                excerpt = s.get("excerpt", s.get("framing_headline", ""))
                emphasizes = s.get("emphasizes", "")
                if excerpt:
                    key_points.append(excerpt[:200])
                if emphasizes:
                    key_points.append(f"Emphasizes: {emphasizes[:150]}")

            view_summaries.append({
                "view_name": view_item.get("view_name", ""),
                "question": view_item.get("question", ""),
                "key_claim": view_item.get("key_claim", ""),
                "key_points": key_points,
            })

        raw = await self._call_llm(
            model=SONNET,
            system=MISSING_INFO_PROMPT,
            user_msg=(
                f"Content being analyzed (from {content_url}):\n"
                f"{content_text[:8000]}\n\n"
                f"Competing views on this same event:\n"
                f"{json.dumps(view_summaries, indent=2)}"
            ),
            max_tokens=4096,
        )

        try:
            result = self._parse_json(raw)
            if isinstance(result, list):
                # Sonnet returned array instead of object
                result = {
                    "factual_omissions": result,
                    "perspective_omissions": [],
                    "context_omissions": [],
                    "completeness_score": 0.5,
                    "summary": "Omissions detected but structured format was not returned.",
                }
            score = result.get("completeness_score", 0)
            omissions = (
                len(result.get("factual_omissions", []))
                + len(result.get("perspective_omissions", []))
                + len(result.get("context_omissions", []))
            )
            print(f"  [omissions] Completeness: {score:.0%}, {omissions} omissions found")
            return result
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            print(f"  [omissions] Parse error: {e}")
            print(f"  [omissions] Raw response: {raw[:300]}")
            return {
                "factual_omissions": [],
                "perspective_omissions": [],
                "context_omissions": [],
                "completeness_score": 0.5,
                "summary": "Missing info analysis failed to parse.",
            }

    async def analyze_shared_foundation(
        self,
        event_text: str,
        views: list[dict],
        primary_source_url: str = "",
        primary_source_content: str = "",
    ) -> dict:
        """
        Identify the shared factual foundation across all views.
        
        Returns facts that appear across views, separated into:
          - verified: explicitly supported by the primary source
          - unverified: appear across views but not directly in primary source
        
        Also returns the points of disagreement that distinguish the views.
        """
        if len(views) < 2:
            return {
                "verified_facts": [],
                "unverified_facts": [],
                "points_of_disagreement": [],
                "summary": "Not enough views to identify shared ground.",
            }

        print(f"\n[shared] Identifying shared foundation across {len(views)} views...")

        # Step 1: Extract candidate shared facts
        view_summaries = []
        for v in views:
            sources_summary = []
            for s in v.get("sources", [])[:3]:
                excerpt = s.get("excerpt", s.get("framing_headline", ""))
                if excerpt:
                    sources_summary.append(excerpt[:300])
            view_summaries.append({
                "name": v.get("view_name", v.get("name", "")),
                "question": v.get("question", ""),
                "key_claim": v.get("key_claim", ""),
                "source_excerpts": sources_summary,
            })

        raw = await self._call_llm(
            model=SONNET,
            system=SHARED_FACTS_EXTRACTOR_PROMPT,
            user_msg=(
                f"Event: \"{event_text}\"\n\n"
                f"Views to analyze:\n{json.dumps(view_summaries, indent=2)}"
            ),
            max_tokens=4096,
        )

        try:
            extracted = self._parse_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [shared] Extraction failed: {e}")
            return {
                "verified_facts": [],
                "unverified_facts": [],
                "points_of_disagreement": [],
                "summary": "Shared foundation extraction failed.",
            }

        candidates = extracted.get("shared_facts", [])
        disagreements = extracted.get("points_of_disagreement", [])
        summary = extracted.get("summary", "")
        print(f"  [shared] Found {len(candidates)} candidate shared facts")

        if not candidates:
            return {
                "verified_facts": [],
                "unverified_facts": [],
                "points_of_disagreement": disagreements,
                "summary": summary or "No shared facts found across views.",
            }

        # Step 2: Verify against the primary source if we have it
        verified = []
        unverified = []

        if primary_source_url and primary_source_content:
            print(f"  [shared] Verifying against primary source...")
            verify_raw = await self._call_llm(
                model=SONNET,
                system=SHARED_FACTS_VERIFIER_PROMPT,
                user_msg=(
                    f"Candidate facts:\n{json.dumps([c['fact'] for c in candidates], indent=2)}\n\n"
                    f"Primary source: {primary_source_url}\n"
                    f"Content:\n{primary_source_content[:8000]}"
                ),
                max_tokens=4096,
            )

            try:
                verification = self._parse_json(verify_raw)
                verified_texts = {v["fact"] for v in verification.get("verified_facts", [])}
                excerpts = {v["fact"]: v.get("supporting_excerpt", "")
                           for v in verification.get("verified_facts", [])}

                for candidate in candidates:
                    fact_text = candidate["fact"]
                    if fact_text in verified_texts:
                        verified.append({
                            **candidate,
                            "primary_source": primary_source_url,
                            "supporting_excerpt": excerpts.get(fact_text, ""),
                        })
                    else:
                        # Look in unverified_facts for the reason
                        reason = next(
                            (u.get("reason", "") for u in verification.get("unverified_facts", [])
                             if u.get("fact") == fact_text),
                            "Not directly supported by primary source"
                        )
                        unverified.append({
                            **candidate,
                            "verification_status": "unverified",
                            "reason": reason,
                        })

                print(f"  [shared] Verified {len(verified)}/{len(candidates)} facts against primary source")

            except (json.JSONDecodeError, ValueError) as e:
                print(f"  [shared] Verification parse failed: {e}, treating all as unverified")
                unverified = [{**c, "verification_status": "verification_failed"} for c in candidates]
        else:
            # No primary source content available — all are unverified
            print(f"  [shared] No primary source content available, all facts marked unverified")
            unverified = [{**c, "verification_status": "no_primary_source"} for c in candidates]

        return {
            "verified_facts": verified,
            "unverified_facts": unverified,
            "points_of_disagreement": disagreements,
            "summary": summary,
            "primary_source_url": primary_source_url,
        }

    # ---------------- Tree-trace mode ----------------

    async def trace_tree(
        self,
        claim_text: str,
        depth: str = "standard",       # quick|standard|deep
        days_back: Optional[int] = None,
        max_nodes: Optional[int] = None,
        include_social: bool = False,
        multi_frame: bool = False,
        discover_framings: bool = False,
        custom_framings: Optional[list[str]] = None,
    ) -> TraceTree:
        """
        Build a full provenance tree for a single claim.
        
        - Plans search queries across origin/reporting/amplification/critic/evolution buckets
        - Searches and analyzes pages, extracting exact phrasings and attributions
        - Reconstructs parent-child relationships into a tree
        - Identifies origins and the main thread (path to most amplified version)
        """
        depth_settings = {
            "quick":    {"per_bucket": 2, "max_pages": 10, "fetch_pages": 8},
            "standard": {"per_bucket": 3, "max_pages": 20, "fetch_pages": 15},
            "deep":     {"per_bucket": 5, "max_pages": 40, "fetch_pages": 30},
        }
        cfg = depth_settings.get(depth, depth_settings["standard"])
        if max_nodes is None:
            max_nodes = cfg["max_pages"]

        print(f"\n{'='*60}")
        print(f"TRACE TREE: {claim_text[:60]}...")
        print(f"Depth: {depth} | Days back: {days_back or 'all history'}")
        print(f"{'='*60}")

        # Step 1: Neutral phrasing
        print("\n[trace 1/5] Neutral rephrasing...")
        neutral_raw = await self._call_llm(
            model=HAIKU,
            system="Rephrase this claim into a neutral, factual statement removing any "
                   "editorial or partisan framing. Preserve all specific facts. Respond "
                   "with ONLY the rephrased statement, no JSON, no explanation.",
            user_msg=claim_text,
            max_tokens=256,
        )
        neutral = (neutral_raw or claim_text).strip().strip('"')
        print(f"  Neutral: {neutral[:80]}")

        # Step 2: Plan search queries across buckets
        print("\n[trace 2/5] Planning search queries...")
        plan_raw = await self._call_llm(
            model=SONNET,
            system=TRACE_QUERY_PLANNER_PROMPT,
            user_msg=f'Claim to trace: "{neutral}"',
            max_tokens=2048,
        )
        try:
            plan = self._parse_json(plan_raw)
        except (json.JSONDecodeError, ValueError):
            plan = {}

        all_queries = []
        for bucket in ["origin_queries", "reporting_queries", "amplification_queries",
                       "critic_queries", "evolution_queries"]:
            queries = plan.get(bucket, [])[:cfg["per_bucket"]]
            for q in queries:
                if days_back:
                    all_queries.append(f"{q} past {days_back} days")
                else:
                    all_queries.append(q)

        # Add Reddit-targeted queries for the amplification and critic buckets
        # since Reddit is where much of the public conversation/criticism lives
        # but doesn't always surface in generic searches.
        amp_queries = plan.get("amplification_queries", [])[:1]
        critic_queries = plan.get("critic_queries", [])[:1]
        for q in amp_queries + critic_queries:
            all_queries.append(f"site:reddit.com {q}")

        print(f"  {len(all_queries)} queries across 5 buckets (incl. Reddit-targeted)")

        # Step 3: Search and gather candidate URLs
        print(f"\n[trace 3/5] Searching across {len(all_queries)} queries...")
        candidate_urls = {}  # url -> {"title", "snippet"}
        for q in all_queries:
            try:
                results = await self.search.search(q, num_results=5)
                for r in results:
                    url = r.get("url", "")
                    if url and url not in candidate_urls:
                        candidate_urls[url] = {
                            "title": r.get("title", ""),
                            "snippet": r.get("snippet", ""),
                        }
            except Exception as e:
                print(f"  [search error] {q[:40]}: {e}")
        print(f"  Found {len(candidate_urls)} unique URLs")

        # Cap by max_pages
        urls_to_fetch = list(candidate_urls.keys())[:cfg["fetch_pages"]]

        # Step 4: Fetch each page and extract phrasing/attribution
        print(f"\n[trace 4/5] Analyzing {len(urls_to_fetch)} sources...")
        nodes_dict = {}  # node_id -> TraceNode

        for i, url in enumerate(urls_to_fetch, 1):
            try:
                content = await self.search.fetch_page(url)
                if not content or str(content).startswith("[Error"):
                    continue
                content = str(content)[:6000]

                analysis_raw = await self._call_llm(
                    model=HAIKU,
                    system=CLAIM_PHRASING_EXTRACTOR_PROMPT,
                    user_msg=(
                        f"Claim being traced: \"{neutral}\"\n\n"
                        f"Page URL: {url}\n"
                        f"Page content:\n{content}"
                    ),
                    max_tokens=1024,
                )
                analysis = self._parse_json(analysis_raw)
                if not analysis.get("carries_claim"):
                    continue
                if analysis.get("confidence", 0) < 0.4:
                    continue

                node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                domain = ""
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).hostname or ""
                    if domain.startswith("www."):
                        domain = domain[4:]
                except Exception:
                    pass

                # Tier inference (will be refined by chain reconstruction)
                tier = "secondary"
                if ("bsky.app" in url or "twitter.com" in url or "x.com" in url
                        or "reddit.com" in url):
                    tier = "social"
                elif domain.endswith(".gov") or "bls.gov" in url or "fbi.gov" in url:
                    tier = "primary"

                node = TraceNode(
                    node_id=node_id,
                    url=url,
                    title=candidate_urls[url].get("title", ""),
                    outlet=domain,
                    author=analysis.get("author", "") or "",
                    published_date=analysis.get("published_date"),
                    tier=tier,
                    exact_phrasing=analysis.get("exact_phrasing", "") or "",
                    attribution_text=analysis.get("attribution_text", "") or "",
                    attributed_to=analysis.get("attributed_to", []) or [],
                    confidence=analysis.get("confidence", 0),
                )
                nodes_dict[node_id] = node

                if i % 5 == 0:
                    print(f"  [{i}/{len(urls_to_fetch)}] {len(nodes_dict)} relevant so far...")

                if len(nodes_dict) >= max_nodes:
                    break

            except Exception as e:
                print(f"  [analyze error] {url[:50]}: {e}")
                continue

        if len(nodes_dict) < 2:
            print(f"\n[trace] Only {len(nodes_dict)} relevant sources found, returning sparse tree")
            return TraceTree(
                claim_text=claim_text,
                neutral_phrasing=neutral,
                nodes=list(nodes_dict.values()),
                origins=[n.node_id for n in nodes_dict.values()],
                main_thread=[n.node_id for n in nodes_dict.values()],
                depth=depth,
                days_back=days_back,
                cost_usd=self.usage["cost_usd"],
            )

        print(f"  Total relevant sources: {len(nodes_dict)}")

        # Step 4.5: Optionally include social media posts (Bluesky + Reddit)
        if include_social:
            social_days = days_back or 90
            social_limit = {"quick": 5, "standard": 10, "deep": 20}.get(depth, 10)

            async def _ingest_posts(posts, platform, outlet):
                """Convert SocialPost list into TraceNodes, deduplicating against existing."""
                added = 0
                for post in posts:
                    if not post.url:
                        continue
                    node_id = hashlib.sha256(post.url.encode()).hexdigest()[:10]
                    if node_id in nodes_dict:
                        continue
                    title = post.text[:80] if platform == "reddit" else f"@{post.author}: {post.text[:60]}"
                    nodes_dict[node_id] = TraceNode(
                        node_id=node_id,
                        url=post.url,
                        title=title,
                        outlet=outlet,
                        author=post.author or "",
                        published_date=post.posted_at,
                        tier="social",
                        exact_phrasing=post.text or "",
                        attribution_text="",
                        attributed_to=[],
                        confidence=0.8,
                    )
                    added += 1
                return added

            # --- Bluesky ---
            print(f"\n[trace 4.5a/5] Searching Bluesky for social spread...")
            try:
                from social_search import BlueskySearch
                bsky = BlueskySearch()
                await bsky.login()
                spread = await bsky.search_narrative(
                    narrative_text=neutral,
                    days_back=social_days,
                    limit=social_limit * 3,
                )
                posts = sorted(
                    spread.posts,
                    key=lambda p: (p.likes or 0) + (p.reposts or 0) * 2,
                    reverse=True,
                )[:social_limit]
                print(f"  Found {spread.total_posts_found} posts; sampling top {len(posts)}")
                added = await _ingest_posts(posts, "bluesky", "bsky.app")
                print(f"  Added {added} Bluesky posts")
                await bsky.close()
            except Exception as e:
                print(f"  [bluesky] Skipped: {e}")

            # --- Reddit ---
            print(f"\n[trace 4.5b/5] Searching Reddit for social spread...")
            try:
                from social_search import RedditSearch
                reddit = RedditSearch()
                await reddit.login()
                spread = await reddit.search_narrative(
                    narrative_text=neutral,
                    days_back=social_days,
                    limit=social_limit * 3,
                )
                posts = sorted(
                    spread.posts,
                    key=lambda p: (p.likes or 0) + (p.replies or 0) * 2,
                    reverse=True,
                )[:social_limit]
                print(f"  Found {spread.total_posts_found} posts; sampling top {len(posts)}")
                added = await _ingest_posts(posts, "reddit", "reddit.com")
                print(f"  Added {added} Reddit posts")
                await reddit.close()
            except Exception as e:
                print(f"  [reddit] Skipped: {e}")

            print(f"  Total sources after social: {len(nodes_dict)}")

        # Step 4.7: Expand by attribution — search for named amplifiers directly
        # When sources name a person/org as the source of the claim (e.g.,
        # "Senator Warren said the system is rigged"), we search for that
        # person's direct statements and add them as nodes.
        if len(nodes_dict) >= 2:
            print(f"\n[trace 4.7] Expanding by named attributions...")

            # Collect all named entities and count mentions
            attribution_counts = {}
            for node in nodes_dict.values():
                for name in (node.attributed_to or []):
                    name_clean = name.strip()
                    if not name_clean or len(name_clean) < 3:
                        continue
                    # Filter generic descriptors that aren't names
                    if name_clean.lower() in {
                        "the report", "the study", "officials", "experts",
                        "sources", "reports", "data", "analysis",
                    }:
                        continue
                    attribution_counts[name_clean] = attribution_counts.get(name_clean, 0) + 1

            # Take names mentioned by 2+ different sources, capped by depth
            attribution_cap = {"quick": 2, "standard": 4, "deep": 8}.get(depth, 4)
            named_amplifiers = [
                name for name, count in attribution_counts.items() if count >= 2
            ]
            named_amplifiers.sort(
                key=lambda n: attribution_counts[n], reverse=True
            )
            named_amplifiers = named_amplifiers[:attribution_cap]

            if named_amplifiers:
                print(f"  Searching for {len(named_amplifiers)} named amplifiers: {', '.join(named_amplifiers[:5])}")

                amplifier_pages_per = {"quick": 2, "standard": 3, "deep": 4}.get(depth, 3)

                for name in named_amplifiers:
                    # Search for this person + the claim
                    queries = [
                        f'"{name}" {neutral[:60]}',
                        f'"{name}" said claim',
                    ]

                    new_urls = {}
                    for q in queries[:1]:  # Just the primary query for each name
                        try:
                            results = await self.search.search(q, num_results=amplifier_pages_per)
                            for r in results:
                                url = r.get("url", "")
                                # Skip URLs already in our corpus
                                node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                                if url and node_id not in nodes_dict and url not in new_urls:
                                    new_urls[url] = r
                        except Exception as e:
                            continue

                    if not new_urls:
                        print(f"    [{name}] No new sources found")
                        continue

                    print(f"    [{name}] Analyzing {len(new_urls)} new sources...")

                    for url, result_meta in list(new_urls.items())[:amplifier_pages_per]:
                        try:
                            content = await self.search.fetch_page(url)
                            if not content or str(content).startswith("[Error"):
                                continue
                            content = str(content)[:6000]

                            analysis_raw = await self._call_llm(
                                model=HAIKU,
                                system=CLAIM_PHRASING_EXTRACTOR_PROMPT,
                                user_msg=(
                                    f"Claim being traced: \"{neutral}\"\n\n"
                                    f"Note: this page should mention {name}, who is "
                                    f"a known amplifier of this claim. Look for their "
                                    f"direct statements, quotes, op-eds, or speeches.\n\n"
                                    f"Page URL: {url}\n"
                                    f"Page content:\n{content}"
                                ),
                                max_tokens=1024,
                            )
                            analysis = self._parse_json(analysis_raw)
                            if not analysis.get("carries_claim"):
                                continue
                            if analysis.get("confidence", 0) < 0.4:
                                continue

                            node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                            if node_id in nodes_dict:
                                continue

                            domain = ""
                            try:
                                from urllib.parse import urlparse
                                domain = urlparse(url).hostname or ""
                                if domain.startswith("www."):
                                    domain = domain[4:]
                            except Exception:
                                pass

                            tier = "amplifier"  # named amplifier by definition
                            if ("bsky.app" in url or "twitter.com" in url
                                    or "x.com" in url or "reddit.com" in url):
                                tier = "social"

                            nodes_dict[node_id] = TraceNode(
                                node_id=node_id,
                                url=url,
                                title=result_meta.get("title", "") or f"{name}: {analysis.get('exact_phrasing', '')[:50]}",
                                outlet=domain,
                                author=analysis.get("author") or name,
                                published_date=analysis.get("published_date"),
                                tier=tier,
                                exact_phrasing=analysis.get("exact_phrasing", "") or "",
                                attribution_text=analysis.get("attribution_text", "") or "",
                                attributed_to=analysis.get("attributed_to", []) or [],
                                confidence=analysis.get("confidence", 0),
                            )

                        except Exception as e:
                            continue

                print(f"  Total sources after attribution expansion: {len(nodes_dict)}")
            else:
                print(f"  No named amplifiers found in 2+ sources")

        # Step 5: Reconstruct chain — identify parent/child and main thread
        print(f"\n[trace 5/5] Reconstructing the chain of attribution...")

        # Build a compact corpus for Sonnet to analyze
        sorted_nodes = sorted(
            nodes_dict.values(),
            key=lambda n: n.published_date or "9999",
        )
        corpus = []
        for n in sorted_nodes:
            corpus.append({
                "id": n.node_id,
                "url": n.url,
                "outlet": n.outlet,
                "tier": n.tier,
                "published_date": n.published_date,
                "exact_phrasing": n.exact_phrasing[:300],
                "attribution_text": n.attribution_text[:200],
                "attributed_to": n.attributed_to,
            })

        chain_raw = await self._call_llm(
            model=SONNET,
            system=CHAIN_RECONSTRUCTION_PROMPT,
            user_msg=(
                f"Claim being traced: \"{neutral}\"\n\n"
                f"Sources (sorted by publication date):\n"
                f"{json.dumps(corpus, indent=2)}"
            ),
            max_tokens=4096,
        )

        try:
            chain_data = self._parse_json(chain_raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [chain] Parse failed: {e}, falling back to date-based chain")
            chain_data = {"nodes": [], "main_thread_ids": []}

        # Apply chain reconstruction to nodes
        node_results = chain_data.get("nodes", [])
        for nr in node_results:
            nid = nr.get("id")
            if nid not in nodes_dict:
                continue
            node = nodes_dict[nid]
            node.is_origin = nr.get("is_origin", False)
            node.parents = nr.get("parents", [])
            # Refine tier based on chain position
            if node.is_origin and node.tier in ("secondary", "unknown"):
                node.tier = "originator"

        # Build children lists
        for node in nodes_dict.values():
            for parent in node.parents:
                pid = parent.get("id") or parent.get("node_id")
                if pid in nodes_dict:
                    parent_node = nodes_dict[pid]
                    if node.node_id not in parent_node.children:
                        parent_node.children.append(node.node_id)

        # Identify origins
        origins = [n.node_id for n in nodes_dict.values() if n.is_origin]
        if not origins:
            # Fallback: nodes with no parents
            origins = [n.node_id for n in nodes_dict.values() if not n.parents]

        # Main thread
        main_thread_ids = chain_data.get("main_thread_ids", [])
        main_thread_ids = [tid for tid in main_thread_ids if tid in nodes_dict]
        if not main_thread_ids and origins:
            # Fallback: longest path from any origin via children with most descendants
            def longest_path_from(nid, visited=None):
                if visited is None:
                    visited = set()
                if nid in visited:
                    return [nid]
                visited.add(nid)
                node = nodes_dict.get(nid)
                if not node or not node.children:
                    return [nid]
                best = []
                for cid in node.children:
                    path = longest_path_from(cid, visited.copy())
                    if len(path) > len(best):
                        best = path
                return [nid] + best
            best_thread = []
            for oid in origins:
                p = longest_path_from(oid)
                if len(p) > len(best_thread):
                    best_thread = p
            main_thread_ids = best_thread

        # Mark main thread nodes
        for nid in main_thread_ids:
            if nid in nodes_dict:
                nodes_dict[nid].on_main_thread = True

        print(f"  {len(origins)} origins identified")
        print(f"  Main thread length: {len(main_thread_ids)}")

        # ---------------- Step 6: Multi-framing analysis (optional) ----------------

        framings_list = []
        framing_mode = "single"

        async def _analyze_url_for_node(url, neutral_text):
            """Fetch a URL, run claim-phrasing extraction, return (node_id, TraceNode) or None."""
            try:
                page_content = await self.search.fetch_page(url)
                if not page_content or str(page_content).startswith("[Error"):
                    return None
                page_content = str(page_content)[:6000]

                analysis_raw = await self._call_llm(
                    model=HAIKU,
                    system=CLAIM_PHRASING_EXTRACTOR_PROMPT,
                    user_msg=(
                        f'Claim being traced: "{neutral_text}"\n\n'
                        f"Page URL: {url}\n"
                        f"Page content:\n{page_content}"
                    ),
                    max_tokens=1024,
                )
                analysis = self._parse_json(analysis_raw)
                if not analysis.get("carries_claim") or analysis.get("confidence", 0) < 0.4:
                    return None

                node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                domain = ""
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).hostname or ""
                    if domain.startswith("www."):
                        domain = domain[4:]
                except Exception:
                    pass

                tier = "secondary"
                if ("bsky.app" in url or "twitter.com" in url or "x.com" in url
                        or "reddit.com" in url):
                    tier = "social"
                elif domain.endswith(".gov"):
                    tier = "primary"

                return node_id, TraceNode(
                    node_id=node_id,
                    url=url,
                    title="",
                    outlet=domain,
                    author=analysis.get("author") or "",
                    published_date=analysis.get("published_date"),
                    tier=tier,
                    exact_phrasing=analysis.get("exact_phrasing", "") or "",
                    attribution_text=analysis.get("attribution_text", "") or "",
                    attributed_to=analysis.get("attributed_to", []) or [],
                    confidence=analysis.get("confidence", 0),
                )
            except Exception:
                return None

        if multi_frame or discover_framings or custom_framings:
            print(f"\n[trace 6/6] Multi-framing analysis...")
            initial_node_count = len(nodes_dict)

            # ---- 6a: For discover-framings only, seed corpus with ideologically diverse searches ----
            if discover_framings:
                framing_mode = "discover-framings"
                print(f"\n  [6a] Seeding corpus with ideologically-diverse searches...")
                diverse_raw = await self._call_llm(
                    model=SONNET,
                    system=DIVERSE_SEED_QUERY_PROMPT,
                    user_msg=f'Claim: "{neutral}"',
                    max_tokens=1024,
                )
                try:
                    diverse_plan = self._parse_json(diverse_raw)
                    diverse_queries = diverse_plan.get("diverse_queries", [])[:10]
                except (json.JSONDecodeError, ValueError):
                    diverse_queries = []

                print(f"    Running {len(diverse_queries)} diverse seed queries")
                seed_urls = {}
                for q in diverse_queries:
                    try:
                        results = await self.search.search(q, num_results=5)
                        for r in results:
                            url = r.get("url", "")
                            node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                            if url and node_id not in nodes_dict and url not in seed_urls:
                                seed_urls[url] = r
                    except Exception:
                        continue

                print(f"    Found {len(seed_urls)} new candidate URLs from diverse search")

                # Analyze the new URLs (cap by depth)
                seed_cap = {"quick": 8, "standard": 15, "deep": 25}.get(depth, 15)
                added = 0
                for url in list(seed_urls.keys())[:seed_cap]:
                    res = await _analyze_url_for_node(url, neutral)
                    if res:
                        nid, new_node = res
                        if nid not in nodes_dict:
                            nodes_dict[nid] = new_node
                            added += 1
                print(f"    Added {added} sources from diverse seed search")

            # ---- 6b: Identify framings (generate, discover, or custom) ----
            print(f"\n  [6b] Identifying framings...")

            if custom_framings:
                framing_mode = "multi-frame"
                for i, ft in enumerate(custom_framings):
                    framings_list.append(Framing(
                        framing_id=f"custom_{i}",
                        label=f"Custom Framing {i+1}",
                        framing_text=ft,
                        ideological_tradition="user-provided",
                        characteristic_phrases=[ft],  # use the phrasing itself as a search phrase
                    ))
                print(f"    Using {len(framings_list)} custom framings")

            elif discover_framings:
                # Build a compact view of the (now-expanded) corpus
                corpus_for_framing = []
                for n in nodes_dict.values():
                    corpus_for_framing.append({
                        "id": n.node_id,
                        "outlet": n.outlet,
                        "exact_phrasing": (n.exact_phrasing or "")[:300],
                    })

                discover_raw = await self._call_llm(
                    model=SONNET,
                    system=FRAMING_DISCOVERER_PROMPT,
                    user_msg=(
                        f'Underlying neutral claim: "{neutral}"\n\n'
                        f"Corpus of sources (with their exact phrasings):\n"
                        f"{json.dumps(corpus_for_framing, indent=2)}"
                    ),
                    max_tokens=4096,
                )
                try:
                    discovered = self._parse_json(discover_raw)
                    for f in discovered.get("framings", []):
                        framings_list.append(Framing(
                            framing_id=f.get("id", f"discovered_{len(framings_list)}"),
                            label=f.get("label", ""),
                            framing_text=f.get("framing_text", ""),
                            ideological_tradition=f.get("ideological_tradition", ""),
                            characteristic_phrases=f.get("characteristic_phrases", []),
                        ))
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"    [framings] Discovery failed: {e}")
                print(f"    Discovered {len(framings_list)} framings from corpus")

            else:
                # multi_frame: generate framings via Sonnet
                framing_mode = "multi-frame"
                gen_raw = await self._call_llm(
                    model=SONNET,
                    system=FRAMING_GENERATOR_PROMPT,
                    user_msg=f'Neutral claim: "{neutral}"',
                    max_tokens=2048,
                )
                try:
                    generated = self._parse_json(gen_raw)
                    for f in generated.get("framings", []):
                        framings_list.append(Framing(
                            framing_id=f.get("id", f"gen_{len(framings_list)}"),
                            label=f.get("label", ""),
                            framing_text=f.get("framing_text", ""),
                            ideological_tradition=f.get("ideological_tradition", ""),
                            characteristic_phrases=f.get("search_phrases", []),
                        ))
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"    [framings] Generation failed: {e}")
                print(f"    Generated {len(framings_list)} framings")

            # Always include neutral and original-input baselines
            if framings_list and not any(f.is_neutral for f in framings_list):
                framings_list.insert(0, Framing(
                    framing_id="neutral",
                    label="Neutral",
                    framing_text=neutral,
                    ideological_tradition="factual baseline",
                    is_neutral=True,
                ))
            if (framings_list and claim_text.strip() != neutral.strip()
                    and not any(f.is_original_input for f in framings_list)):
                framings_list.append(Framing(
                    framing_id="original_input",
                    label="As Submitted",
                    framing_text=claim_text,
                    ideological_tradition="original input phrasing",
                    is_original_input=True,
                ))

            # ---- 6c: Search for each framing's distinctive phrases to expand corpus ----
            if framings_list:
                # Tighter cap per framing to keep cost manageable
                per_framing_cap = {"quick": 4, "standard": 6, "deep": 10}.get(depth, 6)

                print(f"\n  [6c] Expanding corpus by searching each framing's distinctive phrases...")
                framing_new_urls = {}  # url -> (framing_id, search_meta)

                for framing in framings_list:
                    if framing.is_neutral:
                        continue  # neutral already covered by main search
                    if not framing.characteristic_phrases:
                        continue

                    # Search using up to 2 characteristic phrases per framing
                    queries = framing.characteristic_phrases[:2]
                    framing_urls_found = 0
                    for phrase in queries:
                        try:
                            # Combine the phrase with the neutral topic for relevance
                            q = f'"{phrase}" {neutral[:50]}'
                            results = await self.search.search(q, num_results=4)
                            for r in results:
                                url = r.get("url", "")
                                node_id = hashlib.sha256(url.encode()).hexdigest()[:10]
                                if (url and node_id not in nodes_dict
                                        and url not in framing_new_urls
                                        and framing_urls_found < per_framing_cap):
                                    framing_new_urls[url] = (framing.framing_id, r)
                                    framing_urls_found += 1
                        except Exception:
                            continue
                    print(f"    [{framing.label}] Found {framing_urls_found} candidate URLs")

                # Analyze the new URLs gathered from framing-driven searches
                if framing_new_urls:
                    print(f"\n  [6d] Analyzing {len(framing_new_urls)} new sources from framing searches...")
                    added = 0
                    for url, (fid, _meta) in framing_new_urls.items():
                        res = await _analyze_url_for_node(url, neutral)
                        if res:
                            nid, new_node = res
                            if nid not in nodes_dict:
                                nodes_dict[nid] = new_node
                                added += 1
                    print(f"    Added {added} sources to corpus")

                expansion_count = len(nodes_dict) - initial_node_count
                if expansion_count > 0:
                    print(f"\n  Corpus expanded by {expansion_count} sources via framing searches")

                    # ---- 6e: Re-run chain reconstruction on the expanded corpus ----
                    print(f"\n  [6e] Re-running chain reconstruction on expanded corpus...")

                    # Reset chain state on existing nodes (we are recomputing it)
                    for n in nodes_dict.values():
                        n.is_origin = False
                        n.parents = []
                        n.children = []
                        n.on_main_thread = False

                    sorted_nodes_2 = sorted(
                        nodes_dict.values(),
                        key=lambda n: n.published_date or "9999",
                    )
                    corpus_2 = []
                    for n in sorted_nodes_2:
                        corpus_2.append({
                            "id": n.node_id,
                            "url": n.url,
                            "outlet": n.outlet,
                            "tier": n.tier,
                            "published_date": n.published_date,
                            "exact_phrasing": n.exact_phrasing[:300],
                            "attribution_text": n.attribution_text[:200],
                            "attributed_to": n.attributed_to,
                        })

                    chain_raw_2 = await self._call_llm(
                        model=SONNET,
                        system=CHAIN_RECONSTRUCTION_PROMPT,
                        user_msg=(
                            f'Claim being traced: "{neutral}"\n\n'
                            f"Sources (sorted by publication date):\n"
                            f"{json.dumps(corpus_2, indent=2)}"
                        ),
                        max_tokens=4096,
                    )
                    try:
                        chain_data_2 = self._parse_json(chain_raw_2)
                    except (json.JSONDecodeError, ValueError):
                        chain_data_2 = {"nodes": [], "main_thread_ids": []}

                    for nr in chain_data_2.get("nodes", []):
                        nid = nr.get("id")
                        if nid not in nodes_dict:
                            continue
                        node = nodes_dict[nid]
                        node.is_origin = nr.get("is_origin", False)
                        node.parents = nr.get("parents", [])
                        if node.is_origin and node.tier in ("secondary", "unknown"):
                            node.tier = "originator"

                    for node in nodes_dict.values():
                        for parent in node.parents:
                            pid = parent.get("id") or parent.get("node_id")
                            if pid in nodes_dict:
                                pnode = nodes_dict[pid]
                                if node.node_id not in pnode.children:
                                    pnode.children.append(node.node_id)

                    origins = [n.node_id for n in nodes_dict.values() if n.is_origin]
                    if not origins:
                        origins = [n.node_id for n in nodes_dict.values() if not n.parents]

                    main_thread_ids = chain_data_2.get("main_thread_ids", [])
                    main_thread_ids = [tid for tid in main_thread_ids if tid in nodes_dict]
                    for nid in main_thread_ids:
                        if nid in nodes_dict:
                            nodes_dict[nid].on_main_thread = True

                    print(f"    {len(origins)} origins, main thread length {len(main_thread_ids)}")

            # ---- 6f: Classify each node by which framings it carries ----
            if framings_list:
                print(f"\n  [6f] Classifying each source against {len(framings_list)} framings...")
                framing_summary = [
                    {
                        "framing_id": f.framing_id,
                        "label": f.label,
                        "framing_text": f.framing_text,
                        "characteristic_phrases": f.characteristic_phrases,
                    }
                    for f in framings_list
                ]

                classify_count = 0
                for node in nodes_dict.values():
                    if not node.exact_phrasing:
                        continue
                    try:
                        classify_raw = await self._call_llm(
                            model=HAIKU,
                            system=FRAMING_CLASSIFIER_PROMPT,
                            user_msg=(
                                f'Source\'s exact phrasing: "{node.exact_phrasing[:600]}"\n\n'
                                f"Source outlet: {node.outlet}\n\n"
                                f"Candidate framings:\n"
                                f"{json.dumps(framing_summary, indent=2)}"
                            ),
                            max_tokens=1024,
                        )
                        result = self._parse_json(classify_raw)
                        for fs in result.get("framing_strengths", []):
                            fid = fs.get("framing_id")
                            strength = fs.get("strength", 0)
                            if fid and strength > 0:
                                node.framings[fid] = strength
                        classify_count += 1
                    except (json.JSONDecodeError, ValueError):
                        continue
                    except Exception:
                        continue

                print(f"    Classified {classify_count} sources")
                for f in framings_list:
                    count = sum(
                        1 for n in nodes_dict.values()
                        if n.framings.get(f.framing_id, 0) >= 0.5
                    )
                    print(f"    [{f.label}] {count} sources carry this framing")

        tree = TraceTree(
            claim_text=claim_text,
            neutral_phrasing=neutral,
            nodes=list(nodes_dict.values()),
            origins=origins,
            main_thread=main_thread_ids,
            depth=depth,
            days_back=days_back,
            cost_usd=self.usage["cost_usd"],
            framings=framings_list,
            framing_mode=framing_mode,
        )

        return tree


# ---------------------------------------------------------------------------
# Example usage / CLI
# ---------------------------------------------------------------------------

async def main():
    """Example: trace claims in a sample article."""

    sample_article = """
    The Bureau of Labor Statistics reported that the US economy added 256,000 
    jobs in December 2024, significantly exceeding economists' expectations of 
    155,000. The unemployment rate fell to 4.1%, down from 4.2% in November.
    
    Meanwhile, a new study from MIT found that AI-assisted workers were 35% 
    more productive than those without AI tools, though the researchers noted 
    that benefits varied significantly across industries.
    
    Critics argue that these job numbers don't tell the full story, as many 
    new positions are part-time or gig work rather than traditional full-time 
    employment. Some economists believe the labor market is actually weaker 
    than the headline numbers suggest.
    """

    # Initialize with your search adapter
    search = WebSearchAdapter(api_key="your-key-here")
    tracer = SourceTracer(search_adapter=search)

    try:
        chains = await tracer.trace_content(sample_article)

        print("\n" + "=" * 70)
        print("PROVENANCE REPORT")
        print("=" * 70)

        for chain in chains:
            print(f"\nClaim: {chain.claim.text}")
            print(f"  Label: {chain.claim.label.value}")
            print(f"  Confidence: {chain.confidence:.0%}")
            print(f"  Chain complete: {chain.chain_complete}")
            print(f"  Hops: {chain.hops}")
            if chain.primary_source:
                print(f"  Primary source: {chain.primary_source.url}")
            print(f"  Summary: {chain.summary}")
            print()

        print(f"Total cost: ${tracer.usage['cost_usd']:.4f}")
        print(f"Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")
        print(f"Cached claims: {tracer.usage['cached_claims']}")

    finally:
        await search.close()


if __name__ == "__main__":
    asyncio.run(main())
