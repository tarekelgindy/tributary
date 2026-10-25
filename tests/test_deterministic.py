"""
Deterministic regression tests (2026-10-15)
===========================================
Free, offline, no API key: the pure-Python halves of the quality machinery.
Two recorded failure classes motivate the suite existing at all:
  - the cards.py threshold guard read the wrapper instead of fp["fp"] and no
    card rendered for a day — caught late because only the trip case had
    been tested (CORRECTIONS 2026-10-13 lesson: test BOTH sides of a guard);
  - a mid-__init__ edit orphaned max_searches and crashed every generation
    (the Opus A/B's constructor landmine).
validate_prompts.py covers the model-judgment half; this file covers what
must never need a model to be right.

    python tests/test_deterministic.py     # local (Windows): self-runner
    python -m pytest tests/ -q             # CI (Linux)

(The self-runner exists because pytest 8's collector stats every repo-root
entry and dies on the WSL venv's lib64 symlink under Windows [WinError
1920]; the CI checkout never contains the venv, so pytest works there.)
"""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apply_referent_bound import bound_pass_dict  # noqa: E402
from fingerprint import (_embed_recovery_pass, _is_recent_date,  # noqa: E402
                         _norm_ref_date, _parse_referent_json,
                         _referent_bound_pass, _referent_instance_digest,
                         _role_sanity_pass, _single_originator_pass,
                         _snowflake_sanity_pass, _x_snowflake_date)
from models import AmplifierRole, GenealogyStatus  # noqa: E402
from publish import is_weak_trace  # noqa: E402


# --- referent date normalization -------------------------------------------

def test_norm_ref_date_pads_year_and_month():
    assert _norm_ref_date("2012") == "2012-01-01"
    assert _norm_ref_date("2026-07") == "2026-07-01"
    assert _norm_ref_date("1986-06-21") == "1986-06-21"
    assert _norm_ref_date("") == ""
    assert _norm_ref_date("unknown") == "unknown"  # passthrough; bound pass rejects


# --- resolver output parsing (incl. the too-late cap) -----------------------

def test_parse_referent_requires_description_and_date():
    assert _parse_referent_json('{"description": "", "date": "2024-01-01"}') == {}
    assert _parse_referent_json('{"description": "a thing", "date": ""}') == {}
    assert _parse_referent_json("not json at all") == {}


def test_parse_referent_caps_at_earliest_concerning_instance():
    # The Trump-crypto near-miss: resolved date a year late; the cap must pull
    # it back to the earliest concerning instance when copied verbatim.
    raw = ('{"description": "foreign money in Trump crypto", "date": "2025-11-25",'
           ' "earliest_concerning_date": "2024-11-25", "basis": "senate letters"}')
    ref = _parse_referent_json(raw, digest_dates={"2024-11-25", "2025-05-05"})
    assert ref["date"] == "2024-11-25"
    assert "capped" in ref["basis"]


def test_parse_referent_ignores_hallucinated_cap_date():
    raw = ('{"description": "a venture", "date": "2025-11-25",'
           ' "earliest_concerning_date": "2024-01-07", "basis": "x"}')
    ref = _parse_referent_json(raw, digest_dates={"2024-11-25", "2025-05-05"})
    assert ref["date"] == "2025-11-25"  # cap date not in digest -> not trusted


# --- the deterministic invariant, object form (in-pipeline) -----------------

def _inst(date, title="t", rel="", role=AmplifierRole.MENTION, iid="i"):
    return NS(claim_relation=rel, date=date, source_title=title,
              lexical_form_seen="phrasing", exact_quote="", evidence="orig",
              instance_id=iid, source_url=f"https://example.com/{iid}",
              confidence=0.8, amplifier_role=role, role_evidence="",
              date_precision="")


def _record(log, status=GenealogyStatus.EARLIEST_FOUND, anchor=""):
    return NS(status=status, first_attested_date=anchor or (log[0].date if log else ""),
              first_attested_source="", primary_origin_id="",
              attestation_confidence=0.9, attestation_log=log)


def _fp(lex_log, con_log, ref):
    return NS(lexical=NS(canonical_phrase="claim"),
              conceptual=NS(referent=ref),
              genealogy=NS(lexical=_record(lex_log), conceptual=_record(con_log)))


def test_bound_pass_demotes_and_reanchors():
    fp = _fp([_inst("1979-04-02", iid="a"), _inst("2026-10-07", iid="b")], [],
             {"description": "the incident", "date": "2026-10-02"})
    assert _referent_bound_pass(fp) == 1
    lex = fp.genealogy.lexical
    assert lex.attestation_log[0].claim_relation == "related-context"
    assert "[auto: predates this claim's referent" in lex.attestation_log[0].evidence
    assert lex.first_attested_date == "2026-10-07"
    assert lex.status == GenealogyStatus.EARLIEST_FOUND


