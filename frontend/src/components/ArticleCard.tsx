import type { RssCandidate } from '../types'
import { relativeDate } from '../utils'

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(Math.min(score / 10, 1) * 100)
  return (
    <div className="score-bar-wrap" title={`Relevance: ${score.toFixed(1)} / 10`}>
      <div className="score-bar-track">
        <div className="score-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="score-label">{score.toFixed(1)}</span>
    </div>
  )
}

export function ArticleCard({ candidate, rank, onSelect }: {
  candidate: RssCandidate
  rank: number
  onSelect: () => void
}) {
  return (
    <div className="candidate-card">
      <div className="candidate-rank-badge">#{rank}</div>
      <div className="candidate-body">
        <div className="candidate-title">{candidate.title}</div>
        <div className="candidate-meta">
          <span className="small">{candidate.source_name}</span>
          {candidate.published_at && (
            <span className="small">{relativeDate(candidate.published_at)}</span>
          )}
          <ScoreBar score={candidate.score} />
        </div>
        {candidate.summary && (
          <div className="candidate-summary small">
            {candidate.summary.length > 220
              ? candidate.summary.slice(0, 220) + '…'
              : candidate.summary}
          </div>
        )}
        <div className="candidate-footer">
          <a
            href={candidate.url}
            target="_blank"
            rel="noreferrer"
            className="small link"
            onClick={e => e.stopPropagation()}
          >
            {new URL(candidate.url).hostname}
          </a>
          <button className="candidate-select-btn" onClick={onSelect}>
            Use This Article →
          </button>
        </div>
      </div>
    </div>
  )
}
