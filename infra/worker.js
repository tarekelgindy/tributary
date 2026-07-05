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

    return json({ error: "not found" }, 404, h);
  },
};
