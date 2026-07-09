# Tributary Mission Plan — Common Ground & Provenance Loop

> **Status:** Phase 0 — in progress
> **Last updated:** 2026-07-09
> **How to use this file:** This is the working plan of record. Step through phases in order.
> Every task is a checkbox. Every phase ends in a **gate** — do not proceed past a failed gate
> without logging a decision in the Decision Log. Gates after Phase 1 must be facts about
> *other people's behavior*, not about instrument quality.

---

## The Spine (thesis of record)

**One loop, two halves:**

1. **Provenance (upstream):** trace where narratives come from — first attestation, mutations,
   amplification path. This is the verifiable, defensible layer. It protects the common-ground
   layer from false equivalence (organic concern vs. seeded talking point are structurally
   distinguishable).
2. **Framing overlap (downstream):** show what epistemic circles actually foreground, in their
   own quoted words, and where their framings intersect — the agreement that engagement-driven
   feeds bury. This layer gives provenance data its human meaning and protects it from being
   used as tribal ammunition.

**Hero question:** *"Where did this come from — and what do the bubbles agree on that your
feed never showed you?"*

**Theory of change:** Not mass persuasion. Make provenance and overlap so cheap that
multipliers (journalists, educators, bridging orgs, researchers) can afford to do at scale
what currently takes hours per claim. Shared *methods*, not shared arbiters.

**North-star metric:** cross-line travel — an artifact shared by accounts from more than one
cluster. Not traffic.

**Honesty rule (non-negotiable):** the tool is allowed to report *low* overlap. A tool that
always finds heartwarming common ground is harmony propaganda. Conclusion-neutral cuts both ways.

---

## Park List (explicitly deferred — stop paying attention tax)

| Parked item | Why | Revisit when |
|---|---|---|
| Omission census (RSS/sitemap "what your diet hid") | Failed gate twice; consumer version blocked on diet problem anyway | A diet source exists (extension / follow-graph opt-in) AND census bugs fixed |
| Destination-site-as-product | Nobody browses a corpus; feed-native delivery is the surface | Phase 3 labeler proves feed demand |
| Everything-digest (weekly panorama) | Digest survives but changes job: vehicle for ONE artifact (Common Ground Report) | Post-Phase 3, if capacity allows |
| X/Twitter integration | No third-party context mechanism; API hostile | Treat as screenshot marketing channel only |
| Browser extension | High build cost, brutal distribution | Phase 3 gate passed + demand for personalization |

---

## Phase 0 — Write the spine (target: 1 week)

*Mostly thinking. Deliverables are documents, not code.*

- [ ] Rewrite one-page thesis (README top / INTRO) around the loop above. Hero question first.
      Move civilizational framing to an "about/mission" page; the tool pages speak specialist.
- [x] Commit the Park List (above) into the repo so it stops being ambient guilt.
      (Lives in this file, which is committed — standalone file judged unnecessary.)
- [x] Write `INCIDENT_PROTOCOL.md`: what we do, publicly, the first time a community decides
      Tributary is enemy infrastructure. Must cover: (a) corrections posture and turnaround,
      (b) tone rules for responding to hostile viral criticism, (c) what we never do
      (litigate motives, issue truth verdicts in self-defense), (d) who/what we point to
      (methodology, error rate, corrections log). Write it now, while calm.
- [x] Define the recognition-gate concept in `METHODOLOGY.md`: circle summaries must pass
      "would members of that circle call this a fair statement of their framing?"
      v0 proxy rule: **circle summaries may contain only quoted language from that circle's
      own sources.** No paraphrased value-attribution.

**Gate 0 (internal):** thesis doc + park list + incident protocol committed. Trivial to pass;
the point is forcing the decisions into writing.

---

## Phase 1 — The existence proof (target: 2–4 weeks)

*Tests the mission's central empirical claim against data we already have: engagement-driven
curation systematically buries agreement between circles. Mostly query-writing, not building.*

### 1a. Circle as a first-class entity (schema work)

