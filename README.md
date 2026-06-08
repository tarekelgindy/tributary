# Tributary

**See where information on the internet really comes from — and how it spreads.**

The internet has fractured into information ecosystems where people see the same events through completely different lenses, and increasingly can't tell where what they're reading actually originated. Tributary makes information flow transparent. It doesn't tell you what's true; it shows you the machinery behind what you read — where it came from, who amplified it, how it changed along the way, what evidence surrounds it, and what other framings exist — so you can judge for yourself.

Tributary traces information in **two directions**:

- **Upstream** — given a claim or narrative, where did it come from? Who coined the phrasing, who amplified it, how did it mutate, and where does the underlying *idea* trace back to? (a **NarrativeFingerprint**)
- **Downstream** — given an event or statement, what narratives are forming around it, and who creates/amplifies each? What's the shared common ground everyone accepts? (an **EventAnalysis**)

Every element it produces carries **provenance** — whether it's an AI assertion or human-verified — and the schema is built so human contributions can be layered in Wikipedia-style alongside the AI analysis.

---

## Quick start

```bash
# Setup (Python 3.12)
python3.12 -m venv .venv
source .venv/bin/activate           # WSL/Linux;  .venv\Scripts\Activate.ps1 on Windows PS
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."   # console.anthropic.com → API Keys

# Upstream: trace where a narrative comes from (lean by default, ~$0.15–0.25)
python fingerprint.py "the economy is rigged against working people" --save

# Downstream: map the narratives forming around an event (~$0.40–0.70)
python fingerprint.py --event "ICE agent killed Renee Good in Minneapolis" --save

# See it: open fingerprint_viewer.html in a browser, drag a JSON onto it.
# Try demo_event_analysis.json (synthetic, no API needed) to preview the event view.
```

Optional extras:
```bash
pip install yt-dlp openai           # TikTok/Instagram video transcription (+ ffmpeg)
export BLUESKY_HANDLE="you.bsky.social"        # for --social spread analysis
export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
```

Resuming after a break — put the exports in a `.env` (gitignored) and `source .env` each session.

---

## The two directions

### Upstream — `NarrativeFingerprint`

Given a claim, Tributary builds a five-layer fingerprint:

| Layer | What it captures |
|-------|------------------|
| **L1 Lexical** | the canonical phrasing, variants, and diagnostic n-grams (the *words*) |
| **L2 Conceptual** | the vocabulary-independent underlying claim (the *idea*) |
| **L3 Rhetorical** | framing primitives, villain/victim/hero, tone, register |
| **L4 Genealogy** | **two lineages** — *lexical* (where the phrasing came from) and *conceptual* (where the idea came from), each dated, sourced, with amplifier roles, mutation tracking, timeline stats, and an adversarial "try to find something earlier" pass |
| **L5 Taxonomic** | domain + tropes |

Plus an **Evidence Landscape**: a curated map of sources that **support / dispute / redirect** the claim or provide **shared context**, each tagged by venue (peer-reviewed / institutional / news / …), primary-vs-secondary type, strength, and status (e.g. *retracted*). And every cited URL + quote is **verified** against the live page (with a Wayback fallback).

The power is the two-lineage split: for "the economy is rigged," the *phrasing* is ~30 years old (Clinton/Warren/Sanders) while the *idea* traces back through Marx and Adam Smith to Aristotle — Tributary shows both at once.

### Downstream — `EventAnalysis`

Given an event, Tributary maps the **distinct narrative framings** forming around it — each defined by the *question* it asks, not a political side (e.g. for a shooting: Accountability / Use of Force / Human Cost / Political Fallout). For each framing it identifies the **carriers** (who creates/amplifies it, with roles), what it **emphasizes/downplays**, its **tone**, and what it **leaves out**. It also extracts the **shared common ground** — the facts all sides accept — which is often the most clarifying part. Each framing's `key_claim` is itself fingerprintable, so downstream *finds* the narratives and upstream *traces* each.

### Multi-claim — `SourceAnalysis`

Point Tributary at a whole article/transcript (`--url` / `--file`) and it audits the content — classifying every significant claim as fact / study / narrative / opinion / unverifiable, giving a content-makeup breakdown, and fingerprinting the traceable ones.

---

## Usage & cost control

The pipeline is **lean by default** — a bare run does L1/L2/L3/L5 + the lexical lineage + verification (~$0.15–0.25). The deeper, web-search-heavy layers are opt-in.

```bash
# Cheap preview of an article's claims (one Haiku call, ~$0.005)
python fingerprint.py --url https://some-article.com --extract-only

# Lean single-claim trace (default)
python fingerprint.py "a claim" --save

# Add specific deep layers
python fingerprint.py "a claim" --conceptual --evidence --mutations --save
python fingerprint.py "a claim" --full --save          # all three deep layers

# Downstream event analysis (framings only; trace them on demand)
python fingerprint.py --event "an event" --save
python fingerprint.py --event "an event" --trace-framings --save   # also fingerprint each framing

# Cost levers
--max-searches 6     # cap web searches per call (the dominant cost); default 10
--social             # add Bluesky spread (needs creds; gracefully skipped if absent)
--no-verify          # skip URL/quote verification (saves wallclock, no API cost)
--event-model haiku  # blanket model override for the event pipeline
```

