import { useState } from 'react'
import { friendlyError, type FriendlyError } from '../utils'

type RecoveryAction = NonNullable<FriendlyError['action']>

const ACTION_LABEL: Record<RecoveryAction, string> = {
  'retry-video': 'Retry video',
  'retry-images': 'Retry visual background',
  'add-keys': 'Add API keys',
  'reload': 'Try again',
}

/**
 * Turns a raw error into a friendly, actionable recovery card.
 *
 * Pass the raw error (string or Error). The card derives a plain-language
 * title/detail and, when handlers are supplied for the mapped action, renders
 * a primary recovery button. Raw text is hidden behind a "Details" toggle so
 * it's available for debugging without scaring users.
 */
export function ErrorRecoveryCard({
  error,
  onRetryVideo,
  onRetryImages,
  onAddKeys,
  onReload,
  onRetry,
  retryLabel,
  compact = false,
}: {
  error: unknown
  onRetryVideo?: () => void
  onRetryImages?: () => void
  onAddKeys?: () => void
  onReload?: () => void
  /** Generic fallback used when the mapped action has no specific handler. */
  onRetry?: () => void
  /** Override the primary button label (e.g. "Try again" for script/audio). */
  retryLabel?: string
  compact?: boolean
}) {
  const [showRaw, setShowRaw] = useState(false)
  if (!error) return null
  const fe = friendlyError(error)

  const handlers: Record<RecoveryAction, (() => void) | undefined> = {
    'retry-video': onRetryVideo,
    'retry-images': onRetryImages,
    'add-keys': onAddKeys,
    'reload': onReload,
  }
  const action = fe.action
  // Prefer the action-specific handler; fall back to the generic onRetry so a
  // recovery button is always offered when the caller can retry at all.
  const handler = (action ? handlers[action] : undefined) ?? (action === 'add-keys' ? undefined : onRetry)
  const label = action === 'add-keys' ? ACTION_LABEL['add-keys'] : (retryLabel ?? (action ? ACTION_LABEL[action] : 'Try again'))

  return (
    <div className={`error-card${compact ? ' error-card-compact' : ''}`} role="alert">
      <div className="error-card-icon" aria-hidden>!</div>
      <div className="error-card-body">
        <div className="error-card-title">{fe.title}</div>
        <div className="error-card-detail">{fe.detail}</div>
        <div className="error-card-actions">
          {handler && (
            <button type="button" className="error-card-retry" onClick={handler}>
              {label}
            </button>
          )}
          {fe.raw && (
            <button
              type="button"
              className="error-card-details-toggle"
              onClick={() => setShowRaw(v => !v)}
              aria-expanded={showRaw}
            >
              {showRaw ? 'Hide details' : 'Details'}
            </button>
          )}
        </div>
        {showRaw && fe.raw && <pre className="error-card-raw">{fe.raw}</pre>}
      </div>
    </div>
  )
}