- [ ] Add `Circle` node type to the graph (start with 2: left-leaning, right-leaning;
      design for N — independent/international later).
- [ ] Membership edges: `(:Source)-[:MEMBER_OF {confidence, basis}]->(:Circle)`,
      seeded from existing AllSides labels in `bias_db.py`. Crude is fine for v0;
      record `basis: "allsides_snapshot"` so provenance of the labels themselves is kept.
- [ ] Framing rollup: per event, per circle → framing frequency table
      `(circle, event, framing, n_pieces, n_total, representative_quotes[])`.
      Input is existing multi-frame pipeline output; this is aggregation, not new extraction.
- [ ] Every rollup row carries receipts: URLs + archive links for representative quotes.

### 1b. The three computations (per event, per capture week)

- [ ] **Framing intersection:** framings appearing in BOTH circles, with per-circle rates.
      Output: "framing F appears in a/b left pieces and c/d right pieces" + quotes from each.
- [ ] **Convergent claims:** same factual claim asserted by sources in both circles
      (reuse narrative-matcher, filtered cross-circle).
- [ ] **Shared-but-buried:** stories covered by both circles but feed-featured by neither.
      (Mirror image of the existing one-side-only query — invert it.)
- [ ] **Claim-age overlay:** attach trace-engine first-attestation dates to intersecting
      framings, so organic long-lived framings are distinguishable from recently seeded ones.

### 1c. The artifact

- [ ] Draft **Common Ground Report #1** (this is Digest Issue 2 with a new job):
      1. The week's biggest framing intersection (with quotes from both circles)
      2. Convergent claims table
      3. Shared-but-buried stories
      4. One claim-age finding (organic vs. recently-seeded framing, if present)
      5. "What we can't tell you" section (keep this tradition)
- [ ] Apply v0 recognition rule: every circle summary built ONLY from that circle's quotes.
- [ ] Publish it.

**Gate 1 (internal — THE LAST INTERNAL GATE):** Does measurable, non-trivial overlap exist
in a typical capture week?
- **PASS:** ≥1 substantive framing intersection and ≥1 shared-but-buried story per week,
  survivable by a hostile reader (receipts check out).
- **FAIL:** overlap is thin/trivial. → This is a *finding*. Publish it honestly, then run
  a redirect decision in the Decision Log before building anything else. Do not proceed to
  a labeler built on sand.

---

## Phase 2 — The card + first outward gate (target: 2–4 weeks, may overlap Phase 1 tail)

### 2a. The share artifact

- [ ] Card design: **one number leads — claim age.** "Presented as breaking; first attested
      2010." Everything else is tap-through.
- [ ] Mini flow visual (origin → circles → today) as the tap-through / screenshot artifact.
- [ ] Auto-generated plain-language summary sentence at the top of EVERY full trace page
      (structural facts only; this is legibility, not verdict).
- [ ] Static share-card renderer (OG images) so cards unfurl properly when linked.

### 2b. Distribution (native, not broadcast)

- [ ] Post 2–3 cards/week natively on Bluesky. Screenshots elsewhere as marketing.
- [ ] Send Common Ground Report #1 personally to a shortlist of ~8 multipliers.
      Composition: 2 media/disinfo reporters, 2 bridging orgs (e.g., Braver Angels,
      More in Common contact), 2 depolarization/comm researchers, 2 wildcard.
      **The ask is not "use my tool." The ask is: "Is this fair? Is this useful to you?"**
      (Recognition test + distribution in one motion.)
- [ ] Log every reply verbatim in `FEEDBACK_LOG.md`.

**Gate 2 (outward — 8 weeks from first card):**
- **PASS (either):** (a) any single artifact shared by accounts from more than one cluster,
  OR (b) any unsolicited substantive reply/follow-up from the multiplier list.
- **FAIL:** silence after a real distribution effort. → Do NOT build the labeler.
  Run 5 multiplier interviews first; log findings; redirect.

---

