/**
 * Tributary request front door — Cloudflare Worker (Phase 2.9-B).
 *
 * Lets the website request traces entirely in-window: the browser POSTs
 * here, this Worker rate-limits and relays to GitHub Actions
 * (repository_dispatch), the workflow calls back /complete when the trace
 * is published, and the site polls /status until it can show the permalink.
 *
 * Bindings required (Worker settings):
 *   KV namespace binding: STATUS
 *   Secrets: GITHUB_PAT        (fine-grained, this repo only, Contents R/W)
 *            CALLBACK_SECRET   (any long random string; also a repo secret)
 * Optional env vars: DAILY_CAP (default 10), PER_IP_CAP (default 3)
 *
 * Routes:
 *   POST /request   {subject, kind: "claim"|"event"} -> {id} | 429 | 400
 *   GET  /status?id=... -> {state: running|done|failed|unknown, url?}
 *   POST /complete  {id, state, url?}  (X-Callback-Secret header) -> {ok}
 *   POST /contribute (Phase 2c-B; v2 kinds add/edit/confirm/dispute)
 *     {kind, fingerprint_id, ...} -> {ok, ref}
 *     Relays a reader contribution to the contributions workflow for
 *     mechanical verification + maintainer review. PRIVACY SPLIT: the
 *     optional contact field is stored ONLY here in KV (90 days, for
 *     maintainer follow-up) — it never enters the dispatch payload, the
 *     public repo, or the review issue.
 *     Optional env vars: CONTRIB_DAILY_CAP (default 20),
 *                        CONTRIB_PER_IP_CAP (default 5)
 *
 *   Usage metrics (2026-09-18) — privacy-first by design: every log record
 *   carries Cloudflare's country/region/city and NEVER the IP address (IPs
 *   exist only in the 2-day rate-limit counters above). Disclosed on the
 *   site footer.
 *   POST /ping     {type: "view"|"search", path?, q?, kind?} -> {ok}
 *     fire-and-forget beacon from the site (page views, search terms).
 *   GET  /metrics  (X-Callback-Secret header) -> {records: [...], cursor?}
 *     maintainer-only export; metrics.py aggregates it locally.
 *   NB: Workers KV free tier allows ~1,000 writes/day — each ping is one
 *   write. Fine at current traffic; revisit (Analytics Engine) if the site
 *   ever sees thousands of daily views.
 */

const REPO = "tarekelgindy/tributary";
const ALLOWED_ORIGINS = new Set([
  "https://tarekelgindy.github.io",
  "http://localhost:8000",
]);

function cors(request) {
  const origin = request.headers.get("Origin") || "";
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGINS.has(origin) ? origin : "https://tarekelgindy.github.io",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
  };
}

const json = (obj, status, headers) =>
  new Response(JSON.stringify(obj), { status, headers });

// Usage log record: WHAT happened and WHERE FROM (CF-provided geo), never who.
async function logEvent(env, request, type, extra) {
  try {
    const cf = request.cf || {};
    const rec = {
      t: new Date().toISOString(),
      type,
      country: cf.country || "",
      region: cf.region || "",
      city: cf.city || "",
      ...extra,
    };
    const key = "log:" + rec.t + ":" + crypto.randomUUID().slice(0, 8);
    await env.STATUS.put(key, JSON.stringify(rec), { expirationTtl: 180 * 86400 });
  } catch (e) { /* metrics must never break the product */ }
}

