# Tributary Mission Plan — Common Ground & Provenance Loop

> **Status:** Phase 2 — 2a (share artifact) done; next 2b (distribution)
> **Last updated:** 2026-07-10
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
| Beyond-L/R circles (independent / international / endogenous co-carriage clusters) | Code is N-ready by design (CIRCLES table + `basis` field); the blocker is a defensible membership basis — AllSides only rates the US L/R axis — and Gates 1–2 need artifacts on the one attributable basis in hand. Breaking the L/R mould is an explicit goal (Tarek, 2026-07-09), not a nice-to-have | After Gate 2; endogenous circles (outlets clustered by observed co-carriage of framings, `basis` recorded as such) are the preferred mould-breaker over imported ratings |
| Human contribution layer (reader finds what the automated pass missed — e.g. an earlier attestation — and it enters the record) | Explicit goal (Tarek, 2026-07-10; mission has always been human+AI side by side). The groundwork is DORMANT, not absent: models.py carries Provenance review states (ai_generated → human_confirmed / disputed / consensus), Contribution/Contributor types and settlement rules; the viewer's provenance badges light up human states automatically; earlier-attestation + correction issue templates exist as the v0 intake channel. What's missing is the flow that turns a filed issue into a recorded, credited contribution | After Gate 2 (multiplier feedback tells us who would actually contribute); a Phase-3-adjacent build. Until then the issue templates ARE the contribution channel — point people at them |

---

## Phase 0 — Write the spine (target: 1 week)

*Mostly thinking. Deliverables are documents, not code.*

- [x] Rewrite one-page thesis (README top / INTRO) around the loop above. Hero question first.
      Move civilizational framing to an "about/mission" page; the tool pages speak specialist.
      (Civilizational framing lives in ABOUT.md — named to avoid collision with MISSION_PLAN.md.)
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

- [x] Add `Circle` node type to the graph (start with 2: left-leaning, right-leaning;
      design for N — independent/international later).
      (`circles.py`: CIRCLES table; derivation iterates it, N is a new row.)
- [x] Membership edges: `(:Source)-[:MEMBER_OF {confidence, basis}]->(:Circle)`,
      seeded from existing AllSides labels in `bias_db.py`. Crude is fine for v0;
      record `basis: "allsides_snapshot"` so provenance of the labels themselves is kept.
      (MembershipEdge also records the matched rated entity verbatim + match method +
      snapshot date; conservative identity guards — see Decision Log 2026-07-09.)
- [x] Framing rollup: per event, per circle → framing frequency table
      `(circle, event, framing, n_pieces, n_total, representative_quotes[])`.
      Input is existing multi-frame pipeline output; this is aggregation, not new extraction.
      (`framing_rollup()`, backfilled onto 108 events as `circle_rollup`. Rows also carry
      stance counts — needed so a framing one circle only OPPOSES can't read as shared.)
- [x] Every rollup row carries receipts: URLs + archive links for representative quotes.
      (Quotes carry url + archive_url + date; `--archive` adds lookup-only Wayback links,
      written back onto carriers. No-snapshot URLs stay an honest gap.)

### 1b. The three computations (per event, per capture week)

- [x] **Framing intersection:** framings appearing in BOTH circles, with per-circle rates.
      Output: "framing F appears in a/b left pieces and c/d right pieces" + quotes from each.
      (`common_ground.py: framing_intersection()`. Substantiveness filter: a circle must
      ASSERT the framing (champion/mention); oppose-only carriage lands in a separate
      `contested` list, never in the intersection — see Decision Log 2026-07-09.
      Corpus check: 15/108 events show ≥1 intersection; thin circles flagged on the row.)
- [x] **Convergent claims:** same factual claim asserted by sources in both circles
      (reuse narrative-matcher, filtered cross-circle).
      (`convergent_claims()`: claim units are member outlets' verbatim excerpts (receipts
      built in); matcher embeddings propose cross-circle pairs free; pairs stay CANDIDATES
      unless the stage-2 judge confirms (`--confirm`, batched Haiku, ~$0.001/event) —
      the Gate 1 serving discipline, kept. Validation caught embeddings ranking a
      not-same pair (0.81) above a genuinely-same pair (0.76, Greenland "go home").)
