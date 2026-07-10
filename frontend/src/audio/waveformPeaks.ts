import { audioEngine } from './AudioEngine'

export interface PeaksResult {
  peaks: Float32Array
  sampleRate: number
  duration: number
}

// Keyed by URL; stores the in-flight promise so concurrent rows requesting
// the same clip share one decode. Only the downsampled Float32Array is
// retained (never the AudioBuffer), so 40 entries is a few tens of KB.
const cache = new Map<string, Promise<PeaksResult | null>>()
const MAX_ENTRIES = 40

/** Plausible-speech placeholder: seeded envelope x double-sine. Used while
 * a decode is in flight and as the fallback when decoding isn't possible. */
export function proceduralPeaks(seedStr: string, buckets = 96): Float32Array {
  let hash = 0
  for (let i = 0; i < seedStr.length; i++) hash = (hash * 31 + seedStr.charCodeAt(i)) | 0
  const seed = ((Math.abs(hash) % 1000) / 1000) * Math.PI * 2
  const peaks = new Float32Array(buckets)
  for (let i = 0; i < buckets; i++) {
    const env = Math.min(1, i / 6) * Math.min(1, (buckets - i) / 10)
    peaks[i] =
      env *
      (0.35 +
        0.45 * Math.abs(Math.sin(i * 0.53 + seed)) * Math.abs(Math.sin(i * 0.11 + seed * 1.7)))
  }
  return peaks
}

export function getPeaks(url: string, buckets = 96): Promise<PeaksResult | null> {
  const cached = cache.get(url)
  if (cached) return cached
  const promise = compute(url, buckets).catch(() => null)
  cache.set(url, promise)
  if (cache.size > MAX_ENTRIES) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  return promise
}

/** True source sample rate from the RIFF/WAVE header. decodeAudioData
 * resamples to the AudioContext rate, so AudioBuffer.sampleRate reports
 * the context's rate (e.g. 44.1k) even for a 24k TTS clip. */
function wavSampleRate(buf: ArrayBuffer): number | null {
  const view = new DataView(buf)
  if (buf.byteLength < 44) return null
  if (view.getUint32(0, false) !== 0x52494646) return null // 'RIFF'
  if (view.getUint32(8, false) !== 0x57415645) return null // 'WAVE'
  let offset = 12
  while (offset + 8 <= view.byteLength) {
    const chunkId = view.getUint32(offset, false)
    const chunkSize = view.getUint32(offset + 4, true)
    if (chunkId === 0x666d7420) return view.getUint32(offset + 12, true) // 'fmt '
    offset += 8 + chunkSize + (chunkSize & 1)
  }
  return null
}

async function compute(url: string, buckets: number): Promise<PeaksResult | null> {
  // audio_url/preview_url are cross-origin now (Vercel frontend, RunPod
  // backend) -- this relies on the backend's CORS middleware allowing the
  // frontend's origin (see ALLOWED_ORIGINS in backend/.env). getPeaks()
  // catches any failure here and falls back to the procedural shape, so a
  // misconfigured origin just degrades to a fake waveform rather than erroring.
  const res = await fetch(url)
  if (!res.ok) return null
  const buf = await res.arrayBuffer()
  // Read the header before decoding -- decodeAudioData may detach the buffer.
  const sourceRate = wavSampleRate(buf)
  const audio = await audioEngine.decode(buf)
  const data = audio.getChannelData(0)
  const peaks = new Float32Array(buckets)
  const perBucket = Math.max(1, Math.floor(data.length / buckets))
  let max = 0
  for (let b = 0; b < buckets; b++) {
    let m = 0
    const start = b * perBucket
    const end = Math.min(data.length, start + perBucket)
    for (let i = start; i < end; i += 4) {
      const v = Math.abs(data[i])
      if (v > m) m = v
    }
    peaks[b] = m
    if (m > max) max = m
  }
  if (max > 0) {
    const norm = 0.9 / max
    for (let b = 0; b < buckets; b++) peaks[b] *= norm
  }
  return { peaks, sampleRate: sourceRate ?? audio.sampleRate, duration: audio.duration }
}
