"""
Share cards — the Phase 2a artifact (claim age leads)
=====================================================
One card per traced narrative. The card's job is a single number — claim age
("first attested 2002") — with everything else a tap-through into the full
trace. Three renderings of the same design, per the approved mockup
(mockups/design-mockups.html, Mockup 1):

    gallery/cards/<fp_id>.png    1200x630 OG image, so links unfurl in chats,
                                 on Bluesky, and anywhere else that reads
                                 OpenGraph tags (rendered with Pillow — free,
                                 no browser, no API)
    gallery/cards/<fp_id>.html   the share page those tags live on: the card,
                                 the mini flow visual (origin -> circles ->
                                 today), and the tap-through links into the
                                 full trace and the event it surfaced in
    gallery/traces/<fp_id>.json  the standalone trace published so the
                                 tap-through has somewhere to land
                                 (fingerprints/ itself is gitignored)

Usage:
    python cards.py                      # cards for every event-linked trace
    python cards.py --fp df7fa8c0dad4    # explicit fingerprint id(s)
    python cards.py --as-of 2026-07-10   # pin the age anchor (default: today)
    git add gallery/ && git commit && git push   # live

Which traces get a card, by default: fingerprints attached to a published
event's framing intersection with basis "linked" — the trace's own
fingerprint_id, not an embedding lead. An embedding_lead attachment is a
candidate, never a confirmed identity (Decision Log 2026-07-09), so it cannot
headline a share card. Explicit --fp overrides accept any traced fingerprint.

Language rules (METHODOLOGY Principles 1-4): no verdicts — the headline is a
structural fact (age of the earliest attestation we found); the hedge
"earliest found, not provably first" ships on the image itself, not just the
page; circle chips name only outlets whose linked page is under their own
domain (the own-voice rider's conservative cousin: a WSJ editorial quoted via
the Daily Beast is not a left-circle carrier chip).
"""

import argparse
import calendar
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from publish import build_search_index

ROOT = Path(__file__).resolve().parent
SITE = "https://tarekelgindy.github.io/tributary/"
REPO_URL = "https://github.com/tarekelgindy/tributary"

# ---------------------------------------------------------------------------
# dates & ages
# ---------------------------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def parse_date_conservative(s):
    """'YYYY[-MM[-DD]]' -> date, rounding missing precision LATE (a year-only
    attestation becomes Dec 31), so a claim-age headline can only understate
    the age, never inflate it. Returns None if unparseable or BCE."""
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(s or ""))
    if not m:
        return None
    y = int(m.group(1))
    if m.group(2):
        mo = min(12, max(1, int(m.group(2))))
        d = int(m.group(3)) if m.group(3) else calendar.monthrange(y, mo)[1]
    else:
        mo, d = 12, 31
    try:
        return date(y, mo, min(d, calendar.monthrange(y, mo)[1]))
    except ValueError:
        return None


def fmt_date_human(s):
    """Honest display precision: a first-of-month/year date collapses to the
    month/year (we can't distinguish 'attested Sep 1' from month-only
    knowledge, and showing less precision is never wrong)."""
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(s or ""))
    if not m:
        return str(s or "")
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if not mo or (mo == "01" and (not d or d == "01")):
        return y
    if not d or d == "01":
        return f"{MONTHS[int(mo) - 1]} {y}"
    return f"{MONTHS[int(mo) - 1]} {int(d)}, {y}"


def age_parts(first, asof):
    """(number, unit) for the headline, floored — '23 years', never '24' at
    23.9. Under 2 months -> days; under 2 years -> months."""
    days = (asof - first).days
    if days < 0:
        days = 0
    if days < 61:
        return days, "day" + ("" if days == 1 else "s")
    months = (asof.year - first.year) * 12 + (asof.month - first.month)
    if asof.day < first.day:
        months -= 1
    if months < 24:
        return months, "month" + ("" if months == 1 else "s")
    years = months // 12
    return years, "year" + ("" if years == 1 else "s")


# ---------------------------------------------------------------------------
# outlet identity (conservative, mirrors the viewer's viaChip)
# ---------------------------------------------------------------------------

