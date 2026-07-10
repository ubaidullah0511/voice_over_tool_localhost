# Deployment: Vercel frontend + on-demand RunPod backend

Frontend and backend are now separate origins:

```
Browser --> Vercel (static frontend + api/wake.ts edge function)
              |
              | api/wake.ts calls RunPod's REST API to check/start the pod,
              | then polls the pod's own /api/health until model_loaded: true
              v
        RunPod Pod (FastAPI backend, stopped by default)
              |
              | idle auto-stop: backend stops its own pod via RunPod's API
              | after IDLE_STOP_THRESHOLD_MIN of no authenticated requests
              | AND an empty job queue
              v
        (pod stops itself, waiting for the next wake)
```

The backend is stopped by default to save GPU cost. `frontend/src/wake.ts` calls
`frontend/api/wake.ts` (a Vercel Edge Function) before every generation; that
function resumes the pod if needed and reports readiness. The backend's own
`_idle_stop_loop` (in `backend/main.py`) stops the pod again once nobody's
used it for a while.

## Env vars: what goes where

### Vercel project (dashboard -> Settings -> Environment Variables)

| Var | Value | Notes |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | Clerk publishable key (`pk_...`) | Public — gets inlined into the client bundle by Vite. Same key as `frontend/.env.local`. |
| `VITE_BACKEND_URL` | `https://<RUNPOD_POD_ID>-8000.proxy.runpod.net` | Used two ways: inlined into the client bundle at build time (so `api.ts` calls the backend directly), **and** read server-side by `api/wake.ts` (`process.env.VITE_BACKEND_URL`) to know which health endpoint to poll. Must match the port the backend actually listens on. |
| `RUNPOD_API_KEY` | Your RunPod API key | **Server-side only** — read by `api/wake.ts` via `process.env`, never referenced by any client-bundle code. Do not prefix with `VITE_` or Vite will ship it to the browser. |
| `RUNPOD_POD_ID` | Your pod's ID | Same server-side-only rule as above. |

Project settings: Root Directory = `frontend`, Framework Preset = Vite, Build
Command = `tsc -b && vite build` (default from `package.json`), Output
Directory = `dist` (default). `frontend/vercel.json` already has the SPA
rewrite (`/* -> /index.html`) so client-side routing works.

### Backend `.env` (`backend/.env`, already gitignored — never commit this file)

| Var | Value | Notes |
|---|---|---|
| `CLERK_SECRET_KEY` | Clerk secret key | Already set. |
| `CLERK_JWKS_URL` | Clerk JWKS URL | Already set. |
| `MODEL_PATH` | Local model snapshot path on the pod | Already set. |
| `ALLOWED_ORIGINS` | `https://<your-vercel-domain>,https://<pod-id>-8000.proxy.runpod.net,http://localhost:5173` | **Currently a placeholder** (`your-app.vercel.app`) — replace with your real production Vercel domain once deployed. Comma-separated, no spaces. This is CORS-enforcing again now that frontend/backend are cross-origin — see "CORS" below. |
| `RUNPOD_API_KEY` | Your RunPod API key | **Currently blank.** Same key as the Vercel one above, used here by `_stop_runpod_pod()` for self-stop. |
| `RUNPOD_POD_ID` | Your pod's ID | **Currently blank.** Same ID as the Vercel one above. |
| `IDLE_CHECK_INTERVAL_MIN` | `10` (default) | How often the idle loop checks. |
| `IDLE_STOP_THRESHOLD_MIN` | `10` (default) | How many idle minutes (no authenticated request + empty queue) before self-stop. |

**Important:** if `RUNPOD_API_KEY`/`RUNPOD_POD_ID` are left blank on the
backend, `_idle_stop_loop` no-ops (logs once and exits) — this is the
intended local-dev fallback, but it also means idle auto-stop silently does
nothing until you actually fill these in on the pod.

## What you need to do before this is testable

1. Deploy `frontend/` to Vercel (root directory `frontend`), and get the
   assigned `*.vercel.app` domain.
2. In the Vercel dashboard, set the four env vars in the table above.
3. On the RunPod pod, edit `backend/.env`:
   - Fill in `RUNPOD_API_KEY` and `RUNPOD_POD_ID` (same values as step 2).
   - Replace the `ALLOWED_ORIGINS` placeholder with your real Vercel domain
     from step 1 (keep the `*.proxy.runpod.net` and `localhost:5173` entries
     too if you still want direct/local access to work).
4. Restart the backend process on the pod so it picks up the new `.env`.
5. Redeploy the Vercel frontend if you changed env vars after the first
   deploy (Vercel only bakes `VITE_`-prefixed vars in at build time).

## Wake flow: timeout and error handling (what the user actually sees)

`frontend/src/wake.ts` polls `frontend/api/wake.ts` every 3s, client-side
timeout 130s. `api/wake.ts` itself has a 120s server-side timeout budget
(tracked via a `startedAt` query param, since Edge Functions are stateless
per-invocation) — the server's timeout fires first in the normal case, so its
more specific message wins the race.

