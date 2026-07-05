# In-window trace requests — Cloudflare Worker setup (~15 minutes, one-time)

The website's request buttons POST to a tiny Cloudflare Worker (free tier),
which rate-limits and relays to GitHub Actions; the workflow reports back and
the site shows the permalink in place. Code: `infra/worker.js`. Until this is
deployed, the site falls back to GitHub-issue request links automatically.

## 1. Create the Worker (Cloudflare dashboard)

1. Sign up / log in at dash.cloudflare.com (free plan is fine).
2. **Workers & Pages → "Create application"** → pick the **"Hello World"**
   template (first card in the template gallery; do NOT "Import a
   repository"). Name it `tributary-requests` → **Deploy**.
3. On the Worker's page: **"Edit code"** → replace the placeholder with the
   contents of `infra/worker.js` → **Save and deploy**.

(Terminal alternative: `npm create cloudflare@latest` with the Hello World
worker, replace `src/index.js` with `infra/worker.js`, `npx wrangler deploy`.)

## 2. Create the KV namespace and bind it

1. **Storage & Databases → Workers KV → "Create instance"** (the dashboard's
   current name for creating a namespace) — name it `tributary-status`.
2. Worker → **Settings → Bindings → Add → KV namespace** —
   variable name **`STATUS`** (exact, uppercase), select the namespace. Deploy.
3. Health check: `https://<worker-url>/status?id=test` should return
   `{"state":"unknown"}` — confirms code + KV binding are both live.

## 3. Secrets on the Worker (Settings → Variables & Secrets)

| Name | Value |
|---|---|
| `GITHUB_PAT` | A **fine-grained** GitHub token: github.com → Settings → Developer settings → Fine-grained tokens → Generate. Repository access: **only `tributary`**. Repository permissions: **Contents: Read and write**. Nothing else. |
| `CALLBACK_SECRET` | Any long random string (e.g. from a password generator). |

## 4. Secrets on the GitHub repo (Settings → Secrets → Actions)

| Name | Value |
|---|---|
| `WORKER_URL` | The Worker's URL, e.g. `https://tributary-requests.<your-subdomain>.workers.dev` (no trailing slash) |
| `CALLBACK_SECRET` | The **same** random string as above |

## 5. Turn it on

Tell Claude the Worker URL (or edit `index.html` yourself: set
`const WORKER_URL = 'https://…workers.dev'` in the question-box script).
Push — the site switches from GitHub-issue links to the in-window flow.

## Notes

- Spend control: the Worker enforces a global daily cap (default 10, env var
  `DAILY_CAP`) and a per-visitor cap (default 3/day, `PER_IP_CAP`). The
  Worker holds the only PAT, so nothing else can trigger paid generation.
- The GitHub-issue path keeps working as a manual/fallback lane; the
  `approved-trace` label force-runs anything, including past the cap.
- Scale path (deliberate): the Worker's contract is request-in →
  status/permalink-out. When volume justifies it, the GitHub-Actions executor
  behind it swaps for a container service without touching the site or the
  Worker API.