def test_bound_pass_all_demoted_surrenders_anchor():
    # The Milo shape: a referent-specific claim with no same-referent
    # rearticulation gets an honestly empty lineage, never a precedent anchor.
    fp = _fp([], [_inst("1976-01-01", iid="a"), _inst("1992-01-01", iid="b")],
             {"description": "a 2026 incident", "date": "2026-07-01"})
    assert _referent_bound_pass(fp) == 2
    con = fp.genealogy.conceptual
    assert con.first_attested_date == ""
    assert con.status == GenealogyStatus.UNKNOWN
    assert con.attestation_confidence == 0.0


def test_bound_pass_inert_without_usable_date():
    fp = _fp([_inst("1979-04-02")], [], {"description": "tradition", "date": ""})
    assert _referent_bound_pass(fp) == 0
    assert fp.genealogy.lexical.attestation_log[0].claim_relation == ""


def test_bound_pass_skips_existing_context():
    fp = _fp([_inst("1979-04-02", rel="related-context")], [],
             {"description": "x", "date": "2026-01-01"})
    assert _referent_bound_pass(fp) == 0


# --- the invariant, dict form (corpus sweep) --------------------------------

def _trace(log, status="earliest-found", anchor=None):
    return {"genealogy": {"lexical": {"attestation_log": [], "status": "unknown",
                                      "first_attested_date": ""},
                          "conceptual": {"attestation_log": log, "status": status,
                                         "first_attested_date": anchor or (
                                             log[0]["date"] if log else ""),
                                         "attestation_confidence": 0.9,
                                         "primary_origin_id": "old"}}}


def _dinst(date, rel="", role="mention", iid="i"):
    return {"claim_relation": rel, "date": date, "source_title": "t",
            "lexical_form_seen": "p", "exact_quote": "", "evidence": "orig",
            "instance_id": iid, "source_url": "https://e.com", "confidence": 0.8,
            "amplifier_role": role}


def test_dict_pass_demotes_reanchors_and_rederives_stats():
    t = _trace([_dinst("1952-01-01", iid="a"), _dinst("1996-01-05", iid="b")])
    demoted, bits, details = bound_pass_dict(
        t, {"description": "the program", "date": "1996-01-05"})
    con = t["genealogy"]["conceptual"]
    assert demoted == 1 and len(details) == 1
    assert con["first_attested_date"] == "1996-01-05"
    assert con["primary_origin_id"] == "b"
    assert con["timeline_stats"]["active_from"] == "1996-01-05"


def test_dict_pass_clears_stale_anchor_on_context_only_record():
    # The mayor shape: every entry already related-context but the record
    # still claimed an anchor.
    t = _trace([_dinst("2025-01-06", rel="related-context")], anchor="2025-01-06")
    demoted, bits, details = bound_pass_dict(
        t, {"description": "the video", "date": "2026-09-01"})
    con = t["genealogy"]["conceptual"]
    assert demoted == 0
    assert "anchor-cleared" in bits
    assert con["first_attested_date"] == "" and con["status"] == "unknown"


def test_dict_pass_idempotent_on_clean_trace():
    t = _trace([_dinst("1986-10-01", iid="a")])
    before = str(t)
    demoted, bits, details = bound_pass_dict(
        t, {"description": "the film", "date": "1986-06-21"})
    assert demoted == 0 and bits == "" and str(t) == before


# --- resolver digest sampling -----------------------------------------------

def test_digest_excludes_context_and_keeps_spread():
    lex = [_inst(f"20{10 + i:02d}-01-01", iid=str(i)) for i in range(20)]
    lex[0].claim_relation = "related-context"
    fp = _fp(lex, [_inst("", iid="undated")], {})
    digest = _referent_instance_digest(fp, cap=12)
    assert len(digest) == 12
    dates = [d["date"] for d in digest]
    assert "2010-01-01" not in dates          # related-context excluded
    assert dates[0] == "2011-01-01"           # earliest half kept
    assert dates[-1] == "2029-01-01"          # latest half kept (the current wave)
    assert all(d["date"] for d in digest)     # undated excluded


# --- misc deterministic guards ----------------------------------------------

def test_is_recent_date():
    from datetime import date, timedelta
    assert _is_recent_date((date.today() - timedelta(days=3)).isoformat())
    assert not _is_recent_date("1999-01-01")
    assert not _is_recent_date("not a date")


