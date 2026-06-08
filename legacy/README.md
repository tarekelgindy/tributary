# Legacy — the original Tributary pipeline (archived)

These files are the **original** Tributary implementation, kept for
reference. They are **no longer maintained** and are **not** part of the
current pipeline.

The project has been unified onto the `NarrativeFingerprint` model in
`../models.py`, driven by `../fingerprint.py`, with `../fingerprint_viewer.html`
as the viewer. Everything of value here has been (or is being) absorbed into
that world — the perspectives/views concept became the downstream
`EventAnalysis`, and the human-contribution scaffolding became the
`Provenance` / `Contribution` / `Contributor` substrate.

| File | What it was |
|------|-------------|
| `agent.py` | Original core engine — claim extraction, source/narrative tracing, perspectives, mutations, omissions (over the old `TributaryEvent` model) |
| `demo.py` | Original CLI |
| `batch.py` | Trending-topic batch processor (the only file that used `TributaryEvent.from_batch_result`) |
| `search_anthropic.py` | Web-search adapter using Claude's built-in search |
| `graph_store.py` | Neo4j storage for the old model |
| `worker.py` | Scaffolding for a future web-service queue |
| `viewer.html`, `viewer_river.html`, `viewer_comparisons.html` | Original viewers, built for the old `TributaryEvent` JSON |

**Note:** these were moved into `legacy/` and their `from models import …` /
`from social_search import …` imports assume the project root. They are
archived, not meant to run as-is; adjust paths if you need to revive any of
it. The old data types they used (`TributaryEvent`, `TrackedClaim`,
`View`, `LegacyContribution`, `ContributorProfile`, etc.) still live in
`../models.py` under a "Legacy model" section but are referenced by nothing
in the active pipeline.
