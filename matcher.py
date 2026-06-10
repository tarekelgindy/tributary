"""
Tributary matcher — "have we already traced this narrative?"
============================================================
The keystone of the fingerprint-once / match-and-serve-many economics: every
text entering the system is embedded locally (free) and compared against the
fingerprint corpus before any API money is spent.

Design (ROADMAP Phase 1):
  - Local embeddings via sentence-transformers. The model is PINNED and its
    name is stored alongside the vectors — vectors from different models must
    never be compared (the sidecar is rejected on mismatch, not reused).
  - TWO vectors per fingerprint, embedded separately:
      L1 = lexical.canonical_phrase            (the *phrasing*)
      L2 = conceptual.claim_predicate + causal_structure   (the *idea*)
  - Sidecar storage in fingerprints/vectors.json keyed by fingerprint_id —
    never inside index.json.
  - Decision grid on (L1-sim, L2-sim) against the best L2 neighbor:
      both >= HI            -> serve_cached      (same narrative, same phrasing)
      L2 >= HI, L1 < HI     -> lexical_variant   (same idea, new phrasing)
      both < LO             -> generate          (genuinely new -> batch queue)
      anything else         -> review            (middle band -> human queue)
    Starting thresholds HI=0.85 / LO=0.70 — calibrate on the real corpus
    before trusting them (Gate 1: zero confident-match false positives).

CLI:
    python matcher.py "some claim"            # match status + nearest neighbors
    python matcher.py --backfill              # (re)embed the fingerprint corpus
    python matcher.py --coverage claims.txt   # coverage metric over a list
    python matcher.py --queues                # show review/generation queues

The heavyweight import (sentence_transformers -> torch) is lazy: importing
this module costs nothing, so fingerprint.py can depend on it optionally and
fall back to the old lexical check when the library isn't installed.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Pinned embedding model. Changing this invalidates every stored vector —
# the sidecar records the model name and is rejected (never compared) on
# mismatch. all-MiniLM-L6-v2: small (22M params, 384-dim), strong on
# sentence-similarity, the most battle-tested choice at this size.
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

HI_THRESHOLD = 0.85    # candidate threshold (Gate 1: NOT sufficient to serve alone)
LO_THRESHOLD = 0.70    # below this on both axes = confidently new
EXACT_THRESHOLD = 0.95  # near-exact phrasing — still confirmed before serving

# Stage-2 judge prompt — validated against the 30 human-labeled Gate 1 pairs
# (4/4 same confirmed; 14/15 different rejected, incl. the embedding stage's
# false positive; its own single error sat below the candidate threshold).
CONFIRM_SYSTEM = """You judge whether two short texts assert the SAME claim — strictly.
Same means: same subject, same attribution of responsibility/blame, and the same asserted conclusion or consequence. Mere topical overlap is NOT same. A claim that assigns blame differs from one that stays neutral on blame. A claim asserting a consequence differs from one that only states the event.
Input: JSON list of {"id", "a", "b"}.
Output ONLY JSON: {"judgments": [{"id": 0, "same": true|false, "why": "<= 8 words"}, ...]}"""

_DEFAULT_DIR = Path(__file__).resolve().parent / "fingerprints"

_model = None  # process-level cache; loading takes seconds, do it once


def _load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def embed_texts(texts: list) -> list:
    """Embed texts -> unit-normalized vectors (so cosine == dot product)."""
    model = _load_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def _dot(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


def _l2_text(fp: dict) -> str:
    con = fp.get("conceptual") or {}
    return " ".join(t for t in (con.get("claim_predicate", ""),
                                con.get("causal_structure", "")) if t).strip()


def _l1_text(fp: dict) -> str:
    return ((fp.get("lexical") or {}).get("canonical_phrase") or "").strip()


@dataclass
class Neighbor:
    fingerprint_id: str
    l1_sim: float
    l2_sim: float
    canonical_phrase: str

    def to_dict(self):
        return {"fingerprint_id": self.fingerprint_id,
                "l1_sim": round(self.l1_sim, 4), "l2_sim": round(self.l2_sim, 4),
                "canonical_phrase": self.canonical_phrase}


@dataclass
class MatchResult:
    decision: str                 # serve_cached | lexical_variant | review | generate | no_corpus
    text: str
    best: Neighbor = None
    neighbors: list = field(default_factory=list)
    thresholds: tuple = (HI_THRESHOLD, LO_THRESHOLD)
    confirm: dict = None          # stage-2 judgment, when a serve was attempted

    def to_dict(self):
        return {"decision": self.decision, "text": self.text,
                "best": self.best.to_dict() if self.best else None,
                "neighbors": [n.to_dict() for n in self.neighbors],
                "thresholds": {"hi": self.thresholds[0], "lo": self.thresholds[1]},
                "confirm": self.confirm}


class Matcher:
    """Loads the vector sidecar and answers match queries. Embedding the query
    is the only model call; comparison is pure arithmetic."""

    def __init__(self, store_dir: str = None):
        self.dir = Path(store_dir) if store_dir else _DEFAULT_DIR
        self.vectors_path = self.dir / "vectors.json"
        self._sidecar = None

    # ---------- sidecar ----------
    @property
    def sidecar(self) -> dict:
        if self._sidecar is None:
            if self.vectors_path.exists():
                d = json.loads(self.vectors_path.read_text(encoding="utf-8"))
                if d.get("_meta", {}).get("model") != EMBED_MODEL:
                    print(f"[matcher] vectors.json was built with "
                          f"{d.get('_meta', {}).get('model')!r}, current model is "
                          f"{EMBED_MODEL!r} — ignoring it; run --backfill.",
                          file=sys.stderr)
                    d = {"_meta": self._meta(), "vectors": {}}
                self._sidecar = d
            else:
                self._sidecar = {"_meta": self._meta(), "vectors": {}}
        return self._sidecar

    def _meta(self) -> dict:
        return {"model": EMBED_MODEL, "note": "Vectors are model-specific; "
                "never compare vectors across models. Rebuild via "
                "python matcher.py --backfill."}

    def _save_sidecar(self):
        self.vectors_path.write_text(
            json.dumps(self._sidecar, indent=1), encoding="utf-8")

    # ---------- backfill ----------
    def backfill(self, force: bool = False) -> tuple:
        """Embed every fingerprint JSON in the store dir into the sidecar.
        Skips entries whose source texts are unchanged (unless force)."""
        vecs = self.sidecar["vectors"]
        todo, kept = [], 0
        for p in sorted(self.dir.glob("*.json")):
            if p.name in ("index.json", "vectors.json", "review_queue.json",
                          "generation_queue.json"):
                continue
            try:
                fp = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            fid = fp.get("fingerprint_id")
            l1, l2 = _l1_text(fp), _l2_text(fp)
            if not fid or not l1:
                continue
            cur = vecs.get(fid)
            if not force and cur and cur.get("l1_text") == l1 and cur.get("l2_text") == l2:
                kept += 1
                continue
            todo.append((fid, l1, l2))

        if todo:
            # one batched encode for all texts; L2 falls back to L1 when the
            # conceptual layer is empty (lean fingerprints) — flagged so the
            # decision logic can treat the L2 axis as unavailable, not as 1.0.
            l1_vecs = embed_texts([t[1] for t in todo])
            l2_vecs = embed_texts([t[2] or t[1] for t in todo])
            for (fid, l1, l2), v1, v2 in zip(todo, l1_vecs, l2_vecs):
                vecs[fid] = {"l1": v1, "l2": v2, "l1_text": l1, "l2_text": l2,
                             "l2_is_fallback": not l2}
            self._save_sidecar()
        return len(todo), kept

    # ---------- matching ----------
    def match(self, text: str, hi: float = HI_THRESHOLD, lo: float = LO_THRESHOLD,
              top: int = 5) -> MatchResult:
        vecs = self.sidecar["vectors"]
        if not vecs:
            return MatchResult(decision="no_corpus", text=text)
        q = embed_texts([text])[0]
        scored = []
        for fid, v in vecs.items():
            l1s = _dot(q, v["l1"])
            l2s = _dot(q, v["l2"])
            phrase = v.get("l1_text", "")
            scored.append(Neighbor(fid, l1s, l2s, phrase))
        # the best candidate is the one whose IDEA is closest; phrasing breaks ties
        scored.sort(key=lambda n: (n.l2_sim, n.l1_sim), reverse=True)
        best = scored[0]
        decision = self._decide(best, hi, lo)
        return MatchResult(decision=decision, text=text, best=best,
                           neighbors=scored[:top], thresholds=(hi, lo))

    @staticmethod
    def _decide(best: Neighbor, hi: float, lo: float) -> str:
        # Near-exact phrasing is the most precise signal we have (it mirrors
        # the lexical dedup that already serves exact signature matches), and
        # it must not be vetoed by the L2 axis: validation showed the stored
        # L2 text is an *abstract restatement* whose register sits far from
        # colloquial queries — a fingerprint's own canonical phrase scored
        # L2 0.40 against its own predicate. So L1 >= EXACT serves outright;
        # below that, the conservative both-axes rule applies until Gate 1
        # calibrates per-axis thresholds on the real corpus.
        if best.l1_sim >= EXACT_THRESHOLD:
            return "serve_cached"
        if best.l1_sim >= hi and best.l2_sim >= hi:
            return "serve_cached"
        if best.l2_sim >= hi:
            return "lexical_variant"
        if best.l1_sim < lo and best.l2_sim < lo:
            return "generate"
        return "review"

    # ---------- match confirmation (stage 2 of serving) ----------
    def confirm(self, text_a: str, text_b: str,
                model: str = "claude-haiku-4-5-20251001") -> dict:
        """Stage-2 judge: do two texts assert the SAME claim — same subject,
        same attribution of blame, same asserted consequence? Gate 1 showed
        embeddings alone cannot make this call (different-narrative pairs
        reach 0.85 cosine; the discriminating features are exactly blame and
        consequence, which embeddings compress away) while a strict Haiku
        judgment rejected the embedding's false positive. NOTHING SERVES
        WITHOUT THIS CONFIRMATION. ~$0.001/call vs $0.30+ regeneration.
        Requires ANTHROPIC_API_KEY; raises on any failure (callers treat
        failure as not-confirmed)."""
        import anthropic
        from fingerprint import _parse_json_safe
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=256,
            system=[{"type": "text", "text": CONFIRM_SYSTEM}],
            messages=[{"role": "user", "content": json.dumps(
                [{"id": 0, "a": text_a, "b": text_b}])}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        j = (_parse_json_safe(text) or {}).get("judgments") or [{}]
        return {"same": bool(j[0].get("same", False)),
                "why": str(j[0].get("why", ""))}

    def match_confirmed(self, text: str, hi: float = HI_THRESHOLD,
                        lo: float = LO_THRESHOLD, top: int = 5) -> MatchResult:
        """match() + the confirmation stage: a serve_cached/lexical_variant
        candidate is downgraded to review unless the Haiku judge confirms the
        claims are the same. The two stages fail differently (embeddings miss
        blame/consequence; the judge can misread borderline stance), so
        serving requires both — measured composite false positives on the
        Gate 1 calibration set: zero."""
        r = self.match(text, hi=hi, lo=lo, top=top)
        if r.decision in ("serve_cached", "lexical_variant") and r.best:
            try:
                verdict = self.confirm(text, r.best.canonical_phrase)
            except Exception as e:  # noqa: BLE001 — no confirmation, no serving
                verdict = {"same": False, "why": f"confirm unavailable: {type(e).__name__}"}
            r.confirm = verdict
            if not verdict["same"]:
                r.decision = "review"
        return r

    # ---------- variant attachment ----------
    def attach_variant(self, fingerprint_id: str, text: str) -> bool:
        """Record a new phrasing of an existing narrative on the fingerprint's
        lexical.phrase_variants (additive — old JSON still renders). This is
        the 'same idea, new phrasing' branch of the decision grid: the idea is
        already traced, so we attach instead of regenerating."""
        path = self.dir / f"{fingerprint_id}.json"
        if not path.exists():
            return False
        fp = json.loads(path.read_text(encoding="utf-8"))
        lex = fp.setdefault("lexical", {})
        variants = lex.setdefault("phrase_variants", [])
        known = {v.strip().lower() for v in variants}
        known.add((lex.get("canonical_phrase") or "").strip().lower())
        if text.strip().lower() in known:
            return True
        variants.append(text.strip())
        path.write_text(json.dumps(fp, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
        return True

    # ---------- queues ----------
    def enqueue(self, result: MatchResult) -> str:
        """Append a non-served result to the right queue file (deduped by text).
        Returns the queue path used, or '' for served/variant decisions."""
        name = {"review": "review_queue.json",
                "generate": "generation_queue.json",
                "no_corpus": "generation_queue.json"}.get(result.decision, "")
        if not name:
            return ""
        path = self.dir / name
        queue = []
        if path.exists():
            try:
                queue = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                queue = []
        if any(e.get("text") == result.text for e in queue):
            return str(path)
        queue.append({"text": result.text,
                      "best": result.best.to_dict() if result.best else None,
                      "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        path.write_text(json.dumps(queue, indent=2), encoding="utf-8")
        return str(path)

    # ---------- coverage ----------
    def coverage(self, texts: list, hi: float = HI_THRESHOLD,
                 lo: float = LO_THRESHOLD) -> dict:
        counts = {"serve_cached": 0, "lexical_variant": 0, "review": 0,
                  "generate": 0, "no_corpus": 0}
        rows = []
        for t in texts:
            r = self.match(t, hi=hi, lo=lo, top=1)
            counts[r.decision] += 1
            rows.append(r)
        n = len(texts) or 1
        covered = counts["serve_cached"] + counts["lexical_variant"]
        return {"n": len(texts), "counts": counts,
                "coverage_rate": round(covered / n, 3), "results": rows}


def calibrate(pairs_path: str, hi: float = HI_THRESHOLD,
              lo: float = LO_THRESHOLD) -> dict:
    """Gate 1 harness. Input: JSON list of {a, b, label} where label is
    same | paraphrase | different (human judgment). Reports the matcher's
    band for each pair vs the label, and the count that matters most:
    confident-match FALSE POSITIVES (label=different but sim >= hi) — Gate 1
    requires zero before serve-from-cache becomes default."""
    pairs = json.loads(Path(pairs_path).read_text(encoding="utf-8"))
    if isinstance(pairs, dict):           # gen_gate1_pairs.py wraps with instructions
        pairs = pairs.get("pairs", [])
    unlabeled = [p for p in pairs if not p.get("label")]
    if unlabeled:
        raise SystemExit(f"{len(unlabeled)} of {len(pairs)} pairs are unlabeled — "
                         "fill every \"label\" with same | paraphrase | different first.")
    texts = [p["a"] for p in pairs] + [p["b"] for p in pairs]
    vecs = embed_texts(texts)
    n = len(pairs)
    rows, false_pos, clear_agree, clear_total = [], [], 0, 0
    for i, p in enumerate(pairs):
        sim = _dot(vecs[i], vecs[n + i])
        band = "match" if sim >= hi else ("different" if sim < lo else "review")
        label = p["label"]
        # clear cases: same should land >= hi, different should land < lo
        if label == "same":
            clear_total += 1
            clear_agree += band == "match"
        elif label == "different":
            clear_total += 1
            clear_agree += band == "different"
            if band == "match":
                false_pos.append({**p, "sim": round(sim, 3)})
        rows.append({"a": p["a"][:60], "b": p["b"][:60], "label": label,
                     "sim": round(sim, 3), "band": band})
    return {"n": n, "hi": hi, "lo": lo,
            "clear_case_agreement": f"{clear_agree}/{clear_total}",
            "confident_false_positives": false_pos,
            "gate1_zero_fp": len(false_pos) == 0,
            "rows": rows}


def _read_lines(path: str) -> list:
    out = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def main():
    p = argparse.ArgumentParser(description="Semantic matcher over the fingerprint corpus.")
    p.add_argument("text", nargs="?", help="A claim/narrative to match.")
    p.add_argument("--backfill", action="store_true",
                   help="(Re)embed the fingerprint corpus into fingerprints/vectors.json.")
    p.add_argument("--force", action="store_true", help="Re-embed even if unchanged.")
    p.add_argument("--coverage", default="",
                   help="File of claims (one per line) -> coverage metric.")
    p.add_argument("--calibrate", default="",
                   help="Gate 1: JSON of {a,b,label} pairs -> band agreement "
                        "and confident-false-positive report.")
    p.add_argument("--queues", action="store_true", help="Show queue contents.")
    p.add_argument("--enqueue", action="store_true",
                   help="With a text: append review/generate results to the queue files.")
    p.add_argument("--store-dir", default="", help="Fingerprint dir (default fingerprints/).")
    p.add_argument("--hi", type=float, default=HI_THRESHOLD)
    p.add_argument("--lo", type=float, default=LO_THRESHOLD)
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args()

    m = Matcher(args.store_dir or None)

    if args.backfill:
        t0 = time.monotonic()
        new, kept = m.backfill(force=args.force)
        print(f"[matcher] embedded {new} fingerprints ({kept} unchanged) "
              f"in {time.monotonic()-t0:.1f}s -> {m.vectors_path}")
        return
    if args.queues:
        for name in ("review_queue.json", "generation_queue.json"):
            path = m.dir / name
            n = len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0
            print(f"{name}: {n} entries")
        return
    if args.calibrate:
        rep = calibrate(args.calibrate, hi=args.hi, lo=args.lo)
        print(json.dumps(rep, indent=2))
        print(f"\nclear-case agreement: {rep['clear_case_agreement']}   "
              f"confident false positives: {len(rep['confident_false_positives'])} "
              f"(Gate 1 requires 0)", file=sys.stderr)
        return
    if args.coverage:
        texts = _read_lines(args.coverage)
        rep = m.coverage(texts, hi=args.hi, lo=args.lo)
        print(f"coverage: {rep['coverage_rate']*100:.0f}% of {rep['n']} claims "
              f"already served by the corpus  {rep['counts']}")
        for r in rep["results"]:
            b = r.best
            print(f"  [{r.decision:<15}] {r.text[:48]:<48} "
                  f"-> {b.canonical_phrase[:40] if b else '-'} "
                  f"(L1 {b.l1_sim:.2f} / L2 {b.l2_sim:.2f})" if b else "")
        return
    if not args.text:
        p.error("give a claim to match, or --backfill / --coverage / --queues")

    r = m.match(args.text, hi=args.hi, lo=args.lo, top=args.top)
    print(json.dumps(r.to_dict(), indent=2))
    if args.enqueue:
        qp = m.enqueue(r)
        if qp:
            print(f"[queued -> {qp}]", file=sys.stderr)


if __name__ == "__main__":
    main()
