"""
Prompt regression harness (2026-10-11, the role-prompt batch's validator)
=========================================================================
Every hand-corrected trace is a known-answer test: the sources that fooled
the old prompts are replayed against the amended ones, in HARNESS MODE (no
web search — the model evaluates only the candidate material provided).
This tests the RULES, which is what changed; retrieval is untouched.

    python validate_prompts.py          # run all fixtures (~8 Sonnet calls, cents)
    python validate_prompts.py --only mayo_rebuttal

Fixtures cover the four identity gates plus controls proving correct labels
don't regress:
  - CLAIM POLARITY (Mayo inversion, 2026-07-10) + control
  - fiction != attestation (Gunn 1955, 2026-09-25) + Kaysing control
  - first-documented != originator (Parker 1916, 2026-09-18)
  - instance != thesis (Marx 1867, 2026-10-11) with Piketty-Saez control
  - originator stays available where coining IS evidenced (Ministry 1940)

Run this after ANY edit to the attestation/ancestor prompts. Exit code 1 on
any failure.
"""

import argparse
import json
import sys

import anthropic

from fingerprint import (ADVERSARIAL_SYSTEM, CONCEPTUAL_ANCESTORS_SYSTEM,
                         EARLIEST_USE_SYSTEM, SONNET, _parse_json_safe)

HARNESS_PREAMBLE = """\
HARNESS MODE: you have NO web search in this run. Evaluate ONLY the
candidate material supplied in the user message, applying every rule of
your instructions, and return your standard JSON with entries for exactly
those candidates you would include (omit any candidate your rules exclude,
or include it with the claim_relation/role your rules require).

STRICT JSON: every field takes exactly ONE value. If a field could carry
several strings (e.g. lexical_form_seen with multiple wordings), join them
into a single string with " / " — never emit comma-separated bare strings.

"""