def test_weak_event_three_sides():
    from publish import is_weak_event
    fr_ok = {"carriers": [{"verified": True, "verification_status": "url-ok"},
                          {"verified": False, "verification_status": "url-error"}]}
    fr_unchecked = {"carriers": [{"verified": False, "verification_status": "unchecked"}] * 3}
    fr_failed = {"carriers": [{"verified": False, "verification_status": "url-error"}] * 3}
    assert is_weak_event({"framings": [fr_ok]})                      # <2 framings trips
    assert not is_weak_event({"framings": [fr_ok, fr_ok]})           # verified passes
    # never-checked is unknown, not bad (the 28-event June lesson):
    assert not is_weak_event({"framings": [fr_unchecked, fr_unchecked]})
    # checked-and-all-failed trips:
    assert is_weak_event({"framings": [fr_failed, fr_failed]})


def test_event_date_bound_rule():
    from apply_event_dates import bound_event_date
    from datetime import date
    cov = [date(2026, 6, 1), date(2026, 6, 9)]
    # retrospective events keep their stated dates (the Iran-strike lesson)
    d, basis = bound_event_date("2024-04-14", cov)
    assert d == "2024-04-14" and "within coverage horizon" in basis
    # a date AFTER latest coverage is impossible -> earliest-coverage bound
    d, basis = bound_event_date("2026-08-01", cov)
    assert d == "2026-06-01" and "earliest coverage" in basis
    # year precision pads to its earliest day and survives
    d, basis = bound_event_date("2024", cov)
    assert d == "2024-01-01" and "year precision" in basis
    # no carriers: stated date passes through; garbage yields empty
    assert bound_event_date("2025-01-02", [])[0] == "2025-01-02"
    assert bound_event_date("not a date", [])[0] == ""


def test_weak_trace_both_sides():
    # BOTH sides, per the cards-guard lesson: the trip case AND the case that
    # must sail through.
    weak = {"genealogy": {"lexical": {"status": "diffuse", "attestation_confidence": 0.35},
                          "conceptual": {"status": "unknown"}}}
    strong = {"genealogy": {"lexical": {"status": "multiple-independent",
                                        "attestation_confidence": 0.82},
                            "conceptual": {"status": "unknown"}}}
    assert is_weak_trace(weak)
    assert not is_weak_trace(strong)  # emptied-conceptual traces stay listed


# --- X snowflake dating (the Talarico calibration's receipts) ---------------

def test_snowflake_decodes_known_ids():
    # Ground-truth ids from the 2026-10-18 calibration, decoded and confirmed
    # against the Guardian's reconstruction timeline.
    assert str(_x_snowflake_date("2107921836717551858")) == "2026-10-07"
    assert str(_x_snowflake_date("2108313243382952207")) == "2026-10-08"
    assert str(_x_snowflake_date("2108553958696399249")) == "2026-10-09"
    assert _x_snowflake_date("[October-9-2026-post]") is None   # placeholder
    assert _x_snowflake_date("12345") is None                   # pre-snowflake


def test_snowflake_pass_corrects_and_flags():
    wrong_date = _inst("2026-10-10", iid="a")
    wrong_date.source_url = "https://x.com/RealAmVoice/status/2108313243382952207"
    placeholder = _inst("2026-10-09", iid="b")
    placeholder.source_url = "https://x.com/spencerpratt/status/[October-9-2026-post]"
    placeholder.confidence = 0.95
    not_x = _inst("2026-10-09", iid="c")  # non-X URL untouched
    touched = _snowflake_sanity_pass([wrong_date, placeholder, not_x])
    assert touched == 2
    assert wrong_date.date == "2026-10-08" and "snowflake" in wrong_date.evidence.lower()
    assert placeholder.confidence == 0.4 and "placeholder" in placeholder.evidence
    assert not_x.date == "2026-10-09" and not_x.evidence == "orig"


def test_snowflake_pass_accepts_correct_dates_silently():
    ok = _inst("2026-10-08", iid="a")
    ok.source_url = "https://x.com/RealAmVoice/status/2108313243382952207"
    assert _snowflake_sanity_pass([ok]) == 0
    assert ok.evidence == "orig"


# --- embed recovery (the Hormuz case, 2026-10-19) ----------------------------

def _ph(date, handle, cited="https://example.news/article", iid="p"):
    i = _inst(date, iid=iid)
    i.source_url = f"https://twitter.com/{handle}/status/[embedded-in-downstream-articles]"
    i.cited_via = cited
    return i


def test_embed_recovery_sole_match():
    inst = _ph("2026-10-14", "ME_Observer_")
    html = '<blockquote><a href="https://twitter.com/ME_Observer_/status/2110476968571388238">post</a></blockquote>'
    assert _embed_recovery_pass([inst], fetch=lambda u: html) == 1
    assert inst.source_url == "https://x.com/ME_Observer_/status/2110476968571388238"
    assert "Embed-recovery pass" in inst.evidence


