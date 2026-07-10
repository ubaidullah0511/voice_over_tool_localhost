export type WakeStatus = 'starting' | 'ready' | 'error'

interface WakeResponse {
  status: WakeStatus
  message?: string
}

const POLL_INTERVAL_MS = 3000
// Small buffer over api/wake.ts's own ~120s server-side timeout, so the
// server's timeout message (more specific) wins the race in the common case.
const CLIENT_TIMEOUT_MS = 130_000

async function callWake(startedAt: number): Promise<WakeResponse> {
  const res = await fetch(`/api/wake?startedAt=${startedAt}`)
  if (!res.ok) {
    return { status: 'error', message: `Wake endpoint returned HTTP ${res.status}.` }
  }
  return res.json() as Promise<WakeResponse>
}

/** Wakes the RunPod backend (via the Vercel api/wake proxy) and resolves once
 * it reports ready, or rejects with a human-readable message on error/
 * timeout. Safe to call when the backend is already running -- resolves on
 * the first poll in that case. `onStatus` fires after every poll so the UI
 * can show live progress ("Warming up... 42s"). */
export function wakeBackend(
  onStatus?: (status: WakeStatus, elapsedMs: number) => void,
): Promise<void> {
  const startedAt = Date.now()
  return new Promise((resolve, reject) => {
    const poll = async () => {
      const elapsedMs = Date.now() - startedAt
      if (elapsedMs > CLIENT_TIMEOUT_MS) {
        onStatus?.('error', elapsedMs)
        reject(new Error('Timed out waiting for the backend to start. Check the RunPod dashboard.'))
        return
      }
      let result: WakeResponse
      try {
        result = await callWake(startedAt)
      } catch {
        // Transient network hiccup calling our own same-origin endpoint --
        // keep polling rather than failing the whole flow on one blip.
        result = { status: 'starting' }
      }
      onStatus?.(result.status, elapsedMs)
      if (result.status === 'ready') {
        resolve()
      } else if (result.status === 'error') {
        reject(new Error(result.message || 'Failed to start the backend.'))
      } else {
        setTimeout(poll, POLL_INTERVAL_MS)
      }
    }
    poll()
  })
}
