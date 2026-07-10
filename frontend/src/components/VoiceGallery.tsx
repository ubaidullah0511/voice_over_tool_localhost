import { useEffect, useRef, useState } from 'react'
import { mediaUrl, type Preset } from '../api'
import { useAudioActivity } from '../AudioActivityContext'
import { useGenerationActivity } from '../GenerationActivityContext'
import { usePageVisible } from '../hooks/usePageVisible'
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion'
import { CheckIcon, PauseIcon, PlayIcon, TrashIcon } from './Icons'
import TiltCard from './TiltCard'
import { createPortal } from 'react-dom'

interface Props {
  presets: Preset[]
  selectedPresetId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

// Amber/bronze/warm-graphite family derived from the studio palette --
// warm console tones, never recording-red.
const AVATAR_GRADIENTS = [
  'linear-gradient(135deg, #f0a83d, #c77f2a)',
  'linear-gradient(135deg, #c77f2a, #5a4a33)',
  'linear-gradient(135deg, #8b8d91, #4a4d52)',
  'linear-gradient(135deg, #e0b878, #96702f)',
  'linear-gradient(135deg, #5a4a33, #2c2620)',
  'linear-gradient(135deg, #b9955c, #6e5636)',
]

function avatarGradient(seed: string): string {
  let hash = 0
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0
  return AVATAR_GRADIENTS[Math.abs(hash) % AVATAR_GRADIENTS.length]
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** Tiny procedural pulse meter shown on a card while its voice is
 * generating. 2D canvas -- no WebGL context cost per card. */
function MiniMeter() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const pageVisible = usePageVisible()
  const reducedMotion = usePrefersReducedMotion()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = 24
    const h = 16
    canvas.width = w * dpr
    canvas.height = h * dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    let frame = 0
    let raf = 0
    function draw(f: number) {
      ctx!.clearRect(0, 0, w, h)
      const bars = 5
      for (let i = 0; i < bars; i++) {
        const amp = reducedMotion ? 0.55 : 0.3 + 0.65 * Math.abs(Math.sin(f * 0.12 + i * 0.9))
        const barH = Math.max(2, amp * h)
        ctx!.fillStyle = 'rgba(240, 168, 61, 0.9)'
        ctx!.fillRect(i * 5, (h - barH) / 2, 3, barH)
      }
    }

    if (reducedMotion || !pageVisible) {
      draw(0)
      return
    }
    function loop() {
      frame++
      draw(frame)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [reducedMotion, pageVisible])

  return <canvas ref={canvasRef} className="mini-meter" aria-label="Generating" />
}

export default function VoiceGallery({ presets, selectedPresetId, onSelect, onDelete }: Props) {
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const { setActiveAudio } = useAudioActivity()
  const { runningPresetNames } = useGenerationActivity()

  function togglePreview(preset: Preset) {
    const audio = audioRef.current
    if (!audio) return
    if (previewingId === preset.id) {
      audio.pause()
      setPreviewingId(null)
      return
    }
    audio.src = mediaUrl(preset.preview_url)
    audio.play().catch(() => {})
    setPreviewingId(preset.id)
  }

  const builtins = presets.filter((p) => p.is_builtin)
  const custom = presets.filter((p) => !p.is_builtin)

  const confirmingPreset = presets.find((p) => p.id === confirmingId) ?? null

  function renderCard(preset: Preset) {
    const selected = preset.id === selectedPresetId
    const previewing = previewingId === preset.id
    const generating = runningPresetNames.has(preset.name)
    const classNames = ['gallery-card']
    if (selected) classNames.push('gallery-card-selected')
    if (generating) classNames.push('card-generating')
    return (
      <TiltCard key={preset.id} className={classNames.join(' ')}>
        <button
          type="button"
          className="icon-btn gallery-preview-btn"
          aria-label={previewing ? `Pause preview of ${preset.name}` : `Preview ${preset.name}`}
          onClick={() => togglePreview(preset)}
        >
          {previewing ? <PauseIcon size={13} /> : <PlayIcon size={13} />}
        </button>
        <button type="button" className="gallery-card-main" onClick={() => onSelect(preset.id)}>
          <span className="avatar" style={{ background: avatarGradient(preset.name) }}>
            {initials(preset.name)}
          </span>
          <span className="gallery-card-body">
            <strong>{preset.name}</strong>
            <span className="gallery-card-tags">
              <span className="badge-pill">{preset.language}</span>
              {preset.tag && <span className="badge-pill badge-pill-accent">{preset.tag}</span>}
            </span>
          </span>
          {generating && <MiniMeter />}
          {selected && (
            <span className="selected-check">
              <CheckIcon size={12} />
            </span>
          )}
        </button>
        <button
          type="button"
          className="icon-btn icon-btn-danger gallery-delete"
          aria-label={`Delete ${preset.name}`}
          onClick={() => setConfirmingId(preset.id)}
        >
          <TrashIcon size={14} />
        </button>
      </TiltCard>
      
    )
  }

  return (
    <section className="panel rail-voices">
      <div className="panel-header">
        <h2>Voices</h2>
        {presets.length > 0 && <span className="count-badge mono">{presets.length}</span>}
      </div>

      {presets.length === 0 ? (
        <p className="empty-hint">No saved presets yet -- create one below to get started.</p>
      ) : (
        <>
          {builtins.length > 0 && (
            <>
              <span className="gallery-section-label">Studio Voices</span>
              <div className="gallery-grid">{builtins.map(renderCard)}</div>
            </>
          )}
          {custom.length > 0 && (
            <>
              <span className="gallery-section-label">My Voices</span>
              <div className="gallery-grid">{custom.map(renderCard)}</div>
            </>
          )}
        </>
      )}

      {/* Delete confirmation modal -- single instance shared by all cards,
          keyed off confirmingId so hooks stay outside the per-card render loop. */}
      
      {confirmingPreset && createPortal(
      <div className="modal-backdrop" onClick={() => setConfirmingId(null)}>
        <div className="panel modal-confirm" onClick={(e) => e.stopPropagation()}>
          <p>Delete "{confirmingPreset.name}"? This can't be undone.</p>
          <div className="modal-actions">
            <button className="modal-btn-cancel" onClick={() => setConfirmingId(null)}>
              Cancel
            </button>
            <button
              className="modal-btn-delete"
              onClick={() => {
                onDelete(confirmingPreset.id)
                setConfirmingId(null)
              }}
            >
              Delete
            </button>
          </div>
        </div>
      </div>,
      document.body
    )}

      {/* Preview playback reports into AudioActivityContext so the orb and
          meter react to voice previews too. */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio
        ref={audioRef}
        onPlay={(e) => setActiveAudio(e.currentTarget)}
        onPause={(e) => {
          if (e.currentTarget.ended) return
          setActiveAudio(null)
        }}
        onEnded={() => {
          setPreviewingId(null)
          setActiveAudio(null)
        }}
        style={{ display: 'none' }}
      />
    </section>
  )
}