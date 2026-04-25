import { useState } from 'react'
import type { SocialCaption } from '../types'

const PLATFORM_LABELS: Record<string, string> = {
  tiktok: 'TikTok', reels: 'Reels', youtube_shorts: 'YT Shorts',
  youtube: 'YouTube', facebook: 'Facebook',
}

export function SocialCaptionsPanel({ captions }: { captions: SocialCaption[] }) {
  const [copied, setCopied] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState(captions[0]?.platform ?? '')

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key)
      window.setTimeout(() => setCopied(null), 2000)
    }).catch(() => {})
  }

  const active = captions.find(c => c.platform === activeTab) ?? captions[0]
  if (!active) return null

  return (
    <div className="social-captions-panel">
      <div className="social-captions-tabs">
        {captions.map(c => (
          <button
            key={c.platform}
            className={`social-tab ${c.platform === activeTab ? 'social-tab-active' : ''}`}
            onClick={() => setActiveTab(c.platform)}
          >
            {PLATFORM_LABELS[c.platform] ?? c.platform}
          </button>
        ))}
      </div>
      <div className="social-caption-body">
        <div className="social-caption-text">{active.caption}</div>
        {active.hashtags.length > 0 && (
          <div className="social-hashtags">
            {active.hashtags.map(h => (
              <span key={h} className="social-hashtag">{h}</span>
            ))}
          </div>
        )}
        <div className="actions" style={{ marginTop: 10 }}>
          <button
            className="secondary settings-btn"
            onClick={() => copy(`${active.caption}\n\n${active.hashtags.join(' ')}`, `${activeTab}-full`)}
          >
            {copied === `${activeTab}-full` ? 'Copied!' : 'Copy Caption + Hashtags'}
          </button>
          <button
            className="secondary settings-btn"
            onClick={() => copy(active.hashtags.join(' '), `${activeTab}-tags`)}
          >
            {copied === `${activeTab}-tags` ? 'Copied!' : 'Copy Hashtags'}
          </button>
        </div>
      </div>
    </div>
  )
}