# Canonical outlet -> domain pairs for names that don't contain their domain
# label. Everything else is judged by name-token vs host-label overlap; when
# unsure we EXCLUDE the chip — a wrong circle chip is worse than a missing one
# (the 1a membership-conservatism decision, applied to presentation).
DOMAIN_OUTLETS = {
    "nytimes.com": "The New York Times", "washingtonpost.com": "The Washington Post",
    "wsj.com": "The Wall Street Journal", "cnn.com": "CNN", "foxnews.com": "Fox News",
    "nbcnews.com": "NBC News", "cbsnews.com": "CBS News", "abcnews.go.com": "ABC News",
    "go.com": "ABC News", "apnews.com": "Associated Press", "reuters.com": "Reuters",
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "npr.org": "NPR", "pbs.org": "PBS NewsHour",
    "aljazeera.com": "Al Jazeera", "time.com": "TIME", "axios.com": "Axios",
    "politico.com": "Politico", "thehill.com": "The Hill",
    "theguardian.com": "The Guardian", "nypost.com": "New York Post",
    "usatoday.com": "USA Today", "bloomberg.com": "Bloomberg", "forbes.com": "Forbes",
    "cnbc.com": "CNBC", "nationalreview.com": "National Review",
    "washingtonexaminer.com": "Washington Examiner",
    "thegatewaypundit.com": "The Gateway Pundit", "alternet.org": "Alternet",
    "newsweek.com": "Newsweek", "huffpost.com": "HuffPost",
}

_norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def host_outlet(url):
    """Canonical outlet name for a URL's host, or '' when unknown."""
    m = re.match(r"https?://([^/]+)", str(url or ""))
    if not m:
        return ""
    host = re.sub(r"^www\.", "", m.group(1).lower())
    return DOMAIN_OUTLETS.get(host, "") or DOMAIN_OUTLETS.get(".".join(host.split(".")[-2:]), "")


def outlet_is_first_party(name, url):
    """True only when the quote's URL is confidently under the named outlet's
    own domain. A relayed quote (WSJ editorial hosted on thedailybeast.com)
    fails and is excluded from circle chips."""
    m = re.match(r"https?://([^/]+)", str(url or ""))
    if not m or not name:
        return False
    host = re.sub(r"^www\.", "", m.group(1).lower())
    n = _norm(name)
    reg2 = ".".join(host.split(".")[-2:])
    canon = _norm(DOMAIN_OUTLETS.get(host, "") or DOMAIN_OUTLETS.get(reg2, ""))
    if canon and (n in canon or canon in n):
        return True
    labels = [l for l in host.split(".")
              if len(l) > 2 and l not in ("com", "org", "net", "gov", "www")]
    return any(l in n or n in l for l in labels)


# ---------------------------------------------------------------------------
# subject collection: which traces get a card
# ---------------------------------------------------------------------------

def _intersection_rows(ev):
    fi = (ev.get("common_ground") or {}).get("framing_intersection") or {}
    rows = fi.get("intersections") if isinstance(fi, dict) else fi
    return rows or []


def collect_subjects(gallery_dir, fingerprints_dir, only_ids=None):
    """Join fingerprint -> (event, intersection row) across published events.
    Default set: claim_age attachments with basis 'linked'. Returns
    {fp_id: {fp, event, row}}; event/row are None for --fp traces that no
    published event links."""
    subjects = {}
    for p in sorted((gallery_dir / "events").glob("*.json")):
        try:
            ev = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        embedded = {f.get("fingerprint_id"): f for f in ev.get("fingerprints") or []}
        for row in _intersection_rows(ev):
            ca = row.get("claim_age") or {}
            fid = ca.get("fingerprint_id") or ""
            if not fid or ca.get("basis") != "linked":
                continue
            if only_ids is not None and fid not in only_ids:
                continue
            standalone = fingerprints_dir / f"{fid}.json"
            fp = None
            if standalone.exists():
                fp = json.loads(standalone.read_text(encoding="utf-8"))
            elif fid in embedded:
                fp = embedded[fid]
            if not fp:
                continue
            # Keep the newest-created event if several link the same trace.
            prev = subjects.get(fid)
            if prev and (prev["event"].get("created_at") or "") >= (ev.get("created_at") or ""):
                continue
            subjects[fid] = {"fp": fp, "event": ev, "row": row}
    if only_ids is not None:
        for fid in only_ids:
            if fid in subjects:
                continue
            standalone = fingerprints_dir / f"{fid}.json"
            if standalone.exists():
                subjects[fid] = {"fp": json.loads(standalone.read_text(encoding="utf-8")),
                                 "event": None, "row": None}
            else:
                print(f"[cards] --fp {fid}: no fingerprints/{fid}.json and no "
                      f"linked event — skipped", file=sys.stderr)
    return subjects


