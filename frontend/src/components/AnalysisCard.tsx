import type { AnalysisResult } from '../types'

export function AnalysisCard({ analysis }: { analysis: AnalysisResult }) {
  return (
    <div className="analysis-section">
      <div className="analysis-row">
        <span className={`analysis-badge analysis-${analysis.sentiment}`}>{analysis.sentiment}</span>
        <span className={`analysis-badge analysis-urgency-${analysis.medical_urgency}`}>{analysis.medical_urgency}</span>
        <span className="analysis-score small">impact {analysis.impact_score}/10</span>
        {analysis.audience_relevance && (
          <span className="small" style={{ color: '#6b7280' }}>{analysis.audience_relevance}</span>
        )}
      </div>
      {analysis.key_claims.length > 0 && (
        <div className="analysis-claims small">
          {analysis.key_claims.map((c, i) => <div key={i} className="analysis-claim">• {c}</div>)}
        </div>
      )}
    </div>
  )
}
