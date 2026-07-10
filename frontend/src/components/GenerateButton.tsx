import { motion } from 'framer-motion'
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion'
import { WandIcon } from './Icons'

interface Props {
  disabled: boolean
  busy: boolean
  /** True while waking the backend, before any job has actually been
   * submitted -- shown as distinct copy from `busy` so a 30-90s wake isn't
   * mistaken for a stuck submit. */
  warming?: boolean
  count: number
  /** Increment to fire the one-shot pulse ring (keyed remount). */
  pulseEpoch: number
  onClick: () => void
  noCredits?: boolean
}

// framer-motion interpolates box-shadows only between literal strings, so
// the elevation states live here rather than in CSS vars.
const SHADOW_REST =
  '0 1px 0 rgba(255,255,255,0.10) inset, 0 12px 32px rgba(0,0,0,0.55), 0 6px 24px rgba(240,168,61,0.25)'
const SHADOW_HOVER =
  '0 1px 0 rgba(255,255,255,0.12) inset, 0 16px 40px rgba(0,0,0,0.60), 0 8px 30px rgba(240,168,61,0.35)'
const SHADOW_TAP =
  '0 1px 0 rgba(255,255,255,0.06) inset, 0 3px 10px rgba(0,0,0,0.50), 0 2px 8px rgba(240,168,61,0.30)'

export default function GenerateButton({
  disabled,
  busy,
  warming,
  count,
  pulseEpoch,
  onClick,
  noCredits,
}: Props) {
  const reducedMotion = usePrefersReducedMotion()
  const interactive = !disabled && !reducedMotion

  return (
    <div className="generate-dock">
      {!reducedMotion && pulseEpoch > 0 && (
        <motion.span
          key={pulseEpoch}
          className="generate-pulse"
          initial={{ scale: 1, opacity: 0.9 }}
          animate={{ scale: 1.55, opacity: 0 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
        />
      )}
      <motion.button
        type="button"
        className="generate-fab"
        disabled={disabled}
        onClick={onClick}
        initial={false}
        animate={{ boxShadow: SHADOW_REST, y: 0, scale: 1 }}
        whileHover={interactive ? { y: -2, boxShadow: SHADOW_HOVER } : undefined}
        whileTap={interactive ? { scale: 0.94, boxShadow: SHADOW_TAP } : undefined}
        transition={{ type: 'spring', stiffness: 400, damping: 26 }}
      >
        <WandIcon />
        <span>
          {warming
            ? 'Warming up the voice model...'
            : busy
              ? 'Submitting...'
              : noCredits
                ? 'No credits remaining'
                : 'Generate all'}
        </span>
        {!busy && !warming && !noCredits && count > 1 && (
          <span className="mono generate-count">{count}</span>
        )}
      </motion.button>
    </div>
  )
}