def card_data(fid, subject, asof):
    """Everything both renderers need, or None when the trace has no dated
    attestation (no age -> no card; the artifact IS the number)."""
    fp, ev, row = subject["fp"], subject["event"], subject["row"]
    gen = fp.get("genealogy") or {}
    lineage, gl = None, None
    for lin in ("lexical", "conceptual"):  # phrasing preferred; idea fallback
        cand = gen.get(lin) or {}
        if cand.get("first_attested_date"):
            lineage, gl = lin, cand
            break
    if not gl:
        print(f"[cards] {fid}: no dated lineage — skipped", file=sys.stderr)
        return None
    first_raw = gl["first_attested_date"]
    first = parse_date_conservative(first_raw)
    if not first:
        print(f"[cards] {fid}: unparseable first_attested_date {first_raw!r} "
              f"— skipped", file=sys.stderr)
        return None

    log = [i for i in gl.get("attestation_log") or []
           if (i.get("claim_relation") or "") != "related-context"]
    dated = sorted((i for i in log if parse_date_conservative(i.get("date"))),
                   key=lambda i: parse_date_conservative(i.get("date")))
    first_inst = dated[0] if dated else {}
    last_inst = dated[-1] if dated else {}
    # origin label: author, else the outlet the dated URL belongs to, else the
    # source title (an authorless article shouldn't headline as its own title)
    who = (first_inst.get("author") or host_outlet(first_inst.get("source_url"))
           or first_inst.get("source_title") or "").strip()

    n, unit = age_parts(first, asof)
    noun = "narrative" if lineage == "lexical" else "idea"

    # circles: asserting outlets from the intersection row, first-party only
    circles = []
    for cname, cdata in ((row or {}).get("per_circle") or {}).items():
        outlets, seen = [], set()
        for q in cdata.get("quotes") or []:
            o = (q.get("outlet") or "").strip()
            if not o or o in seen or not outlet_is_first_party(o, q.get("url")):
                continue
            seen.add(o)
            outlets.append({"name": o, "date": q.get("date") or ""})
        circles.append({"circle": cname, "outlets": outlets,
                        "n_pieces": cdata.get("n_pieces", 0),
                        "n_total": cdata.get("n_total", 0)})
    circles = [c for c in circles if c["outlets"]]

    event = None
    if ev:
        # today-node date: event_date, else the freshest circle quote, else
        # the analysis' own creation date
        ed = ev.get("event_date") or ""
        if not ed:
            qd = [q.get("date") or "" for c in ((row or {}).get("per_circle") or {}).values()
                  for q in c.get("quotes") or []]
            ed = max(qd) if any(qd) else (ev.get("created_at") or "")[:10]
        event = {"id": ev.get("analysis_id") or "", "title": ev.get("event") or "",
                 "date": ed, "framing": (row or {}).get("framing") or ""}

    return {
        "fingerprint_id": fid,
        "phrase": (fp.get("lexical") or {}).get("canonical_phrase") or "",
        "lineage": lineage,
        "headline": f"This {noun} is {n} {unit} old.",
        "age_text": f"{n} {unit}",
        "first_raw": first_raw,
        "first_human": fmt_date_human(first_raw),
        "first_who": who,
        "confidence": gl.get("attestation_confidence") or 0.0,
        "n_uses": len(log),
        "last_human": fmt_date_human(last_inst.get("date") or ""),
        "last_who": (last_inst.get("author") or last_inst.get("source_title") or "").strip(),
        "circles": circles,
        "event": event,
        "asof": asof.isoformat(),
    }