## Phase 3 — Bluesky labeler pilot (target: 4–8 weeks; ONLY after Gate 2 passes)

*The highest-cost, highest-payoff build. It waits behind evidence on purpose.*

- [ ] Stand up AT Protocol labeler service (stackable moderation / Ozone or minimal custom).
- [ ] Weekly pipeline: captures surface top-spreading narratives → pre-trace the head of the
      distribution → phrase-match posts against traced narratives (reuse retrieve-then-confirm
      matcher) → emit label: claim age + trace link.
- [ ] **Silence on the tail.** Sparse coverage is acceptable (Community Notes precedent).
      No label is better than a shaky label — carry the omission-guard philosophy over.
- [ ] Label copy review: structural language only; run banned-vocabulary check on label text.
- [ ] Publish labeler methodology page + corrections path for label disputes
      (link INCIDENT_PROTOCOL).
- [ ] Optional if capacity: custom feed ("traced narratives this week") as second surface.

**Gate 3 (outward):** opt-in subscribers I don't personally know, retained past week 2.
- **PASS:** nonzero and retained. Scale slowly.
- **FAIL:** investigate before building more — subscriber interviews, label copy tests.

---

## Phase 4 — Validation & legitimacy (parallel slow-burn; start during Phase 2/3)

- [ ] **Measured error rate (overdue debt):** manual audit of ~50 attestations; publish
      precision number + anatomy of failures in METHODOLOGY.md. This is the single most
      citable asset for every audience. (~a few hours of labeling.)
- [ ] **Real recognition test:** circle summaries rated by cross-partisan readers
      ("is this a fair statement of what your side is saying?"). Borrow raters through
      whichever bridging org replied in Phase 2. Target: ≥70% "fair" from members of the
      summarized circle before overlap claims are marketed as validated.
- [ ] **Grant applications:** New Pluralists, Hewlett (US democracy program), Knight, RJI,
      Mozilla. By now the evidence pack exists: published report(s), travel data, live
      labeler, error rate. Grants > product path (AllSides CC BY-NC constraint).
- [ ] Revisit AllSides licensing / alternative circle-membership bases if any commercial
      path is ever wanted.

**Gate 4 (outward):** one external institution engages concretely — grant interview,
researcher data request, bridging-org pilot, or educator classroom use.

---

## Standing Disciplines

1. **Gates outward:** after Gate 1, every gate is about other people's behavior.
   Instrument-quality gates are retired — they've been passed enough.
2. **Honesty rule:** low overlap gets reported. Failed gates get logged publicly
   (continue the ROADMAP failure-log tradition in this file's Gate Log).
3. **Capacity rule:** one person with a job. If cadence vs. gates ever conflict,
   **protect the gates, drop the cadence.** Biweekly-that-ships beats weekly-that-breaks.
4. **No new internal phases.** If tempted to insert a Phase 2.5-style audit, it goes in
   the Decision Log with a justification for why it beats the next outward gate.
5. **Localized honesty over blanket disclaimers:** move toward per-claim confidence
   (n corroborating sources / archived / verified) instead of page-level "AI-generated" shrugs.

---

## Gate Log

| Date | Gate | Result | Evidence | Notes |
|---|---|---|---|---|
| | 0 | | | |
| | 1 | | | |
| | 2 | | | |
| | 3 | | | |
| | 4 | | | |

## Decision Log

| Date | Decision | Alternatives considered | Reasoning |
|---|---|---|---|
| 2026-07-09 | Adopted loop thesis (provenance + framing overlap), parked omission census & destination-site framing | Upstream-only; downstream-only; status quo | Each half fixes the other's failure mode: provenance prevents false equivalence in overlap claims; overlap prevents tribal weaponization of traces. Evidence base: perception-gap correction is the depolarization intervention with support; pure exposure/mirror interventions are not. |

## Feedback Log

*(Verbatim replies from multipliers, labeler subscribers, hostile readers. Date, who, quote, action taken.)*
