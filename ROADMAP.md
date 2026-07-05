# Tributary — Roadmap

**Mission:** make the identity-information landscape visible and measurable — where information comes from, who amplifies it, what each ecosystem covers and omits — so that people, and the journalists and organizers who serve them, can see the structure behind what they read. Tributary does not adjudicate truth. It targets *mutual legibility* (you can see the shape of your information world and others') and *shared structural facts* (who said what first, who covered what, who never mentioned it).

This document is both a public commitment and the working plan. Phases are gated: each gate is evidence that must exist before the next phase starts. Standing principles govern all work in every phase.

---

## Standing principles

**P1 — Process-normative, conclusion-neutral.**
Tributary is openly normative about *epistemic process* — citing primary sources, issuing corrections, originating vs. relaying, attributing, exposing methods — and strictly neutral about *conclusions*.
- Banned vocabulary in product output: "misinformation," "disinformation," "fake news," "AI slop," "true," "false," "debunked" (as a verdict), or any truth/quality verdict.
- Required vocabulary: behavioral-provenance descriptors that a reader can verify — *originates N% / relays N%*, *links primary documents: rarely/often*, *corrections issued: none observed*, *citation pattern: predominantly self-referential*, *provenance: untraceable beyond X*, *machine-generation signals present*.
- A verdict asks for trust; a structural description invites verification. Inviting verification is the only move that works on a reader whose trust we don't have.

**P2 — Tributary practices the epistemics it measures.**
A provenance tool that scores others on corrections and methods must visibly have both.
- `METHODOLOGY.md` — what each output claims, how it's produced, known failure modes and limits.
- `CORRECTIONS.md` — a public, dated log of every substantive error found in published output, and the fix.
- A published, *measured* error rate (see Phase 0/3 audit task). A known error rate is citable; an assumed-perfect one is disqualifying.
- Every element already carries `Provenance`; keep it that way. Provenance for the provenance tool.

**P3 — Neutrality laws for any intervention** (recommendations, onramps, curation):
1. Optimize only for cross-cutting *exposure/diversity* — never toward a "correct" ideology or outlet list.
2. Always multiple options; the user chooses.
3. Always show *why* each option was surfaced.
4. User-initiated, never pushed.
5. Trust signals are structural/behavioral (P1), never verdicts.

**P4 — Local-first for personal data.**
Browser history and watch history are maximally sensitive, and the target users are trust-poor by definition. Personal-mirror processing runs on-device; raw history never leaves the machine; at most, anonymized claim embeddings touch a remote matcher — and in the pilot, not even that (ship the corpus snapshot to the client).

**P5 — Robust signals over interpretive signals.**
Prefer signals built from volume, presence, position, and structure (what was covered, how prominently, by whom) over signals that depend on AI interpretation (stance, role, framing labels). Where interpretive labels are used:
- Try to **disprove** a signal before presenting it as valuable.
- Report "it works" (mechanical) and "it's valuable" (semantic) as separate claims.
- Audit AI-generated labels on real examples before building anything on top of them.

**P6 — Share results, not access.**
Outputs are static JSON + a dependency-free viewer. Browsing is free for everyone, forever. Generation cost is solved by *fingerprint once, match-and-serve many* — not by gating access.

**P7 — Gates are binding.**
The Non-goals list at the bottom stays closed until a gate explicitly reopens an item. Solo-builder hours are the scarcest resource in the project; the gates exist to spend them on evidence, not optimism.

---

## Strategy in brief

**First user:** the media-and-misinformation desk — press critics, disinfo/OSINT reporters, verification teams, J-school researchers. Their job is literally "where did this narrative come from and who is pushing it," and they can only cite a tool whose methodology they can defend. METHODOLOGY.md, the corrections log, and the measured error rate *are the product* for this user.

**The ladder (personal product):** mirror → annotate → recommend → opt-in curation. Never feed override — technically impossible from outside the platforms and ethically fraught even if it weren't. The strongest mirror feature is the **omission report**: *here's what happened this period; here's the slice your diet showed you; here's what your sources never mentioned.* It is purely additive — it disputes nothing the person believes — which makes it the intervention least likely to trip the identity-protective immune system.

**The heartbeat:** research instruments don't get discovered; recurring publications do. A weekly digest ("This Week in Narratives") is simultaneously the distribution engine, the operational forcing function, and the corpus-growth mechanism.

**The flywheel:** journalists read the digest/gallery → cite and embed trace permalinks → readers meet provenance in-context (the answer to the selection-effect problem) → some become mirror pilots → their feeds surface novel narratives → corpus grows → matcher coverage rises → marginal cost falls → more analytical capacity per digest.

**Defensible assets:** the corpus (compounds via matcher coverage) and earned trust (compounds via methodology + corrections). The LLM pipeline itself is *not* defensible — anyone can call a model. When choosing between pipeline cleverness and corpus/trust/distribution, the latter wins.

---

## Phase 0 — Public face (~1 week)

The repo is now public; it is the first impression and the "share results" surface. Make it show the product.

- [x] Rewrite `INTRO.md` for the current pipeline (it still documents `demo.py`/`batch.py`, which now live in `legacy/`). *(2026-06-10)*
- [x] Delete `backup/` (duplicates `legacy/`); `legacy/` is the single archive. *(2026-06-10)*
- [x] Move or delete stale old-format root JSONs (`rigged_full.json`, `iran_war.json`, `border_trace.json`, `perspectives_*.json`, `world_cup_1936.json`, `ev_lifetime_emissions.json`, `results/`, `traces/`) — none deserialize in the current viewer/schema. *(2026-06-10 — deleted, plus `rigged_economy.json` and `fox_trans_kids.json`, verified same old format; all recoverable from git history)*
- [x] Create `examples/` with 4–6 *real, current-format* outputs… *(2026-06-10 — five shipped: rigged-economy + chewing-gum fingerprints, Platner + Gaza + House-war-powers events, each verified to parse and route; typo fixed to "Minnesota Star Tribune". **Remaining:** one `SourceAnalysis` example — none exists locally; needs a fresh API run.)*
- [x] GitHub Pages: host `fingerprint_viewer.html` + a gallery index. Add a `?load=<url>` query param to the viewer so every example has a **stable permalink**. *(2026-06-10 — `?load=` added via shared `routeData()`, unit-tested; `index.html` gallery created; Pages enabled via API on `main` `/`, build status "built". URL reachability blocked by the account-level custom-domain issue — see Gate 0 log.)*
- [x] `METHODOLOGY.md` v1: port the README's honest-limits sections into a standalone page; state what each layer does and does not claim. *(2026-06-10)*
- [x] `CORRECTIONS.md`: create with format (date, output affected, error, fix, root cause). *(2026-06-10 — opens with three pipeline-level errors caught during development, because the pattern is the point)*

**Deliverable:** a link you would willingly send a journalist.
**Gate 0:** a person outside the project can open an example trace from a bare URL with no instructions.

> **Gate 0 log (2026-06-10): PASSED.**
> *Path there:* Pages was enabled and built, but every URL 301-redirected to `tarek-elgindy.com` — a custom domain on the *user* site that had gone NXDOMAIN (GitHub serves all project sites under a user site's custom domain). After Tarek confirmed the domain was dead, the stale `CNAME` file and Pages setting were removed from `tarekelgindy.github.io` (restorable by re-adding the CNAME if the domain ever returns).
> *Evidence:* live, unauthenticated, no instructions: `https://tarekelgindy.github.io/tributary/` → 200 (gallery); the viewer page → 200; the deep-link permalink (`fingerprint_viewer.html?load=examples/event_platner_allegations.json`) serves the viewer with the load bootstrap, and the JSON it fetches → 200 same-origin. The bootstrap itself was unit-tested (happy/404/no-param) and all five examples verified to parse and route.
> *Decision:* gate passed → Phase 1 (matcher + corpus) is unblocked. Residual Phase 0 item carried forward: one `SourceAnalysis` example for the gallery (needs an API run).

---

## Phase 1 — Matcher + corpus (~2–3 weeks, ~$25 API)

The matcher answers "have we already traced this narrative?" for every text entering the system. It is the keystone: it collapses serving cost, gives narrative identity across events, and makes the personal mirror affordable.

**Matcher (`matcher.py`, new module — do not grow `fingerprint.py`):**
- [x] Local embedding model via `sentence-transformers` (pinned: `sentence-transformers/all-MiniLM-L6-v2`; model name stored in the sidecar meta; mismatched sidecars are rejected, never compared). *(2026-06-10)*
- [x] Embed **two texts per fingerprint, separately**: L1 `canonical_phrase`, and L2 (`claim_predicate` + `causal_structure`). *(2026-06-10 — L2 falls back to L1 for lean fingerprints, flagged)*
- [x] Store vectors in a sidecar (`fingerprints/vectors.json`), not inside `index.json`. Backfill the existing corpus. *(2026-06-10 — 16 fingerprints, 12.7s, incremental re-embeds only on text change)*
- [x] Decision grid on (L1-sim, L2-sim) + queues. *(2026-06-10 — plus `attach_variant()` writing new phrasings onto `phrase_variants`, additive. **Validation finding for Gate 1:** the stored L2 text is an abstract restatement whose register sits far from colloquial queries — a fingerprint's own canonical phrase scored L2 0.40 against its own predicate (spread 0.40–0.87). Added a near-exact rule: L1 ≥ 0.95 serves outright. Probe set in `test_claims.txt`: novel claims reject at 0.03–0.09; the related-topic trap (capital-gains vs negative-gearing) correctly not served; paraphrases land in review — conservative under-serving until Gate 1 calibrates per-axis thresholds.)*
- [x] Thresholds: start ~0.85 / ~0.70, calibrate at Gate 1. *(2026-06-10 — defaults in place; `--calibrate <pairs.json>` harness built: band agreement + confident-false-positive count)*
- [x] Replace `FingerprintStore.find_matching` at its call site; keep the old lexical check as a cheap pre-filter. *(2026-06-10 — lexical pre-filter kept; semantic matcher runs **advisory** at the save path, with `--trust-matcher` to opt into serve/variant; flips to default only when Gate 1 logs zero confident false positives. Graceful no-op without the library.)*
- [x] CLI: match one claim → decision + neighbors; `--coverage <file>` → coverage metric; plus `--backfill`, `--queues`, `--enqueue`, `--calibrate`. *(2026-06-10)*

**Corpus:**
- [x] `discover.py` → `--min-contestedness 6` → `corpus.py --batch` to **~100 events**. *(2026-06-10 — done, with two fixes along the way: Haiku triage for discovery (`--clean`/`--clean-file`; bare names/list pages dropped, zero real events lost in the audited 114 drops) and a contestedness-scorer bug found by Tarek's first run — 215 topics in one 4096-token call truncated and silently "unscored" everything to 5.0, dropping the whole run; now chunked, salvage-parsed, and loud-failing before any spend. Build: batch `msgbatch_015144…`, **75/75 succeeded**; Tarek's machine crashed mid-run but all 75 events had already been retrieved and saved — the batch design held. **103 events now published and live.**)*
- [x] Every event lands in the public gallery. *(2026-06-10 — `publish.py` exports events/ → tracked `gallery/` + `index.json`; `index.html` renders the corpus list from it. First batch live: 28 events at the public gallery, verified 200.)*

**Deliverable:** a measured coverage rate; a browsable 100-event public corpus.
**Gate 1 (semantic, per P5):** 30 claim pairs spanning same-narrative / paraphrase / different — human judgment vs. matcher band. Require agreement on clear cases and **zero confident-match false positives** before serve-from-cache becomes default behavior.

> **Gate 1 log (2026-06-10): embedding-only serving FAILED; two-stage retrieve-then-confirm PASSED and is now the default.**
> *Setup:* 30 pairs sampled blind from the live corpus (474 claims; similarity-stratified, sims withheld from the labeler); Tarek labeled 4 same / 11 paraphrase / 15 different, observing that the differences hinge on **blame attribution** and **stated consequences vs. bare events**.
> *Embedding-only result:* 1 confident false positive at sim 0.85 (two "Operation Epic Fury was necessary" claims with different conclusions) — different-narrative pairs reach 0.85 cosine while paraphrases sit 0.77–0.90, so **no threshold separates them**. Tarek's observation is the mechanism: narrative identity lives in blame/consequence, exactly what topical embeddings compress away — and exactly what Tributary's framings are *defined* by. Embedding-only serving is permanently off (available only behind `--trust-matcher`, not recommended).
> *Two-stage result:* a strict Haiku same-claim judge (same subject + same blame attribution + same asserted consequence, ~$0.001/call) validated against the same 30 labels: 4/4 same confirmed, 14/15 different rejected — including the embedding's false positive; its own single error sat below the candidate threshold where serving is impossible. **Composite confident false positives: 0.** Serving now requires both stages to agree (`Matcher.match_confirmed`), wired as the default in the fingerprint save path; live end-to-end checks pass (exact → serve+confirmed; blame-shifted variant → review; novel → generate, no confirm spend).
> *Caveats:* n=30 with only 4 "same" labels — margins are thin; re-run the calibration as the corpus grows (the harness is `gen_gate1_pairs.py` + `matcher.py --calibrate`). Decision: gate satisfied **via the two-stage rule**; Phase 1 complete except the carried-over SourceAnalysis example.

---

## Phase 2 — Agenda layer v1 (~2–3 weeks)

Selection and prominence are where outlet bias lives even when individual stories are fair — and they are countable, structural, robust signals (P5). No LLM interpretation on the hot path.

- [x] `agenda.py`: RSS prominence capture for ~25 outlets (curated across the AllSides spectrum plus several international outlets); snapshot feed contents + item position on a schedule; store raw snapshots. *(2026-06-11 — 31 news + 5 fact-check feeds, all validated by real fetch; US balance 9 LoC / 5 C / 9 RoC, leans resolved at runtime via bias_db, never stored. Five dead/blocked feeds swapped (logged in the roster changelog) — including CNN, whose "top stories" RSS is a **zombie serving April-2023 items**; that find produced the staleness tripwire (capture flags feeds whose newest item is days old; aggregation drops pre-window items) without which the omission report would have claimed "CNN never mentioned X" from a dead feed. Scheduled: Task Scheduler "Tributary agenda capture", every 6h via `agenda_capture.cmd`, verified live.)*
- [x] The same RSS capture doubles as **discovery** (decided 2026-06-10): cluster headlines into events via the matcher and emit topics annotated *carried by N outlets / one-side-only* — observed coverage divergence as the contestedness signal, replacing prediction. Include **fact-checker RSS** (PolitiFact, Snopes, FactCheck.org, AFP…) as a second stream: their selection (never their verdicts — P1) is contested-by-construction and yields *claim-shaped* inputs that grow the fingerprint corpus, which event discovery alone never feeds. *(2026-06-11 — `--stories` writes `agenda/topics_<date>.txt` for corpus.py; greedy-centroid clustering over MiniLM title embeddings at 0.62, purity eyeballed on real clusters (Platner/inflation/SpaceX/Gates clean; the Iran mega-story absorbs satellites — known limit). AFP feed 403s; Full Fact + Lead Stories instead. one-side-only computes over rated US outlets only (≥3 rated, one side ≥2, other 0) after internationals inflated the flag.)*
- [x] Daily event-universe snapshot via `discover.py` (both Current Events and topview sources). *(2026-06-11 — `--universe`, defaults to the last completed UTC day because topview publishes after day end; verified 29 events + 50 topview for 2026-06-10.)*
- [x] Headline → event mapping (the matcher from Phase 1 does the heavy lifting: headline embeddings vs. event key-claims). *(2026-06-11 — three mappings: headline↔headline (stories), story↔universe (cross-check), and story↔fingerprint corpus via `attach_traces()` — embedding-stage leads only, labeled unconfirmed per Gate 1.)*
- [x] Per-outlet **attention distribution** vs. the event universe → outlet *agenda fingerprints*. *(2026-06-11 — share of outlet's in-window items per story + best feed position, both raw counts (P5). **v2 same day, after Tarek caught the artifact:** a 29-item mixed "latest" feed made Fox's top stories look like the NBA Finals; volume now comes from the sitemap census (Fox: 284 articles, politics-first) with feed position kept as the separate prominence signal — and "covered, not feed-featured" attention is its own visible texture. Feed-only outlets are labeled as such.)*
- [x] **Omission report** generator: per outlet/ecosystem, events with zero observed coverage in the period. *(2026-06-11 — three guards, each erring toward NOT claiming: no sample → no claim; <8 in-window items → "insufficient sample" (a 1-day window left WaPo with 2 items); any item within 0.42 of the story centroid → "adjacent coverage", not omission (CS Monitor covered the Iran strikes via a sea-drones angle at 0.424). Every claim ships its nearest-item evidence + feed scope. Thresholds uncalibrated until Gate 2.)*
- [x] Store as JSON; render in the viewer as a new card (additive). *(2026-06-11 — `agenda/reports/<date>_<N>d.json`, `is_agenda_report`; viewer card renders stories/attention/omissions/universe with the method caveat first; node smoke test against the real report, event routing untouched.)*

**Deliverable:** Digest issue #1 produced end-to-end from a real week of captures. *(In progress: capture scheduled 2026-06-11; a real week accrues by ~2026-06-18.)*
**Gate 2 (the most important audit in the project):** for 10 claimed "never mentioned" items, manually search the outlet's own site. Require ≥9/10 to hold. RSS is not full coverage — if the audit fails, add sitemap/news-sitemap capture **before any omission claim is published**. A false "they never covered it" is Tributary's single most damaging possible error. *(Run this against the 7-day report once a week of captures exists. Known feed-scope risk to probe: Fox's and Newsweek's mixed-latest feeds undersample hard news.)*

> **Gate 2 pre-log (2026-06-11): the informal spot check FAILED — the sitemap contingency is triggered.** Tarek checked the first report's "no observed coverage" rows against the outlets' own homepages: Fox News, Newsmax, ABC Australia and others all visibly carried the Iran-strikes story. Two compounding causes: **one feed ≠ the outlet** (Fox's "latest" feed is lifestyle-heavy; every feed is a thin, oddly-scoped slice of an editorial surface), and **related-but-differently-angled headlines fall below even the adjacency guard** (ABC AU's Hormuz-oil angle sat at 0.277 vs. the strikes centroid). Per this gate's own contingency: RSS-based omission claims are dead on arrival; existence claims move to news-sitemap capture (Phase 2.6), and Gate 2 will be run against sitemap-based claims. RSS remains the *prominence* instrument (feed position), which sitemaps cannot provide.

> **Phase 2 side-find (2026-06-11):** the P5 disprove-first audit of the new lean labels caught a live published-output error — bias_db's fuzzy contains pass had equated unrelated outlets by substring (National Post→The Nation, Iran International→The Nation, Kyiv Independent→The Independent…), fabricating 7 published "possible blindspot" skew claims. Fixed, corpus re-backfilled, gallery republished, logged in CORRECTIONS.md.

---

## Phase 2.5 — Presentation pass (before the first digest; from Tarek's 2026-06-10 QA)

The digest links readers to traces; the traces must be readable by someone who didn't build them.

- [x] **Event view: progressive disclosure.** The framing cards carry more than most readers will absorb. Default to a compact card (name, question, key claim, carrier count); expand on click for emphases/omissions/carriers. Lead with an at-a-glance strip: N framings · the common ground in one line · who's contesting it. *(2026-06-12 — glance strip (count · truncated common-ground summary · coalition actor mix, all structural per P5); cards collapsed to name/question/claim/carrier-count, expand-on-click spans the grid for reading width; duplicate event-statement block removed (the header already carries it). Validated on the densest gallery event (Platner, 8 framings / 48 carriers) via stub-DOM checks, 22/22 incl. agenda/matrix/fingerprint route regressions.)*
- [x] **Upstream view: de-clutter.** Trim superfluous fields from the default fingerprint view; foreground the genealogy's *milestone* moments (origin, first amplification, institutional adoption, major mutations) over the full attestation log (keep the log behind a toggle). *(2026-06-12 — Overview tab is now a chronological milestone rail: oldest conceptual ancestor (BCE dates now sort/parse correctly), phrase origin, first amplification, institutional adoption, first documented pushback, plus ≤3 major mutations (distortion outranks addition, interleaved across lineages so the phrase's own drift and the idea's deepest shift both surface). Old Mass-Amplifiers/Institutional/Critics/Stats cards replaced by a one-line stats strip; full attestation log behind a toggle with click-to-expand instance detail. Semantic check on rigged-economy: rail reads Amos −760 → Barlett 1991 → Sanders 2005 → Warren 2012 → Obama 2013 → pushback 2019.)*
- [x] **Amplifier-impact visualization — scoped honestly (P5).** Tarek's instinct: show the *impact* of amplification (e.g. interaction rising after an actor amplifies). A timeline of dated attestations with role-colored milestones and cumulative-spread shading is supportable from current data. A Sankey/flow diagram implying *who caused whose uptake* is NOT — temporal order isn't influence, and the attestation log is a sparse sample; flow visuals would assert causality we can't verify. Park true flow-viz until Phase 4's cross-event actor profiles (and even then, label it observed-sequence, not influence). *(2026-06-12 — `drawSpreadTimeline`: x-axis at date resolution (the old year-only axis stacked a fresh narrative's whole spread on one x; ticks now adapt year→month→day to the span), BCE dates render instead of being silently dropped, milestones draw enlarged + labeled, and a cumulative step-area shades the share of observed attestations over time — counts only, with the caveat inline: "order shows sequence, not influence; a sample, not a census." Headlines the Overview milestone card and replaces both Lineages timelines. No flow-viz, per this item's own scoping.)*

## Phase 2.6 — Omission integrity + the narrative bridge (~1–2 weeks; from Tarek's 2026-06-11 review; runs while captures accrue)

Two findings from the first real agenda reports set this phase. (1) RSS-based omission claims failed the informal Gate 2 spot check — see the Gate 2 pre-log. (2) The agenda layer measures *story selection* but says nothing about *narratives*: framings live in the event analyses, and nothing connects an outlet's headlines to an event's framings. This phase makes the most dangerous claim safe and makes the instrument narrative-centric — measuring divergence against an event's **own framings** rather than a national L/R axis (the AllSides backdrop stays, attributed, but stops being the headline).

**Omission integrity — sitemaps for existence, RSS for prominence:**
- [x] Roster: add a per-outlet `news_sitemap` URL (discover via robots.txt `Sitemap:` entries + common paths; validate every URL with a real fetch, exactly as the RSS roster was). Outlets without a usable news-sitemap are listed as such and get **no omission claims at all** — an honest gap, not a guess. *(2026-06-11 — 21 of 31 validated; the 10 without (WaPo timeout, Politico/WSJ/Federalist 403, Telegraph 402, Daily Wire unparseable, CSM/ABC-AU 404, Reason empty, TOI lifestyle-only) spread evenly across the spectrum, so no side is shielded from or exposed to claims.)*
- [x] `sitemaps.py` (new module — keep `agenda.py` from growing): daily capture of news-sitemap article lists (URL, title, pubdate; news-sitemaps enumerate ~48h of *all* articles — a near-census, which is what an absence claim requires) → `agenda/sitemaps/`. Free, keyless, added to the scheduled wrapper. *(2026-06-11 — first capture 21/21 ok, 6,386 articles ≤72h; handles sitemap indexes, gzip, slug-derived titles.)*
- [x] Rework the omission test in `agenda.py`: **existence = sitemap titles** (embedding adjacency guard runs against them); RSS feeds only existence-check outlets with no sitemap — and those outlets are excluded from publishable claims. Feed position keeps powering prominence/attention as today. *(2026-06-11 — plus the category the spot check implied: sitemap hit ≥0.62 for a story absent from the front feeds = **"covered, not feed-featured"**, a prominence observation, not an omission. Guards: no sitemap → no claim; <30 in-window articles → insufficient; ≥0.42 → adjacent; every claim ships nearest-article evidence + URL.)*
- [x] **Validation before anything else proceeds:** the Iran-strikes spot check must flip — Fox/Newsmax/ABC-AU must show coverage via their sitemaps for the same story the RSS layer claimed they missed. *(2026-06-11 — FLIPPED: Fox missed=0, Iran covered at 0.782; Newsmax 0.818; HuffPost 0.857; France24 0.81; ABC-AU is sitemap-less so claims against it are structurally impossible. Side-find fixed en route: DW's sitemap mixes languages — Arabic/German titles were generating false misses; pinned to dw.com/en/news-sitemap.xml, after which DW's Canada-story "miss" flipped to covered.)*

**Framing-attention bridge — the agenda layer meets the framing layer:**
- [x] Weekly: top 3–5 agenda stories (by carriage + divergence signals) → `corpus.py` event analyses with `--trace-framings` (~$2–4/week). This also fixes the starved fingerprint corpus (16 fingerprints vs. 103 events): the corpus grows **where attention actually is**, and `attach_traces` finally has something to find. *(2026-06-11 — first two ran `--framings-only` (~$1): the Iran second-day strikes + "I love the inflation". **Remaining for the weekly cadence:** add `--trace-framings` so fingerprints actually grow — the fingerprint corpus is still 16.)*
- [x] `framing_attention()`: for a story with an event analysis, align each outlet's captured headlines to the event's framings — embedding prefilter against framing `key_claim`s, then a batched Haiku **framing-alignment judge** (which framing does this headline express, or "none"). This label is interpretive, so the full P5 protocol applies: provenance on every label, disprove-first audit before anything builds on it, "it works" and "it's valuable" reported separately. P1 holds: alignment with a framing, never quality or truth of one. *(2026-06-11 — `framing_attention.py`; every label carries model/prompt-version/confidence/reason; failed batches stay unlabeled, never guessed; output carries audit_status "PENDING Gate 2.6b — not publishable".)*
- [x] Output per event: an **outlet × framing attention matrix** — which framings each outlet's headlines express, with what prominence, and which framings it never touches. No L/R axis anywhere in the claim; international outlets become first-class (Al Jazeera is unplaceable on AllSides but perfectly placeable against an event's framings). *(2026-06-11 — validated live on both events, and the matrices say things the L/R lens can't: Breitbart's only framed Iran coverage is "Diplomatic Failure / Negotiation Sabotage" ×2 at feed position 1; Al Jazeera alone carries "Human Cost" and "Iranian Sovereignty"; the inflation story's "Presidential Gaffe" framing has zero right-of-center carriers — the one-side-only flag reproduced at narrative granularity — while Reason uniquely frames it as working-class squeeze. "It works" shown; "it's valuable" awaits Gate 2.6b.)*
- [x] Viewer: render the matrix on the event card (additive). *(2026-06-11 — standalone `is_framing_attention` card: legend, outlet×framing table with prominence, expandable judge reasons, PENDING badge; 16/16 stub-DOM checks against the real matrix. Embedding it inside the event card joins the Phase 2.5 presentation pass.)*

**Durability + calibration (same window, smaller):**
- [x] Move capture off the single desktop: GitHub Actions cron running `--capture`/`--universe`/sitemap capture (free, keyless), committing snapshots to the repo or a data branch (gzip + a retention policy). One crashed machine must not break the digest cadence; public raw captures also suit P6. Local Task Scheduler becomes the backup. *(2026-06-11 — `.github/workflows/agenda-capture.yml`, every 6h; snapshots commit to the `agenda-data` branch, checked out as `agenda/` in CI. **Verify the first scheduled run lands.** Retention: revisit pruning/gzip near ~1 GB; never rewrite cited history.)*
- [x] Calibrate the clustering thresholds Gate-1-style: ~40 labeled headline pairs (same-story / related / different) sampled from real captures → calibrate story-join 0.62 and adjacency 0.42; log the result here. *(2026-06-12 — **PASSED on Tarek's 40 blind labels: zero confident errors on both thresholds.** Different-story pairs max out at 0.481 (safely below the 0.62 join); only 2 same-story pairs sit below it — under-merging, conservative rather than dangerous. Same-pair sims 0.581–0.969, different-pair 0.029–0.481. The 0.62 / 0.42 defaults stand.)*
- [ ] **Digest editorial line (binding until the gates below pass):** publishable — shared-agenda stories, attention distributions, one-side-only flags *with their evidence shown*; **not publishable** — outlet-level omission claims (until Gate 2 passes on sitemap data), the universe cross-check (experimental), framing-attention matrices (until Gate 2.6b passes).

**Deliverable:** an omission report that survives the spot check that killed the last one, plus one real outlet × framing matrix for a current event.
**Gate 2 (rerun, sitemap-based):** as specified in Phase 2 — 10 claimed omissions, manual site search, ≥9/10 hold.

> **Gate 2 rerun (2026-07-04, sitemap-based): FAILED at 5/10 held — omission claims stay unpublishable.**
> *Setup:* the GitHub Actions capture cron ran unbroken 06-11→07-04 (96 commits, zero gap days); 7-day report generated from the CI `agenda-data` branch (5,040 stories / 8,602 items / 27 captures); `gen_gate2_audit.py` sampled 10 of the report's **185** publishable omission claims (seeded by report_id, ≤2/outlet). Tarek searched each outlet's own site; every falsifying URL was then date-verified by fetch — a step now baked into the audit protocol, because it flipped two verdicts: the HuffPost "fail" was the **2024** conviction story (not the in-window sentencing), and the National Post Afghanistan "fail" was a **2008** article about a different incident. Echo of Gate 2.6b: single-pass human labels drift; mechanical date checks are cheap and decisive.
> *Score:* 5 held · 3 failed (verified in-window, same story) · 2 unclear (out-of-window evidence).
> *Anatomy of the three genuine fails — both are census gaps, not matcher errors:*
> 1. **Format blind spot (×2):** National Post covered the Monaco parcel bomb and Newsweek covered the Paraguay upset **only as `/video/` pages** — which news-sitemaps do not enumerate. Their captured sitemaps contain zero entries for either story.
> 2. **Sitemap ≠ census (×1):** BBC's Tom Kean article (bbc.com, 2026-06-30, a regular article) never appeared in BBC's pinned news-sitemap — 7,567 captured entries and not one Kean/congressman title. A single pinned sitemap under-covers even article content on multi-sitemap sites.
> *Decision:* per the gate, **no outlet-level omission claims are publishable** — Digest #1's editorial line already excluded them, so nothing changes for issue #1. Claims that survive unharmed: **positive** existence ("covered, not feed-featured" — if it's in the sitemap, they covered it) and attention distributions (basis already labeled "sitemap census").
> *Contingency identified (same design DNA as the Gate 1 fix — retrieve-then-confirm):* absence claims become **two-stage**: the census says missing AND a targeted per-claim search of the outlet's site finds nothing in-window → only then publishable. Optionally add video-sitemap capture for the roster. Build the confirm stage, rerun this gate on fresh claims, and only a pass unlocks publication.
> *Process fix:* the audit checklist md is now machine-readable — `--transcribe` reads ticked boxes into the json (the first audit was naturally filled in the md; the instrument should meet the human where they work).
**Gate 2.6b (framing labels, per P5):** ~30 sampled headline→framing alignments vs. Tarek's blind judgment. Require ≥80% agreement and **zero confidently-wrong-framing assignments** before any matrix ships in a digest. Embedding-only alignment is presumed insufficient (Gate 1 showed embeddings compress away exactly what framings are made of); if the judge fails too, the matrix waits.

> **Gate 2.6b round 1 (2026-06-12): FAILED at 57% — but the anatomy says construct ambiguity, not judge error, and in the safe direction.** Of 13 disagreements, **11 were the model UNDER-assigning**: it sent factual strike-report headlines ("US military launches fresh round of Iran strikes") to "none" per the fa-1 prompt's strict perspective rule, while Tarek labeled them into the framing whose *narrative the facts belong to* (mostly Escalation Spiral). Only 1 over-assignment — the model was not fabricating alignment (the failure mode the gate most fears). Tarek's qualitative read matched: headlines felt "related but not super aligned." *Decision:* the label was underspecified, so the construct is now split — **"expresses"** (takes the framing's perspective) vs **"reports"** (neutrally covers that framing's subject matter) vs none — judge prompt fa-2. The distinction is itself product texture: wire-style outlets *report* a framing's subject matter; perspective-forward outlets *express* it (first fa-2 run: Escalation Spiral = 5 expressed / 15 reported across 13 outlets; DW/France24/NPR almost all "reports", Vox e2/r0 at feed pos 1). Re-audit pending on the fa-2 blind file (audit files are now versioned by prompt so labeled rounds are never overwritten). One round of iteration; if fa-2 also fails the gate, the matrix waits.

> **Gate 2.6b round 2 (2026-06-12): FAILED at 30% — and the matrix now WAITS, per this gate's own rule.** The anatomy flipped: round 1 the human was more generous than the model (11 under-assignments); round 2, after a stricter rubric briefing, more strict (19 over-assignments) — and internally inconsistent on near-identical headlines ("Iran attacks Bahrain, Kuwait…" → Escalation/reports, but "Iran says it attacks US bases" → none; "The U.S. Strikes Iran Again" was War Powers in round 1, none in round 2). The model-says-none group agreed 5/6. *The real finding:* across two rounds the judge was the most stable rater in the room — the failure is **gold-standard instability**, not measured judge error; a single-pass single-rater label cannot anchor this construct at headline granularity (15 words is thin evidence of framing, and the gray band is wide). *Decision:* the matrix is out of Digest #1 (the editorial line already says so).
>
> **Revival package (refined 2026-06-12 from Tarek's diagnosis; one coherent unit, in this order):**
> 1. **Contested framings, not comprehensive framings.** Tarek's read of the failed rounds: most headlines are neutral descriptions; many generated framings are analytical lenses nobody *pushes*; and the framing space was derived from the event seed while the headline stream had moved on (temporal drift). The fix is structural, not taste: a framing earns a matrix column only if it **(a) assigns blame or asserts a consequence** (Gate 1's definition of narrative identity — agenda-grade framings have this; "Effectiveness Doubt" doesn't), **(b) has identifiable advocates** among the event's carriers, and **(c) doesn't overlap a kept framing**. Target 3–5 columns. Generate bridge events with the window's *captured headlines as context* so the framing space describes the discourse the matrix will measure.
> 2. **The neutral mass becomes a first-class stat, not forced columns**: "N% of front-feed coverage takes no side" is itself a structural finding; the matrix classifies only the minority that does — a smaller, crisper task for judge and labeler alike.
> 3. **Deliberative anchor session** (~30 min, Tarek + assistant): settle ~12 disputed headlines against the NEW framing set; bake exemplars into both the labeling instructions and the judge prompt.
> 4. **One final blind round on fresh headlines.** Pass → matrix publishes. Fail → headline-level framing classification is parked until article-level input (ledes) is affordable, and METHODOLOGY.md says so.
>
> The matrices remain viewable with their PENDING badge — evidence always shown, counts never presented as validated.

## Phase 2.7 — Reader tiers (2026-07-04; from Tarek's product review)

The viewer had grown schema-shaped: tabs named after pipeline layers (L1–L5), raw IDs and model names in every meta-bar, similarity floats and CLI flags in reader-facing strings. The rule now: **Tier 1** (default-visible) = the finding in plain language; **Tier 2** (one click) = the evidence; **Tier 3** (footer "Details for auditors" / tooltips / methodology) = the instrument. P2 requires provenance *available* on every page, not ambient in every card.

- [x] Tabs renamed to reader words (Phrase / Idea / Rhetoric / Spread / Evidence / Category); phrasing/idea vocabulary through milestones, lineages, and the attestation log. *(2026-07-04)*
- [x] Meta-bars carry only reader-meaningful facts; IDs, schema/model/prompt versions, and thresholds moved to a per-view **about-footer**: a visible "AI-generated X — not yet human-reviewed" line + methodology/corrections/suggest-a-correction links + a collapsed auditor block. *(2026-07-04)*
- [x] Per-element `AI` chips retired (the footer states the default once); provenance badges now light up only for the exception states (human-added / confirmed / disputed / consensus) — the dormant contribution layer's display path, ready for when it populates. *(2026-07-04)*
- [x] Similarity floats, lean index, pipeline status enums → tooltips; CLI-flag fallback strings (`--trace-framings` et al.) removed from UI copy; n-grams/stopword signature behind a "Search internals" toggle; dev-only "Inductive Cluster IDs" card renders only with data. *(2026-07-04)*
- [x] Contribution intake, cheapest honest version: GitHub issue templates (correction / earlier sighting) + "Suggest a correction" links in the viewer footer and gallery. Evidence submission, not judgment submission — labels stay out until the contribution layer earns its gates. *(2026-07-04)*
- [x] Validated: stub-DOM smoke test, 40/40 across all five routeData branches (fingerprint, event, agenda report, matrix, source analysis) on real corpus files. *(2026-07-04)*

**Slice 2 (2026-07-05, from the general-audience redesign dialog — strategy note: design for the general reader first; journalists go deep, but aren't the primary lens).** Principles adopted: *shape first, sentence second, prose third* (a trace's shape is its timeline; an event's is its framing fan); *names beat counts* (WHO is pushing a narrative is the product — Tarek's call); *magnitude is encoded visually only for census-grade numbers* (carrier counts are search-bounded samples → equal-weight framing cards with names, never sized bars; bar-length encoding reserved for sitemap-census data). Design mockups at `mockups/design-mockups.html`.

- [x] Homepage rebuilt: one-sentence value prop → chewing-gum hero trace (non-political on purpose; idea → Hippocratic era, phrasing → a 1993 column) with a WHO line + mini-timeline → Digest #1 front door → three curated traces. The full-corpus list moved to `corpus.html`. *(2026-07-05)*
- [x] Viewer front door: bare `fingerprint_viewer.html` now offers curated traces; the drag/browse affordance demoted to a footnote. *(2026-07-05)*
- [x] Upstream tabs collapsed to **Overview · Spread · Evidence** (per Tarek). Reader-gold from the retired tabs folded into Overview: a **WHO strip** under the hero timeline (origin → amplified by → adopted by → pushback, names computed from the attestation logs) and a **narrative-anatomy strip** (villain/victim/hero, tropes, domain). Phrase/predicate/n-gram internals live on in the JSON only. *(2026-07-05)*
- [x] Event framing cards lead with **carrier names** (first 3 + n) instead of a bare count. *(2026-07-05)*
- [ ] Share layer: publish.py emits per-trace stub pages with OG tags + a rendered story-card image, so links carry their finding into chats/Bluesky unclicked; `?embed=1` compact mode for embedding in articles.
- [ ] Story-card share image: who-first layout (per Tarek — the cast, not the age, is the headline).
- [ ] Image-provenance spike (Tarek green-lit ahead of demand): given an image URL → C2PA content credentials + EXIF + Wayback CDX first-capture → an "earliest observed instance" card. Structural descriptors only, never an "AI or not" verdict (P1 already reserves "machine-generation signals present").
- [ ] **Social relevance confirm stage** (from the 2026-07-05 correction): no social post attaches to a lineage without passing a second-stage relevance check against the canonical phrase (embedding + cheap judge — Gate 1's retrieve-then-confirm DNA); plus a generic-n-gram gate so terms like "seven years" never drive social search alone.
- [ ] **Conceptual-ancestor identity gate**: a deep idea-lineage ancestor earns a milestone only if it passes the narrative-identity bar (same claim, not same topic — Gate 1's blame/consequence rule); loosely-related antiquity (Hippocrates on digestion vs. "gum stays seven years") stays in the log but never headlines. Hero surfaces lead with the phrasing's own story (shipped in the viewer 2026-07-05; the generation-side gate is the open item).
- Parked, per Tarek: browser overlay/extension (until the structure is set and matcher coverage makes it non-embarrassing); corpus browsing stays a plain archive until a paste-a-claim search replaces it.

---

## Phase 2.8 — Trace integrity (from the 2026-07-05 chewing-gum audits; jumps the queue ahead of the share layer)

Tarek's review and the follow-up full audit of the flagship trace found **one disease with many symptoms: interpretive labels rendered with structural confidence** (P5's exact target). The worst: a lineage composed mostly of fact-checks carried *zero* `critic` roles — McGill's debunk displayed as "Institutional adoption"; retrospective web content plotted decades before it was written ("1950" = a 2025 listicle); mutation endpoints keyed by URL, which collapses whenever sources share one (a viewer bug affecting every trace); double-counted documents across lineages; secondary-URL attributions far beyond the fact-checker hosts the "documented by" chip can catch. Also verified clean, for calibration: all six Snopes-bibliography print citations are real; the 1890 PMC scan is real; the evidence landscape is largely sound. The share layer waits — shareable cards built on mislabeled roles would broadcast the disease.

**A — Stop the bleeding (viewer + verification semantics; no API):**
- [x] Mutation rendering: resolve endpoints by attestation identity with URL as fallback; never render a from==to mutation; skip mutation objects with no semantic fields. (Every trace benefits; the data fix is in B.) *(2026-07-05)*
- [x] Verify-badge honesty: "✓ verified" only when the quote was found; URL-only checks render "↻ URL reachable"; the tooltip states exactly what was checked. *(2026-07-05 — heuristic on existing data via verification_notes; 2.8-B adds a real `url-ok` status)*
- [x] Social posts stop rendering amplifier roles (engagement counts only) — a 1-like anecdote is not a "mass-amplifier"; roles return only when they're evidence-based. *(2026-07-05 — role data stays in the JSON)*
- [x] Stats count **distinct documents** (first-party identity = URL; secondhand-cited identity = title, so five print articles cited via one Snopes page stay five documents). *(2026-07-05)*
- [x] `cited_via` honored as a first-class field when present (explicit empty = first-party, suppressing the host heuristic — Snopes' own article no longer reads "documented by Snopes"); milestone-rail caveat strengthened to name role labels as unaudited pending Gate 2.8. *(2026-07-05)*

**B — Schema + generation fixes (all additive, per convention 3; validate each on one cheap real trace before corpus use):**
- [x] `cited_via` is a real attestation field, filled at generation whenever the dated source isn't what the link points to; the viewer prefers it over host heuristics. *(2026-07-05 — validation trace: 13 of 39 entries populated)*
- [x] Date honesty: `date` = the document's date; `date_precision` (day/month/year/circa); `describes_period` for retrospectives; viewer renders precision without fake `-01-01` exactness. *(2026-07-05 — validation: 39/39 precision set; a republished article correctly dated 2025 with "2013 original publication" in describes_period)*
- [x] Role prompt: examining/debunking sources default to `critic`; `originator` requires coining evidence; status `earliest-found` replaces `single-origin` unless coining is evidenced (code-side too). *(2026-07-05 — validation: exactly 1 originator/lineage, and the lexical one is the actual 1940 Ministry propaganda statement, earning single-origin; conceptual honestly earliest-found. **Residual:** a university "Myth or Fact?" explainer still drew institutional-adoption — prompt sharpened with that exact class; the C2 regeneration is the re-test and Gate 2.8 measures what remains)*
- [x] Mutations: attestation-index endpoints, ≥1 semantic field required, same-URL pairs skipped, capped at 10/lineage. *(2026-07-05 — validation: 18/18 index-keyed, 0 degenerate)*
- [x] Conceptual-ancestor identity gate: `claim_relation` field ("asserts"/"related-context") with an explicit identity test in the prompt (a source contradicting the claim's asserted consequence is never its ancestor); related-context stays in the log, excluded from milestones/WHO/hero. *(2026-07-05 — validation: 0 related-context needed; earliest ancestor is Papyrus Ebers asserting the dietary-intake→vision claim, defensible; Gate 2.8's blind audit judges)*
- [x] Social relevance confirm + generic-n-gram gate: generic n-grams excluded from search queries; one Haiku pass marks relevance AND role; unlabeled/irrelevant posts are DROPPED (failure mode = missing post, never junk presented as spread); verifier statuses downgraded to honest `url-ok` for URL-only checks (4 sites). *(2026-07-05 — validation: 478 found → 17 kept, ZERO off-vocabulary posts)*

**C — Flagship repair (documented, then regenerated):**
- [x] Hand-repair the chewing-gum trace per the audit: 19 fact-check-function entries → `critic`; retrospective entries re-dated to publication dates + `describes_period`; the opposite-claim 400 CE entry removed; wrong-URL entry flagged not guessed; mutations pruned to unambiguous (9→3, 22→8); `cited_via` recorded; status → `earliest-found`. Table-driven + asserted script; CORRECTIONS.md entry; homepage hero WHO line re-checked against repaired data; stub-DOM 18/18. *(2026-07-05)*
- [x] After B validates: regenerate the chewing-gum trace with the fixed pipeline (~$1) and diff against the hand-repair — the diff is a free labeler test. *(2026-07-05 — the regenerated trace independently reproduced the audit's key judgments: zero backdated retrospectives (the folk warning became ~1900 circa via Snopes with describes_period), 1848/1869 commercial history as related-context, 16/16 indexed non-degenerate mutations, 12/12 on-topic socials. Role agreement on shared sources 7/13; the 6 differences were mostly defensible gray zone, but the misses clustered again in the examining-titles class ("Medical Mythbuster", "Doctor debunks") → added a deterministic `_role_sanity_pass` to the pipeline (conservative title regex → critic; related-context can never be originator; status/origin anchor on asserting entries only). The regenerated + sanity-passed trace is now the published flagship; the hand-repair lives in git history. Residual role gray zone is what Gate 2.8 measures.)*

> **Design decision (2026-07-05, Tarek): the idea lineage is scoped to LIVING MEMORY.** Deep ancestry is resemblance, not transmission — a Papyrus-Ebers-era text provides no insight into where the current claim came from, implies an unverifiable chain across centuries, and produced every one of the audit's worst errors. The conceptual search now defaults to roughly the last century; a `--deep-history` flag re-enables pre-modern tracing for the rare trace where antiquity is the story (and even then, related-context unless transmission is evidenced). This knowingly reverses the June celebration of the Amos-−760 milestone rail — the audit changed the information. The demotion made the traces *better*: rigged-economy's idea lineage now anchors on Eugene V. Debs (1912) and carrots/eyesight on Mori's 1904 clinical work — origins that actually explain the current waves.

**D — Corpus sweep (mechanical scans first, judgment flagged not silently rewritten):**
- [x] Scans + mechanical fixes across all 35 fingerprints (store, examples, gallery-embedded). **The measured label-error counts (P2's promised error rate, pre-fix):** 23 examiner-titled sources mislabeled as amplifier/adopter; 28 extra `originator` labels beyond each lineage's earliest (13 lineages affected); 93 pre-1900 conceptual entries presented as lineage rather than context; 17 of those were "originators"; 11 degenerate mutations; 25 `single-origin` overclaims. All mechanically corrected in place with per-entry notes; 137 legacy year-only dates rendered as exact days remain flagged (display-safe; regeneration fixes them). Sweep bug worth admitting: the first pass exempted BCE dates (a `^\d` check), which would have left the *most* ancient entries as status anchors — caught because the post-sweep check re-verified anchors. *(2026-07-05)*
- [ ] **Digest editorial line addition (binding):** no upstream trace is *featured* in a digest until it passes a human role/date read-through; the Digest #1 traces get that read-through retroactively.

**Deliverable:** a chewing-gum page a skeptical stranger can walk end-to-end without hitting a mislabeled role, a backdated dot, or an unexplained link — plus measured, published label-error counts for the corpus.
**Gate 2.8 (semantic, per P5):** ~30 blind-sampled attestations from repaired + regenerated traces; Tarek labels role and date-type; require ≥90% agreement, **zero** adoption/critic inversions, **zero** backdated retrospectives. Until it passes, the milestone-rail caveat is strengthened to name role labels as unaudited.

---

## Phase 2.9 — The front door (2026-07-05; Tarek's "presentable" bar, made finite)

A journalist arrives with *their* question, not ours. The presentable bar is four items, deliberately closed-ended:

- [x] **Ask-your-own-question**: a question box on the homepage matches pasted claims/events against the published corpus (lexical scoring over `gallery/search_index.json`, rebuilt by `publish.py`); hits serve instantly (match-and-serve, finally visible); misses fall through to **request-a-trace** (issue template) with a stated ~24h turnaround. Fulfillment is approval-gated automation: a maintainer applies `approved-trace` → GitHub Action generates (lean, search-capped, title/body passed via env only), publishes to `gallery/traces/`, rebuilds the index, posts the permalink, closes the issue. The label is the spend control; every request is Gate-3 signal and grows the corpus where demand actually is. *(2026-07-05 — needs the `ANTHROPIC_API_KEY` repo secret + `trace-request`/`approved-trace` labels created, then one dry-run request end-to-end)*
- [x] **The digest is a page on the site** (`digests/issue-01.html`), not a GitHub blob; homepage links to it. *(2026-07-05 — future issues should generate HTML from the md; hand-converted for #1)*
- [ ] One outsider read-through of a trace page (someone who isn't Tarek, watched, not coached).
- [ ] The turnaround promise proven once end-to-end (dry-run request → approve → permalink lands).

Explicitly NOT on this bar: story cards, share images, matrix revival, role-ontology work — post-contact improvements.

---

## Phase 3 — The heartbeat (ongoing; ~1–2 hrs/week)

- [ ] Publish **"This Week in Narratives"** weekly: 3–5 contested events; framings + common ground; coverage lean; the omission report; one featured upstream fingerprint. Newsletter + Bluesky + the gallery. *(Issue #1 follows the Phase 2.6 editorial line: omission claims and framing matrices appear only after their gates pass; everything else ships with evidence shown.)*
- [ ] Send each issue directly to five named media reporters/desks you'd want as users.
- [ ] Every issue's underlying JSON ships to the public gallery (corpus grows as a byproduct).
- [ ] Maintain `CORRECTIONS.md` religiously; publish the first audited error rate (sample ~50 attestations from the corpus, manually verify, publish precision + the audit itself).
- [ ] Side quest, using the evidence pack this phase creates (open methodology, public instrument, demonstrated cadence, named user segment): grant applications — Knight, Mozilla, Sloan, Reynolds Journalism Institute and similar.

**Gate 3:** by issue 8 — any *organic* engagement (an unsolicited reply, a citation, a subscriber, a journalist-shaped repo star). Engagement → double down on distribution and proceed to Phase 4. Silence → stop building, run five user interviews, revise.

---

## Phase 4 — Cross-event graph (~2–3 weeks; requires Phase 1)

- [ ] Persist `ActorRegistry` across the corpus (SQLite or `registry.json`) — **not Neo4j**.
- [ ] Name↔domain bridge table to merge identities currently split between name-keys (downstream) and domain-keys (upstream); explicit handling for aggregator hosts.
- [ ] Narrative identity across events via the matcher: cluster framings/fingerprints whose embeddings match → durable narrative nodes.
- [ ] Corpus-scale queries: **actor profile** (everything X amplified across events, with stance/tier breakdown over time) and **narrative profile** (who carried it, where, when).
- [ ] First endogenous community-detection pass on the bipartite graph — structural only. Community labels are *descriptive exemplars* ("cluster anchored by A, B, C"), never AI-assigned identity names.

**Deliverable:** `python graph.py --actor <key>` answering "show me everything this actor amplified across 100 events" — plus a new recurring digest section.
**Gate 4:** entity-resolution audit — sample 30 actor nodes; merges/splits correct ≥90%; confirm no identity-name community labels shipped.

---

## Phase 5 — Mirror pilot (~3–4 weeks)

The personal information-diet mirror, v1, built on the cheapest honest ingestion available.

- [ ] `importers/youtube_takeout.py`: parse YouTube Takeout watch history (**metadata-only v1** — titles, channels, timestamps; no transcription). Channels resolve through the actor registry; titles run through the matcher.
- [ ] Diet map report (local HTML, P4-compliant): actor/community composition of the diet; behavioral-provenance texture of its sources; and the flagship — the **personal omission report**: the event universe vs. what this diet surfaced.
- [ ] Run on your own takeout first; then five volunteers with fresh takeouts.

**Deliverable:** five real diet maps.
**Gate 5:** the second-week open — do ≥2 of 5 volunteers voluntarily look at a week-2 report? Yes → plan the browser extension and a second importer. No → interviews before any further personal-product build. This single retention signal is the cheapest possible test of the entire personal-product thesis; it runs **before** any extension, transcription, or second-platform work.

---

## Non-goals (binding until a gate reopens them — P7)

- Model-routing Phases 2/3 (DeepSeek/Gemini provider abstraction, cross-provider search). Cost analysis showed web-search fees dominate; Batch API + caps + the matcher are the levers that matter.
- Neo4j (SQLite/JSON registry is sufficient at this scale).
- TikTok/Instagram ingestion and any transcription-dependent importer (until Gate 5 passes).
- Browser extension (until Gate 5 passes).
- Any feed-curation or feed-override feature (until far up the ladder, and only within P3's laws).
- Truth verdicts of any kind — **permanent**, not deferred (P1).

---

## Working conventions (for AI-assisted implementation)

These encode the project's established workflow; AI assistants (Claude Code) should follow them on every task.

1. **Two commits per feature** where applicable: one for the pipeline (`models.py` + engine), one for the viewer. Detailed commit messages; end with the Co-Authored-By trailer.
2. **Compile-check before committing** (`python -m py_compile <files>`). Runtime checks require the `.venv`; system-Python import failures for `anthropic` are expected, not bugs.
3. **Schema changes are additive.** New fields get defaults; previously saved JSON must always deserialize and render (placeholders are fine).
4. **Validate with a real run** before calling a feature done: run a real claim/event, read the JSON, then commit.
5. **Validate semantically, not just mechanically (P5).** Before presenting any new signal as valuable: try to disprove it; check it corpus-wide, not on one vivid example; audit any AI-generated labels it depends on; report "it works" and "it's valuable" as separate claims with explicit confidence.
6. **API flakiness ≠ code bug.** 429/529 overloads happen; the pipeline retries with backoff. Don't "fix" an overload.
7. **Threading pattern for features:** `models.py` (schema) → engine (generation + CLI flag) → `fingerprint_viewer.html` (render).
8. Housekeeping: add `analyses/` to `.gitignore` (known one-line fix).
9. **Keep this roadmap current**: check off tasks, and log each gate's result inline under its phase (date + evidence + decision).

---

*The plan in one line: the matcher makes it affordable, the corpus makes it defensible, the weekly digest makes it discoverable, and the gates make sure each rung of the ladder holds weight before standing on it.*
