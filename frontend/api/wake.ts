// Vercel Edge Function: wakes the RunPod backend on demand and reports
// readiness. This is the only place RUNPOD_API_KEY / RUNPOD_POD_ID may be
// read -- they live in Vercel's server-side project env and must never be
// exposed to the client bundle (unlike VITE_-prefixed vars, which Vite
// inlines into the built frontend at compile time).
//
// Design note: rather than blocking inside a single invocation for up to two
// minutes (risking Vercel's function duration limits), each call does ONE
// cheap round of work -- check pod status, resume if needed, single health
// check -- and returns immediately. The frontend re-calls this endpoint every
// few seconds and treats it as the poll loop; "starting" just means "call me
// again shortly." Elapsed time is tracked via a `startedAt` query param the
// client sets once and echoes back on every call, since Edge Functions are
// stateless across invocations.
export const config = { runtime: 'edge' }

type WakeStatus = 'starting' | 'ready' | 'error'

interface WakeResponse {
  status: WakeStatus
  message?: string
}

const RUNPOD_REST_BASE = 'https://rest.runpod.io/v1'
const OVERALL_TIMEOUT_MS = 120_000 // matches the ~2min budget in the spec
const FETCH_TIMEOUT_MS = 8_000 // per outbound request, keeps each invocation fast

function json(body: WakeResponse, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export default async function handler(req: Request): Promise<Response> {
  const apiKey = process.env.RUNPOD_API_KEY
  const podId = process.env.RUNPOD_POD_ID
  const backendUrl = process.env.VITE_BACKEND_URL

  if (!apiKey || !podId || !backendUrl) {
    return json(
      {
        status: 'error',
        message:
          'Server misconfigured: RUNPOD_API_KEY, RUNPOD_POD_ID, or VITE_BACKEND_URL is not set in the Vercel project env.',
      },
      500,
    )
  }

  const url = new URL(req.url)
  const startedAtParam = url.searchParams.get('startedAt')
  const startedAt = startedAtParam ? Number(startedAtParam) : Date.now()
  const elapsedMs = Date.now() - startedAt

  if (Number.isFinite(elapsedMs) && elapsedMs > OVERALL_TIMEOUT_MS) {
    return json({
      status: 'error',
      message: `Timed out after ${Math.round(OVERALL_TIMEOUT_MS / 1000)}s waiting for the backend to start. It may still be booting -- check the RunPod dashboard, or try again.`,
    })
  }

  const authHeaders = { Authorization: `Bearer ${apiKey}` }

  // 1. Check the pod's current desired status.
  let podStatus: string
  try {
    const res = await fetchWithTimeout(
      `${RUNPOD_REST_BASE}/pods/${podId}`,
      { headers: authHeaders },
      FETCH_TIMEOUT_MS,
    )
    if (res.status === 401) {
      return json({ status: 'error', message: 'RunPod rejected the API key (401) -- check RUNPOD_API_KEY.' })
    }
    if (res.status === 404) {
      return json({ status: 'error', message: `RunPod pod ${podId} not found (404) -- check RUNPOD_POD_ID.` })
    }
    if (!res.ok) {
      return json({ status: 'error', message: `RunPod status check failed (HTTP ${res.status}).` })
    }
    const data = (await res.json()) as { desiredStatus?: string }
    podStatus = data.desiredStatus ?? 'UNKNOWN'
  } catch (e) {
    return json({
      status: 'error',
      message: `Could not reach the RunPod API: ${e instanceof Error ? e.message : String(e)}`,
    })
  }

  if (podStatus === 'TERMINATED') {
    return json({
      status: 'error',
      message:
        'This RunPod pod has been terminated and cannot be resumed automatically -- start a new pod and update RUNPOD_POD_ID.',
    })
  }

  // 2. Resume it if it isn't already running. desiredStatus flips to RUNNING
  // almost immediately after this call, well before the container -- and the
  // model inside it -- is actually ready, so step 3 is the real readiness
  // gate, not this status field.
  if (podStatus !== 'RUNNING') {
    try {
      const res = await fetchWithTimeout(
        `${RUNPOD_REST_BASE}/pods/${podId}/start`,
        { method: 'POST', headers: authHeaders },
        FETCH_TIMEOUT_MS,
      )
      // A 400 here typically means "already starting/running" -- non-fatal.
      if (!res.ok && res.status !== 400) {
        return json({ status: 'error', message: `Failed to start the RunPod pod (HTTP ${res.status}).` })
      }
    } catch (e) {
      return json({
        status: 'error',
        message: `Failed to start the RunPod pod: ${e instanceof Error ? e.message : String(e)}`,
      })
    }
    return json({ status: 'starting' })
  }

  // 3. Pod is running -- the real readiness gate is the backend's own health
  // check, which only reports model_loaded: true once the TTS model has
  // finished loading onto the GPU (can take 30-90s+ after the container boots).
  try {
    const res = await fetchWithTimeout(`${backendUrl}/api/health`, {}, FETCH_TIMEOUT_MS)
    if (!res.ok) return json({ status: 'starting' })
    const health = (await res.json()) as { model_loaded?: boolean }
    return json({ status: health.model_loaded ? 'ready' : 'starting' })
  } catch {
    // Pod says RUNNING but the backend isn't answering yet (container still
    // booting / installing deps / uvicorn not up) -- keep polling.
    return json({ status: 'starting' })
  }
}
