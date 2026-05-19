# Tributary Source Tracer

**Follow your feed to its source.**

Tributary traces where information comes from. Given any piece of content — a news article, a Bluesky post, a YouTube video — it identifies every factual claim and narrative framing, traces each one to its origin, finds competing perspectives on the same data, tracks how claims mutate as they spread, and reveals what each perspective leaves out.

---

## Setup

### 1. Create virtual environment

```bash
cd /mnt/c/Users/Tarek/Documents/tributary_tracer

# Create venv (Python 3.12)
python3.12 -m venv .venv

# Activate it
source .venv/bin/activate          # Linux / WSL
# .venv\Scripts\activate           # Windows CMD
# .venv\Scripts\Activate.ps1       # Windows PowerShell
```

### 2. Install dependencies

```bash
pip install anthropic httpx youtube-transcript-api atproto
```

Optional (for TikTok/Instagram video transcription):
```bash
pip install yt-dlp openai
# Also requires ffmpeg installed on your system
```

### 3. Set API keys

```bash
# Required: Anthropic API key
# Get one at: https://console.anthropic.com → API Keys → Create Key
# Then add credits at: console.anthropic.com/settings/billing
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: Bluesky auth (for --social flag and social search)
# Get app password: Bluesky → Settings → App Passwords → Create
export BLUESKY_HANDLE="yourhandle.bsky.social"
export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
```

### 4. Verify it works

```bash
python demo.py --extract-only
```

### Resuming after a break

