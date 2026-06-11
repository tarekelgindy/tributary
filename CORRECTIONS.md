# Corrections

A public, dated log of every substantive error found in Tributary's published output, and what was done about it. An empty section below is not a claim of perfection — it means no published-output errors have been *found yet*. The measured error rate (once published, per [METHODOLOGY.md](METHODOLOGY.md)) lives here too.

**Format:** date · output affected · the error · the fix · root cause.

---

## Published-output corrections

| Date | Output affected | Error | Fix | Root cause |
|------|-----------------|-------|-----|------------|
| 2026-06-10 | The entire public gallery (103 events) | Every gallery event link returned 404: the index was live but none of the event files had been deployed | `.gitignore`'s unanchored `events/` pattern also matched `gallery/events/`, so the files were silently never committed; pattern root-anchored, files deployed, live URLs verified | Unanchored ignore pattern — and a deployment check that verified the index but not the content it points to. Found by Tarek's manual QA. |
| 2026-06-10 | ~7.5% of carrier links across the corpus (274 of 3,667) | Carrier links labeled with a named source (e.g. "UN OCHA") pointed to encyclopedia pages *about the event* rather than the named carrier's own statement — the link did not support the attribution | Viewer now flags such links ("via Wikipedia") rather than letting the label imply first-party sourcing; generation prompt now requires the carrier's own URL or none ("a missing link is honest, a mislabeled one is not") | Web search surfaces aggregator pages; the model substituted them for primary links; URL verification checked existence, not that the page belongs to the named carrier. Found by Tarek's manual QA. Existing corpus entries retain the flag rather than being silently rewritten. |

## Pipeline-level errors caught before/at publication

These shaped the methodology and are disclosed in the same spirit:

| Date | Component | Error | Fix | Root cause |
|------|-----------|-------|-----|------------|
| 2026-06-08 | Coverage-lean | Institutional actors (NGOs, courts, ministries) matched AllSides' think-tank/author entries and fabricated a left/right skew for several events | Distribution gated to news-media outlets only | Counting non-press actors as press coverage |
| 2026-06-08 | Coverage-lean | International events showed "one-sided" coverage that actually reflected unrated (non-US) outlets | Resolve-rate guard: below threshold, output says "insufficient rated coverage to judge skew" | US-centric ratings database mistaken for universal coverage |
| 2026-06-09 | Fingerprint viewer | A short-lived "Amplifiers" tab flattened spread-position roles into endorsement, presenting sources that *dispute* a claim as its champions | Tab removed; lineage view (which keeps critics distinct) remains | Treating interpretive AI role labels as stance; insufficient semantic validation before shipping |

*Root-cause pattern worth admitting: every entry above came from over-trusting an interpretive label or an incomplete database. The standing rule that resulted: prefer structural signals, audit AI labels before building on them, and say "insufficient data" instead of guessing.*