def test_embed_recovery_disambiguates_by_date():
    inst = _ph("2026-10-14", "HormuzLetter")
    html = ('x.com/HormuzLetter/status/2109708749044887703 '   # 2026-10-12
            'x.com/HormuzLetter/status/2110380717385318482')   # 2026-10-14
    assert _embed_recovery_pass([inst], fetch=lambda u: html) == 1
    assert inst.source_url.endswith("/2110380717385318482")


def test_embed_recovery_refuses_ambiguity_and_absence():
    # two same-handle embeds, NEITHER matching the entry date -> refuse
    a = _ph("2026-10-01", "HormuzLetter", iid="a")
    html = ('x.com/HormuzLetter/status/2109708749044887703 '
            'x.com/HormuzLetter/status/2110380717385318482')
    assert _embed_recovery_pass([a], fetch=lambda u: html) == 0
    assert "[embedded-in-downstream-articles]" in a.source_url
    # handle not embedded at all -> refuse
    b = _ph("2026-10-14", "SomeoneElse", iid="b")
    assert _embed_recovery_pass([b], fetch=lambda u: html) == 0
    # unfetchable article -> refuse
    c = _ph("2026-10-14", "ME_Observer_", iid="c")
    assert _embed_recovery_pass([c], fetch=lambda u: None) == 0


def test_embed_recovery_ignores_real_urls():
    inst = _inst("2026-10-14", iid="r")
    inst.source_url = "https://x.com/RealAmVoice/status/2108313243382952207"
    inst.cited_via = "https://example.news/a"
    calls = []
    assert _embed_recovery_pass([inst], fetch=lambda u: calls.append(u)) == 0
    assert not calls  # a decoding id is never a placeholder; no fetch happens


# --- one crown per lineage ----------------------------------------------------

def test_single_originator_earliest_keeps_crown():
    a = _inst("2026-10-08", role=AmplifierRole.ORIGINATOR, iid="rav")
    b = _inst("2026-10-09", role=AmplifierRole.ORIGINATOR, iid="pratt")
    c = _inst("2026-10-08", rel="related-context", role=AmplifierRole.ORIGINATOR, iid="ctx")
    assert _single_originator_pass([a, b, c]) == 1
    assert a.amplifier_role == AmplifierRole.ORIGINATOR
    assert b.amplifier_role == AmplifierRole.EARLY_AMPLIFIER
    assert "at most one evidenced originator" in b.role_evidence
    assert c.amplifier_role == AmplifierRole.ORIGINATOR  # context never judged here


def test_single_originator_tie_breaks_on_confidence():
    a = _inst("2026-10-08", role=AmplifierRole.ORIGINATOR, iid="a"); a.confidence = 0.97
    b = _inst("2026-10-08", role=AmplifierRole.ORIGINATOR, iid="b"); b.confidence = 0.95
    _single_originator_pass([b, a])
    assert a.amplifier_role == AmplifierRole.ORIGINATOR
    assert b.amplifier_role == AmplifierRole.EARLY_AMPLIFIER


def test_single_originator_lone_crown_untouched():
    a = _inst("1940-01-01", role=AmplifierRole.ORIGINATOR, iid="ministry")
    assert _single_originator_pass([a, _inst("1941-01-01", iid="b")]) == 0
    assert a.amplifier_role == AmplifierRole.ORIGINATOR


# --- examiner-title regex: function words only, never stance words -----------

def test_examiner_title_forces_critic():
    inst = _inst("2026-10-11", role=AmplifierRole.MASS_AMPLIFIER, iid="fc")
    inst.source_title = "Fact-check: did James Talarico enter an Ohio facility?"
    _role_sanity_pass([inst])
    assert inst.amplifier_role == AmplifierRole.CRITIC


def test_deny_titles_are_NOT_auto_flipped():
    # Deliberate revert (2026-10-19): on an accusation-claim, denial coverage
    # CONFIRMS the accusation exists — stance depends on claim shape, which a
    # title regex cannot know. The Platner sweep caught the counterexample.
    inst = _inst("2026-07-06", role=AmplifierRole.EARLY_AMPLIFIER, iid="pl")
    inst.source_title = "Graham Platner denies sexual assault allegations published this week"
    _role_sanity_pass([inst])
    assert inst.amplifier_role == AmplifierRole.EARLY_AMPLIFIER


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception:
            failed += 1
            print(f"  [FAIL] {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} tests pass")
    sys.exit(1 if failed else 0)
