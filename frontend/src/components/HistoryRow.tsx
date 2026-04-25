import type { ArticleSummary, Source } from '../types'
import { relativeDate } from '../utils'

export function HistoryRow({ article, sources, onLoad, onPin }: {
  article: ArticleSummary
  sources: Source[]
  onLoad: (id: string) => void
  onPin?: (id: string, pinned: boolean) => void
}) {
  const sourceName = sources.find(s => String(s.id) === String(article.source_id))?.name ?? article.source_id
  return (
    <div
      className="history-row"
      onClick={() => onLoad(article.id)}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && onLoad(article.id)}
    >
      <div className="history-title" title={article.title}>{article.title}</div>
      <div className="history-meta">
        <span className="small">{sourceName}</span>
        <span className="small">{relativeDate(article.created_at)}</span>
        {article.has_audio && <span className="badge badge-audio">audio</span>}
        {article.has_video && <span className="badge badge-video">video</span>}
        {article.analysis_sentiment && (
          <span className={`analysis-badge analysis-${article.analysis_sentiment}`} style={{ fontSize: 9 }}>
            {article.analysis_sentiment}
          </span>
        )}
        {article.analysis_impact != null && (
          <span className="small" style={{ fontSize: 9 }}>{article.analysis_impact}/10</span>
        )}
        {onPin && (
          <button
            className="pin-btn"
            title={article.is_pinned ? 'Unpin' : 'Pin'}
            onClick={e => { e.stopPropagation(); onPin(article.id, !article.is_pinned) }}
          >
            {article.is_pinned ? '★' : '☆'}
          </button>
        )}
      </div>
    </div>
  )
}