| Flag | Effect | Approx. added cost |
|------|--------|--------------------|
| *(default, lean)* | L1/L2/L3/L5 + lexical lineage + verify | ~$0.15–0.25 |
| `--conceptual` | + the conceptual (idea) lineage | +$0.30 |
| `--evidence` | + the evidence landscape | +$0.20 |
| `--mutations` | + per-transition mutation analysis | +$0.10 |
| `--full` | all three of the above | ~$0.75–1.00 total |
| `--event` | downstream framing map | ~$0.40–0.70 |

**Why cost matters and where it's going:** web-search fees (paid per search) dominate, and they're roughly model-independent — so the path to affordability is (1) cheaper generation (per-step model routing, external search, batch), and (2) **fingerprint once, match-and-serve many**: a given narrative is traced once and then served to many views as a near-free lookup. Sharing results costs nothing — the viewer is a static HTML file and fingerprints are just JSON.

---

## Building a corpus

Two helpers turn "what happened" into a browsable corpus of event analyses:

```bash
# 1. DISCOVER (free) — pull notable events for a date from Wikipedia Current Events
python discover.py --date 2026-06-07          # → topics_2026-06-07.txt
#    (review/trim the file — delete any non-event fragments)

# 2. BUILD — analyze each event into the corpus (incremental, resumable, resilient)
python corpus.py topics_2026-06-07.txt --max-searches 6
python corpus.py topics_2026-06-07.txt --batch      # ~50% off via the Batch API (async)
python corpus.py topics.txt --claims --full         # upstream fingerprints instead of events
```

`corpus.py` saves each result as it completes (a crash or credit-out never loses finished work), dedups by input line so re-runs skip what's done, and logs+continues on a single-item failure. `--batch` submits all the framing searches as one async Batch-API job for ~50% off tokens — slower (minutes–hours), so it's for "no rush" corpus building; for fast iteration run live with `--framings-only`. Realistic cost: ~$0.30/event live, ~$0.15–0.20 batched.

A caveat worth knowing: *notable ≠ contested*. Wikipedia's daily events include many neutral/factual items whose framings end up very similar. For a corpus that shows real disagreement, lean toward genuinely contested events (selecting by coverage divergence — e.g. topics with skewed left/right reporting — is a planned discovery enhancement).

---

## The viewer

`fingerprint_viewer.html` is a self-contained, dependency-free page. Open it in any browser and drag a JSON file onto it — it auto-detects whether it's a fingerprint, a source analysis, or an event analysis and renders the appropriate view. Nothing leaves your machine. Provenance badges show whether each element is AI-generated or human-verified; verification badges show which citations check out; the event view shows the common ground, framing cards, and carriers.

---

## Architecture

Three top-level types, unified by one **provenance + contribution substrate**:

```
                       ┌─────────────────────────────┐
                       │   shared substrate (1a/1b)  │
                       │   Provenance on every elem  │
                       │   Contribution / Contributor│
                       └─────────────────────────────┘
                          ▲           ▲           ▲
            ┌─────────────┘     ┌─────┘     └─────────────┐
   NarrativeFingerprint    SourceAnalysis           EventAnalysis
   (trace one narrative)   (an article →            (an event → the
                            its claims)              framings around it)
            │                     │                       │
            └───── fingerprints ◄─┴──── framings link via fingerprint_id
```

| File | Purpose |
|------|---------|
| `fingerprint.py` | The engine — `FingerprintGenerator` (upstream), `EventAnalyzer` (downstream), multi-claim mode, verification, the CLI |
| `models.py` | All data types — the fingerprint layers, `EventAnalysis`/`NarrativeFraming`, `SourceAnalysis`, and the `Provenance`/`Contribution`/`Contributor` substrate |
| `fingerprint_viewer.html` | Self-contained viewer for all three output types |
| `discover.py` | Free topic discovery from Wikipedia Current Events → topics file |
| `corpus.py` | Batch corpus builder (events or `--claims`), with `--batch` Batch-API mode |
| `batch_probe.py` | One-shot check that web_search works in the Batch API |
| `ingestors.py` | Multi-platform content extraction (web, Bluesky, YouTube, X, TikTok) |
| `social_search.py` | Bluesky spread analysis (used by `--social`) |
| `requirements.txt` | Dependencies |
| `legacy/` | The original pipeline (`agent.py`, `demo.py`, `batch.py`, old viewers), archived — see `legacy/README.md`. Its ideas have been absorbed into the unified model. |

Outputs are saved under `fingerprints/`, `analyses/` (source analyses), and `events/` (event analyses) as JSON, with deduplication by lexical signature.

### Provenance & human contributions

Every element carries a `Provenance` (origin = ai/human, review status, model, and dormant `confirmations`/`disputes`/`revisions`). The contribution vocabulary (`Contribution`, `Contributor` with reputation-gated privileges) is defined and ready — so a Wikipedia-style human-contribution layer can be added later by building a service that *populates* these fields, with no schema migration. Today everything reads "AI-generated"; the honest signal that nothing is yet human-verified.

---

## Status

**Working:** both directions end-to-end (upstream fingerprints, downstream event analyses), multi-claim article analysis, the evidence landscape, source verification, the unified provenance/contribution-ready schema, and the viewer.

**In progress / next:** retiring the legacy `agent.py` world; driving generation cost down (per-step model routing / cheaper providers); the embedding-based match-and-serve layer that makes serving near-free at scale; and, further out, the live human-contribution service and inductive "bubbles" clustering across a corpus of events.

Tributary is a research-stage tool today — a CLI plus a static viewer — on the way toward something journalists, and eventually anyone, can use to see where their information comes from.
