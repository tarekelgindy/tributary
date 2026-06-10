# Examples — real Tributary output

Every file here is a **real, unedited analysis** produced by the current pipeline (not mockups). Each carries full provenance: every element is labeled AI-generated, every cited URL went through live verification, and the limits of each layer are documented in [METHODOLOGY.md](../METHODOLOGY.md).

**Two ways to view:**
- **Online (no install):** open the [gallery](https://tarekelgindy.github.io/tributary/) and click a trace, or use the permalinks below.
- **Local:** open `fingerprint_viewer.html` (repo root) in any browser and drag a JSON file onto it. Nothing leaves your machine.

---

## Upstream — narrative fingerprints

### [`fingerprint_rigged_economy.json`](https://tarekelgindy.github.io/tributary/fingerprint_viewer.html?load=examples/fingerprint_rigged_economy.json)
*"The economy is rigged against working people."*
The dual-lineage signature demo. The **phrasing** traces through recent US politics (Sanders, Warren, and earlier), while the underlying **idea** — structural economic disadvantage — traces back through centuries of economic thought. Two different questions, two different timelines, shown side by side. Includes a 15-source evidence landscape (supporting / disputing / redirecting / shared context) and observed social-media spread.

### [`fingerprint_chewing_gum.json`](https://tarekelgindy.github.io/tributary/fingerprint_viewer.html?load=examples/fingerprint_chewing_gum.json)
*"Chewing gum stays in your stomach for years."*
A decades-old folk claim with a genealogy reaching back to pre-modern medicine. The attestation log includes both the claim's spreaders **and** the medical literature and fact-checking organizations that dispute it — Tributary records who said what and when, and issues no verdict of its own.

## Downstream — event analyses

### [`event_platner_allegations.json`](https://tarekelgindy.github.io/tributary/fingerprint_viewer.html?load=examples/event_platner_allegations.json)
A contested US-political story (misconduct allegations against a Senate candidate). Eight competing framings, the common ground beneath them, and the richest coalition map in the corpus — including actors that champion multiple framings and the coverage-lean backdrop (15 rated outlets, spread across the spectrum).

### [`event_gaza_airstrikes.json`](https://tarekelgindy.github.io/tributary/fingerprint_viewer.html?load=examples/event_gaza_airstrikes.json)
An international event where a US left/right axis simply doesn't map. The coalition view shows the actual structure of the coverage (regional, international, and institutional actors carrying different framings), while the AllSides backdrop honestly reports *insufficient rated coverage to judge skew* — the designed behavior when a US-centric ratings database meets a non-US story.

### [`event_house_war_powers.json`](https://tarekelgindy.github.io/tributary/fingerprint_viewer.html?load=examples/event_house_war_powers.json)
The US House vote on limiting war powers over Iran operations — the largest actor coalition in the corpus (30+ actors across 8 framings), mixing officials, outlets, and institutions on every side of the question.

---

*A `SourceAnalysis` example (whole-article claim audit) is planned for this folder; it requires a fresh API run.*

*Generated with the pipeline in this repo. Costs: a fingerprint ≈ $0.15–1.00 depending on depth; an event analysis ≈ $0.30–0.70. See [README.md](../README.md) for cost controls.*
