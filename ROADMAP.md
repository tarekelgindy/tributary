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
- [ ] `discover.py` → `--min-contestedness 6` → `corpus.py --batch` to **~100 events**; backfill coverage-lean.
- [ ] Every event lands in the public gallery (Phase 0 made this possible).

**Deliverable:** a measured coverage rate; a browsable 100-event public corpus.
**Gate 1 (semantic, per P5):** 30 claim pairs spanning same-narrative / paraphrase / different — human judgment vs. matcher band. Require agreement on clear cases and **zero confident-match false positives** before serve-from-cache becomes default behavior.

---

## Phase 2 — Agenda layer v1 (~2–3 weeks)

Selection and prominence are where outlet bias lives even when individual stories are fair — and they are countable, structural, robust signals (P5). No LLM interpretation on the hot path.

- [ ] `agenda.py`: RSS prominence capture for ~25 outlets (curated across the AllSides spectrum plus several international outlets); snapshot feed contents + item position on a schedule; store raw snapshots.
- [ ] Daily event-universe snapshot via `discover.py` (both Current Events and topview sources).
- [ ] Headline → event mapping (the matcher from Phase 1 does the heavy lifting: headline embeddings vs. event key-claims).
- [ ] Per-outlet **attention distribution** vs. the event universe → outlet *agenda fingerprints*.
- [ ] **Omission report** generator: per outlet/ecosystem, events with zero observed coverage in the period.
- [ ] Store as JSON; render in the viewer as a new card (additive).

**Deliverable:** Digest issue #1 produced end-to-end from a real week of captures.
**Gate 2 (the most important audit in the project):** for 10 claimed "never mentioned" items, manually search the outlet's own site. Require ≥9/10 to hold. RSS is not full coverage — if the audit fails, add sitemap/news-sitemap capture **before any omission claim is published**. A false "they never covered it" is Tributary's single most damaging possible error.

---

## Phase 3 — The heartbeat (ongoing; ~1–2 hrs/week)

- [ ] Publish **"This Week in Narratives"** weekly: 3–5 contested events; framings + common ground; coverage lean; the omission report; one featured upstream fingerprint. Newsletter + Bluesky + the gallery.
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