- [x] **Shared-but-buried:** stories covered by both circles but feed-featured by neither.
      (Mirror image of the existing one-side-only query — invert it.)
      (`shared_but_buried()`: coverage = feed carriage OR sitemap match (omission-report
      standard); featuring = feed position ≤ 5; ≥2 rated US outlets per side. Feed-only
      coverage found 0 buried in the validation week — the coverage-evidence choice is
      load-bearing, see Decision Log. 2026-07-09 week: 49 buried of 115 both-circle
      stories; non-circle outlets that DID feature are listed per row.)
- [x] **Claim-age overlay:** attach trace-engine first-attestation dates to intersecting
      framings, so organic long-lived framings are distinguishable from recently seeded ones.
      (`claim_age_overlay()`: basis explicit per attachment — `linked` (framing's own
      fingerprint_id) or `embedding_lead` (≥0.60, never presented as confirmed). Reports
      dates/status/confidence + age-at-event only; the reader judges organic vs. seeded.
      Validation: birthright event attached 5/5 linked; lead path re-found the same
      fingerprints 3/5, 2 honestly unattached. Stale vectors.json refreshed via
      `matcher.py --backfill` (was 15/25 fingerprints).
      Backfilled onto all 108 events as `common_ground` (confirm off — free path).)

### 1c. The artifact

- [x] Draft **Common Ground Report #1** (this is Digest Issue 2 with a new job):
      1. The week's biggest framing intersection (with quotes from both circles)
      2. Convergent claims table
      3. Shared-but-buried stories
      4. One claim-age finding (organic vs. recently-seeded framing, if present)
      5. "What we can't tell you" section (keep this tradition)
      (Draft at `digests/issue-02.md` (2026-07-09), buried-report data shipped alongside.
      Lead exhibits: Odeh decapitation framing + Al Jazeera↔Fox / NBC↔Fox confirmed
      convergences; Le Pen / Farage / Hormuz / Memphis as buried stories. Claim-age
      section honestly empty (no intersecting framing has a trace yet) with the
      birthright 2010 finding shown as capability demo. AWAITING TAREK'S EDITORIAL
      REVIEW — publish is deliberately still unchecked.)