def _call(client, system, payload, _retries=2):
    """One harness call; retries on UNPARSEABLE output (a model quirk —
    e.g. several naked strings glued to one value — not a rules failure,
    so it must not fail a fixture)."""
    for attempt in range(_retries + 1):
        resp = client.messages.create(
            model=SONNET, max_tokens=1600, temperature=0,
            system=[{"type": "text", "text": HARNESS_PREAMBLE + system}],
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        out = _parse_json_safe(text)
        if out:
            return out
        print(f"       (unparseable JSON from the model, retry {attempt + 1})")
    return {}


def _entries(out):
    for key in ("instances", "earlier_instances", "contributors", "earlier_ancestors"):
        if key in out:
            return out[key] or []
    return []


MAYO = {
    "source_title": "Autism and vaccines: separating fact from fiction (Mayo Clinic patient education page)",
    "author": "Mayo Clinic staff",
    "date": "2019-05-04",
    "source_url": "https://www.mayoclinic.org/autism-vaccines",
    "content_summary": ("An official Mayo Clinic patient-education page stating as the "
                        "institution's own medical position: 'Vaccines do not cause autism. "
                        "Decades of large studies have found no link between childhood "
                        "vaccines and autism spectrum disorder.' The page uses the exact "
                        "phrases 'vaccines cause autism' and 'vaccines do not cause "
                        "autism' repeatedly (heading: 'Do vaccines cause autism?'). It "
                        "explains why the myth persists and reassures parents about "
                        "vaccine safety."),
}

FIXTURES = {}


def fixture(fn):
    FIXTURES[fn.__name__] = fn
    return fn


@fixture
def mayo_rebuttal(client):
    """CLAIM POLARITY: on a rebuttal trace, Mayo asserting the rebuttal is
    adoption/amplification — the recorded failure labeled it critic."""
    out = _call(client, EARLIEST_USE_SYSTEM, {
        "claim_being_traced": "vaccines do not cause autism",
        "diagnostic_ngrams": ["vaccines do not cause autism", "no link between vaccines and autism"],
        "candidates": [MAYO]})
    ent = _entries(out)
    if not ent:
        return False, "Mayo entry missing entirely"
    role = ent[0].get("amplifier_role", "")
    return role != "critic", f"role={role!r} (must not be critic on a rebuttal trace)"


@fixture
def mayo_control_myth(client):
    """Control: tracing the MYTH, the same Mayo page is critic — the old
    correct behavior must not regress."""
    out = _call(client, EARLIEST_USE_SYSTEM, {
        "claim_being_traced": "vaccines cause autism",
        "diagnostic_ngrams": ["vaccines cause autism"],
        "candidates": [MAYO]})
    ent = _entries(out)
    if not ent:
        return False, "Mayo entry missing entirely"
    role = ent[0].get("amplifier_role", "")
    return role == "critic", f"role={role!r} (must be critic on the myth trace)"


@fixture
def fiction_adversarial(client):
    """fiction != attestation: the 1955 Gunn story must not become an
    earlier instance of the moon-hoax claim (recorded failure: crowned
    originator)."""
    out = _call(client, ADVERSARIAL_SYSTEM.format(proposed_date="1976-01-01"), {
        "claim_being_traced": "the moon landing was faked",
        "diagnostic_ngrams": ["moon landing was faked", "faked the moon landing"],
        "candidates": [{
            "source_title": "\"The Cave of Night\", Galaxy Science Fiction, Feb 1955",
            "author": "James E. Gunn",
            "date": "1955-02-01",
            "source_url": "https://www.isfdb.org/cgi-bin/title.cgi?45830",
            "content_summary": ("A science-fiction short story whose plot depicts a U.S. "
                                "agency faking a crewed spaceflight using prerecorded "
                                "transmissions to deceive the public. Fiction; the author "
                                "asserts nothing about any real spaceflight.")}]})
    ent = _entries(out)
    if not ent:
        return True, "correctly excluded"
    e = ent[0]
    ok = (e.get("claim_relation") == "related-context"
          and e.get("amplifier_role") not in ("originator",))
    return ok, (f"claim_relation={e.get('claim_relation')!r} role={e.get('amplifier_role')!r}"
                " (fiction must be related-context, never originator)")


@fixture
def kaysing_control(client):
    """Control: genuinely coined earlier accusations still earn originator —
    the fiction gate must not neuter real finds."""
    out = _call(client, ADVERSARIAL_SYSTEM.format(proposed_date="1990-01-01"), {
        "claim_being_traced": "the moon landing was faked",
        "diagnostic_ngrams": ["moon landing was faked", "we never went to the moon"],
        "candidates": [{
            "source_title": "We Never Went to the Moon: America's Thirty Billion Dollar Swindle",
            "author": "Bill Kaysing",
            "date": "1976-01-01",
            "source_url": "https://archive.org/details/wenevertwenttothemoon",
            "content_summary": ("A self-published nonfiction book asserting as fact that "
                                "NASA faked the Apollo moon landings. Documented as the "
                                "coining articulation of the accusation: no earlier "
                                "articulation by anyone is documented anywhere, and "
                                "every later proponent traces back to this book.")}]})
    ent = _entries(out)
    if not ent:
        return False, "Kaysing entry missing — a real earlier find was dropped"
    role = ent[0].get("amplifier_role", "")
    return role == "originator", f"role={role!r} (evidenced coining must stay originator)"


@fixture
def parker_oral(client):
    """first-documented != originator: Parker transcribing a centuries-old
    oral principle is its documenter (recorded failure: originator)."""
    out = _call(client, CONCEPTUAL_ANCESTORS_SYSTEM, {
        "claim_being_traced": ("decisions should weigh their impact on the seventh "
                               "generation to come"),
        "diagnostic_ngrams": ["seven generations", "seventh generation"],
        "candidates": [{
            "source_title": "The Constitution of the Five Nations (New York State Museum Bulletin)",
            "author": "Arthur C. Parker",
            "date": "1916-01-01",
            "source_url": "https://archive.org/details/constitutionoffi00park",
            "content_summary": ("Parker's published transcription of the Haudenosaunee "
                                "Great Law from the Seth Newhouse manuscript — a written "
                                "documentation of a much older oral constitutional "
                                "tradition, which Parker credits as pre-existing.")}]})
    ent = _entries(out)
    if not ent:
        # Exclusion is rule-compliant: 1916 sits outside the prompt's own
        # living-memory window. The failure mode under test is ONLY Parker
        # being crowned originator of a centuries-old oral teaching.
        return True, "excluded (era rule) — acceptable; never originator"
    role = ent[0].get("amplifier_role", "")
    return role != "originator", f"role={role!r} (documenter of an oral tradition, never originator)"


@fixture
def marx_instance(client):
    """instance != thesis: a wealth statistic's ancestors are quantitative
    wealth claims, not Capital (recorded failure: Marx single-origin 0.92)."""
    out = _call(client, CONCEPTUAL_ANCESTORS_SYSTEM, {
        "claim_being_traced": ("the poorest billionaire in California makes about "
                               "$300 million this year"),
        "diagnostic_ngrams": ["poorest billionaire", "$300 million"],
        "candidates": [
            {"source_title": "Capital, Volume I", "author": "Karl Marx",
             "date": "1867-01-01",
             "source_url": "https://www.marxists.org/archive/marx/works/1867-c1/",
             "content_summary": ("Foundational political economy: surplus value, primitive "
                                 "accumulation, the general tendency of capital to "
                                 "concentrate. Contains no quantitative claims about "
                                 "individual top-wealth gains.")},
            {"source_title": "Income Inequality in the United States, 1913-1998",
             "author": "Thomas Piketty and Emmanuel Saez", "date": "2003-01-01",
             "source_url": "https://eml.berkeley.edu/~saez/pikettyqje.pdf",
             "content_summary": ("Quantitative economics: constructs top income and wealth "
                                 "shares from tax data, establishing the modern tradition of "
                                 "specific numeric claims about what the richest earn and "
                                 "hold per year.")}]})
    ent = _entries(out)
    marx = next((e for e in ent if "Marx" in str(e.get("author", ""))), None)
    ps = next((e for e in ent if "Piketty" in str(e.get("author", ""))), None)
    marx_ok = marx is None or marx.get("claim_relation") == "related-context"
    ps_ok = ps is not None and ps.get("claim_relation") == "asserts"
    return marx_ok and ps_ok, (
        f"Marx={'excluded' if marx is None else marx.get('claim_relation')!r} "
        f"(needs related-context/excluded) · Piketty-Saez="
        f"{'MISSING' if ps is None else ps.get('claim_relation')!r} (needs asserts)")


@fixture
def ministry_control(client):
    """Control: evidenced coining still earns originator on the lexical pass
    (the carrots card's hero must survive the batch)."""
    out = _call(client, EARLIEST_USE_SYSTEM, {
        "claim_being_traced": "carrots improve your eyesight and night vision",
        "diagnostic_ngrams": ["carrots help you see in the dark", "carrots improve eyesight"],
        "candidates": [{
            "source_title": "UK Ministry of Food wartime campaign: 'Carrots keep you healthy and help you see in the blackout'",
            "author": "UK Ministry of Food",
            "date": "1940-01-01",
            "source_url": "https://www.iwm.org.uk/history/carrots-blackout",
            "content_summary": ("The 1940 British Ministry of Food propaganda campaign that "
                                "coined the claim that carrots improve night vision — "
                                "invented by the Ministry to explain RAF night-interception "
                                "success and promote carrot consumption. No earlier "
                                "articulation of the claim is documented; historians "
                                "credit this campaign as the myth's origin.")}]})
    ent = _entries(out)
    if not ent:
        return False, "Ministry entry missing entirely"
    role = ent[0].get("amplifier_role", "")
    return role == "originator", f"role={role!r} (evidenced coining must stay originator)"


def main():
    ap = argparse.ArgumentParser(description="Replay known-answer fixtures against the live prompts.")
    ap.add_argument("--only", default="", help="run a single fixture by name")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    names = [args.only] if args.only else list(FIXTURES)
    failures = 0
    for name in names:
        # Best-of-3: even at temperature 0, serving is not deterministic and
        # borderline role calls flip between runs. A RULES regression (the
        # thing this harness exists to catch) fails all three attempts; a
        # sampling wobble passes at least one. Measured 2026-10-11: single-
        # shot runs flipped different fixtures on consecutive identical runs.
        ok, detail, attempt = False, "", 0
        for attempt in range(1, 4):
            try:
                ok, detail = FIXTURES[name](client)
            except Exception as e:  # noqa: BLE001 — a crashed attempt is a failed attempt
                ok, detail = False, f"{type(e).__name__}: {e}"
            if ok:
                break
        tag = "PASS" if ok else "FAIL"
        note = f" (attempt {attempt}/3)" if ok and attempt > 1 else ""
        print(f"  [{tag}] {name:20} {detail}{note}")
        failures += (not ok)
    print(f"\n{len(names) - failures}/{len(names)} fixtures pass")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
