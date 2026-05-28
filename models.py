"""
Tributary Data Model
=====================
The core data structure for Tributary. Designed to support:

  - AI-generated initial analysis (first draft)
  - Human contributions that enrich, correct, or extend the analysis
  - Information flow tracking with precise relationship types
  - Reputation-weighted consensus for disputed elements
  - Timeline reconstruction of how information spread

Every element carries attribution (who produced it, when, how confident)
so the provenance of the analysis itself is transparent.

This module defines the data structures only — no AI calls, no database.
It's the contract between the AI pipeline, the storage layer, and the 
future web interface.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import hashlib
import json


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ClaimLabel(str, Enum):
    FACT = "fact"
    STUDY = "study"
    NARRATIVE = "narrative"
    OPINION = "opinion"
    UNVERIFIABLE = "unverifiable"
    UNPROVEN = "unproven"


class FlowType(str, Enum):
    """
    Precise relationship types for information flow.
    Distinguishes between explicit citation, independent co-reporting,
    amplification, and transformation.
    """
    # Direct attribution
    CITES = "cites"                 # Explicitly attributes: "according to X..."
    QUOTES = "quotes"               # Directly quotes from the source

    # Independent coverage
    CO_REPORTS = "co_reports"        # Both independently cover the same primary source
    RESPONDS_TO = "responds_to"     # Published in reaction to, but not citing

    # Amplification
    AMPLIFIES = "amplifies"          # Spreads the same framing without attribution
    REPOSTS = "reposts"              # Direct share/repost on social media
    PARAPHRASES = "paraphrases"      # Restates in different words

    # Transformation
    DISTORTS = "distorts"            # Changes meaning, strips context, exaggerates
    SIMPLIFIES = "simplifies"        # Reduces complexity, drops nuance
    POLITICIZES = "politicizes"      # Adds partisan framing to neutral data
    RECONTEXTUALIZES = "recontextualizes"  # Places in a different interpretive frame

    # Opposition
    REBUTS = "rebuts"                # Directly argues against
    FACT_CHECKS = "fact_checks"      # Verifies or debunks

    # Unknown / AI-inferred
    RELATED = "related"              # Connected but relationship unclear


class ContributionType(str, Enum):
    """Types of contributions a human can make."""
    ADD_CLAIM = "add_claim"
    EDIT_CLAIM = "edit_claim"
    ADD_SOURCE = "add_source"
    ADD_FLOW = "add_flow"              # Human identifies an information flow connection
    EDIT_FLOW = "edit_flow"            # Correct a flow type (e.g., co_reports not cites)
    ADD_VIEW = "add_view"
    EDIT_VIEW = "edit_view"
    ADD_MUTATION = "add_mutation"
    ADD_OMISSION = "add_omission"
    FLAG = "flag"                      # Flag something as incorrect
    CONFIRM = "confirm"                # Confirm AI analysis is correct
    ADD_CONTEXT = "add_context"        # Add context the AI missed


class ElementStatus(str, Enum):
    """Status of any element in the analysis."""
    AI_GENERATED = "ai_generated"       # Initial AI pass, unreviewed
    HUMAN_CONFIRMED = "human_confirmed" # At least one human confirmed
    DISPUTED = "disputed"               # Humans disagree with AI or each other
    HUMAN_ADDED = "human_added"         # Added by a human, not AI
    CONSENSUS = "consensus"             # Multiple humans agree
    RETRACTED = "retracted"             # Marked as incorrect by consensus


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

@dataclass
class Attribution:
    """Who produced this element and when."""
    source: str                    # "ai" or contributor_id
    model: str = ""                # "claude-sonnet-4-6" etc. (if AI)
    contributor_name: str = ""     # Display name (if human)
    timestamp: str = ""
    confidence: float = 0.0        # AI confidence or human certainty
    reasoning: str = ""            # Why this classification/connection was made

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "source": self.source,
            "model": self.model,
            "contributor_name": self.contributor_name,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


# ---------------------------------------------------------------------------
# Core elements
# ---------------------------------------------------------------------------

@dataclass
class TrackedClaim:
    """
    A discrete claim extracted from content.
    Carries full attribution and status tracking.
    """
    claim_id: str
    text: str
    label: ClaimLabel
    context: str = ""
    status: ElementStatus = ElementStatus.AI_GENERATED
    attribution: Attribution = None
    confirmations: list[Attribution] = field(default_factory=list)
    disputes: list[dict] = field(default_factory=list)
    # disputes: [{"attribution": Attribution, "proposed_label": ClaimLabel, "reason": str}]

    def __post_init__(self):
        if not self.claim_id:
            self.claim_id = hashlib.sha256(
                self.text.lower().strip().encode()
            ).hexdigest()[:12]
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    @property
    def confirmation_count(self) -> int:
        return len(self.confirmations)

    @property
    def dispute_count(self) -> int:
        return len(self.disputes)

    @property
    def is_settled(self) -> bool:
        """A claim is settled if confirmed by 3+ humans with no disputes,
        or by 5+ humans even with disputes."""
        if self.dispute_count == 0 and self.confirmation_count >= 3:
            return True
        if self.confirmation_count >= 5 and self.confirmation_count > self.dispute_count * 2:
            return True
        return False

    def to_dict(self):
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "label": self.label.value,
            "context": self.context,
            "status": self.status.value,
            "attribution": self.attribution.to_dict() if self.attribution else None,
            "confirmations": len(self.confirmations),
            "disputes": len(self.disputes),
            "is_settled": self.is_settled,
        }


@dataclass
class TrackedSource:
    """A source (article, post, report) with attribution."""
    url: str
    title: str = ""
    source_type: str = ""          # news, think_tank, government, academic, social, etc.
    published_at: str = ""
    author: str = ""
    excerpt: str = ""
    tier: str = ""                 # primary, secondary, tertiary, originator, amplifier, critic
    attribution: Attribution = None

    def __post_init__(self):
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "published_at": self.published_at,
            "author": self.author,
            "excerpt": self.excerpt,
            "tier": self.tier,
            "attribution": self.attribution.to_dict() if self.attribution else None,
        }


@dataclass
class InformationFlow:
    """
    A directional connection between two sources representing
    how information moved from one to the other.
    
    The flow_type is critical — it distinguishes between:
    - "cites": explicit attribution (A says "according to B")
    - "co_reports": both independently cover the same primary source
    - "amplifies": A spreads B's framing without attribution
    - "distorts": A changes B's meaning
    
    Evidence should explain WHY this flow type was assigned.
    For "cites" — the attribution language found.
    For "co_reports" — the shared primary source and timing.
    For "amplifies" — the similarity in framing.
    For "distorts" — what changed.
    """
    flow_id: str = ""
    from_source: str = ""          # URL of source
    to_source: str = ""            # URL of destination
    flow_type: FlowType = FlowType.RELATED
    evidence: str = ""             # Why this flow type was assigned
    timing: str = ""               # When this flow occurred
    status: ElementStatus = ElementStatus.AI_GENERATED
    attribution: Attribution = None
    confirmations: list[Attribution] = field(default_factory=list)
    disputes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.flow_id:
            self.flow_id = hashlib.sha256(
                f"{self.from_source}→{self.to_source}".encode()
            ).hexdigest()[:12]
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "flow_id": self.flow_id,
            "from_source": self.from_source,
            "to_source": self.to_source,
            "flow_type": self.flow_type.value,
            "evidence": self.evidence,
            "timing": self.timing,
            "status": self.status.value,
            "attribution": self.attribution.to_dict() if self.attribution else None,
            "confirmations": len(self.confirmations),
            "disputes": len(self.disputes),
        }


@dataclass
class View:
    """
    An analytical perspective on an event — a specific question
    being asked about what happened.
    """
    view_id: str = ""
    name: str = ""
    question: str = ""
    description: str = ""
    key_claim: str = ""
    sources: list[TrackedSource] = field(default_factory=list)
    status: ElementStatus = ElementStatus.AI_GENERATED
    attribution: Attribution = None

    def __post_init__(self):
        if not self.view_id:
            self.view_id = hashlib.sha256(
                self.name.lower().strip().encode()
            ).hexdigest()[:8]
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "view_id": self.view_id,
            "name": self.name,
            "question": self.question,
            "description": self.description,
            "key_claim": self.key_claim,
            "sources": [s.to_dict() for s in self.sources],
            "status": self.status.value,
            "attribution": self.attribution.to_dict() if self.attribution else None,
        }


@dataclass
class Mutation:
    """How a claim changed between two sources."""
    from_source: str = ""
    to_source: str = ""
    preserved: str = ""
    dropped: str = ""
    added: str = ""
    distorted: str = ""
    status: ElementStatus = ElementStatus.AI_GENERATED
    attribution: Attribution = None

    def __post_init__(self):
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "from_source": self.from_source,
            "to_source": self.to_source,
            "preserved": self.preserved,
            "dropped": self.dropped,
            "added": self.added,
            "distorted": self.distorted,
            "status": self.status.value,
            "attribution": self.attribution.to_dict() if self.attribution else None,
        }


@dataclass
class Omission:
    """Something missing from a particular view."""
    view_name: str = ""
    what_is_missing: str = ""
    found_in_view: str = ""
    impact: str = ""
    omission_type: str = ""        # factual, perspective, context
    status: ElementStatus = ElementStatus.AI_GENERATED
    attribution: Attribution = None

    def __post_init__(self):
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "view_name": self.view_name,
            "what_is_missing": self.what_is_missing,
            "found_in_view": self.found_in_view,
            "impact": self.impact,
            "omission_type": self.omission_type,
            "status": self.status.value,
            "attribution": self.attribution.to_dict() if self.attribution else None,
        }


# ---------------------------------------------------------------------------
# Narrative Fingerprints
# ---------------------------------------------------------------------------
# A NarrativeFingerprint codifies the structure of a narrative so Tributary
# can search for earliest documented uses (L1+L4), detect paraphrases that
# share meaning but not vocabulary (L2), categorize by rhetorical structure
# (L3), and cluster by domain and inductively-discovered communities (L5).
#
# v1 populates L1 + L4 only. The L2 / L3 / L5 schemas exist here so the
# storage format is stable from day one and later layers can be added
# without migrating existing fingerprints.


class FramePrimitive(str, Enum):
    """L3 structural framing primitives — neutrally-phrased, domain-agnostic.
    A fingerprint may carry multiple as an unordered tag set."""
    ATTRIBUTION_OF_CAUSE = "attribution-of-cause"
    ATTRIBUTION_OF_BLAME = "attribution-of-blame"
    HARM_CLAIM = "harm-claim"
    THREAT_CLAIM = "threat-claim"
    SOLUTION_PRESCRIPTION = "solution-prescription"
    IDENTITY_DEFENSE = "identity-defense"
    PROCESS_VIOLATION = "process-violation"
    VALUE_COMPARISON = "value-comparison"
    HISTORICAL_ARC = "historical-arc"
    REVELATION = "revelation"


class Domain(str, Enum):
    """L5 subject area — broad, mutually exclusive."""
    ECONOMIC = "economic"
    RACIAL_ETHNIC = "racial-ethnic"
    IMMIGRATION = "immigration"
    HEALTH = "health"
    FOREIGN_POLICY = "foreign-policy"
    CULTURAL = "cultural"
    TECHNOLOGY = "technology"
    ENVIRONMENT = "environment"
    RELIGION = "religion"
    GENDER_SEXUALITY = "gender-sexuality"
    EDUCATION = "education"
    CRIMINAL_JUSTICE = "criminal-justice"
    MEDIA_META = "media-meta"
    WELLNESS_LIFESTYLE = "wellness-lifestyle"
    FINANCE_INVESTING = "finance-investing"
    FANDOM_ENTERTAINMENT = "fandom-entertainment"
    SCIENCE = "science"
    OTHER = "other"


class GenealogyStatus(str, Enum):
    """Whether the narrative has one origin, several parallel origins, or is
    too widespread/diffuse to pinpoint."""
    SINGLE_ORIGIN = "single-origin"
    MULTIPLE_INDEPENDENT = "multiple-independent"
    DIFFUSE = "diffuse"
    UNKNOWN = "unknown"


class AmplifierRole(str, Enum):
    """The functional role an attested instance played in the narrative's
    spread. Default is UNKNOWN so old fingerprints (which predate this field)
    deserialize cleanly."""
    UNKNOWN = "unknown"
    ORIGINATOR = "originator"                  # earliest credible articulation
    EARLY_AMPLIFIER = "early-amplifier"        # spread before mainstream uptake
    MASS_AMPLIFIER = "mass-amplifier"           # drove broad public adoption
    INSTITUTIONAL_ADOPTION = "institutional-adoption"  # adopted into party, govt, major outlet, official platform
    CRITIC = "critic"                          # pushed back, fact-checked, or rebutted
    MENTION = "mention"                        # used the framing without driving spread


class SourceDirection(str, Enum):
    """How a piece of information relates to a claim in the evidence landscape."""
    SUPPORTS = "supports"               # provides evidence backing the claim
    DISPUTES = "disputes"               # provides evidence against the claim
    REDIRECTS = "redirects"             # same starting material, different conclusion
    SHARED_CONTEXT = "shared-context"   # accepted by all sides, relevant background


class SourceStrength(str, Enum):
    """How decisively a source bears on the claim."""
    STRONG = "strong"        # compelling: large sample / robust methodology /
                             # replicated / widely accepted
    MODERATE = "moderate"    # decent evidence with caveats
    WEAK = "weak"            # underpowered / contested methodology / anecdotal /
                             # supports only a weaker version of the claim


class SourceVenue(str, Enum):
    """Where the source was published / who issued it."""
    PEER_REVIEWED = "peer-reviewed"          # peer-reviewed scientific journal
    INSTITUTIONAL = "institutional"          # govt agency, intergovernmental org, major NGO
    NEWS_OUTLET = "news-outlet"              # mainstream journalism
    OPINION_VENUE = "opinion-venue"          # editorial, op-ed, blog, Substack
    AGGREGATOR = "aggregator"                # Wikipedia, encyclopedia, summary site
    PREPRINT_SERVER = "preprint-server"      # arXiv, bioRxiv, SSRN — distinct from peer-reviewed
    SELF_PUBLISHED = "self-published"        # personal blog, YouTube, podcast
    OTHER = "other"


class SourceType(str, Enum):
    """How direct the source is — orthogonal to venue."""
    PRIMARY = "primary"      # original research, raw data, official statement,
                             # direct observation, original document
    SECONDARY = "secondary"  # analysis, interpretation, review, journalism about events
    TERTIARY = "tertiary"    # compilation of secondary sources, encyclopedia, textbook


class SourceStatus(str, Enum):
    """Issues with the source itself — flags retractions, supersessions, etc."""
    CURRENT = "current"
    RETRACTED = "retracted"
    METHODOLOGY_DISPUTED = "methodology-disputed"
    SUPERSEDED = "superseded"
    CORRECTION_ISSUED = "correction-issued"


@dataclass
class Scope:
    """User-configurable analysis scope. v1 fixes language to English."""
    language: str = "en"
    region: str = "US"                # "US", "EU", "global", country code, etc.
    time_window_start: str = ""        # ISO date or "" for unbounded
    time_window_end: str = ""

    def to_dict(self):
        return {
            "language": self.language,
            "region": self.region,
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
        }


@dataclass
class LexicalLayer:
    """L1 — what to search for. The keys that drive earliest-use search."""
    canonical_phrase: str = ""
    phrase_variants: list[str] = field(default_factory=list)
    diagnostic_ngrams: list[str] = field(default_factory=list)
    stopword_stripped_signature: str = ""

    def to_dict(self):
        return {
            "canonical_phrase": self.canonical_phrase,
            "phrase_variants": self.phrase_variants,
            "diagnostic_ngrams": self.diagnostic_ngrams,
            "stopword_stripped_signature": self.stopword_stripped_signature,
        }


@dataclass
class ConceptualLayer:
    """L2 — vocabulary-independent meaning. Schema only in v1."""
    claim_predicate: str = ""
    entities: dict = field(default_factory=dict)
    causal_structure: str = ""

    def to_dict(self):
        return {
            "claim_predicate": self.claim_predicate,
            "entities": self.entities,
            "causal_structure": self.causal_structure,
        }


@dataclass
class RhetoricalLayer:
    """L3 — structural rhetorical signature. Schema only in v1."""
    frame_primitives: list[FramePrimitive] = field(default_factory=list)
    valence: dict = field(default_factory=dict)
    epistemic_stance: str = ""
    register: str = ""

    def to_dict(self):
        return {
            "frame_primitives": [p.value for p in self.frame_primitives],
            "valence": self.valence,
            "epistemic_stance": self.epistemic_stance,
            "register": self.register,
        }


@dataclass
class AttestedInstance:
    """A single observed use of a narrative — one entry in the attestation log."""
    instance_id: str = ""
    date: str = ""
    source_url: str = ""
    source_title: str = ""
    author: str = ""
    lexical_form_seen: str = ""
    exact_quote: str = ""
    confidence: float = 0.0
    evidence: str = ""
    amplifier_role: AmplifierRole = AmplifierRole.UNKNOWN
    role_evidence: str = ""             # short justification for the assigned role
    # Verification fields — populated by the post-generation URL+quote check
    verified: bool = False
    verification_status: str = "unchecked"  # unchecked / verified / url-error / fetch-error / quote-not-found
    verification_notes: str = ""
    archive_url: str = ""               # Wayback snapshot if the live URL is down

    def __post_init__(self):
        if not self.instance_id:
            key = f"{self.source_url}|{self.date}|{self.exact_quote[:64]}"
            self.instance_id = hashlib.sha256(key.encode()).hexdigest()[:12]

    def to_dict(self):
        return {
            "instance_id": self.instance_id,
            "date": self.date,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "author": self.author,
            "lexical_form_seen": self.lexical_form_seen,
            "exact_quote": self.exact_quote,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "amplifier_role": self.amplifier_role.value,
            "role_evidence": self.role_evidence,
            "verified": self.verified,
            "verification_status": self.verification_status,
            "verification_notes": self.verification_notes,
            "archive_url": self.archive_url,
        }


@dataclass
class SocialAttestedInstance:
    """A social-platform observation of a narrative — distinct from formal
    AttestedInstance because platform context and engagement metrics matter
    differently than citable sources. Lives on LineageRecord.social_spread."""
    instance_id: str = ""
    platform: str = ""               # "bluesky", "twitter", "reddit", etc.
    post_url: str = ""
    author_handle: str = ""
    post_date: str = ""              # ISO timestamp
    post_text: str = ""
    matched_ngram: str = ""          # which diagnostic n-gram (or canonical phrase) matched
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    amplifier_role: AmplifierRole = AmplifierRole.UNKNOWN
    role_evidence: str = ""
    # Verification — URL existence only; post_text rarely scrapable from page
    verified: bool = False
    verification_status: str = "unchecked"
    verification_notes: str = ""
    archive_url: str = ""

    def __post_init__(self):
        if not self.instance_id:
            key = f"{self.platform}|{self.post_url}|{self.post_date}"
            self.instance_id = hashlib.sha256(key.encode()).hexdigest()[:12]

    def to_dict(self):
        return {
            "instance_id": self.instance_id,
            "platform": self.platform,
            "post_url": self.post_url,
            "author_handle": self.author_handle,
            "post_date": self.post_date,
            "post_text": self.post_text,
            "matched_ngram": self.matched_ngram,
            "likes": self.likes,
            "reposts": self.reposts,
            "replies": self.replies,
            "amplifier_role": self.amplifier_role.value,
            "role_evidence": self.role_evidence,
            "verified": self.verified,
            "verification_status": self.verification_status,
            "verification_notes": self.verification_notes,
            "archive_url": self.archive_url,
        }


@dataclass
class LineageRecord:
    """One lineage's worth of dating, attestation, and adversarial verification.
    GenealogyLayer carries two: lexical (where the phrasing comes from) and
    conceptual (where the underlying structural claim comes from).

    `mutations` records how the framing changed between significant
    attested instances — populated as a post-processing step over the
    attestation_log."""
    lineage_type: str = ""              # "lexical" or "conceptual"
    status: GenealogyStatus = GenealogyStatus.UNKNOWN
    first_attested_date: str = ""
    first_attested_source: str = ""
    attestation_confidence: float = 0.0
    primary_origin_id: str = ""
    parallel_origin_ids: list[str] = field(default_factory=list)
    attestation_log: list[AttestedInstance] = field(default_factory=list)
    adversarial_check_performed: bool = False
    adversarial_notes: str = ""
    mutations: list[Mutation] = field(default_factory=list)
    timeline_stats: dict = field(default_factory=dict)
    social_spread: list[SocialAttestedInstance] = field(default_factory=list)
    social_search_performed: bool = False
    social_search_notes: str = ""

    def to_dict(self):
        return {
            "lineage_type": self.lineage_type,
            "status": self.status.value,
            "first_attested_date": self.first_attested_date,
            "first_attested_source": self.first_attested_source,
            "attestation_confidence": self.attestation_confidence,
            "primary_origin_id": self.primary_origin_id,
            "parallel_origin_ids": self.parallel_origin_ids,
            "attestation_log": [a.to_dict() for a in self.attestation_log],
            "adversarial_check_performed": self.adversarial_check_performed,
            "adversarial_notes": self.adversarial_notes,
            "mutations": [m.to_dict() for m in self.mutations],
            "timeline_stats": self.timeline_stats,
            "social_spread": [s.to_dict() for s in self.social_spread],
            "social_search_performed": self.social_search_performed,
            "social_search_notes": self.social_search_notes,
        }


@dataclass
class GenealogyLayer:
    """L4 — when/where the narrative has been seen, across two parallel lineages.

    `lexical` traces the specific phrasing (where did "rigged economy" come
    from?). `conceptual` traces the underlying structural claim (where did the
    idea of structural worker disadvantage come from?). The two often answer
    very different questions: a phrase may be 15 years old but its underlying
    claim 200 years old."""
    lexical: LineageRecord = field(
        default_factory=lambda: LineageRecord(lineage_type="lexical")
    )
    conceptual: LineageRecord = field(
        default_factory=lambda: LineageRecord(lineage_type="conceptual")
    )
    predecessor_fingerprint_ids: list[str] = field(default_factory=list)
    successor_fingerprint_ids: list[str] = field(default_factory=list)
    earlier_candidates_rejected: list[dict] = field(default_factory=list)

    def to_dict(self):
        return {
            "lexical": self.lexical.to_dict(),
            "conceptual": self.conceptual.to_dict(),
            "predecessor_fingerprint_ids": self.predecessor_fingerprint_ids,
            "successor_fingerprint_ids": self.successor_fingerprint_ids,
            "earlier_candidates_rejected": self.earlier_candidates_rejected,
        }


@dataclass
class TaxonomicLayer:
    """L5 — taxonomic placement. Inductive tradition clusters and tropes
    populate over time; only `domain` is enumerated up front."""
    domain: Domain = Domain.OTHER
    domain_confidence: float = 0.0
    inductive_cluster_ids: list[str] = field(default_factory=list)
    tropes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "domain": self.domain.value,
            "domain_confidence": self.domain_confidence,
            "inductive_cluster_ids": self.inductive_cluster_ids,
            "tropes": self.tropes,
        }


@dataclass
class InformationSource:
    """A single piece of information in the claim's epistemic landscape —
    a study, dataset, document, or event tagged by how it relates to the
    claim and by its quality characteristics."""
    source_id: str = ""
    title: str = ""
    description: str = ""            # one-sentence summary
    direction: SourceDirection = SourceDirection.SHARED_CONTEXT
    strength: SourceStrength = SourceStrength.MODERATE
    source_venue: SourceVenue = SourceVenue.OTHER
    source_type: SourceType = SourceType.SECONDARY
    source_url: str = ""
    date: str = ""                   # ISO date if known
    status: SourceStatus = SourceStatus.CURRENT
    status_notes: str = ""           # only when status != current
    notes: str = ""                  # one-sentence justification of direction
    # Verification — URL existence check; description is summary not quote
    verified: bool = False
    verification_status: str = "unchecked"
    verification_notes: str = ""
    archive_url: str = ""

    def __post_init__(self):
        if not self.source_id:
            key = f"{self.source_url}|{self.title}|{self.date}"
            self.source_id = hashlib.sha256(key.encode()).hexdigest()[:12]

    def to_dict(self):
        return {
            "source_id": self.source_id,
            "title": self.title,
            "description": self.description,
            "direction": self.direction.value,
            "strength": self.strength.value,
            "source_venue": self.source_venue.value,
            "source_type": self.source_type.value,
            "source_url": self.source_url,
            "date": self.date,
            "status": self.status.value,
            "status_notes": self.status_notes,
            "notes": self.notes,
            "verified": self.verified,
            "verification_status": self.verification_status,
            "verification_notes": self.verification_notes,
            "archive_url": self.archive_url,
        }


@dataclass
class EvidenceLandscape:
    """The information ecosystem around a claim — what supports, disputes,
    redirects, or contextualizes it. Curated (not exhaustive), generated
    via web search + classification. Takes no side on whether the claim
    is true; surfaces the epistemic terrain so the reader can judge."""
    sources: list[InformationSource] = field(default_factory=list)
    summary: str = ""                # one-paragraph synthesis
    search_notes: str = ""           # what was searched, what was under-covered
    generated_at: str = ""

    def to_dict(self):
        return {
            "sources": [s.to_dict() for s in self.sources],
            "summary": self.summary,
            "search_notes": self.search_notes,
            "generated_at": self.generated_at,
        }


@dataclass
class NarrativeFingerprint:
    """Codified signature of a narrative — searchable, categorizable, dateable.

    Identity is keyed on the lexical layer (canonical_phrase + diagnostic_ngrams)
    so fingerprints for the same framing collapse to one entry regardless of
    which surface variant triggered generation."""
    fingerprint_id: str = ""
    created_at: str = ""
    last_updated: str = ""
    taxonomy_version: str = "0.1"
    scope: Scope = field(default_factory=Scope)

    lexical: LexicalLayer = field(default_factory=LexicalLayer)
    conceptual: ConceptualLayer = field(default_factory=ConceptualLayer)
    rhetorical: RhetoricalLayer = field(default_factory=RhetoricalLayer)
    genealogy: GenealogyLayer = field(default_factory=GenealogyLayer)
    taxonomic: TaxonomicLayer = field(default_factory=TaxonomicLayer)
    evidence_landscape: EvidenceLandscape = field(default_factory=EvidenceLandscape)

    attribution: Attribution = None

    def __post_init__(self):
        if not self.fingerprint_id:
            key = (
                self.lexical.canonical_phrase.lower().strip()
                + "|"
                + "|".join(sorted(n.lower() for n in self.lexical.diagnostic_ngrams))
            )
            self.fingerprint_id = hashlib.sha256(key.encode()).hexdigest()[:12]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.last_updated:
            self.last_updated = now
        if self.attribution is None:
            self.attribution = Attribution(source="ai")

    def to_dict(self):
        return {
            "fingerprint_id": self.fingerprint_id,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "taxonomy_version": self.taxonomy_version,
            "scope": self.scope.to_dict(),
            "lexical": self.lexical.to_dict(),
            "conceptual": self.conceptual.to_dict(),
            "rhetorical": self.rhetorical.to_dict(),
            "genealogy": self.genealogy.to_dict(),
            "taxonomic": self.taxonomic.to_dict(),
            "evidence_landscape": self.evidence_landscape.to_dict(),
            "attribution": self.attribution.to_dict() if self.attribution else None,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Multi-claim source analysis
# ---------------------------------------------------------------------------

@dataclass
class ExtractedClaim:
    """A claim extracted from a piece of content, classified by type. Only
    traceable claims (fact / study / narrative) get fingerprinted; opinions
    and unverifiable statements are inventoried but not traced."""
    claim_text: str = ""
    claim_type: str = ""        # fact / study / narrative / opinion / unverifiable
    significance: str = ""      # why this claim is significant in the piece
    context: str = ""           # where it sits in the source
    traceable: bool = True      # True for fact/study/narrative; False otherwise
    fingerprint_id: str = ""    # populated once fingerprinted (traceable only)

    def to_dict(self):
        return {
            "claim_text": self.claim_text,
            "claim_type": self.claim_type,
            "significance": self.significance,
            "context": self.context,
            "traceable": self.traceable,
            "fingerprint_id": self.fingerprint_id,
        }


@dataclass
class SourceAnalysis:
    """Multi-claim analysis of one piece of content (article, blog post,
    podcast transcript, social thread). Extracts the significant traceable
    claims and fingerprints each — so you can trace where the ideas in a
    whole piece come from, not just one hand-typed claim."""
    analysis_id: str = ""
    source_url: str = ""
    source_title: str = ""
    source_platform: str = ""
    source_author: str = ""
    source_published_at: str = ""
    extracted_at: str = ""
    content_excerpt: str = ""   # first chunk of extracted text, for reference
    breakdown: dict = field(default_factory=dict)  # content profile: counts/percentages by type
    claims: list[ExtractedClaim] = field(default_factory=list)
    fingerprints: list[NarrativeFingerprint] = field(default_factory=list)

    def __post_init__(self):
        if not self.analysis_id:
            key = f"{self.source_url}|{self.source_title}"
            self.analysis_id = hashlib.sha256(key.encode()).hexdigest()[:12]
        if not self.extracted_at:
            self.extracted_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            # Marker so the viewer can tell an article analysis from a
            # single fingerprint.
            "is_source_analysis": True,
            "analysis_id": self.analysis_id,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "source_platform": self.source_platform,
            "source_author": self.source_author,
            "source_published_at": self.source_published_at,
            "extracted_at": self.extracted_at,
            "content_excerpt": self.content_excerpt,
            "breakdown": self.breakdown,
            "claims": [c.to_dict() for c in self.claims],
            "fingerprints": [f.to_dict() for f in self.fingerprints],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Contribution & Reputation
# ---------------------------------------------------------------------------

@dataclass
class Contribution:
    """A single human contribution to an analysis."""
    contribution_id: str = ""
    contributor_id: str = ""
    contributor_name: str = ""
    contribution_type: ContributionType = ContributionType.ADD_CONTEXT
    target_id: str = ""            # ID of the element being modified
    content: dict = field(default_factory=dict)  # The actual contribution
    timestamp: str = ""
    upvotes: int = 0
    downvotes: int = 0

    def __post_init__(self):
        if not self.contribution_id:
            self.contribution_id = hashlib.sha256(
                f"{self.contributor_id}:{self.target_id}:{self.timestamp}".encode()
            ).hexdigest()[:10]
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def score(self) -> int:
        return self.upvotes - self.downvotes

    def to_dict(self):
        return {
            "contribution_id": self.contribution_id,
            "contributor_id": self.contributor_id,
            "contributor_name": self.contributor_name,
            "contribution_type": self.contribution_type.value,
            "target_id": self.target_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "score": self.score,
        }


@dataclass
class ContributorProfile:
    """
    Reputation tracking for a contributor.
    
    Reputation is earned by:
      - Confirming AI analysis that others also confirm (+1)
      - Adding information that gets upvoted (+2)
      - Adding flows/connections that get confirmed (+3)
      - Having contributions that reach consensus (+5)
    
    Reputation is lost by:
      - Having contributions downvoted (-1)
      - Having contributions retracted (-3)
      - Repeated flagging by other contributors (-5)
    
    Contribution privileges:
      - 0+ rep: Can confirm/dispute AI analysis, flag errors
      - 10+ rep: Can add sources, claims, omissions
      - 25+ rep: Can add flows, mutations, views
      - 50+ rep: Can edit AI-generated content
      - 100+ rep: Can participate in consensus votes to override AI
    """
    contributor_id: str
    display_name: str = ""
    reputation: int = 0
    contributions_count: int = 0
    confirmations_count: int = 0
    disputes_count: int = 0
    joined_at: str = ""

    def __post_init__(self):
        if not self.joined_at:
            self.joined_at = datetime.now(timezone.utc).isoformat()

    @property
    def can_confirm(self) -> bool:
        return self.reputation >= 0

    @property
    def can_add_sources(self) -> bool:
        return self.reputation >= 10

    @property
    def can_add_flows(self) -> bool:
        return self.reputation >= 25

    @property
    def can_edit_ai(self) -> bool:
        return self.reputation >= 50

    @property
    def can_override_ai(self) -> bool:
        return self.reputation >= 100

    def to_dict(self):
        return {
            "contributor_id": self.contributor_id,
            "display_name": self.display_name,
            "reputation": self.reputation,
            "contributions_count": self.contributions_count,
            "level": self._level,
        }

    @property
    def _level(self) -> str:
        if self.reputation >= 100:
            return "trusted"
        if self.reputation >= 50:
            return "editor"
        if self.reputation >= 25:
            return "contributor"
        if self.reputation >= 10:
            return "member"
        return "observer"


# ---------------------------------------------------------------------------
# Top-level event (the root container)
# ---------------------------------------------------------------------------

@dataclass
class TributaryEvent:
    """
    The top-level container for a complete Tributary analysis.
    
    Represents a single event or topic with all its claims, sources,
    views, information flows, mutations, omissions, and contributions.
    
    The AI pipeline creates the initial version. Humans enrich it over time.
    """
    event_id: str = ""
    topic: str = ""                    # Original phrasing
    neutral_statement: str = ""        # AI-neutralized version
    created_at: str = ""
    last_updated: str = ""
    ai_cost_usd: float = 0.0

    # Core analysis
    claims: list[TrackedClaim] = field(default_factory=list)
    sources: list[TrackedSource] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    flows: list[InformationFlow] = field(default_factory=list)
    mutations: list[Mutation] = field(default_factory=list)
    omissions: list[Omission] = field(default_factory=list)

    # Human contributions
    contributions: list[Contribution] = field(default_factory=list)
    contributors: list[ContributorProfile] = field(default_factory=list)

    # Metadata
    primary_source_url: str = ""
    primary_source_title: str = ""
    gap_analysis: str = ""
    mutation_summary: str = ""

    # Shared foundation — what all views agree on
    shared_facts_verified: list[dict] = field(default_factory=list)
    shared_facts_unverified: list[dict] = field(default_factory=list)
    points_of_disagreement: list[dict] = field(default_factory=list)
    shared_summary: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = hashlib.sha256(
                self.neutral_statement.lower().strip().encode()
                if self.neutral_statement
                else self.topic.lower().strip().encode()
            ).hexdigest()[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.last_updated:
            self.last_updated = self.created_at

    def add_claim(self, claim: TrackedClaim):
        self.claims.append(claim)
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def add_source(self, source: TrackedSource):
        # Dedup by URL
        if not any(s.url == source.url for s in self.sources):
            self.sources.append(source)

    def add_flow(self, flow: InformationFlow):
        # Dedup by from→to
        if not any(f.from_source == flow.from_source and f.to_source == flow.to_source
                   for f in self.flows):
            self.flows.append(flow)
            self.last_updated = datetime.now(timezone.utc).isoformat()

    def add_contribution(self, contribution: Contribution):
        self.contributions.append(contribution)
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def get_timeline(self) -> list[dict]:
        """
        Build a chronological timeline of information flow.
        Returns flows sorted by timing, with source details.
        """
        timeline = []
        for flow in self.flows:
            from_src = next((s for s in self.sources if s.url == flow.from_source), None)
            to_src = next((s for s in self.sources if s.url == flow.to_source), None)

            timeline.append({
                "timing": flow.timing or "",
                "from": {
                    "url": flow.from_source,
                    "title": from_src.title if from_src else "",
                    "type": from_src.source_type if from_src else "",
                },
                "to": {
                    "url": flow.to_source,
                    "title": to_src.title if to_src else "",
                    "type": to_src.source_type if to_src else "",
                },
                "flow_type": flow.flow_type.value,
                "evidence": flow.evidence,
                "status": flow.status.value,
                "attribution": flow.attribution.source if flow.attribution else "unknown",
            })

        timeline.sort(key=lambda t: t["timing"])
        return timeline

    def get_stats(self) -> dict:
        """Summary statistics for the analysis."""
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "claims": len(self.claims),
            "claims_by_label": {
                label.value: sum(1 for c in self.claims if c.label == label)
                for label in ClaimLabel
                if any(c.label == label for c in self.claims)
            },
            "sources": len(self.sources),
            "views": len(self.views),
            "flows": len(self.flows),
            "flows_by_type": {
                ft.value: sum(1 for f in self.flows if f.flow_type == ft)
                for ft in FlowType
                if any(f.flow_type == ft for f in self.flows)
            },
            "mutations": len(self.mutations),
            "omissions": len(self.omissions),
            "contributions": len(self.contributions),
            "contributors": len(self.contributors),
            "ai_elements": sum(1 for c in self.claims if c.status == ElementStatus.AI_GENERATED)
                          + sum(1 for f in self.flows if f.status == ElementStatus.AI_GENERATED),
            "human_elements": sum(1 for c in self.claims if c.status != ElementStatus.AI_GENERATED)
                            + sum(1 for f in self.flows if f.status != ElementStatus.AI_GENERATED),
            "settled_claims": sum(1 for c in self.claims if c.is_settled),
        }

    def to_dict(self) -> dict:
        """Serialize the entire event to a dict (for JSON export)."""
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "neutral_statement": self.neutral_statement,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "ai_cost_usd": self.ai_cost_usd,
            "primary_source": {
                "url": self.primary_source_url,
                "title": self.primary_source_title,
            },
            "claims": [c.to_dict() for c in self.claims],
            "sources": [s.to_dict() for s in self.sources],
            "views": [v.to_dict() for v in self.views],
            "flows": [f.to_dict() for f in self.flows],
            "timeline": self.get_timeline(),
            "mutations": [m.to_dict() for m in self.mutations],
            "omissions": [o.to_dict() for o in self.omissions],
            "gap_analysis": self.gap_analysis,
            "mutation_summary": self.mutation_summary,
            "shared_foundation": {
                "verified_facts": self.shared_facts_verified,
                "unverified_facts": self.shared_facts_unverified,
                "points_of_disagreement": self.points_of_disagreement,
                "summary": self.shared_summary,
            },
            "contributions": [c.to_dict() for c in self.contributions],
            "stats": self.get_stats(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_batch_result(cls, result: dict) -> "TributaryEvent":
        """
        Convert a batch.py result dict into a TributaryEvent.
        This bridges the current pipeline output to the new data model.
        """
        event = cls(
            topic=result.get("topic", ""),
            neutral_statement=result.get("neutral_topic", ""),
            ai_cost_usd=result.get("cost_usd", 0),
            primary_source_url=(result.get("primary_source") or {}).get("url", ""),
            primary_source_title=(result.get("primary_source") or {}).get("title", ""),
            gap_analysis=result.get("gap_analysis", ""),
        )

        ai_attr = Attribution(source="ai", model="claude-sonnet-4-6")

        # Convert trace sources and extract flows
        for src in result.get("trace_sources", []):
            ts = TrackedSource(
                url=src.get("url", ""),
                title=src.get("title", ""),
                source_type=src.get("source_type", ""),
                excerpt=src.get("excerpt", ""),
                published_at=src.get("published_date", ""),
                author=src.get("author", ""),
                tier=src.get("tier", ""),
                attribution=ai_attr,
            )
            event.add_source(ts)

            ref_url = src.get("referenced_url")
            flow_type_str = src.get("flow_type")
            if ref_url and flow_type_str:
                try:
                    ft = FlowType(flow_type_str)
                except ValueError:
                    ft = FlowType.RELATED
                event.add_flow(InformationFlow(
                    from_source=ref_url,
                    to_source=src.get("url", ""),
                    flow_type=ft,
                    evidence=src.get("flow_evidence", ""),
                    timing=src.get("published_date", ""),
                    attribution=ai_attr,
                ))

        # Convert views and extract flows
        for view_data in result.get("views", result.get("lenses", [])):
            view = View(
                name=view_data.get("view_name", view_data.get("lens_name", "")),
                question=view_data.get("question", ""),
                key_claim=view_data.get("key_claim", ""),
                attribution=ai_attr,
            )
            for src in view_data.get("sources", []):
                ts = TrackedSource(
                    url=src.get("url", ""),
                    title=src.get("title", ""),
                    source_type=src.get("source_type", ""),
                    excerpt=src.get("excerpt", src.get("framing_headline", "")),
                    published_at=src.get("published_date", ""),
                    author=src.get("author", ""),
                    tier=src.get("tier", ""),
                    attribution=ai_attr,
                )
                view.sources.append(ts)
                event.add_source(ts)

                # Extract information flows from source relationships
                ref_url = src.get("referenced_url", src.get("cites"))
                flow_type_str = src.get("flow_type")
                if ref_url and flow_type_str:
                    try:
                        ft = FlowType(flow_type_str)
                    except ValueError:
                        ft = FlowType.RELATED
                    event.add_flow(InformationFlow(
                        from_source=ref_url,
                        to_source=src.get("url", ""),
                        flow_type=ft,
                        evidence=src.get("flow_evidence", ""),
                        timing=src.get("published_date", ""),
                        attribution=ai_attr,
                    ))

            event.views.append(view)

        # Convert mutations
        mutations = result.get("mutations") or {}
        if mutations.get("transitions"):
            for t in mutations["transitions"]:
                event.mutations.append(Mutation(
                    from_source=t.get("from_source", ""),
                    to_source=t.get("to_source", ""),
                    preserved=t.get("preserved", ""),
                    dropped=t.get("dropped", ""),
                    added=t.get("added", ""),
                    distorted=t.get("distorted", ""),
                    attribution=ai_attr,
                ))
            event.mutation_summary = mutations.get("overall", {}).get("summary", "")

        # Convert omissions
        for omission_data in result.get("omissions_by_view", result.get("omissions_by_lens", [])):
            for o in omission_data.get("factual_omissions", []):
                event.omissions.append(Omission(
                    view_name=omission_data.get("view_name", omission_data.get("lens_name", "")),
                    what_is_missing=o.get("what_is_missing", ""),
                    found_in_view=o.get("found_in_view", o.get("found_in_lens", "")),
                    impact=o.get("impact", ""),
                    omission_type="factual",
                    attribution=ai_attr,
                ))
            for o in omission_data.get("perspective_omissions", []):
                event.omissions.append(Omission(
                    view_name=omission_data.get("view_name", omission_data.get("lens_name", "")),
                    what_is_missing=o.get("missing_viewpoint", o.get("what_is_missing", "")),
                    found_in_view=o.get("found_in_view", o.get("found_in_lens", "")),
                    impact=o.get("impact", ""),
                    omission_type="perspective",
                    attribution=ai_attr,
                ))
            for o in omission_data.get("context_omissions", []):
                event.omissions.append(Omission(
                    view_name=omission_data.get("view_name", omission_data.get("lens_name", "")),
                    what_is_missing=o.get("missing_context", o.get("what_is_missing", "")),
                    found_in_view=o.get("found_in_view", o.get("found_in_lens", "")),
                    impact=o.get("impact", ""),
                    omission_type="context",
                    attribution=ai_attr,
                ))

        # Convert shared foundation
        shared = result.get("shared_foundation") or {}
        event.shared_facts_verified = shared.get("verified_facts", [])
        event.shared_facts_unverified = shared.get("unverified_facts", [])
        event.points_of_disagreement = shared.get("points_of_disagreement", [])
        event.shared_summary = shared.get("summary", "")

        return event
