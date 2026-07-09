# Methodology

What each Tributary output claims, how it is produced, and — just as important — what it does *not* claim and where it is known to fail. This page exists because a provenance tool must hold itself to the standards it measures (see [CORRECTIONS.md](CORRECTIONS.md) for the error log).

**The one-line honesty statement:** everything Tributary currently publishes is **AI-generated and not yet human-reviewed**. Every element carries a provenance label saying exactly that. Cited URLs are mechanically verified; the *interpretations* (framings, roles, lineage judgments) are a language model's, with the failure modes that implies.

---

## Principles that govern every output

1. **No truth verdicts.** Tributary never labels a claim true, false, or any synonym of those. It produces *structural descriptions a reader can check*: who said it first, who repeated it, who disputed it, who linked primary documents, who never mentioned it. A verdict asks for trust; a structural description invites verification.
2. **Provenance on everything.** Every element records whether it was AI-generated or human-contributed, and its review status. Today the honest answer is: all AI, none reviewed.
3. **Robust signals over interpretive signals.** Counts, presence, position, and structure (what was covered, by whom, how prominently) are more trustworthy than AI-interpreted labels (stance, role, framing). Where interpretive labels appear, this page says so explicitly.
4. **The recognition gate (circle summaries).** Any summary of what an epistemic circle is saying — starting with the Common Ground Reports — must pass this test: *would members of that circle call it a fair statement of their framing?* Fairness is for the summarized circle to judge, not for us to assert. Until that judgment can be collected from real cross-partisan raters (a committed roadmap item; the bar is ≥70% "fair" from members of the summarized circle), a mechanical v0 proxy rule stands in: **a circle summary may contain only quoted language from that circle's own sources.** No paraphrased value-attribution — not "the right is angry about X" but "a/b right-leaning pieces ask: '[their own quoted words]'." What the quote-only rule costs in fluency it pays back in never putting words in anyone's mouth.
   **Rider (2026-07-09): the quote must be in the outlet's own voice.** An outlet *reporting someone's statement* is not the circle speaking — a left-rated outlet relaying Sen. Fetterman's attack, or a right-rated outlet writing up Bill Maher's monologue, satisfies the letter of the quote-only rule while putting a non-member's words in the circle's mouth (both cases occurred in real output and were caught on review). Relayed individual speech never stands as circle voice; where it appears at all it is attributed to its speaker explicitly ("CNN, reporting Fetterman's attack").

---

## Output type 1 — Narrative fingerprint (upstream)

**What it claims:** for a given claim/narrative, a structured profile of its phrasing (L1), underlying idea (L2), rhetorical structure (L3), genealogy (L4 — *two* lineages: where the phrasing came from and where the idea came from, as dated, sourced attestation logs), domain placement (L5), and an evidence landscape (sources that support / dispute / redirect / provide shared context).

**How it's produced:** Claude (Anthropic) with server-side web search performs the searches and drafts each layer; an adversarial "find something earlier" pass challenges the genealogy; a verification pass then mechanically checks every cited URL (does the page exist? does the quoted text appear on it?) with a Wayback Machine fallback. Verification status is shown per-citation in the viewer.

**What it does NOT claim:**
- The first attestation found is the **earliest we found**, not provably the earliest that exists. Earlier instances missed by web search are an expected failure mode.
- The evidence landscape is a **curated sample**, not a literature review.
- `amplifier_role` labels (originator / early-amplifier / mass-amplifier / institutional-adoption / critic / mention) are **interpretive AI labels** describing a source's position in the *spread*, not its endorsement of the claim. A fact-checking organization that drove broad awareness of a claim can legitimately appear as a "mass-amplifier" while disputing it. Audit before building on these labels.
- Social-spread entries (Bluesky) are **observed mentions, unweighted by reach** — presence in the log does not mean influence.

**Known failure modes:** missed earlier attestations; model misattribution of quotes or dates (mitigated but not eliminated by URL+quote verification); interpretive role labels (above); web-search recency bias.

## Output type 2 — Event analysis (downstream)

**What it claims:** for one event, the distinct narrative **framings** forming around it (each defined by the question it asks), the **carriers** of each framing (who creates/amplifies it), each framing's emphases/omissions, and the **shared foundation** — facts all sides accept, claims all sides repeat without verification, and the actual points of disagreement.

**How it's produced:** a web-search sweep maps the framings and carriers; cheaper model calls extract the shared foundation, per-framing omissions, and a gap analysis; carrier URLs get the same mechanical verification.

**What it does NOT claim:**
- The carriers are a **sample of coverage surfaced by web search — not a census**. Absence of an outlet from a framing's carrier list is *not* evidence the outlet ignored the story.
- Framing boundaries are an AI judgment; reasonable people could split or merge them differently.
- Omissions ("this framing leaves out X") are comparative AI observations across the framings found, not exhaustive.

### Enrichment: coverage lean (the AllSides backdrop)

**What it claims:** of the news outlets surfaced as carriers, how many sit in each AllSides bias bucket (left / lean-left / center / lean-right / right).

**Hard limits, by design:**
- The bias ratings are **AllSides'**, not ours (CC BY-NC 4.0, attributed; the bundled snapshot is a dated community mirror — refreshable via `gen_bias_data.py`). Tributary only aggregates them and never invents a rating.
- **News media only.** Officials, NGOs, think tanks, and courts are actors *in* a story, not press coverage *of* it; they are excluded (AllSides rates some of them, and counting them fabricates skew — a bug we caught and fixed; see CORRECTIONS).
- **US-centric.** AllSides rates mostly US outlets, so international stories resolve poorly. When too little coverage is rateable (low resolve-rate or too few rated outlets), the output says *"insufficient rated coverage to judge skew"* rather than guessing. "One-sided" is always hedged as *possible* blindspot, with the resolve-rate shown.
- It describes **the lean of the carriers we sampled** — a sample, not a census (see above).

### Enrichment: coalition structure

**What it claims:** the actor↔framing structure of one event — which actors carry which framings, which actors **champion more than one framing** (bridges), and raw connectivity counts.

**Limits:** actor identity is resolved pragmatically (outlets by domain, people by normalized name) and can over- or under-merge; "champion / mention / oppose" stances are **derived from the interpretive role labels** above and inherit their weaknesses; reference/aggregator hosts (e.g. Wikipedia) are excluded from the bridge signal because being cited everywhere is not cross-cutting behavior. Within a single event, bridging usually indicates thorough coverage rather than anything ideological — corpus-scale claims wait for corpus-scale evidence.

## Output type 3 — Source analysis (whole article/transcript)

**What it claims:** the significant claims a piece of content makes, each classified (fact / study / narrative / opinion / unverifiable), a content-makeup breakdown, and fingerprints for the traceable ones.

**Limits:** claim extraction and classification are AI judgments; the boundary between "narrative" and "opinion" is genuinely fuzzy; the makeup percentages are per-claim counts, not per-word weights.

---

## Error rate

**Not yet measured.** A precision audit (manually verifying a random sample of ~50 attestations from the public corpus) is a committed roadmap item; the result — including the audit itself — will be published here. Until then, treat every figure above as carrying an unquantified error rate; that is precisely why the viewer shows verification badges per-citation instead of asking you to trust the whole.

Errors found in published output are logged in [CORRECTIONS.md](CORRECTIONS.md), with root causes.

---

*Pipeline: Anthropic Claude (Sonnet for search-heavy steps, Haiku for cheap steps) + server-side web search; Message Batches API for corpus builds; mechanical URL/quote verification with Wayback fallback; AllSides ratings snapshot for the coverage backdrop. Costs and cost levers are documented in [README.md](README.md).*
