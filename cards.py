"""
Share cards — the Phase 2a artifact (claim age leads)
=====================================================
One card per traced narrative. The card's job is a single number — claim age
("first attested 2002") — over the trace's spread-over-time shape, with
everything else a tap-through into the full trace. Three renderings of the
same design, per the approved mockup (mockups/design-mockups.html, Mockup 1;
Tarek's 2026-07-10 review restored the mockup's timeline as the card visual
and demoted the L/R framing to one text line):

    gallery/cards/<fp_id>.png    1200x630 OG image, so links unfurl in chats,
                                 on Bluesky, and anywhere else that reads
                                 OpenGraph tags (rendered with Pillow — free,
                                 no browser, no API)
    gallery/cards/<fp_id>.html   the share page those tags live on: the card
                                 with its spread-over-time timeline (each dot
                                 one recorded use; origin and in-the-news
                                 labeled) and the tap-through links into the
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
page; timeline dots are role-unlabeled (role labels are unaudited AI labels
and a card has no room for that caveat); outlets that carried the framing are
named without lean labels — who carried it is the fact, the axis is not the
focus. Carrier names appear only when the linked page is under the outlet's
own domain (the own-voice rider's conservative cousin: a WSJ editorial quoted
via the Daily Beast is not a CBS-style first-party carrier).
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


def date_frac(s):
    """Fractional year for PLOTTING (missing precision defaults early, like
    the viewer's parseDateFrac — plot position is cosmetic; the age headline
    keeps its separate conservative-late parse)."""
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(s or ""))
    if not m:
        return None
    y = int(m.group(1))
    mo = int(m.group(2) or 1)
    d = int(m.group(3) or 1)
    return y + ((mo - 1) + (d - 1) / 31) / 12


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
# unsure we EXCLUDE the name — a wrong carrier name is worse than a missing
# one (the 1a membership-conservatism decision, applied to presentation).
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
    fails and is excluded from the carried-by names."""
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
    attestation (no age, no timeline -> no card; the artifact IS the shape)."""
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
    dated = sorted((i for i in log if date_frac(i.get("date")) is not None),
                   key=lambda i: date_frac(i.get("date")))
    if not dated:
        print(f"[cards] {fid}: no dated attestations — skipped", file=sys.stderr)
        return None
    first_inst, last_inst = dated[0], dated[-1]
    # origin label: author, else the outlet the dated URL belongs to, else the
    # source title (an authorless article shouldn't headline as its own title)
    def who_of(i):
        return (i.get("author") or host_outlet(i.get("source_url"))
                or i.get("source_title") or "").strip().split(" (")[0]

    n, unit = age_parts(first, asof)
    noun = "narrative" if lineage == "lexical" else "idea"

    # who carried it around the event, names only, first-party only — the
    # cross-circle fact lives in one line of page text, not in the visual
    circles = []
    for cname, cdata in ((row or {}).get("per_circle") or {}).items():
        outlets, seen = [], set()
        for q in cdata.get("quotes") or []:
            o = (q.get("outlet") or "").strip()
            if not o or o in seen or not outlet_is_first_party(o, q.get("url")):
                continue
            seen.add(o)
            outlets.append({"name": o, "date": q.get("date") or ""})
        circles.append({"circle": cname, "outlets": outlets})
    circles = [c for c in circles if c["outlets"]]
    outlets_flat, seen = [], set()
    for c in circles:
        for o in c["outlets"]:
            if o["name"] not in seen:
                seen.add(o["name"])
                outlets_flat.append(o)

    event = None
    if ev:
        # in-the-news date: event_date, else the freshest carried quote, else
        # the analysis' own creation date
        ed = ev.get("event_date") or ""
        if not ed:
            qd = [q.get("date") or "" for c in ((row or {}).get("per_circle") or {}).values()
                  for q in c.get("quotes") or []]
            ed = max(qd) if any(qd) else (ev.get("created_at") or "")[:10]
        event = {"id": ev.get("analysis_id") or "", "title": ev.get("event") or "",
                 "date": ed, "framing": (row or {}).get("framing") or ""}

    # points carry author/outlet names only — a bare article title standing in
    # a "who" list reads as a person and misleads; unknown carriers stay silent
    points = [{"frac": date_frac(i.get("date")), "date": i.get("date") or "",
               "who": (i.get("author") or host_outlet(i.get("source_url"))
                       or "").strip().split(" (")[0]} for i in dated]
    return {
        "fingerprint_id": fid,
        "phrase": (fp.get("lexical") or {}).get("canonical_phrase") or "",
        "lineage": lineage,
        "headline": f"This {noun} is {n} {unit} old.",
        "age_text": f"{n} {unit}",
        "first_raw": first_raw,
        "first_human": fmt_date_human(first_raw),
        "first_who": who_of(first_inst),
        "confidence": gl.get("attestation_confidence") or 0.0,
        "n_uses": len(log),
        "last_human": fmt_date_human(last_inst.get("date") or ""),
        "points": points,
        "spikes": spike_clusters(points),
        "circles": circles,
        "outlets": outlets_flat,
        "event": event,
        "asof": asof.isoformat(),
    }


# ---------------------------------------------------------------------------
# timeline geometry (shared by the PNG and the SVG)
# ---------------------------------------------------------------------------

def _range_label(cluster):
    def ym(s):
        m = re.match(r"^(\d{4})(?:-(\d{1,2}))?", str(s or ""))
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else (None, None)
    y1, m1 = ym(cluster[0]["date"])
    y2, m2 = ym(cluster[-1]["date"])
    if y1 is None:
        return ""
    if y1 == y2:
        if m1 and m2:
            return f"{MONTHS[m1 - 1][:3]} {y1}" if m1 == m2 \
                else f"{MONTHS[m1 - 1][:3]}–{MONTHS[m2 - 1][:3]} {y1}"
        return str(y1)
    return f"{y1}–{y2}"


def spike_clusters(points, max_labels=2):
    """Bursts of recorded uses — the places where the discussion jumped, and
    who appears in them. Contiguous dated uses separated by less than ~4% of
    the span (min 45 days) form a cluster; the biggest ones (>=3 uses) get
    named. The origin's own cluster is skipped — it already has a label.
    Structural on purpose: names, dates, counts — no role claims."""
    span = max(points[-1]["frac"] - points[0]["frac"], 1 / 12)
    gap = max(span * 0.04, 45 / 365)
    clusters, cur = [], [points[0]]
    for p in points[1:]:
        if p["frac"] - cur[-1]["frac"] <= gap:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    spikes = []
    for c in clusters:
        if len(c) < 3 or c[0] is points[0]:
            continue
        names, seen = [], set()
        for p in c:
            if p["who"] and p["who"] not in seen:
                seen.add(p["who"])
                names.append(p["who"])
        spikes.append({"f0": c[0]["frac"], "f1": c[-1]["frac"], "n": len(c),
                       "range": _range_label(c), "names": names})
    spikes.sort(key=lambda s: -s["n"])
    return spikes[:max_labels]

def timeline_geometry(cd, x0, x1, axis_y, area_top, bucket_px=12, dot_gap=11):
    """Positions for the spread-over-time chart: cumulative step-area top
    edge, stacked dots (each = one recorded dated use), year/month ticks, and
    the in-the-news marker. Pure structure — sequence, not influence."""
    pts = cd["points"]
    today = cd["event"] or {}
    today_frac = date_frac(today.get("date") or "")
    t0 = pts[0]["frac"]
    t1 = max(pts[-1]["frac"], today_frac or pts[-1]["frac"])
    span = max(t1 - t0, 1 / 12)
    t0 -= span * 0.03
    t1 += span * 0.03
    span = t1 - t0
    X = lambda t: x0 + (t - t0) / span * (x1 - x0)

    # cumulative step outline (top edge), left to right
    n = len(pts)
    y_of = lambda c: axis_y - (c / n) * (axis_y - area_top)
    steps, prev_y = [(x0, axis_y)], axis_y
    for i, p in enumerate(pts):
        if i + 1 < n and pts[i + 1]["frac"] - p["frac"] < 1e-9:
            continue
        x = X(p["frac"])
        steps += [(x, prev_y), (x, y_of(i + 1))]
        prev_y = y_of(i + 1)
    steps.append((x1, prev_y))
    area = steps + [(x1, axis_y)]

    # dots, stacked per x-bucket (two columns when a cluster is dense)
    buckets = {}
    for i, p in enumerate(pts):
        key = round(X(p["frac"]) / bucket_px) * bucket_px
        buckets.setdefault(key, []).append(i)
    dots = []
    for key, idxs in buckets.items():
        two_col = len(idxs) > 6
        for j, i in enumerate(idxs):
            col = (4.5 if j % 2 else -4.5) if two_col else 0
            row = j // 2 if two_col else j
            dots.append({"x": X(pts[i]["frac"]) + col,
                         "y": axis_y - 13 - row * dot_gap,
                         "ms": i == 0, "p": pts[i]})

    # ticks: ~5, at nice year/month steps
    for step in (1 / 12, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100):
        if span / step <= 6:
            break
    ticks, t = [], (int(t0 / step) + 1) * step
    while t < t1 - span * 0.02:
        y, mo = int(t // 1), int(round((t % 1) * 12))
        if mo > 11:
            y, mo = y + 1, 0
        label = str(y) if step >= 1 else f"{MONTHS[mo][:3]} {y}"
        ticks.append((X(t), label))
        t += step

    marker = None
    if today_frac is not None and today_frac >= pts[-1]["frac"] - span * 0.001:
        marker = {"x": X(today_frac)}
    return {"area": area, "dots": dots, "ticks": ticks, "marker": marker, "X": X}


# ---------------------------------------------------------------------------
# PNG renderer (the OG image)
# ---------------------------------------------------------------------------

W, H = 1200, 630
INK1, INK2, INK3 = (11, 11, 11), (82, 81, 78), (137, 135, 129)
PAPER, CARD_BG = (249, 249, 247), (252, 252, 251)
GRID, BASELINE = (225, 224, 217), (195, 194, 183)
BLUE, BLUE_DEEP = (42, 120, 214), (24, 79, 149)
BLUE_WASH, BLUE_MID = (205, 226, 251), (134, 182, 239)


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


def _dashed_vline(draw, x, y1, y2, color, dash=6, gap=5, width=2):
    y = y1
    while y < y2:
        draw.line([x, y, x, min(y + dash, y2)], fill=color, width=width)
        y += dash + gap


def render_png(cd, out_path):
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([24, 24, W - 24, H - 24], radius=18, fill=CARD_BG,
                        outline=GRID, width=1)
    ML, MR = 70, W - 70   # content margins
    cw = MR - ML

    kicker = "T R I B U T A R Y   ·   N A R R A T I V E   T R A C E"
    d.text((ML, 56), kicker, font=_font(22, "semibold"), fill=INK3)

    f_head = _fit(d, cd["headline"], "bold", 74, cw)
    d.text((ML, 96), cd["headline"], font=f_head, fill=INK1)

    f_phrase = _font(30)
    py = 206
    for ln in _wrap(d, "“" + cd["phrase"] + "”", f_phrase, cw, 2):
        d.text((ML, py), ln, font=f_phrase, fill=INK2)
        py += 40

    # --- spread over time: each dot one recorded use; shading = cumulative
    # share of observed uses; sequence, not influence -----------------------
    axis_y, area_top = 500, 362   # area stays below the two chart labels
    g = timeline_geometry(cd, ML, MR, axis_y, area_top)

    d.polygon(g["area"], fill=BLUE_WASH + (150,))
    d.line(g["area"][:-1], fill=BLUE_MID, width=2)
    d.line([ML, axis_y, MR, axis_y], fill=BASELINE, width=2)
    for x, label in g["ticks"]:
        d.line([x, axis_y - 3, x, axis_y + 4], fill=BASELINE, width=2)
        f_t = _font(19)
        d.text((x - d.textlength(label, font=f_t) / 2, axis_y + 10), label,
               font=f_t, fill=INK3)

    if g["marker"]:
        _dashed_vline(d, g["marker"]["x"], area_top - 14, axis_y - 2, BASELINE)

    for dot in g["dots"]:
        r = 7 if dot["ms"] else 5
        d.ellipse([dot["x"] - r, dot["y"] - r, dot["x"] + r, dot["y"] + r],
                  fill=BLUE_DEEP if dot["ms"] else BLUE,
                  outline=CARD_BG, width=2)

    # bursts get named: who appears where the curve jumps (names + dates +
    # counts only — no role claims; roles are unaudited AI labels)
    f_sp, f_spn = _font(20, "semibold"), _font(17)
    placed = []
    for s in cd["spikes"]:
        in_range = [dot["y"] for dot in g["dots"]
                    if g["X"](s["f0"]) - 12 <= dot["x"] <= g["X"](s["f1"]) + 12]
        top = min(in_range, default=axis_y - 13)
        line1 = f'{s["n"]} uses · {s["range"]}'
        line2 = _ellipsize(d, ", ".join(s["names"][:3]) +
                           (f' +{len(s["names"]) - 3}' if len(s["names"]) > 3 else ""),
                           f_spn, 400) if s["names"] else ""
        w = max(d.textlength(line1, font=f_sp),
                d.textlength(line2, font=f_spn) if line2 else 0)
        cx = g["X"]((s["f0"] + s["f1"]) / 2)
        lx = min(max(cx - w / 2, ML), MR - w)
        if any(lx < p1 + 24 and lx + w > p0 - 24 for p0, p1 in placed):
            continue   # keep the bigger spike when labels would collide
        placed.append((lx, lx + w))
        ly = max(top - (58 if line2 else 34), area_top - 4)
        d.text((lx, ly), line1, font=f_sp, fill=INK1)
        if line2:
            d.text((lx, ly + 26), line2, font=f_spn, fill=INK2)

    # origin label (top-left of the chart, where the area is still low)
    f_ms, f_sub = _font(23, "semibold"), _font(19)
    d.text((ML, 300), f'First attested {cd["first_human"]}', font=f_ms, fill=INK1)
    if cd["first_who"]:
        d.text((ML, 332), _ellipsize(d, cd["first_who"], f_sub, 430),
               font=f_sub, fill=INK3)

    # in-the-news label (top-right), carriers named without lean labels
    today = cd["event"] or {}
    right_lbl = f'In the news {fmt_date_human(today.get("date") or "")}' if today \
        else f'Most recent use {cd["last_human"]}'
    tw = d.textlength(right_lbl, font=f_ms)
    d.text((MR - tw, 300), right_lbl, font=f_ms, fill=INK1)
    if cd["outlets"]:
        names = " · ".join(o["name"] for o in cd["outlets"][:3])
        names = _ellipsize(d, names, f_sub, 430)
        d.text((MR - d.textlength(names, font=f_sub), 332), names,
               font=f_sub, fill=INK3)

    # --- footer: count + the honesty clauses, on the image itself ----------
    d.line([ML, 546, MR, 546], fill=GRID, width=2)
    n_dots = len(cd["points"])
    counts = f'{cd["n_uses"]} recorded uses' + \
        (f' ({n_dots} dated)' if n_dots != cd["n_uses"] else '')
    f_f = _font(19)
    d.text((ML, 555), f'{counts} · each dot = one use from a cited source — a sample, not a census',
           font=f_f, fill=INK3)
    d.text((ML, 580), 'earliest found, not provably first · AI-traced, not human-reviewed',
           font=f_f, fill=INK3)
    wordmark = "tributary"
    f_wm = _font(24, "semibold")
    d.text((MR - d.textlength(wordmark, font=f_wm), 565), wordmark,
           font=f_wm, fill=BLUE_DEEP)

    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# event cards: the framing fan (Mockup B — equal cells, carrier names)
# ---------------------------------------------------------------------------

def event_card_data(ev):
    """Card data for an event analysis: the fan of competing framings.
    Equal-weight cells by design (P5 rule: carrier counts are search-bounded,
    so no sized bars — unit dots + names); sorted by carrier count, capped at
    8 cells with the remainder counted honestly."""
    framings = ev.get("framings") or []
    if len(framings) < 2:
        return None
    cells = []
    for f in framings:
        carriers = f.get("carriers") or []
        cells.append({
            "name": f.get("name") or "",
            "question": f.get("question") or "",
            "claim": f.get("key_claim") or "",
            "n": len(carriers),
            "names": [c.get("name") or "" for c in carriers if c.get("name")],
        })
    cells.sort(key=lambda c: -c["n"])
    sf = ev.get("shared_foundation") or {}
    agrees = (sf.get("summary") or "").split(". ")[0].strip()
    # the summary's own preamble is redundant after "Everyone agrees:"
    agrees = re.sub(r"^All (?:\w+ )?(?:framings|sides) (?:accept|agree)( that)?\s*",
                    "", agrees, flags=re.I)
    if agrees:
        agrees = agrees[0].upper() + agrees[1:]
        if not agrees.endswith("."):
            agrees += "."
    else:
        facts = [v.get("statement") or "" for v in sf.get("verified_facts") or []]
        agrees = " ".join(facts[:2])
    return {
        "analysis_id": ev.get("analysis_id") or "",
        "title": (ev.get("event") or "").strip(),
        "n_framings": len(framings),
        "cells": cells[:8],
        "n_hidden": max(0, len(framings) - 8),
        "agrees": agrees,
        "headline": f"{len(framings)} competing framings.",
    }


def render_event_png(cd, out_path):
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([24, 24, W - 24, H - 24], radius=18, fill=CARD_BG,
                        outline=GRID, width=1)
    ML, MR = 70, W - 70
    cw = MR - ML

    d.text((ML, 52), "T R I B U T A R Y   ·   E V E N T   A N A L Y S I S",
           font=_font(22, "semibold"), fill=INK3)

    f_head = _fit(d, cd["headline"], "bold", 64, cw)
    d.text((ML, 90), cd["headline"], font=f_head, fill=INK1)

    f_title = _font(26)
    ty = 178
    for ln in _wrap(d, cd["title"], f_title, cw, 2):
        d.text((ML, ty), ln, font=f_title, fill=INK2)
        ty += 35

    # "everyone agrees" strip — the shared foundation, then the contest
    if cd["agrees"]:
        f_a = _font(20)
        d.rounded_rectangle([ML, 256, MR, 322], radius=10, fill=(244, 243, 239))
        lines = _wrap(d, "Everyone agrees: " + cd["agrees"] +
                      " The contest is over what it means.", f_a, cw - 40, 2)
        ay = 264
        for ln in lines:
            d.text((ML + 20, ay), ln, font=f_a, fill=INK2)
            ay += 27

    # the fan: 2 x 4 equal cells — framing name over unit dots + carrier names
    f_name, f_car = _font(21, "semibold"), _font(17)
    col_w = (cw - 60) // 2
    top = 340
    for i, c in enumerate(cd["cells"]):
        x = ML + (i % 2) * (col_w + 60)
        y = top + (i // 2) * 52
        d.text((x, y), _ellipsize(d, c["name"], f_name, col_w), font=f_name, fill=INK1)
        dots_n = min(c["n"], 10)
        for j in range(dots_n):
            dx = x + j * 12
            d.ellipse([dx, y + 33, dx + 8, y + 41], fill=BLUE)
        names = ", ".join(c["names"][:2]) + \
            (f' +{len(c["names"]) - 2}' if len(c["names"]) > 2 else "")
        d.text((x + dots_n * 12 + 6, y + 27),
               _ellipsize(d, f'{c["n"]} recorded · {names}', f_car, col_w - dots_n * 12 - 6),
               font=f_car, fill=INK3)

    if cd["n_hidden"]:
        d.text((ML, top + 4 * 52 + 2), f'+ {cd["n_hidden"]} more framings',
               font=_font(17), fill=INK3)

    d.line([ML, 562, MR, 562], fill=GRID, width=2)
    f_f = _font(19)
    d.text((ML, 570), 'dots = carriers our search recorded — a floor, not a census; smaller outlets and individual voices are undercounted',
           font=f_f, fill=INK3)
    d.text((ML, 594), 'framing boundaries are AI judgments · AI-generated, not human-reviewed',
           font=f_f, fill=INK3)
    f_wm = _font(24, "semibold")
    d.text((MR - d.textlength("tributary", font=f_wm), 578), "tributary",
           font=f_wm, fill=BLUE_DEEP)

    img.save(out_path, "PNG")


def render_event_page(cd, out_path):
    aid = cd["analysis_id"]
    event_href = f"../../fingerprint_viewer.html?load=gallery/events/{aid}.json"
    og_img = f"{SITE}gallery/cards/{aid}.png"
    og_url = f"{SITE}gallery/cards/{aid}.html"
    og_desc = (f"{cd['title']} Everyone agrees on the base facts; the contest is over "
               f"what they mean. Carriers shown per framing are a sample, not a census. "
               f"AI-generated, not human-reviewed.")

    cells_html = "\n".join(
        f'''<div class="fcell">
      <div class="fname">{esc(c["name"])}</div>
      <div class="fq">{esc(c["question"])}</div>
      <div class="dots">{'<span class="dot"></span>' * min(c["n"], 10)}
        <span class="fcount">{c["n"]} recorded</span></div>
      <div class="fcar">{esc(", ".join(c["names"][:3]) + (f' +{len(c["names"]) - 3}' if len(c["names"]) > 3 else ""))}</div>
    </div>''' for c in cd["cells"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(cd["headline"])} — Tributary</title>
<meta property="og:type" content="article">
<meta property="og:site_name" content="Tributary">
<meta property="og:title" content="One event, {cd["n_framings"]} competing framings.">
<meta property="og:description" content="{esc(og_desc)}">
<meta property="og:image" content="{esc(og_img)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(cd["headline"])} {esc(cd["title"])}">
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
  .headline {{ font-size: 1.6rem; font-weight: 650; letter-spacing: -0.01em; margin: 0 0 0.3rem; }}
  .title {{ color: #52514e; font-size: 0.95rem; margin: 0 0 1rem; }}
  .agree {{ background: #f4f3ef; border-radius: 8px; padding: 0.7rem 0.9rem; font-size: 0.88rem;
           color: #52514e; margin: 0 0 1.1rem; }}
  .agree strong {{ color: #0b0b0b; }}
  .fgrid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; }}
  .fcell {{ border: 1px solid #e1e0d9; border-radius: 10px; padding: 0.8rem 0.95rem; }}
  .fname {{ font-weight: 600; font-size: 0.95rem; }}
  .fq {{ color: #52514e; font-size: 0.82rem; font-style: italic; margin: 0.15rem 0 0.4rem; }}
  .fcar {{ color: #898781; font-size: 0.8rem; margin-top: 0.35rem; }}
  .dots {{ display: flex; gap: 4px; align-items: center; }}
  .dot {{ width: 9px; height: 9px; border-radius: 50%; background: #2a78d6; }}
  .fcount {{ font-size: 0.78rem; color: #898781; margin-left: 4px; white-space: nowrap; }}
  .cardfoot {{ border-top: 1px solid #e1e0d9; margin-top: 1rem; padding-top: 0.65rem;
              font-size: 0.78rem; color: #898781; display: flex; justify-content: space-between;
              flex-wrap: wrap; gap: 0.4rem; }}
  .cta {{ display: inline-block; margin: 1.3rem 0 0; background: #2a78d6; color: #fff;
         text-decoration: none; font-weight: 600; font-size: 0.95rem;
         padding: 0.55rem 1.1rem; border-radius: 8px; }}
  .cta:hover {{ background: #184f95; }}
  .honesty {{ margin-top: 2.2rem; padding-top: 0.9rem; border-top: 1px solid #e1e0d9;
             color: #898781; font-size: 0.82rem; }}
  .honesty a {{ color: #2a78d6; text-decoration: none; }}
  @media (max-width: 640px) {{ .fgrid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="kicker">Tributary · event analysis</div>
    <div class="headline">{esc(cd["headline"])}</div>
    <p class="title">{esc(cd["title"])}</p>
    {f'<div class="agree"><strong>Everyone agrees:</strong> {esc(cd["agrees"])} The contest is over what it <em>means</em>.</div>' if cd["agrees"] else ''}
    <div class="fgrid">
{cells_html}
    </div>
    {f'<p class="fcar" style="margin-top:0.6rem;">+ {cd["n_hidden"]} more framings on the full analysis.</p>' if cd["n_hidden"] else ''}
    <div class="cardfoot">
      <span>dots = carriers our search recorded — a floor, not a census; smaller outlets and individual voices are undercounted · framing boundaries are AI judgments</span>
      <span>AI-generated, not human-reviewed</span>
    </div>
  </div>

  <a class="cta" href="{esc(event_href)}">See the full analysis — every framing, carrier, and receipt →</a>

  <p class="honesty">
    This card is AI-generated and not yet human-reviewed. Carrier counts are a floor:
    the sample is what web search surfaced, so smaller outlets and individual commentators
    carrying a framing are systematically undercounted, and absence of an outlet is not
    evidence it ignored the story. Equal cell sizes are deliberate: we did not measure
    each framing's true share of the discourse. Card image for sharing: <a href="{aid}.png">PNG</a>.<br>
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
# share page (the tap-through artifact)
# ---------------------------------------------------------------------------

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _names_prose(outlets, with_dates=True):
    bits = [o["name"] + (f' ({fmt_date_human(o["date"])})' if with_dates and o["date"] else "")
            for o in outlets]
    if len(bits) <= 1:
        return "".join(bits)
    return ", ".join(bits[:-1]) + " and " + bits[-1]


def svg_timeline(cd):
    """The spread-over-time visual as a standalone inline SVG. Each dot is one
    recorded dated use (hover for date + who); the dark dot is the earliest
    found; the dashed line marks the event in the news."""
    x0, x1, axis_y, area_top = 10, 750, 168, 58
    g = timeline_geometry(cd, x0, x1, axis_y, area_top, bucket_px=10, dot_gap=9)
    today = cd["event"] or {}
    right_lbl = f'In the news {fmt_date_human(today.get("date") or "")}' if today \
        else f'Most recent use {cd["last_human"]}'

    parts = [f'<svg viewBox="0 0 760 200" role="img" aria-label="Timeline of '
             f'{len(cd["points"])} recorded uses from {esc(cd["first_human"])}; '
             f'{esc(right_lbl)}">',
             '<style>.ms{font:600 15px system-ui;fill:#0b0b0b}'
             '.s{font:12.5px system-ui;fill:#898781}.t{font:11.5px system-ui;fill:#898781}'
             '</style>']
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in g["area"])
    edge = " ".join(f"{x:.1f},{y:.1f}" for x, y in g["area"][:-1])
    parts.append(f'<polygon points="{pts}" fill="#cde2fb" fill-opacity="0.55"/>'
                 f'<polyline points="{edge}" fill="none" stroke="#86b6ef" stroke-width="1.5"/>'
                 f'<line x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}" stroke="#c3c2b7" stroke-width="1.5"/>')
    for x, label in g["ticks"]:
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y - 3}" x2="{x:.1f}" y2="{axis_y + 3}" stroke="#c3c2b7"/>'
                     f'<text x="{x:.1f}" y="{axis_y + 17}" text-anchor="middle" class="t">{esc(label)}</text>')
    if g["marker"]:
        parts.append(f'<line x1="{g["marker"]["x"]:.1f}" y1="{area_top - 12}" '
                     f'x2="{g["marker"]["x"]:.1f}" y2="{axis_y - 2}" '
                     f'stroke="#c3c2b7" stroke-width="1.5" stroke-dasharray="5 4"/>')
    for dot in g["dots"]:
        r = 5.5 if dot["ms"] else 4
        fill = "#184f95" if dot["ms"] else "#2a78d6"
        tip = f'{dot["p"]["date"]}' + (f' · {dot["p"]["who"]}' if dot["p"]["who"] else "")
        parts.append(f'<circle cx="{dot["x"]:.1f}" cy="{dot["y"]:.1f}" r="{r}" fill="{fill}" '
                     f'stroke="#fcfcfb" stroke-width="1.5"><title>{esc(tip)}</title></circle>')

    # bursts named: who appears where the curve jumps
    placed = []
    for s in cd["spikes"]:
        in_range = [dot["y"] for dot in g["dots"]
                    if g["X"](s["f0"]) - 10 <= dot["x"] <= g["X"](s["f1"]) + 10]
        top = min(in_range, default=axis_y - 13)
        line1 = f'{s["n"]} uses · {s["range"]}'
        line2 = ", ".join(s["names"][:3]) + \
            (f' +{len(s["names"]) - 3}' if len(s["names"]) > 3 else "")
        if len(line2) > 52:
            line2 = line2[:51].rstrip() + "…"
        half = max(len(line1) * 3.9, len(line2) * 3.2)
        cx = min(max(g["X"]((s["f0"] + s["f1"]) / 2), x0 + half), x1 - half)
        if any(cx - half < p1 + 16 and cx + half > p0 - 16 for p0, p1 in placed):
            continue
        placed.append((cx - half, cx + half))
        ly = max(top - (42 if line2 else 24), area_top - 6)
        parts.append(f'<text x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                     f'style="font:600 13.5px system-ui" fill="#0b0b0b">{esc(line1)}</text>')
        if line2:
            parts.append(f'<text x="{cx:.1f}" y="{ly + 17:.1f}" text-anchor="middle" '
                         f'class="s">{esc(line2)}</text>')
    parts.append(f'<text x="{x0}" y="20" class="ms">First attested {esc(cd["first_human"])}</text>')
    if cd["first_who"]:
        parts.append(f'<text x="{x0}" y="38" class="s">{esc(cd["first_who"][:52])}</text>')
    parts.append(f'<text x="{x1}" y="20" text-anchor="end" class="ms">{esc(right_lbl)}</text>')
    if cd["outlets"]:
        names = " · ".join(o["name"] for o in cd["outlets"][:3])[:60]
        parts.append(f'<text x="{x1}" y="38" text-anchor="end" class="s">{esc(names)}</text>')
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
    if cd["outlets"]:
        desc_bits.append(f"Recently carried by {_names_prose(cd['outlets'][:3], with_dates=False)}.")
    desc_bits.append("Every date and source has a receipt. AI-traced, not human-reviewed.")
    og_desc = " ".join(desc_bits)

    # the cross-circle fact, demoted to one sentence of page text — outlet
    # names carry the point; the L/R axis is context, not the focus
    carried_note = ""
    if cd["outlets"]:
        span_note = (" — outlets AllSides rates on opposite sides of center "
                     "(their ratings, not ours)") if len(cd["circles"]) >= 2 else ""
        carried_note = (f'<p class="carried">Recently carried by '
                        f'{esc(_names_prose(cd["outlets"]))}{span_note}.</p>')

    event_link = ""
    if ev and ev.get("id"):
        t = ev["title"]
        if len(t) > 90:  # cut on a word boundary, honestly marked
            t = t[:90].rsplit(" ", 1)[0] + " …"
        event_link = (f'<a class="ctx" href="../../fingerprint_viewer.html?load=gallery/events/{esc(ev["id"])}.json">'
                      f'See it in the news: {esc(t)} →</a>')

    alt = (f'{cd["headline"]} Timeline of {cd["n_uses"]} recorded uses from '
           f'{cd["first_human"]} onward. “{cd["phrase"]}”')

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
<meta property="og:image:alt" content="{esc(alt)}">
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
  .carried {{ color: #52514e; font-size: 0.85rem; margin: 0.5rem 0 0; }}
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
    {svg_timeline(cd)}
    {carried_note}
    <div class="cardfoot">
      <span>{cd["n_uses"]} recorded uses · each dot = one use from a cited source — a sample, not a census · first attested {esc(cd["first_human"])}, the earliest we found</span>
      <span>AI-traced, not human-reviewed</span>
    </div>
  </div>

  <a class="cta" href="{esc(trace_href)}">See the full trace — every date, source, and receipt →</a>
  {event_link}

  <p class="honesty">
    This card is AI-generated and not yet human-reviewed. “First attested” is the earliest
    use our search found — attestation confidence as recorded by the pipeline: {cd["confidence"]:.2f} —
    and an earlier one may exist. Dot order shows sequence, not influence. The full trace
    shows every source, archive link, and verification status{", and the event page shows each carrier’s own quoted words" if cd["outlets"] else ""}.
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
    ap.add_argument("--event", action="append", default=None, metavar="ID",
                    help="render a framing-fan card for this event analysis id "
                         "(repeatable; explicit only — no default event set)")
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
              f"{len(cd['outlets'])} carriers named)", file=sys.stderr)

    for aid in args.event or []:
        p = gallery / "events" / f"{aid}.json"
        if not p.exists():
            print(f"[cards] --event {aid}: no gallery/events/{aid}.json — skipped",
                  file=sys.stderr)
            continue
        ecd = event_card_data(json.loads(p.read_text(encoding="utf-8")))
        if not ecd:
            print(f"[cards] --event {aid}: fewer than 2 framings — skipped",
                  file=sys.stderr)
            continue
        render_event_png(ecd, cards_dir / f"{aid}.png")
        render_event_page(ecd, cards_dir / f"{aid}.html")
        index.append({"kind": "event", "analysis_id": aid,
                      "headline": ecd["headline"], "phrase": ecd["title"][:200],
                      "page": f"gallery/cards/{aid}.html",
                      "image": f"gallery/cards/{aid}.png", "event_id": aid})
        rendered += 1
        print(f"[cards] {aid}: {ecd['headline']}  (event card)", file=sys.stderr)

    (cards_dir / "index.json").write_text(
        json.dumps({"count": rendered, "as_of": asof.isoformat(), "cards": index},
                   indent=1, ensure_ascii=False), encoding="utf-8")
    build_search_index(gallery)   # new gallery/traces JSONs join the search corpus
    print(f"[cards] {rendered} cards -> {cards_dir}. "
          f"Now: git add gallery/ && git commit && git push", file=sys.stderr)


if __name__ == "__main__":
    main()
