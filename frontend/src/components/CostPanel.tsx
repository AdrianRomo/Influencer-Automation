import { useState } from 'react'
import type { CostSummary } from '../types'

export function CostPanel({ cost }: { cost: CostSummary }) {
  const [expanded, setExpanded] = useState(false)
  const fmt = (n: number) => n < 0.001 ? '<$0.001' : `$${n.toFixed(4)}`
  const fmtNum = (n: number) => n.toLocaleString()

  const stageLabels: Record<string, string> = {
    script: 'Script', rewrite: 'Rewrite', storyboard: 'Storyboard',
    analysis: 'Analysis', image: 'Images', tts: 'Voiceover',
  }

  return (
    <div className="cost-panel">
      <div className="cost-panel-header" onClick={() => setExpanded(e => !e)} role="button" tabIndex={0}
           onKeyDown={e => e.key === 'Enter' && setExpanded(v => !v)}>
        <span className="cost-panel-title">
          <span className="cost-icon">💰</span> Estimated Cost
        </span>
        <span className="cost-total">{fmt(cost.total_estimated_usd)}</span>
        <span className="cost-chevron">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="cost-panel-body">
          <div className="cost-section-label">By provider</div>
          <div className="cost-rows">
            {Object.entries(cost.by_provider).map(([p, v]) => (
              <div key={p} className="cost-row">
                <span className={`cost-provider-badge cost-provider-${p}`}>{p}</span>
                <span className="cost-row-value">{fmt(v)}</span>
              </div>
            ))}
          </div>

          <div className="cost-section-label" style={{ marginTop: 10 }}>By stage</div>
          <div className="cost-rows">
            {Object.entries(cost.by_stage).map(([s, v]) => (
              <div key={s} className="cost-row">
                <span className="cost-stage">{stageLabels[s] ?? s}</span>
                <span className="cost-row-value">{fmt(v)}</span>
              </div>
            ))}
          </div>

          <div className="cost-usage-row small">
            {cost.total_tokens > 0 && <span>{fmtNum(cost.total_tokens)} tokens</span>}
            {cost.total_characters > 0 && <span>{fmtNum(cost.total_characters)} chars</span>}
            <span>{cost.event_count} API calls</span>
          </div>

          <div className="cost-note small">{cost.pricing_note}</div>
        </div>
      )}
    </div>
  )
}
