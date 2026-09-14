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
 *   POST /contribute (Phase 2c-B) {kind, fingerprint_id, ...} -> {ok, ref}
 *     Relays a reader contribution to the contributions workflow for
 *     mechanical verification + maintainer review. PRIVACY SPLIT: the
 *     optional contact field is stored ONLY here in KV (90 days, for
 *     maintainer follow-up) — it never enters the dispatch payload, the
 *     public repo, or the review issue.
 *     Optional env vars: CONTRIB_DAILY_CAP (default 20),
 *                        CONTRIB_PER_IP_CAP (default 5)
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
      if (dayCount >= dailyCap)
        return json({ error: "Today's generation capacity is used up — please try again tomorrow." }, 429, h);
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

      return json({ id }, 200, h);
    }

    if (url.pathname === "/contribute" && request.method === "POST") {
      const b = await request.json().catch(() => null);
      if (!b) return json({ error: "bad request" }, 400, h);
      const kind = ["add", "confirm", "dispute"].includes(b.kind) ? b.kind : "";
      const fp = /^[a-f0-9]{12}$/.test(String(b.fingerprint_id || "")) ? b.fingerprint_id : "";
      if (!kind || !fp)
        return json({ error: "Missing or malformed contribution fields." }, 400, h);
      if (kind === "add" && !/^https?:\/\/.{4,}/.test(String(b.url || "")))
        return json({ error: "An earlier-use contribution needs a link to the source." }, 400, h);
      if (kind !== "add" && !/^[a-f0-9]{12}$/.test(String(b.element_id || "")))
        return json({ error: "Pick which entry you are confirming or disputing." }, 400, h);

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

      return json({ ok: true, ref }, 200, h);
    }

    return json({ error: "not found" }, 404, h);
  },
};
