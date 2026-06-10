# Corrections

A public, dated log of every substantive error found in Tributary's published output, and what was done about it. An empty section below is not a claim of perfection — it means no published-output errors have been *found yet*. The measured error rate (once published, per [METHODOLOGY.md](METHODOLOGY.md)) lives here too.

**Format:** date · output affected · the error · the fix · root cause.

---

## Published-output corrections

*None logged yet. The first precision audit (≈50 randomly sampled attestations, manually verified) is a committed roadmap item and will be recorded here, whatever it finds.*

## Pipeline-level errors caught before/at publication

These shaped the methodology and are disclosed in the same spirit:

| Date | Component | Error | Fix | Root cause |
|------|-----------|-------|-----|------------|
| 2026-06-08 | Coverage-lean | Institutional actors (NGOs, courts, ministries) matched AllSides' think-tank/author entries and fabricated a left/right skew for several events | Distribution gated to news-media outlets only | Counting non-press actors as press coverage |
| 2026-06-08 | Coverage-lean | International events showed "one-sided" coverage that actually reflected unrated (non-US) outlets | Resolve-rate guard: below threshold, output says "insufficient rated coverage to judge skew" | US-centric ratings database mistaken for universal coverage |
| 2026-06-09 | Fingerprint viewer | A short-lived "Amplifiers" tab flattened spread-position roles into endorsement, presenting sources that *dispute* a claim as its champions | Tab removed; lineage view (which keeps critics distinct) remains | Treating interpretive AI role labels as stance; insufficient semantic validation before shipping |

*Root-cause pattern worth admitting: every entry above came from over-trusting an interpretive label or an incomplete database. The standing rule that resulted: prefer structural signals, audit AI labels before building on them, and say "insufficient data" instead of guessing.*