export default {
  async fetch(request, env) {
    const h = cors(request);
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: h });

    if (url.pathname === "/status" && request.method === "GET") {
      const id = (url.searchParams.get("id") || "").slice(0, 64);
      const raw = id && (await env.STATUS.get("req:" + id));
      return json(raw ? JSON.parse(raw) : { state: "unknown" }, 200, h);
    }

    if (url.pathname === "/complete" && request.method === "POST") {
      if (request.headers.get("X-Callback-Secret") !== env.CALLBACK_SECRET)
        return json({ error: "forbidden" }, 403, h);
      const body = await request.json().catch(() => null);
      if (!body || !body.id) return json({ error: "bad request" }, 400, h);
      const raw = await env.STATUS.get("req:" + body.id);
      const rec = raw ? JSON.parse(raw) : {};
      rec.state = body.state === "done" ? "done" : "failed";
      if (body.url) rec.url = String(body.url).slice(0, 500);
      rec.finished = new Date().toISOString();
      await env.STATUS.put("req:" + body.id, JSON.stringify(rec), { expirationTtl: 7 * 86400 });
      return json({ ok: true }, 200, h);
    }

    if (url.pathname === "/request" && request.method === "POST") {
      const body = await request.json().catch(() => null);
      const subject = body && String(body.subject || "").trim().slice(0, 200);
      const kind = body && body.kind === "event" ? "event" : "claim";
      if (!subject || subject.length < 8)
        return json({ error: "Please describe the claim or event in a full sentence." }, 400, h);

      // ---- rate limits: global daily cap + per-IP cap (UTC day) ----
      const day = new Date().toISOString().slice(0, 10);
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const dailyCap = parseInt(env.DAILY_CAP || "10", 10);
      const perIpCap = parseInt(env.PER_IP_CAP || "3", 10);
      const dayKey = "count:" + day;
      const ipKey = "ip:" + day + ":" + ip;
      const dayCount = parseInt((await env.STATUS.get(dayKey)) || "0", 10);
      const ipCount = parseInt((await env.STATUS.get(ipKey)) || "0", 10);
      if (dayCount >= dailyCap) {
        await logEvent(env, request, "request", { kind, q: subject.slice(0, 140), ok: 0, why: "daily-cap" });
        return json({ error: "Today's generation capacity is used up — please try again tomorrow." }, 429, h);
      }
      if (ipCount >= perIpCap)
        return json({ error: "You've reached today's per-visitor limit — please try again tomorrow." }, 429, h);

      const id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);

      const dispatch = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + env.GITHUB_PAT,
          "Accept": "application/vnd.github+json",
          "User-Agent": "tributary-request-worker",
        },
        body: JSON.stringify({
          event_type: "trace-request",
          client_payload: { subject, kind, request_id: id },
        }),
      });
      if (dispatch.status !== 204) {
        return json({ error: "Could not start generation (upstream error) — please try again." }, 502, h);
      }

      await env.STATUS.put(dayKey, String(dayCount + 1), { expirationTtl: 2 * 86400 });
      await env.STATUS.put(ipKey, String(ipCount + 1), { expirationTtl: 2 * 86400 });
      await env.STATUS.put("req:" + id, JSON.stringify({
        state: "running", subject, kind, created: new Date().toISOString(),
      }), { expirationTtl: 7 * 86400 });
      await logEvent(env, request, "request", { kind, q: subject.slice(0, 140), ok: 1 });

      return json({ id }, 200, h);
    }

    if (url.pathname === "/contribute" && request.method === "POST") {
      const b = await request.json().catch(() => null);
      if (!b) return json({ error: "bad request" }, 400, h);
      const kind = ["add", "edit", "confirm", "dispute"].includes(b.kind) ? b.kind : "";
      const fp = /^[a-f0-9]{12}$/.test(String(b.fingerprint_id || "")) ? b.fingerprint_id : "";
      if (!kind || !fp)
        return json({ error: "Missing or malformed contribution fields." }, 400, h);
      if (kind === "add" && !/^https?:\/\/.{4,}/.test(String(b.url || "")))
        return json({ error: "An added use needs a link to the source." }, 400, h);
      if (kind !== "add" && !/^[a-f0-9]{12}$/.test(String(b.element_id || "")))
        return json({ error: "Pick which entry this is about." }, 400, h);

      // ---- rate limits (separate counters from trace requests) ----
      const day = new Date().toISOString().slice(0, 10);
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const dailyCap = parseInt(env.CONTRIB_DAILY_CAP || "20", 10);
      const perIpCap = parseInt(env.CONTRIB_PER_IP_CAP || "5", 10);
      const dayKey = "ccount:" + day;
      const ipKey = "cip:" + day + ":" + ip;
      const dayCount = parseInt((await env.STATUS.get(dayKey)) || "0", 10);
      const ipCount = parseInt((await env.STATUS.get(ipKey)) || "0", 10);
      if (dayCount >= dailyCap || ipCount >= perIpCap)
        return json({ error: "Contribution limit reached for today — please try again tomorrow." }, 429, h);

      const ref = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
      const clip = (v, n) => String(v || "").slice(0, n);
      const payload = {
        kind,
        fingerprint_id: fp,
        url: clip(b.url, 500),
        date: clip(b.date, 10),
        quote: clip(b.quote, 600),
        source_author: clip(b.source_author, 120),
        reason: clip(b.reason, 600),
        element_id: clip(b.element_id, 12),
        role: clip(b.role, 30),
        lineage: b.lineage === "conceptual" ? "conceptual" : "lexical",
        name: clip(b.name, 80),
        anonymous: b.anonymous ? "1" : "",
        ref,
      };

      const dispatch = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + env.GITHUB_PAT,
          "Accept": "application/vnd.github+json",
          "User-Agent": "tributary-request-worker",
        },
        body: JSON.stringify({ event_type: "contribution", client_payload: payload }),
      });
      if (dispatch.status !== 204)
        return json({ error: "Could not submit (upstream error) — please try again." }, 502, h);

      await env.STATUS.put(dayKey, String(dayCount + 1), { expirationTtl: 2 * 86400 });
      await env.STATUS.put(ipKey, String(ipCount + 1), { expirationTtl: 2 * 86400 });
      // full record incl. contact stays HERE only (private KV, 90 days)
      await env.STATUS.put("contrib:" + ref, JSON.stringify({
        ...payload, contact: clip(b.contact, 200), created: new Date().toISOString(),
      }), { expirationTtl: 90 * 86400 });

      await logEvent(env, request, "contribution", { kind, fp });
      return json({ ok: true, ref }, 200, h);
    }

    if (url.pathname === "/ping" && request.method === "POST") {
      const b = await request.json().catch(() => null);
      const type = b && (b.type === "view" || b.type === "search") ? b.type : "";
      if (!type) return json({ ok: false }, 400, h);
      // soft per-IP daily cap so a stuck tab can't burn the KV write budget
      const day = new Date().toISOString().slice(0, 10);
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const pKey = "pip:" + day + ":" + ip;
      const pCount = parseInt((await env.STATUS.get(pKey)) || "0", 10);
      if (pCount >= 200) return json({ ok: true }, 200, h);
      await env.STATUS.put(pKey, String(pCount + 1), { expirationTtl: 2 * 86400 });
      const clip = (v, n) => String(v || "").slice(0, n);
      await logEvent(env, request, type, type === "search"
        ? { kind: clip(b.kind, 12), q: clip(b.q, 140) }
        : { path: clip(b.path, 180) });
      return json({ ok: true }, 200, h);
    }

    if (url.pathname === "/metrics" && request.method === "GET") {
      if (request.headers.get("X-Callback-Secret") !== env.CALLBACK_SECRET)
        return json({ error: "forbidden" }, 403, h);
      const cursor = url.searchParams.get("cursor") || undefined;
      const list = await env.STATUS.list({ prefix: "log:", limit: 500, cursor });
      const records = [];
      for (let i = 0; i < list.keys.length; i += 50) {
        const vals = await Promise.all(
          list.keys.slice(i, i + 50).map((k) => env.STATUS.get(k.name)),
        );
        for (const v of vals) if (v) records.push(JSON.parse(v));
      }
      return json({
        records,
        cursor: list.list_complete ? null : list.cursor,
      }, 200, h);
    }

    return json({ error: "not found" }, 404, h);
  },
};
