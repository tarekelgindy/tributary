# Tributary — Where Did This Come From, and What Do the Bubbles Agree On?

## What is Tributary?

Every claim in your feed has a hidden **history**: someone said it first, others amplified it, and it mutated along the way. And every event in your feed is being framed by circles you never see — whose framings sometimes overlap with your own circle's more than either side knows, because an engagement-driven feed surfaces the other side's worst take, not its overlapping one. Tributary is a tool that makes both visible:

- **Upstream — trace a narrative to its origins.** Give it a claim like *"the economy is rigged"* and it builds a **narrative fingerprint**: who coined the phrasing, who amplified it, how it mutated as it spread — and, separately, where the underlying *idea* comes from (the phrasing may be 30 years old while the idea traces back centuries). It also maps the surrounding evidence landscape: what supports it, what disputes it, what context all sides share.

- **Downstream — map the narratives forming around an event, and where they intersect.** Give it an event and it finds the **distinct framings** competing to define it — each one a different *question* being asked, not just a political side — plus who carries each framing, what each emphasizes and leaves out, and the **common ground**: the facts every side accepts and the framings that appear across circles, in each circle's own quoted words. This is often the most clarifying part — it's the agreement your feed buried.

The two directions form one loop, and each half protects the other. Provenance tells you whether a shared framing is an organic, decades-old concern or a recently seeded talking point — so finding overlap never means false equivalence. And overlap gives a trace its human context — so provenance doesn't collapse into a gotcha weapon ("*their* narrative was seeded").

- **Whole articles too.** Point it at an article or transcript and it classifies every significant claim (fact / study / narrative / opinion / unverifiable) and traces the traceable ones.

Every cited source is verified against the live page (with an archive fallback), and every element is labeled with its provenance — including the honest admission that today, all of it is AI-generated and none of it is yet human-reviewed.

## What Tributary will never do

It doesn't tell you what to think, and it will never label a claim **true** or **false**. Those are verdicts, and verdicts ask for your trust. Tributary instead gives you *structural descriptions you can check yourself*: who said it first, who repeated it, who linked primary documents, who issued corrections, who never mentioned it. The methodology is public ([METHODOLOGY.md](METHODOLOGY.md)), and so is the corrections log ([CORRECTIONS.md](CORRECTIONS.md)) — a provenance tool should hold itself to the standards it measures.

One more commitment: Tributary is allowed to find **low** overlap. When circles genuinely don't agree, it says so — a tool that always finds heartwarming common ground is harmony propaganda, and conclusion-neutral cuts both ways.

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
- [ABOUT.md](ABOUT.md) — why this exists: the problem, the theory of change, what success means
- [METHODOLOGY.md](METHODOLOGY.md) — what each output claims, how it's produced, and its known limits
- [MISSION_PLAN.md](MISSION_PLAN.md) — the working plan of record, phase by phase, with binding gates