Create a `.env` file (don't commit to git):

```bash
cat > .env << 'EOF'
export ANTHROPIC_API_KEY="sk-ant-..."
export BLUESKY_HANDLE="yourhandle.bsky.social"
export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
EOF

echo ".env" >> .gitignore
```

Then each session:

```bash
cd /mnt/c/Users/Tarek/Documents/tributary_tracer
source .venv/bin/activate
source .env
```

---

## Files

| File | Purpose |
|------|---------|
| `demo.py` | Main CLI — run all features from here |
| `agent.py` | Core tracing engine — claim extraction, source tracing, narrative tracing, perspectives, mutations, omissions |
| `search_anthropic.py` | Web search adapter using Claude's built-in search |
| `ingestors.py` | Multi-platform content extraction (web, Bluesky, YouTube, X, TikTok) |
| `social_search.py` | Bluesky social media search with smart sampling and spread analysis |
| `batch.py` | Batch processor — discovers trending topics and analyzes them |
| `graph_store.py` | Neo4j graph storage for all provenance data (views, mutations, social amplifiers) |
| `worker.py` | Background job queue and API endpoints (for future web service) |
| `requirements.txt` | Python dependencies |

---

## Features

### 1. Extract & Classify Claims

Reads content and labels every claim:

| Label | Meaning | Traceable? |
|-------|---------|-----------|
| **Fact** | Specific, verifiable data with names/numbers/dates | → Primary data source |
| **Study** | References specific research or datasets | → Research paper |
| **Narrative** | Interpretive framing with a traceable origin | → Who coined this framing |
| **Opinion** | Genuinely personal preference | Not traceable |
| **Unverifiable** | Sounds factual but too vague to trace | Not traceable |

Classification uses the **traceability test**: "Could I find a primary source to confirm or deny this?"

```bash
# Sample article
python demo.py --extract-only

# From a URL (auto-detects platform: web, Bluesky, YouTube, X)
python demo.py --url https://www.nytimes.com/some-article --extract-only

# From a YouTube video (auto-transcribes)
python demo.py --url https://www.youtube.com/watch?v=VIDEO_ID --extract-only

# From a Bluesky user's recent posts
python demo.py --bluesky-feed user.bsky.social --limit 20 --extract-only

# From a file
python demo.py --file article.txt --extract-only
```

**Cost:** ~$0.001 per article (Haiku only)

---

### 2. Trace Facts to Primary Sources

For claims labeled as facts, finds the original data source and builds a provenance chain.

```bash
python demo.py --claim "The US economy added 256,000 jobs in December 2024" --verbose
python demo.py --claim "US unemployment fell to 4.1%" --verbose --json trace.json
```

**Cost:** ~$0.15-0.25 per claim

---

### 3. Trace Narratives to Their Origin

For claims labeled as narratives, finds who coined the framing, who amplified it, and who pushed back. Sources are classified as:

- ★ **Originator** — coined or first prominently used the framing
- ↗ **Amplifier** — repeated or spread the framing
- ✗ **Critic** — pushed back on or fact-checked the framing

```bash
python demo.py --claim "The economy is rigged against working people" --verbose --json rigged.json
python demo.py --claim "The 2026 World Cup is the new 1936 Olympics" --verbose
```

**Cost:** ~$0.20-0.40 per narrative

---

### 4. Social Media Spread Analysis

Searches Bluesky for how a narrative spreads among real users. Uses smart sampling (top engaged + time spread + author diversity) and classifies the spread pattern:

- **Propagation** — spread from a source outward
- **Convergence** — independent parallel emergence
- **Mixed** — elements of both

```bash
# Standalone social search
python social_search.py the economy is rigged

# Integrated with tracing
python demo.py --claim "The economy is rigged" --social --verbose

# Longer lookback
python demo.py --claim "The border is wide open" --social --days-back 90
```

**Requires:** `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` environment variables

**Cost:** Free (Bluesky API) + ~$0.001 for spread analysis (Haiku)

---

### 5. Same Data, Different Stories (Perspectives)

Given any claim, finds the underlying factual data and searches for how different institutions and ideologies frame it differently. Uses a three-phase approach:

1. **Broad search** — including deliberately adversarial queries to find opposing viewpoints
2. **Gap detection** — identifies which analytical views are missing and searches specifically for them
3. **Consolidation** — groups raw framings into 4-8 distinct analytical views

Each view represents a different **question** being asked, not just a different political position.

```bash
# From a claim
python demo.py --claim "ICE agent killed Renee Good" --perspectives --verbose

# With social media grouped by lens
python demo.py --claim "ICE agent killed Renee Good" --perspectives --social --verbose

# From a URL — extracts the most significant claim and runs perspectives
python demo.py --url https://some-article --perspectives --verbose

# Export everything
python demo.py --claim "..." --perspectives --social --verbose --json full.json
```

Input claims are automatically **neutrally rephrased** before analysis to avoid biasing the search results.

**Cost:** ~$1-3 per analysis

---

### 6. Mutation Tracking

Traces how a claim **changes** as it passes through the information chain. For each transition between sources, identifies what was preserved, dropped, added, or distorted.

Mutation patterns detected: simplification, exaggeration, politicization, decontextualization, sensationalization, mixed.

Runs automatically in `--verbose` mode for claim tracing and perspectives analysis, and in all batch analyses.

```bash
# See mutations in a narrative trace
python demo.py --claim "The economy is rigged" --verbose

# See mutations across perspectives
python demo.py --claim "ICE agent killed Renee Good" --perspectives --verbose
```

**Cost:** ~$0.15-0.30 per analysis (one Sonnet call)

---

### 7. Missing Information Analysis

Analyzes what each perspective view **omits** compared to the others — the mechanism by which echo chambers actually work. Each view gets a completeness score (0-100%).

```bash
python demo.py --claim "ICE agent killed Renee Good" --perspectives --verbose
```

Output shows per-view:
- **Factual omissions** — specific data points left out
- **Perspective omissions** — viewpoints not mentioned
- **Context omissions** — qualifying information missing
- **Completeness score** — how much of the full picture this view provides
- **Bias direction** — pattern in what's systematically omitted

Runs automatically in `--perspectives --verbose` mode and in batch analyses.

**Cost:** ~$0.15-0.30 per view analyzed

---

### 8. Bluesky Feed Analysis (Creator Profile)

Analyzes a creator's recent posts to build an information profile: what mix of facts, narratives, and opinions they produce, and where their claims originate.

```bash
# Classification with creator profile
python demo.py --bluesky-feed user.bsky.social --limit 20 --extract-only

# Full trace with social spread
python demo.py --bluesky-feed user.bsky.social --limit 10 --social --json feed.json
```

Accepts handles (`user.bsky.social`) or full URLs (`https://bsky.app/profile/user.bsky.social`).

Shows a visual profile:
```
CREATOR PROFILE
  Handle: @user.bsky.social
  Posts analyzed: 20
  Total claims: 34
    narrative       ████████████░░░░░░░░ 59% (20)
    fact            ████████░░░░░░░░░░░░ 26% (9)
    opinion         ███░░░░░░░░░░░░░░░░░ 15% (5)
```

---

### 9. Multi-Platform Content Ingestion

Auto-detects platform from URL:

```bash
python demo.py --url https://nytimes.com/article --verbose          # News article
python demo.py --url https://bsky.app/profile/user/post/abc --verbose  # Bluesky
python demo.py --url https://youtube.com/watch?v=ID --verbose        # YouTube
python demo.py --url https://x.com/user/status/123 --verbose        # X/Twitter
```

TikTok/Instagram require additional setup (`pip install yt-dlp openai` + ffmpeg + `OPENAI_API_KEY`).

---

### 10. Batch Processing (Trending Topics)

Discovers trending topics, runs full analysis on each, and stores results for the web interface.

```bash
# Discover topics from Bluesky trending (default)
python batch.py --discover-only

# Discover from Google web search
python batch.py --source web --discover-only

# Filter by region
python batch.py --region Minneapolis --discover-only
python batch.py --region Europe --discover-only

# Filter by category
python batch.py --category politics --discover-only
python batch.py --category technology --discover-only
python batch.py --category health --region Europe --discover-only

# Analyze all discovered topics
python batch.py --social

# Analyze fewer topics
python batch.py --max-topics 3 --social

# Analyze a specific topic
python batch.py --topic "ICE agent killed Renee Good in Minneapolis" --social

# Analyze topics from a file (one per line)
python batch.py --topics my_topics.txt --social

# Re-analyze even if already done today
python batch.py --force

# Custom output directory
python batch.py --output-dir ./my_results
```

Available categories: `politics`, `economy`, `technology`, `world`, `science`, `social`, `legal`, `health`, `business`, `media`.

Results are stored in `./results/YYYY-MM-DD/` with `index.json` and individual `topic_*.json` files. Discovery uses neutral rephrasing — both original and neutral versions are displayed.

The batch pipeline for each topic runs: neutral rephrasing → claim extraction → source tracing → perspectives (with gap detection) → mutation tracking → missing info analysis → social spread (if `--social`).

---

## Flag Reference

### demo.py

| Flag | Purpose |
|------|---------|
| `--claim "..."` | Trace a single claim |
| `--url URL` | Extract and trace from any URL |
| `--file PATH` | Extract and trace from a text file |
| `--bluesky-feed HANDLE` | Analyze a Bluesky user's recent posts |
| `--limit N` | Posts to fetch for `--bluesky-feed` (default: 10) |
| `--extract-only` | Only classify claims, skip tracing (cheapest) |
| `--perspectives` | Find competing framings (works with `--claim`, `--url`, `--file`) |
| `--social` | Include Bluesky social spread analysis |
| `--verbose` / `-v` | Show detailed sources, mutations, omissions |
| `--days-back N` | Social search lookback in days (default: 30) |
| `--json FILE` | Export results to JSON |

### batch.py

| Flag | Purpose |
|------|---------|
| `--discover-only` | Just show topics, don't analyze |
| `--topic "..."` | Analyze a single specific topic |
| `--topics FILE` | Analyze topics from a file (one per line) |
| `--max-topics N` | Max topics to discover (default: 10) |
| `--source bluesky\|web` | Discovery source (default: bluesky) |
| `--region REGION` | Regional focus (e.g., "Minneapolis", "Europe") |
| `--category CAT` | Filter by topic category |
| `--social` | Include Bluesky social spread |
| `--days-back N` | Social search lookback (default: 30) |
| `--output-dir DIR` | Results directory (default: ./results) |
| `--force` | Re-analyze topics already done today |

---

## Architecture

```
Content (article, post, video, URL)
    │
    ▼
┌─────────────────────────────┐
│  Neutral Rephrasing (Haiku) │ ── Remove editorial bias
└──────────┬──────────────────┘
           │
┌──────────┴──────────────────┐
│  Claim Extraction (Haiku)   │ ── Fact / Narrative / Opinion / Study / Unverifiable
└──────────┬──────────────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌─────────┐  ┌──────────┐
│  Facts  │  │Narratives│
│ (Sonnet)│  │ (Sonnet) │
│         │  │          │
│ Primary │  │ Who      │
│ data    │  │ coined   │
│ source  │  │ this     │
│         │  │ framing  │
└────┬────┘  └────┬─────┘
     │            │
     ▼            ▼
┌─────────────────────────────┐
│  Perspectives               │ ── Same data, different framings
│  Gap detection + 2nd pass   │ ── Multi-view consolidation
└──────────┬──────────────────┘
           │
     ┌─────┼─────┐
     ▼     ▼     ▼
┌────────┐┌────────┐┌────────────┐
│Mutation││Missing ││  Social    │
│Tracking││Info    ││  Search    │
│(Sonnet)││(Sonnet)││  (Bluesky) │
│        ││        ││            │
│How did ││What    ││ Spread by  │
│claims  ││does    ││ view       │
│change? ││each    ││ Amplifiers │
│        ││view    ││            │
│        ││omit?  ││            │
└────────┘└────────┘└────────────┘
```

---

## Cost Guide

| Operation | Model | Approx. Cost |
|-----------|-------|-------------|
| Classify claims in an article | Haiku | $0.001 |
| Trace one factual claim | Sonnet + Haiku | $0.15-0.25 |
| Trace one narrative | Sonnet + Haiku | $0.20-0.40 |
| Perspectives analysis | Sonnet + Haiku | $1-3 |
| Mutation tracking | Sonnet | $0.15-0.30 |
| Missing info analysis (per view) | Sonnet | $0.15-0.30 |
| Social search | Bluesky API + Haiku | $0.01 |
| Full batch topic (all features) | All | $3-8 |

Costs decrease over time as the cache fills — identical claims return cached results instantly.

---

## Rate Limits

At Tier 1 (30,000 input tokens/min for Sonnet), analyses run sequentially. The system automatically retries on transient errors (429 rate limit, 529 overloaded) with 30-150 second waits. As your API tier increases with usage, everything speeds up. Haiku calls have much higher rate limits and are not throttled.

---

## JSON Export Structure

### Batch result (`results/YYYY-MM-DD/topic_*.json`)

```json
{
  "topic": "original phrasing from discovery",
  "neutral_topic": "neutrally rephrased version used for analysis",
  "label": "fact|narrative|study",
  "primary_source": {"url": "...", "title": "...", "tier": "primary|originator"},
  "trace_summary": "where this information comes from",
  "views": [
    {
      "view_name": "Accountability",
      "question": "Will anyone be held responsible?",
      "key_claim": "...",
      "sources": [{"url": "...", "perspective": "...", "emphasizes": "...", "downplays": "..."}]
    }
  ],
  "mutations": {
    "transitions": [
      {"from_source": "...", "to_source": "...", "preserved": "...", "dropped": "...", "added": "...", "distorted": "..."}
    ],
    "overall": {"mutation_severity": "significant", "mutation_pattern": "sensationalization", "summary": "..."}
  },
  "omissions_by_view": [
    {
      "view_name": "Law & Order",
      "completeness_score": 0.35,
      "factual_omissions": [{"what_is_missing": "...", "found_in_lens": "...", "impact": "..."}],
      "summary": "A reader of only this view would not know..."
    }
  ],
  "social": {"spread_type": "mixed", "total_posts_found": 65, "sample_posts": [...]},
  "social_by_view": {"Accountability": [{"author": "@user", "likes": 45, "url": "..."}]},
  "gap_analysis": "what perspectives might still be missing",
  "cost_usd": 4.23
}
```

### Bluesky feed export (`--bluesky-feed --json`)

```json
{
  "handle": "@user.bsky.social",
  "posts_analyzed": 20,
  "profile": {
    "total_claims": 34,
    "label_distribution": {
      "narrative": {"count": 20, "percentage": 58.8},
      "fact": {"count": 9, "percentage": 26.5},
      "opinion": {"count": 5, "percentage": 14.7}
    }
  },
  "claims": [...],
  "social_spread": {...}
}
```