- [x] Apply v0 recognition rule: every circle summary built ONLY from that circle's quotes,
      **in the outlet's own voice** — relayed individual speech (Fetterman-via-CNN,
      Maher-via-Fox, both found in real 1b output) never stands as circle voice; if used
      at all it is explicitly attributed to its speaker. (Rider added to METHODOLOGY
      Principle 4 after Tarek's 2026-07-09 review — see Decision Log. Applied in the
      draft: WSJ-editorial-via-Daily-Beast receipt excluded from circle voice; the
      Fetterman/Maher catch disclosed in the issue's own "what we can't tell you".)
- [x] Publish it.
      (2026-07-10: `digests/issue-02.md` + HTML twin live; gallery resynced with the
      three embedded origin traces, common_ground blocks, and 766 archive links; front
      door leads with the claim-age finding. Insight-first v2 shape after Tarek's
      review: claim-age contrast (24yr / 17yr / 20mo) leads; detail-is-the-product —
      every claim routes to a trace/event page.)

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

- [x] Card design: **one number leads — claim age.** "Presented as breaking; first attested
      2010." Everything else is tap-through.
      (Instantiated from the approved mockup (design-mockups Mockup 1): headline is the
      age of the earliest attestation — "This narrative is 23 years old." — phrase
      beneath, all detail tap-through. Ages FLOOR (23 at 23.86, never 24) so the number
      can only understate; the plan's sample copy "presented as breaking" was dropped as
      a characterization of intent — the card shows first-attested vs. in-the-news dates
      and lets the contrast speak. The hedge ("earliest found, not provably first ·
      AI-traced, not human-reviewed") ships on the image itself, not just the page.)
- [x] Mini flow visual (origin → circles → today) as the tap-through / screenshot artifact.
      (Built as origin → circle chips → in-the-news, then superseded the same day on
      Tarek's review: the card visual is now the SPREAD-OVER-TIME timeline — the
      approved mockup's original — cumulative step-area, one dot per recorded use,
      origin and in-the-news labeled, dashed event marker. The origin→circles→today
      facts survive as the chart's end labels plus one line of page text; L/R circle
      chips are gone from the visuals — carriers are named without lean labels
      (de-emphasizing the L/R axis is standing direction). Names stay first-party-domain
      gated: the WSJ-editorial-via-Daily-Beast quote drops out, CBS News stands.)
- [x] Auto-generated plain-language summary sentence at the top of EVERY full trace page
      (structural facts only; this is legibility, not verdict).
      (plainSummaryHtml() in the viewer: earliest dated use + who (+ documented-by from
      the URL host), total recorded uses, most recent use, idea-predates-phrasing note
      when true, earliest-found hedge. Deterministic template — no model call; event/
      article/agenda views hide it. Validated over four trace shapes incl. circa dates
      and authorless articles, which are named by outlet domain, not their own headline.)
- [x] Static share-card renderer (OG images) so cards unfurl properly when linked.
      (cards.py: Pillow-rendered 1200×630 PNG + share page carrying og:/twitter: tags,
      at gallery/cards/<id>.{png,html}; the standalone trace JSON publishes to
      gallery/traces/ so the tap-through lands on the full trace — verified rendering
      over HTTP; search index rebuilt. Default card set = traces a published event links
      with claim_age basis "linked"; an embedding_lead attachment never headlines a card
      (1b candidate discipline). Three cards staged: Odeh decapitation (23y), Arctic
      imperative (17y), Trump-crypto foreign money (19mo).)

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
| 2026-07-09 | 0 | PASS | Thesis rewritten around the loop (README top, INTRO, ABOUT.md); Park List committed; INCIDENT_PROTOCOL.md written; recognition gate defined in METHODOLOGY.md — commits 2db9034, 191ca9b, 7c39888 | Internal gate, trivial by design; the point was forcing the decisions into writing |
| 2026-07-10 | 1 | PASS | Report #1 published with: 19 framing intersections across 15/108 events (3 traced to origins — 2002 / 2009 / 2024-11-25 — with receipts); 3 judge-confirmed cross-circle convergent claims (5 candidates rejected, incl. the highest-similarity one); 49 shared-but-buried stories of 115 both-circle stories in the capture week (receipts vetted; adjacent-coverage row excluded) | Verdict: Tarek, 2026-07-10, after a hostile-reader receipt check — both exhibit articles verified with quoted language; the first-party 2003 trace receipt verified; Le Pen coverage receipts verified (row survives without its one paywalled receipt). His check raised the right question (NBC carries the lead framing as reportage, not championing): under the stricter champion-on-every-side rule 14/19 intersections still stand, so the bar holds under either reading. Caveats: every intersection thin (1–2 outlets/side); stance labels are unaudited AI labels. This was THE LAST INTERNAL GATE — every gate from here is about other people's behavior |
| | 2 | | | |
| | 3 | | | |
| | 4 | | | |

## Decision Log

| Date | Decision | Alternatives considered | Reasoning |
|---|---|---|---|
| 2026-07-09 | Adopted loop thesis (provenance + framing overlap), parked omission census & destination-site framing | Upstream-only; downstream-only; status quo | Each half fixes the other's failure mode: provenance prevents false equivalence in overlap claims; overlap prevents tribal weaponization of traces. Evidence base: perception-gap correction is the depolarization intervention with support; pure exposure/mirror interventions are not. |
| 2026-07-09 | Circle membership (1a) is deliberately conservative: foreign-ccTLD domains join a circle via the curated alias file only; name-based matches must be consistent with the domain's own brand label; membership counts dedupe by rated outlet, not actor key | Accept bias_db's fuzzy lookup as-is (coverage_lean's bar) | Corpus audit found nation.com.pk (The Nation, Pakistan) landing in the left circle via brand-label collision, and wire-copy sites (wfmz.com) inheriting AP's identity by display name. A MEMBER_OF edge is a graph fact hostile readers will check — better to miss a membership (honest unrated gap) than to invent one. Same audit fixed 8 drifted alias values that had been silently falling through to fuzzy matching. |
| 2026-07-09 | 1b intersection requires ASSERTION, not carriage: a framing a circle only opposes goes to a separate `contested` list, never the intersection | Count any both-circle carriage (the raw "both" count from circles.py --sweep) | Stance data exists for exactly this. An intersection built on oppose-only carriage would present contestation as agreement — the harmony-propaganda failure mode the honesty rule exists to prevent. Live case: Platner's "Media Bias & Journalistic Malpractice" framing (right champions, left only opposes) correctly lands in contested. |
| 2026-07-09 | Convergent claims keep the two-stage serving discipline: embedding pairs are only CANDIDATES; the label "convergent" requires the stage-2 judge (`--confirm`, batched reuse of matcher's validated Haiku prompt, ~$0.001/event, opt-in) | Label by embedding threshold alone (free, no key needed) | Gate 1 measured embeddings missing blame/consequence distinctions, and 1b validation reproduced it: a not-same pair (snub vs. consulate-opening, 0.81) outranked a genuinely-same pair (Greenlanders' "go home", 0.76). Opt-in because default runs and backfills must stay free (cost constraint) and no key is assumed in the environment. |
| 2026-07-09 | Shared-but-buried coverage evidence = feed carriage OR news-sitemap match at the existing COVERED_THRESHOLD; featuring stays feed-only (position ≤ 5); presence bar ≥2 rated US outlets per side (one_side_only's own bar) | Feed-only coverage — the strict mirror of one_side_only | Feed-only found 0 buried of 21 both-circle stories in the validation week: anything feed-carried by 2+ outlets per side touches some top-5 within a week, so the strict mirror measures nothing. "Covered" should mean "wrote about it" — the sitemap census already provides that under the omission report's standard (49/115 buried once used). Honesty note: the risk direction flips here (a borderline sitemap match could overstate "both circles covered it"), so every sitemap receipt ships the matched article title/URL/similarity and the caveat names the failure mode; Gate 2's audit calibrates the threshold. |
| 2026-07-09 | Recognition rule gains a rider: circle quotes must be in the outlet's OWN VOICE — relayed individual speech never stands as circle voice (METHODOLOGY Principle 4 updated) | Keep the letter-only quote rule | Tarek's review of the Platner intersection caught both representative quotes being relayed speech from individuals whose politics don't match the circle shown (Fetterman's takedown via CNN as the "left" voice; Bill Maher's line via a foxnews.com write-up as the "right" voice). The quote-only rule was satisfied in letter, failed in spirit. Root causes are systematic: the role→stance ladder counts relaying as championing (the expresses/reports construct that failed Gate 2.6b), and quote selection prefers champions. v0 fix is editorial (1c checkbox); a mechanical relay detector (news-typed carrier whose display names a person/show while the URL is an outlet domain) is future instrument work; Phase 4's recognition test is the gate for this class. |
| 2026-07-09 | Beyond-L/R circle expansion acknowledged as an explicit goal and PARKED (new Park List row) rather than worked now | Start an independent/international/endogenous circle immediately | Tarek wants out of the L/R mould, but derailing Phase 1 for it would trade a committed gate for an uncommitted basis: AllSides is the only attributable membership source in hand, and endogenous circles (the preferred path — co-carriage clustering, mission-true, coalitions.py groundwork) need corpus scale and their own validation to survive hostile reading. The 1a schema means expansion is a new CIRCLES row + a new `basis`, not a refactor, so parking costs nothing structurally. |
| 2026-07-10 | Share-card presentation honesty (2a): ages floor ("is 23 years old" at 23.86); circle chips require the quote's URL to sit under the named outlet's own domain; default card set is linked-basis claim-age traces only; "presented as breaking" copy dropped for date-vs-date contrast | Round ages the way issue-02's prose does ("24 years"); chips straight from intersection quotes; cards for every traced fingerprint; keep the plan's sample copy | A floored age is true under hostile reading ("is 23 years old" holds at 23.86; "24" doesn't) — issue-02's "across 24 years" describes an attestation *span*, so the two coexist, but cards are the artifact strangers screenshot and check. A chip naming an outlet whose quote lives on another outlet's domain would repeat the Fetterman/Maher relay trap in miniature (live case: WSJ-editorial-via-Daily-Beast excluded, CBS News stands). embedding_lead attachments are candidates by 1b definition — a candidate can't headline a share artifact. "Presented as breaking" asserts intent we didn't measure; showing first-attested next to in-the-news is the same punch, structurally. |

## Feedback Log

*(Verbatim replies from multipliers, labeler subscribers, hostile readers. Date, who, quote, action taken.)*
