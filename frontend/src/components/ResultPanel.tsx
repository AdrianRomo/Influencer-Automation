import type { ReactNode } from 'react'

/**
 * 9:16 phone-style frame for previewing short-form video. Wraps whatever is
 * passed (a <video>, an <img>, or a placeholder) so the result always reads as
 * a real vertical clip. Children should fill the frame (width/height 100%).
 */
export function VideoPreviewPanel({
  children,
  caption,
}: {
  children: ReactNode
  caption?: string
}) {
  return (
    <div className="phone-preview">
      <div className="phone-frame">{children}</div>
      {caption && <div className="phone-caption small">{caption}</div>}
    </div>
  )
}

/**
 * Primary actions for a finished video. Download is the obvious primary action;
 * the rest are secondary recovery/iteration paths. Handlers are optional —
 * buttons only render when the parent can actually perform the action.
 */
export function ResultActions({
  downloadUrl,
  onRegenerateBackground,
  onRegenerateSubtitles,
  onEditScript,
  onNewVideo,
  busy,
}: {
  downloadUrl?: string
  onRegenerateBackground?: () => void
  onRegenerateSubtitles?: () => void
  onEditScript?: () => void
  onNewVideo?: () => void
  busy?: boolean
}) {
  return (
    <div className="result-actions">
      {downloadUrl && (
        <a href={downloadUrl} className="dl-btn dl-btn-primary result-action-primary" download>
          Download MP4
        </a>
      )}
      <div className="result-actions-secondary">
        {onRegenerateBackground && (
          <button type="button" className="secondary settings-btn" onClick={onRegenerateBackground} disabled={busy}>
            Regenerate background
          </button>
        )}
        {onRegenerateSubtitles && (
          <button type="button" className="secondary settings-btn" onClick={onRegenerateSubtitles} disabled={busy}>
            Regenerate subtitles
          </button>
        )}
        {onEditScript && (
          <button type="button" className="secondary settings-btn" onClick={onEditScript} disabled={busy}>
            Edit script
          </button>
        )}
        {onNewVideo && (
          <button type="button" className="secondary settings-btn" onClick={onNewVideo} disabled={busy}>
            Create another
          </button>
        )}
      </div>
    </div>
  )
}