# ---------------------------------------------------------------------------
# PNG renderer (the OG image)
# ---------------------------------------------------------------------------

W, H = 1200, 630
INK1, INK2, INK3 = (11, 11, 11), (82, 81, 78), (137, 135, 129)
PAPER, CARD_BG = (249, 249, 247), (252, 252, 251)
GRID, BASELINE = (225, 224, 217), (195, 194, 183)
BLUE, BLUE_DEEP = (42, 120, 214), (24, 79, 149)


def _font(size, weight="regular"):
    names = {"bold": ["segoeuib.ttf", "arialbd.ttf"],
             "semibold": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"],
             "regular": ["segoeui.ttf", "arial.ttf"]}[weight]
    for n in names:
        for base in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype")):
            f = base / n
            if f.exists():
                return ImageFont.truetype(str(f), size)
    return ImageFont.load_default(size)


def _fit(draw, text, weight, size, max_w, min_size=18):
    while size > min_size:
        f = _font(size, weight)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(min_size, weight)


def _ellipsize(draw, text, font, max_w):
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1].rstrip()
    return text + "…"


def _wrap(draw, text, font, max_w, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                lines[-1] = _ellipsize(draw, lines[-1] + " …", font, max_w)
                return lines
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _arrow(draw, x1, x2, y):
    draw.line([x1, y, x2 - 10, y], fill=BASELINE, width=3)
    draw.polygon([(x2, y), (x2 - 14, y - 7), (x2 - 14, y + 7)], fill=BASELINE)


def _node_date(draw, x, y, w, big, small, who=None):
    f_big = _fit(draw, big, "bold", 40, w)
    draw.text((x, y), big, font=f_big, fill=INK1)
    f_small = _font(21)
    draw.text((x, y + 52), small, font=f_small, fill=INK3)
    if who:
        draw.text((x, y + 80), _ellipsize(draw, who, f_small, w), font=f_small, fill=INK2)


def render_png(cd, out_path):
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([24, 24, W - 24, H - 24], radius=18, fill=CARD_BG,
                        outline=GRID, width=1)
    ML, MR = 70, W - 70   # content margins
    cw = MR - ML

    kicker = "T R I B U T A R Y   ·   N A R R A T I V E   T R A C E"
    d.text((ML, 58), kicker, font=_font(22, "semibold"), fill=INK3)

    f_head = _fit(d, cd["headline"], "bold", 78, cw)
    d.text((ML, 100), cd["headline"], font=f_head, fill=INK1)

    f_phrase = _font(31)
    py = 215
    for ln in _wrap(d, "“" + cd["phrase"] + "”", f_phrase, cw, 2):
        d.text((ML, py), ln, font=f_phrase, fill=INK2)
        py += 42

    # --- mini flow strip: origin -> circles (or spread) -> today ------------
    top = 330
    x_origin, x_mid, x_today = ML, 430, 870
    _node_date(d, x_origin, top, 300,
               cd["first_human"], "first attested — earliest we found",
               cd["first_who"] or None)

    if cd["circles"]:
        y = top - 4
        f_o = _font(21)
        for c in cd["circles"]:
            names = ", ".join(o["name"] for o in c["outlets"][:2])
            chip = _ellipsize(d, f'{c["circle"]} · {names}', f_o, 330)
            bb = d.textbbox((x_mid, y), chip, font=f_o)
            d.rounded_rectangle([x_mid - 14, y - 7, bb[2] + 14, y + 33],
                                radius=17, outline=GRID, width=2)
            d.text((x_mid, y), chip, font=f_o, fill=INK1)
            y += 56
        d.text((x_mid, y + 2), "carried in both circles (AllSides-rated)",
               font=_font(19), fill=INK3)
    else:
        _node_date(d, x_mid, top, 330, f'{cd["n_uses"]} recorded uses',
                   "spread " + cd["first_human"] + " → " + (cd["last_human"] or "present"))

    today = cd["event"] or {}
    _node_date(d, x_today, top, MR - x_today,
               fmt_date_human(today.get("date") or "") or "today",
               "in the news",
               (today.get("title") or "").strip() or None)

    ay = top + 24
    _arrow(d, x_origin + 310, x_mid - 26, ay)
    _arrow(d, x_mid + 350, x_today - 26, ay)

    # --- footer: count + the hedge, on the image itself. The first-attested
    # date already headlines the flow strip; repeating it here cost the
    # honesty clause its space (it got ellipsized) — the hedge wins.
    d.line([ML, 545, MR, 545], fill=GRID, width=2)
    foot = (f'{cd["n_uses"]} recorded uses · earliest found, not provably first'
            f' · AI-traced, not human-reviewed')
    d.text((ML, 560), _ellipsize(d, foot, _font(22), cw - 190),
           font=_font(22), fill=INK3)
    wordmark = "tributary"
    f_wm = _font(24, "semibold")
    d.text((MR - d.textlength(wordmark, font=f_wm), 558), wordmark,
           font=f_wm, fill=BLUE_DEEP)

    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# share page (the tap-through artifact)
# ---------------------------------------------------------------------------

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg_flow(cd):
    """The mini flow visual, as a standalone inline SVG: origin -> circles ->
    today. Deliberately screenshot-shaped; every fact on it also appears in
    the page text for copy-paste and screen readers."""
    today = cd["event"] or {}
    today_date = fmt_date_human(today.get("date") or "") or "today"
    today_title = (today.get("title") or "").strip()

    def trunc(s, n):
        return s if len(s) <= n else s[:n - 1].rstrip() + "…"

    parts = [f'<svg viewBox="0 0 800 200" role="img" aria-label="Flow: first attested '
             f'{esc(cd["first_human"])}, carried into circles, in the news {esc(today_date)}">']
    parts.append('<style>.d{font:600 22px system-ui}.s{font:13px system-ui;fill:#898781}'
                 '.w{font:13.5px system-ui;fill:#52514e}.c{font:600 13.5px system-ui;fill:#0b0b0b}'
                 '</style>')

    def arrow(x1, x2, y):
        parts.append(f'<line x1="{x1}" y1="{y}" x2="{x2 - 8}" y2="{y}" stroke="#c3c2b7" stroke-width="2"/>'
                     f'<path d="M {x2} {y} l -11 -5.5 v 11 z" fill="#c3c2b7"/>')

    # origin
    parts.append(f'<text x="0" y="86" class="d" fill="#0b0b0b">{esc(cd["first_human"])}</text>'
                 f'<text x="0" y="108" class="s">first attested — earliest we found</text>')
    if cd["first_who"]:
        parts.append(f'<text x="0" y="128" class="w">{esc(trunc(cd["first_who"], 34))}</text>')

    if cd["circles"]:
        y = 62
        for c in cd["circles"]:
            names = ", ".join(o["name"] for o in c["outlets"][:2])
            chip = trunc(f'{c["circle"]} · {names}', 40)
            wpx = int(len(chip) * 7.4) + 24
            parts.append(f'<rect x="268" y="{y - 20}" width="{wpx}" height="30" rx="15" '
                         f'fill="none" stroke="#e1e0d9" stroke-width="1.5"/>'
                         f'<text x="280" y="{y}" class="c">{esc(chip)}</text>')
            y += 44
        parts.append(f'<text x="268" y="{y + 4}" class="s">carried in both circles (AllSides-rated)</text>')
    else:
        parts.append(f'<text x="268" y="86" class="d" fill="#0b0b0b">{cd["n_uses"]} recorded uses</text>'
                     f'<text x="268" y="108" class="s">spread {esc(cd["first_human"])} → '
                     f'{esc(cd["last_human"] or "present")}</text>')

    parts.append(f'<text x="660" y="86" class="d" fill="#0b0b0b">{esc(today_date)}</text>'
                 f'<text x="660" y="108" class="s">in the news</text>')
    if today_title:
        parts.append(f'<text x="660" y="128" class="w">{esc(trunc(today_title, 17))}</text>')

    arrow(204, 256, 80)
    arrow(608, 648, 80)
    parts.append("</svg>")
    return "".join(parts)


def render_page(cd, out_path):
    fid = cd["fingerprint_id"]
    trace_href = f"../../fingerprint_viewer.html?load=gallery/traces/{fid}.json"
    ev = cd["event"]
    og_img = f"{SITE}gallery/cards/{fid}.png"
    og_url = f"{SITE}gallery/cards/{fid}.html"
    desc_bits = [f"“{cd['phrase']}” — first attested {cd['first_human']}.",
                 f"{cd['n_uses']} recorded uses."]
    if cd["circles"]:
        desc_bits.append("Carried by outlets in more than one circle.")
    desc_bits.append("Every date and source has a receipt. AI-traced, not human-reviewed.")
    og_desc = " ".join(desc_bits)

    circles_note = ""
    if cd["circles"]:
        rows = []
        for c in cd["circles"]:
            names = ", ".join(f'{o["name"]}' + (f' ({fmt_date_human(o["date"])})' if o["date"] else "")
                              for o in c["outlets"])
            rows.append(f'<strong>{esc(c["circle"])}</strong>: {esc(names)} '
                        f'— {c["n_pieces"]}/{c["n_total"]} pieces on this event')
        circles_note = ('<p class="circles">' + " · ".join(rows) +
                        '. Circle membership is AllSides’ rating of the outlet, not ours.</p>')

    event_link = ""
    if ev and ev.get("id"):
        t = ev["title"]
        if len(t) > 90:  # cut on a word boundary, honestly marked
            t = t[:90].rsplit(" ", 1)[0] + " …"
        event_link = (f'<a class="ctx" href="../../fingerprint_viewer.html?load=gallery/events/{esc(ev["id"])}.json">'
                      f'See it in the news: {esc(t)} →</a>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(cd["headline"])} — Tributary</title>
<meta property="og:type" content="article">
<meta property="og:site_name" content="Tributary">
<meta property="og:title" content="{esc(cd["headline"])}">
<meta property="og:description" content="{esc(og_desc)}">
<meta property="og:image" content="{esc(og_img)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(cd["headline"])} “{esc(cd["phrase"])}” First attested {esc(cd["first_human"])}; {cd["n_uses"]} recorded uses.">
<meta property="og:url" content="{esc(og_url)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="description" content="{esc(og_desc)}">
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f9f9f7; color: #0b0b0b;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.5; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 2.2rem 1.2rem 3rem; }}
  .card {{ background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10); border-radius: 12px;
          padding: 1.4rem 1.6rem 1.1rem; box-shadow: 0 1px 3px rgba(11,11,11,0.04); }}
  .kicker {{ font-size: 0.68rem; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
            color: #898781; margin-bottom: 0.5rem; }}
  .headline {{ font-size: 1.75rem; font-weight: 650; letter-spacing: -0.01em; margin: 0 0 0.15rem; }}
  .phrase {{ color: #52514e; font-size: 0.95rem; margin: 0 0 1.1rem; }}
  .circles {{ color: #52514e; font-size: 0.85rem; margin: 0.4rem 0 0; }}
  .cardfoot {{ border-top: 1px solid #e1e0d9; margin-top: 0.9rem; padding-top: 0.65rem;
              font-size: 0.78rem; color: #898781; display: flex; justify-content: space-between;
              flex-wrap: wrap; gap: 0.4rem; }}
  .cta {{ display: inline-block; margin: 1.3rem 0 0; background: #2a78d6; color: #fff;
         text-decoration: none; font-weight: 600; font-size: 0.95rem;
         padding: 0.55rem 1.1rem; border-radius: 8px; }}
  .cta:hover {{ background: #184f95; }}
  .ctx {{ display: inline-block; margin: 1.3rem 0 0 0.9rem; color: #2a78d6; text-decoration: none;
         font-size: 0.9rem; }}
  .honesty {{ margin-top: 2.2rem; padding-top: 0.9rem; border-top: 1px solid #e1e0d9;
             color: #898781; font-size: 0.82rem; }}
  .honesty a {{ color: #2a78d6; text-decoration: none; }}
  svg {{ width: 100%; height: auto; display: block; margin: 0.4rem 0 0; }}
  @media (max-width: 640px) {{ .headline {{ font-size: 1.4rem; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="kicker">Tributary · narrative trace</div>
    <div class="headline">{esc(cd["headline"])}</div>
    <p class="phrase">“{esc(cd["phrase"])}”</p>
    {svg_flow(cd)}
    {circles_note}
    <div class="cardfoot">
      <span>{cd["n_uses"]} recorded uses · first attested {esc(cd["first_human"])} — earliest we found, not provably first</span>
      <span>AI-traced, not human-reviewed</span>
    </div>
  </div>

  <a class="cta" href="{esc(trace_href)}">See the full trace — every date, source, and receipt →</a>
  {event_link}

  <p class="honesty">
    This card is AI-generated and not yet human-reviewed. “First attested” is the earliest
    use our search found — attestation confidence as recorded by the pipeline: {cd["confidence"]:.2f} —
    and an earlier one may exist. The full trace shows every source, archive link, and
    verification status{", and the event page shows each circle’s own quoted words" if cd["circles"] else ""}.
    Card image for sharing: <a href="{fid}.png">PNG</a>.<br>
    <a href="{REPO_URL}/blob/main/METHODOLOGY.md">How it’s made</a> ·
    <a href="{REPO_URL}/blob/main/CORRECTIONS.md">Corrections log</a> ·
    <a href="{REPO_URL}/issues/new/choose">Suggest a correction</a> ·
    <a href="../../index.html">Tributary</a>
  </p>
</div>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Render share cards (OG image + share page) "
                                             "for traced narratives.")
    ap.add_argument("--fp", action="append", default=None, metavar="ID",
                    help="explicit fingerprint id (repeatable); default: every "
                         "event-linked trace in the gallery")
    ap.add_argument("--as-of", default=None,
                    help="age anchor date YYYY-MM-DD (default: today)")
    ap.add_argument("--gallery-dir", default=str(ROOT / "gallery"))
    ap.add_argument("--fingerprints-dir", default=str(ROOT / "fingerprints"))
    args = ap.parse_args()

    asof = date.fromisoformat(args.as_of) if args.as_of else date.today()
    gallery = Path(args.gallery_dir)
    fpdir = Path(args.fingerprints_dir)
    cards_dir = gallery / "cards"
    traces_dir = gallery / "traces"
    cards_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)

    subjects = collect_subjects(gallery, fpdir, set(args.fp) if args.fp else None)
    index, rendered = [], 0
    for fid, subject in sorted(subjects.items()):
        cd = card_data(fid, subject, asof)
        if not cd:
            continue
        render_png(cd, cards_dir / f"{fid}.png")
        render_page(cd, cards_dir / f"{fid}.html")
        # publish the standalone trace so the tap-through lands on it
        src = fpdir / f"{fid}.json"
        if src.exists():
            shutil.copyfile(src, traces_dir / f"{fid}.json")
        index.append({"fingerprint_id": fid, "headline": cd["headline"],
                      "phrase": cd["phrase"], "first_attested": cd["first_raw"],
                      "as_of": cd["asof"],
                      "page": f"gallery/cards/{fid}.html",
                      "image": f"gallery/cards/{fid}.png",
                      "event_id": (cd["event"] or {}).get("id", "")})
        rendered += 1
        print(f"[cards] {fid}: {cd['headline']}  ({cd['n_uses']} uses, "
              f"circles={len(cd['circles'])})", file=sys.stderr)

    (cards_dir / "index.json").write_text(
        json.dumps({"count": rendered, "as_of": asof.isoformat(), "cards": index},
                   indent=1, ensure_ascii=False), encoding="utf-8")
    build_search_index(gallery)   # new gallery/traces JSONs join the search corpus
    print(f"[cards] {rendered} cards -> {cards_dir}. "
          f"Now: git add gallery/ && git commit && git push", file=sys.stderr)


if __name__ == "__main__":
    main()