Every terminal state surfaces as English text via `StudioShell`'s
`wakeMessage`/`modelStatus` state and the `GenerateButton`'s `warming` prop:

- **Misconfigured Vercel env** (`RUNPOD_API_KEY`/`RUNPOD_POD_ID`/`VITE_BACKEND_URL` unset) → *"Server misconfigured: RUNPOD_API_KEY, RUNPOD_POD_ID, or VITE_BACKEND_URL is not set in the Vercel project env."*
- **Bad API key** (RunPod returns 401) → *"RunPod rejected the API key (401) -- check RUNPOD_API_KEY."*
- **Bad pod ID** (RunPod returns 404) → *"RunPod pod &lt;id&gt; not found (404) -- check RUNPOD_POD_ID."*
- **Pod terminated** (not just stopped) → *"This RunPod pod has been terminated and cannot be resumed automatically -- start a new pod and update RUNPOD_POD_ID."* (no auto-recovery — terminated pods are gone, not resumable)
- **Failed to start the pod** (RunPod start call errors) → *"Failed to start the RunPod pod (HTTP &lt;code&gt;)."*
- **Server-side timeout** (>120s waiting, still not ready) → *"Timed out after 120s waiting for the backend to start. It may still be booting -- check the RunPod dashboard, or try again."*
- **Client-side timeout** (>130s, only reachable if the server never terminates the loop for some reason) → *"Timed out waiting for the backend to start. Check the RunPod dashboard."*
- **Transient network blip calling `/api/wake` itself** → not surfaced as an error — treated as "still starting" and retried on the next 3s tick, so a single flaky request doesn't fail the whole flow.

In all error cases, `StudioShell` sets `modelStatus = 'down'` and shows the
message in a status badge; `handleGenerateAll`'s own `wakeBackend()` call
(the pre-Generate race guard) additionally surfaces the rejection as a
user-facing error near the Generate button, and the button reverts from
"Warming up the voice model..." to its normal disabled/enabled state — it
never gets stuck showing "Warming up" forever.

## Confirmed: in-flight/queued jobs block auto-stop

`backend/main.py`'s `_idle_stop_loop` checks, every `IDLE_CHECK_INTERVAL_MIN`:

```python
with _jobs_lock:
    busy = _current_running_job_id is not None or bool(_pending_job_ids)
if busy:
    continue  # skip this check entirely, no stop
```

This is evaluated *before* the idle-duration check, so a pod with a running
or queued job is never stopped, no matter how long `IDLE_STOP_THRESHOLD_MIN`
has otherwise elapsed. The loop only proceeds to stop the pod when the queue
is completely empty AND no authenticated request (any protected endpoint —
`backend/auth.py`'s `get_current_user` updates the last-activity timestamp on
every call) has landed within the threshold.

## CORS: confirmed active, not dead code

`CORSMiddleware` is registered unconditionally in `backend/main.py`
(`app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, ...)`).
It was inert before only because frontend and backend shared an origin, so
the browser never needed the preflight/response headers it adds. Now that
they're cross-origin, `ALLOWED_ORIGINS` is load-bearing: any origin not
listed there gets its API responses blocked by the browser. This is why
filling in the real Vercel domain (step 3 above) is required, not optional.

## Part 6: reference-audio duration limit, 15s -> 60s

Every place the old 15-second limit lived, and its current (already-applied,
uncommitted) state:

| Location | Before | After |
|---|---|---|
| `backend/main.py:112` — `MAX_REF_AUDIO_SECS` | `15.0` | `60.0` |
| `backend/main.py:693-707` — `create_preset` validation error message | interpolates `MAX_REF_AUDIO_SECS`, so it read "maximum 15.0s" | now reads "maximum 60.0s" automatically (no separate hardcoded string) |
| `frontend/src/components/LandingPage.tsx:31` | "2-15 second reference clip" | "2-60 second reference clip" |
| `frontend/src/components/LandingPage.tsx:55` | "2-15 second reference clip" | "2-60 second reference clip" |
| `frontend/src/components/LandingPage.tsx:121` | "(2-15 sec)" | "(2-60 sec)" |
| `frontend/src/components/LandingPage.tsx:166` | "2-15s" spec value | "2-60s" spec value |

No frontend-side JS duration check exists (`ReferenceUpload.tsx` just wires a
raw file input) — the 15s/60s limit was always backend-enforced only, so
there was no separate client-side number to find and change. Confirmed no
other "15 second" references remain anywhere in `backend/` or `frontend/src/`.

## Remaining manual steps (not something code can do for you)

- Fill in the real values for `RUNPOD_API_KEY`, `RUNPOD_POD_ID`, and the
  production `ALLOWED_ORIGINS` domain — these require your actual RunPod
  account/pod and Vercel deployment, so they're left as placeholders in
  `backend/.env` and must be set for real before this is live.
