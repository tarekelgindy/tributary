# Tributary — See Where Your Information Really Comes From

## What is Tributary?

Every day you read news articles, scroll through social media, and watch videos that shape how you understand the world. But have you ever wondered: where did this information actually come from? Who said it first? Who is amplifying it? And what are you *not* being told?

Tributary is a tool that answers these questions, in two directions:

- **Upstream — trace a narrative to its origins.** Give it a claim like *"the economy is rigged"* and it builds a **narrative fingerprint**: who coined the phrasing, who amplified it, how it mutated as it spread — and, separately, where the underlying *idea* comes from (the phrasing may be 30 years old while the idea traces back centuries). It also maps the surrounding evidence landscape: what supports it, what disputes it, what context all sides share.

- **Downstream — map the narratives forming around an event.** Give it an event and it finds the **distinct framings** competing to define it — each one a different *question* being asked, not just a political side — plus who carries each framing, what each emphasizes and leaves out, and the **common ground**: the facts every side accepts, which is often the most clarifying part.

- **Whole articles too.** Point it at an article or transcript and it classifies every significant claim (fact / study / narrative / opinion / unverifiable) and traces the traceable ones.

Every cited source is verified against the live page (with an archive fallback), and every element is labeled with its provenance — including the honest admission that today, all of it is AI-generated and none of it is yet human-reviewed.

## What Tributary will never do

It doesn't tell you what to think, and it will never label a claim **true** or **false**. Those are verdicts, and verdicts ask for your trust. Tributary instead gives you *structural descriptions you can check yourself*: who said it first, who repeated it, who linked primary documents, who issued corrections, who never mentioned it. The methodology is public ([METHODOLOGY.md](METHODOLOGY.md)), and so is the corrections log ([CORRECTIONS.md](CORRECTIONS.md)) — a provenance tool should hold itself to the standards it measures.

## See it without installing anything

The viewer is a single self-contained HTML page; analyses are plain JSON files. Browse real examples in [`examples/`](examples/) — open the gallery (GitHub Pages) and click any trace, or open `fingerprint_viewer.html` locally in any browser and drag a JSON file onto it. Nothing leaves your machine.

## Quick start (running your own analyses)

You need Python 3.12 and an Anthropic API key ([console.anthropic.com](https://console.anthropic.com), $5 minimum credit).

```bash
# One-time setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key-here"

# Upstream: trace where a narrative comes from (~$0.15–0.25)
python fingerprint.py "the economy is rigged against working people" --save

# Downstream: map the narratives forming around an event (~$0.40–0.70)
python fingerprint.py --event "ICE agent killed Renee Good in Minneapolis" --save

# Cheap preview of an article's claims (~$0.005)
python fingerprint.py --url https://some-news-article.com --extract-only

# Build a corpus: discover the day's events, keep the contested ones, batch-analyze
python discover.py --date 2026-06-07
python corpus.py topics_2026-06-07.txt --min-contestedness 6 --batch
```

Then open `fingerprint_viewer.html` and drag your saved JSON (from `fingerprints/` or `events/`) onto it.

## What does the output look like?

For an event, the viewer leads with the **common ground** (verified shared facts, shared-but-unverified claims, the actual points of disagreement), then **who's amplifying what** — the actors carrying each framing, including the connectors that cut across framings — then the competing framings themselves, each with its carriers, emphases, omissions, and verified links. A collapsible backdrop shows the left/right lean of the coverage (per AllSides' outlet ratings) with its limits stated plainly.

For a narrative, you get the five-layer fingerprint: the phrasing and its variants, the underlying claim, the rhetorical structure, the dual genealogy (phrasing vs. idea, each a dated, sourced timeline), and the evidence landscape.

## Learn more

- [README.md](README.md) — full setup, all features, cost controls, architecture
- [METHODOLOGY.md](METHODOLOGY.md) — what each output claims, how it's produced, and its known limits
- [ROADMAP.md](ROADMAP.md) — where this is going, with binding gates
