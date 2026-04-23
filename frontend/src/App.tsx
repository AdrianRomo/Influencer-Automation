import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AnalysisResult, ArticleSummary, ContentPackage, CostSummary, GenerateReq, ImageAssetRef,
  JobStatus, PlatformsResp, RenderMode, RssCandidate, SceneVideoRef, SocialCaption,
  Source, StoryboardScene, UserResp, UserKeysOut,
} from './types'
import {
  cancelJob, deleteArticle, editScript, fetchRssCandidates, getArticlePackage, getExportZipUrl,
  getPlatforms, jobStatus, listArticles, listSources, logout, pinArticle, prepareArticle,
  refreshAccessToken, regenerateStage, reorderStoryboard, resolveAudioUrl, resolveCaptionUrl,
  resolveImageUrl, resolveThumbnailUrl, resolveVideoUrl, setApiKey, startGenerate,
  startGenerateVideo, startRegenerateScript, uploadSceneImage, register, login, getMe,
  getUserKeys, saveUserKeys, setAuthToken, clearAuthToken, setRefreshToken,
} from './api'

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtSeconds(s: number | null | undefined): string {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

function assetBadgeClass(type: string): string {
  if (type === 'title-card') return 'badge badge-title'
  if (type === 'outro') return 'badge badge-outro'
  return 'badge badge-broll'
}

function relativeDate(iso: string): string {
  const d = new Date(iso)
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.floor(days / 7)}w ago`
  return d.toLocaleDateString()
}

const STATE_LABELS: Record<string, string> = {
  PENDING: 'Queued…',
  RECEIVED: 'Starting…',
  STARTED: 'Running…',
  RETRY: 'Retrying…',
  SUCCESS: 'Done',
  FAILURE: 'Failed',
  REVOKED: 'Cancelled',
}

function stageLabel(s: JobStatus): string {
  if (s.state === 'PROGRESS' && s.meta?.msg) return s.meta.msg
  return STATE_LABELS[s.state] ?? s.state
}

// ── Toast system ───────────────────────────────────────────────────────────

type ToastKind = 'success' | 'error' | 'info'
type Toast = { id: number; kind: ToastKind; msg: string }

let _toastSeq = 0

function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => onDismiss(t.id)}>
          {t.msg}
        </div>
      ))}
    </div>
  )
}

// ── Auth Modal ─────────────────────────────────────────────────────────────

function AuthModal({ onSuccess }: { onSuccess: (token: string, user: UserResp, refreshToken?: string) => void }) {
  const [tab, setTab] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const resp = tab === 'login'
        ? await login(email.trim(), password)
        : await register(email.trim(), password)
      setAuthToken(resp.access_token)
      localStorage.setItem('jwt_token', resp.access_token)
      if (resp.refresh_token) localStorage.setItem('refresh_token', resp.refresh_token)
      const user: UserResp = { id: resp.user_id, email: resp.email, created_at: '', has_keys: false }
      onSuccess(resp.access_token, user, resp.refresh_token)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-card">
        <div className="modal-title">Medical Content Generator</div>
        <div className="modal-subtitle">Sign in to generate and manage your content</div>

        <div className="tab-row">
          <button
            className={`tab-btn ${tab === 'login' ? 'tab-active' : ''}`}
            onClick={() => { setTab('login'); setError('') }}
            type="button"
          >Sign In</button>
          <button
            className={`tab-btn ${tab === 'register' ? 'tab-active' : ''}`}
            onClick={() => { setTab('register'); setError('') }}
            type="button"
          >Create Account</button>
        </div>

        <form onSubmit={submit} className="auth-form">
          <div>
            <label>Email</label>
            <input
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div>
            <label>Password {tab === 'register' && <span className="small">(min 8 chars)</span>}</label>
            <input
              type="password"
              placeholder={tab === 'register' ? 'Create a password' : 'Your password'}
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              minLength={tab === 'register' ? 8 : undefined}
            />
          </div>
          {error && <div className="error-text">{error}</div>}
          <button type="submit" disabled={loading} style={{ width: '100%', marginTop: 4 }}>
            {loading ? '…' : tab === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Keys Setup Modal ───────────────────────────────────────────────────────

function KeysModal({
  existing,
  onSave,
  onSkip,
}: {
  existing: UserKeysOut | null
  onSave: (keys: UserKeysOut) => void
  onSkip: () => void
}) {
  const [openaiKey, setOpenaiKey] = useState('')
  const [elKey, setElKey] = useState('')
  const [elVoice, setElVoice] = useState(existing?.elevenlabs_voice_id ?? '')
  const [elModel, setElModel] = useState(existing?.elevenlabs_model_id ?? 'eleven_multilingual_v2')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const saved = await saveUserKeys({
        openai_key: openaiKey.trim() || null,
        elevenlabs_key: elKey.trim() || null,
        elevenlabs_voice_id: elVoice.trim() || null,
        elevenlabs_model_id: elModel.trim() || null,
      })
      onSave(saved)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-card modal-card-wide">
        <div className="modal-title">Configure API Keys</div>
        <div className="modal-subtitle">
          Your keys are encrypted before storage and used only for your generations.
        </div>

        <form onSubmit={save} className="auth-form">
          <div>
            <label>OpenAI API Key {existing?.has_openai_key && <span className="badge badge-audio" style={{ marginLeft: 6 }}>saved</span>}</label>
            <input
              type="password"
              placeholder={existing?.has_openai_key ? '••••••••••••••••••••••••••• (leave blank to keep)' : 'sk-…'}
              value={openaiKey}
              onChange={e => setOpenaiKey(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div>
            <label>ElevenLabs API Key {existing?.has_elevenlabs_key && <span className="badge badge-audio" style={{ marginLeft: 6 }}>saved</span>}</label>
            <input
              type="password"
              placeholder={existing?.has_elevenlabs_key ? '••••••••••••••••••••••••••• (leave blank to keep)' : 'your ElevenLabs key'}
              value={elKey}
              onChange={e => setElKey(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="row">
            <div>
              <label>ElevenLabs Voice ID <span className="small">(optional)</span></label>
              <input
                placeholder="voice_id override"
                value={elVoice}
                onChange={e => setElVoice(e.target.value)}
              />
            </div>
            <div>
              <label>ElevenLabs Model ID <span className="small">(optional)</span></label>
              <input
                placeholder="eleven_multilingual_v2"
                value={elModel}
                onChange={e => setElModel(e.target.value)}
              />
            </div>
          </div>
          {error && <div className="error-text">{error}</div>}
          <div className="actions" style={{ marginTop: 8 }}>
            <button type="submit" disabled={loading}>
              {loading ? 'Saving…' : 'Save Keys'}
            </button>
            <button type="button" className="secondary" onClick={onSkip}>
              {existing?.has_openai_key ? 'Close' : 'Skip for now'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────

function Collapsible({ title, children, defaultOpen = false }: {
  title: string; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="collapsible">
      <button className="collapsible-header" onClick={() => setOpen(o => !o)}>
        <span>{title}</span>
        <span className="chevron">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}

function SceneCard({
  scene,
  imageRef,
  articleId,
  onImageUploaded,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
  onToast,
}: {
  scene: StoryboardScene
  imageRef?: ImageAssetRef
  articleId?: string
  onImageUploaded?: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  isFirst?: boolean
  isLast?: boolean
  onToast?: (kind: ToastKind, msg: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadErr, setUploadErr] = useState('')

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !articleId) return
    setUploading(true)
    setUploadErr('')
    try {
      await uploadSceneImage(articleId, scene.scene_number, file)
      onToast?.('success', `Image uploaded for scene ${scene.scene_number}`)
      onImageUploaded?.()
    } catch (err: unknown) {
      const msg = String((err as Error)?.message ?? err)
      setUploadErr(msg)
      onToast?.('error', `Scene ${scene.scene_number}: ${msg}`)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="scene-card">
      <div className="scene-card-header">
        <span className="scene-num">Scene {scene.scene_number}</span>
        <span className={assetBadgeClass(scene.asset_type)}>{scene.asset_type}</span>
        <span className="scene-time">{fmtSeconds(scene.start_time_estimate)}</span>
        <span className="scene-dur">{fmtSeconds(scene.duration_estimate)}</span>
        {(onMoveUp || onMoveDown) && (
          <div className="scene-reorder">
            <button
              className="secondary settings-btn"
              style={{ padding: '2px 7px', fontSize: 11, opacity: isFirst ? 0.3 : 1 }}
              onClick={onMoveUp}
              disabled={isFirst}
              title="Move up"
            >↑</button>
            <button
              className="secondary settings-btn"
              style={{ padding: '2px 7px', fontSize: 11, opacity: isLast ? 0.3 : 1 }}
              onClick={onMoveDown}
              disabled={isLast}
              title="Move down"
            >↓</button>
          </div>
        )}
      </div>
      <div className="scene-card-content">
        <div className="scene-card-text">
          {scene.on_screen_text && (
            <div className="scene-overlay">"{scene.on_screen_text}"</div>
          )}
          <div className="scene-narration">{scene.narration}</div>
          <div className="scene-visual small">{scene.visual_prompt}</div>
          {articleId && (
            <div style={{ marginTop: 6 }}>
              <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFile} />
              <button
                className="secondary settings-btn"
                style={{ fontSize: 11, padding: '3px 8px' }}
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? 'Uploading…' : imageRef?.status === 'ready' ? 'Replace Image' : 'Upload Image'}
              </button>
              {uploadErr && <div className="error-text" style={{ fontSize: 11, marginTop: 2 }}>{uploadErr}</div>}
            </div>
          )}
        </div>
        {imageRef?.status === 'ready' && (
          <a href={resolveImageUrl(imageRef.id)} target="_blank" rel="noreferrer" className="scene-thumb-link">
            <img
              className="scene-thumb"
              src={resolveImageUrl(imageRef.id)}
              alt={`Scene ${scene.scene_number}`}
              loading="lazy"
            />
          </a>
        )}
      </div>
    </div>
  )
}

function AnalysisCard({ analysis }: { analysis: AnalysisResult }) {
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

function CostPanel({ cost }: { cost: CostSummary }) {
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
          {/* Provider breakdown */}
          <div className="cost-section-label">By provider</div>
          <div className="cost-rows">
            {Object.entries(cost.by_provider).map(([p, v]) => (
              <div key={p} className="cost-row">
                <span className={`cost-provider-badge cost-provider-${p}`}>{p}</span>
                <span className="cost-row-value">{fmt(v)}</span>
              </div>
            ))}
          </div>

          {/* Stage breakdown */}
          <div className="cost-section-label" style={{ marginTop: 10 }}>By stage</div>
          <div className="cost-rows">
            {Object.entries(cost.by_stage).map(([s, v]) => (
              <div key={s} className="cost-row">
                <span className="cost-stage">{stageLabels[s] ?? s}</span>
                <span className="cost-row-value">{fmt(v)}</span>
              </div>
            ))}
          </div>

          {/* Usage totals */}
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

const PLATFORM_LABELS: Record<string, string> = {
  tiktok: 'TikTok', reels: 'Reels', youtube_shorts: 'YT Shorts',
  youtube: 'YouTube', facebook: 'Facebook',
}

function SocialCaptionsPanel({ captions }: { captions: SocialCaption[] }) {
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

function HistoryRow({ article, sources, onLoad, onPin }: {
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

function ArticleCard({
  candidate,
  rank,
  onSelect,
}: {
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

// ── Main App ───────────────────────────────────────────────────────────────

export default function App() {
  // ── Auth state ─────────────────────────────────────────────────────────
  const [authToken, setAuthTokenState] = useState<string | null>(() => localStorage.getItem('jwt_token'))
  const [currentUser, setCurrentUser] = useState<UserResp | null>(null)
  const [userKeys, setUserKeys] = useState<UserKeysOut | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [showAuthModal, setShowAuthModal] = useState(false)
  const [showKeysModal, setShowKeysModal] = useState(false)

  // ── Settings (legacy API key) ───────────────────────────────────────────
  const [apiKey, setApiKeyState] = useState(() => localStorage.getItem('api_key') ?? '')
  const [showSettings, setShowSettings] = useState(false)

  // ── Platforms / language metadata ─────────────────────────────────────
  const [platformsData, setPlatformsData] = useState<PlatformsResp | null>(null)

  // ── Form state (restored from localStorage) ───────────────────────────
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState('')
  const [targetSeconds, setTargetSeconds] = useState<number>(() => {
    const v = localStorage.getItem('gen_targetSeconds')
    return v ? Number(v) : 180
  })
  const [nScenes, setNScenes] = useState<number>(() => {
    const v = localStorage.getItem('gen_nScenes')
    return v ? Number(v) : 8
  })
  const [language, setLanguage] = useState<string>(() =>
    localStorage.getItem('gen_language') ?? 'es-MX'
  )
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(() => {
    try {
      const v = localStorage.getItem('gen_selectedPlatforms')
      return v ? JSON.parse(v) : ['tiktok']
    } catch { return ['tiktok'] }
  })
  const [animationPrompt, setAnimationPrompt] = useState<string>(() =>
    localStorage.getItem('gen_animationPrompt') ?? ''
  )

  // ── Audio generation ───────────────────────────────────────────────────
  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('Idle')
  const [audioJob, setAudioJob] = useState<JobStatus | null>(null)
  const [error, setError] = useState('')
  const pollTimer = useRef<number | null>(null)
  const videoPollTimer = useRef<number | null>(null)
  const activeAudioTaskId = useRef<string | null>(null)
  const activeVideoTaskId = useRef<string | null>(null)

  // ── Content package ────────────────────────────────────────────────────
  const [pkg, setPkg] = useState<ContentPackage | null>(null)
  const [pkgLoading, setPkgLoading] = useState(false)

  // ── Video generation ───────────────────────────────────────────────────
  // Per-platform tracking: platform → {taskId, loading, stage, error}
  type PlatVideoState = { taskId: string; loading: boolean; stage: string; error: string }
  const [platVideos, setPlatVideos] = useState<Record<string, PlatVideoState>>({})
  // Derived aggregate — any platform still running
  const videoLoading = Object.values(platVideos).some(v => v.loading)
  const videoError = Object.values(platVideos).map(v => v.error).filter(Boolean).join(' · ')
  const videoStage = Object.values(platVideos).map(v => v.stage).filter(Boolean).join(' · ')
  // Legacy single-platform compat (first platform's job for scene animation grid)
  const videoJob: JobStatus | null = null
  const [renderMode, setRenderMode] = useState<RenderMode>('static')

  // ── Script prepare (preview-before-audio) ──────────────────────────────
  const [prepareLoading, setPrepareLoading] = useState(false)

  // ── Script editing ─────────────────────────────────────────────────────
  const [scriptEditing, setScriptEditing] = useState(false)
  const [scriptDraft, setScriptDraft] = useState('')
  const [scriptSaving, setScriptSaving] = useState(false)
  const [scriptSaveError, setScriptSaveError] = useState('')
  const [regenScriptLoading, setRegenScriptLoading] = useState(false)

  // ── Article picker flow ──────────────────────────────────────────────────
  type FlowStep = 'pick' | 'configure'
  const [flowStep, setFlowStep] = useState<FlowStep>('pick')
  const [candidates, setCandidates] = useState<RssCandidate[]>([])
  const [candidatesLoading, setCandidatesLoading] = useState(false)
  const [candidatesError, setCandidatesError] = useState('')
  const [selectedCandidate, setSelectedCandidate] = useState<RssCandidate | null>(null)

  // ── History + pagination ─────────────────────────────────────────────────
  const [history, setHistory] = useState<ArticleSummary[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyHasMore, setHistoryHasMore] = useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const HISTORY_PAGE = 20

  // ── Toasts ───────────────────────────────────────────────────────────────
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((kind: ToastKind, msg: string) => {
    const id = ++_toastSeq
    setToasts(prev => [...prev, { id, kind, msg }])
    window.setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000)
  }, [])

  const dismissToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  // ── Bootstrap: validate stored JWT, try refresh on failure ───────────────
  useEffect(() => {
    const stored = localStorage.getItem('jwt_token')
    const legacyKey = localStorage.getItem('api_key') ?? ''
    setApiKey(legacyKey)

    if (!stored) {
      setAuthLoading(false)
      setShowAuthModal(true)
      return
    }

    setAuthToken(stored)
    getMe()
      .then(user => {
        setCurrentUser(user)
        return getUserKeys()
      })
      .then(keys => {
        setUserKeys(keys)
        setAuthLoading(false)
      })
      .catch(async () => {
        // Access token expired — attempt silent refresh before showing login
        try {
          const tokens = await refreshAccessToken()
          setAuthToken(tokens.access_token)
          localStorage.setItem('jwt_token', tokens.access_token)
          if (tokens.refresh_token) setRefreshToken(tokens.refresh_token)
          setAuthTokenState(tokens.access_token)
          const user = await getMe()
          setCurrentUser(user)
          const keys = await getUserKeys()
          setUserKeys(keys)
          setAuthLoading(false)
        } catch {
          localStorage.removeItem('jwt_token')
          localStorage.removeItem('refresh_token')
          clearAuthToken()
          setAuthTokenState(null)
          setAuthLoading(false)
          setShowAuthModal(true)
        }
      })
  }, [])

  // Sync legacy API key
  useEffect(() => {
    setApiKey(apiKey)
    localStorage.setItem('api_key', apiKey)
  }, [apiKey])

  // Persist generation settings to localStorage
  useEffect(() => { localStorage.setItem('gen_targetSeconds', String(targetSeconds)) }, [targetSeconds])
  useEffect(() => { localStorage.setItem('gen_nScenes', String(nScenes)) }, [nScenes])
  useEffect(() => { localStorage.setItem('gen_language', language) }, [language])
  useEffect(() => { localStorage.setItem('gen_selectedPlatforms', JSON.stringify(selectedPlatforms)) }, [selectedPlatforms])
  useEffect(() => { localStorage.setItem('gen_animationPrompt', animationPrompt) }, [animationPrompt])

  // Load sources + history + platform metadata once authenticated
  useEffect(() => {
    if (authLoading || showAuthModal) return
    listSources()
      .then(s => { setSources(s); if (s.length) setSourceId(String(s[0].id)) })
      .catch(e => setError(String((e as Error)?.message ?? e)))
    getPlatforms()
      .then(p => {
        setPlatformsData(p)
        if (!localStorage.getItem('gen_language')) setLanguage(p.defaults.language)
        if (!localStorage.getItem('gen_animationPrompt')) setAnimationPrompt(p.defaults.animation_prompt)
      })
      .catch(e => {
        console.warn('getPlatforms failed; falling back to hardcoded defaults', e)
        addToast('error', 'Could not load platform list — using defaults')
      })
    loadHistory()
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current)
      if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    }
  }, [authLoading, showAuthModal])

  // Refresh history while tasks run
  useEffect(() => {
    if (!loading && !videoLoading) return
    const id = window.setInterval(loadHistory, 5000)
    return () => window.clearInterval(id)
  }, [loading, videoLoading])

  // Refresh content package every 8s during video generation. Skip the refresh
  // while the user is editing the script so we don't blow away their draft.
  useEffect(() => {
    const aid = pkg?.article_id
    if (!videoLoading || !aid) return
    let consecutiveErrors = 0
    const id = window.setInterval(async () => {
      if (scriptEditing) return
      try {
        setPkg(await getArticlePackage(aid))
        consecutiveErrors = 0
      } catch (e) {
        consecutiveErrors++
        if (consecutiveErrors === 3) {
          console.warn('Package refresh failing repeatedly:', e)
          addToast('error', 'Live progress updates are stalled — check your connection')
        }
      }
    }, 8000)
    return () => window.clearInterval(id)
  }, [videoLoading, pkg?.article_id, scriptEditing, addToast])

  // ── Auth handlers ───────────────────────────────────────────────────────

  function handleAuthSuccess(token: string, user: UserResp, refreshToken?: string) {
    setAuthTokenState(token)
    setCurrentUser(user)
    setShowAuthModal(false)
    if (refreshToken) setRefreshToken(refreshToken)
    // Fetch keys for this user
    getUserKeys()
      .then(keys => {
        setUserKeys(keys)
        if (!keys.has_openai_key || !keys.has_elevenlabs_key) {
          setShowKeysModal(true)
        }
      })
      .catch(() => setShowKeysModal(true))
  }

  async function handleLogout() {
    await logout().catch(() => {})
    localStorage.removeItem('jwt_token')
    localStorage.removeItem('refresh_token')
    clearAuthToken()
    setAuthTokenState(null)
    setCurrentUser(null)
    setUserKeys(null)
    setHistory([])
    setPkg(null)
    setAudioJob(null)
    setPlatVideos({})
    setError('')
    setStatusText('Idle')
    setShowAuthModal(true)
  }

  async function loadHistory() {
    try {
      const resp = await listArticles({ limit: HISTORY_PAGE, offset: 0 })
      setHistory(resp.items)
      setHistoryTotal(resp.total)
      setHistoryHasMore(resp.has_more)
    } catch {
      // History is best-effort
    }
  }

  async function loadMoreHistory() {
    setHistoryLoadingMore(true)
    try {
      const resp = await listArticles({ limit: HISTORY_PAGE, offset: history.length })
      setHistory(prev => [...prev, ...resp.items])
      setHistoryHasMore(resp.has_more)
    } catch {
      // best-effort
    } finally {
      setHistoryLoadingMore(false)
    }
  }

  async function loadFromHistory(articleId: string) {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setAudioJob(null); setPlatVideos({})
    setError(''); setLoading(false)
    setPkg(null); setPkgLoading(true); setStatusText('Loading…')
    setSelectedCandidate(null); setCandidates([])
    try {
      const p = await getArticlePackage(articleId)
      setPkg(p)
      setStatusText('Ready')
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
      setStatusText('Error')
    } finally {
      setPkgLoading(false)
    }
  }

  // ── Audio generation ─────────────────────────────────────────────────────

  async function startAudio() {
    if (!selectedCandidate) return
    setError(''); setLoading(true); setAudioJob(null); setPkg(null)
    setPlatVideos({})
    setStatusText('Queueing…')
    try {
      const payload: GenerateReq = {
        source_id: selectedCandidate.source_id,
        target_seconds: targetSeconds,
        n_scenes: nScenes,
        article_url: selectedCandidate.url,
        article_title: selectedCandidate.title,
        article_summary: selectedCandidate.summary ?? null,
        article_published_at: selectedCandidate.published_at ?? null,
      }
      const resp = await startGenerate(payload)
      activeAudioTaskId.current = resp.task_id
      setStatusText(`Queued: ${resp.task_id.slice(0, 8)}…`)
      pollAudio(resp.task_id)
    } catch (e: unknown) {
      setLoading(false); setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    }
  }

  async function cancelAudio() {
    const tid = activeAudioTaskId.current
    if (!tid) return
    try { await cancelJob(tid) } catch { /* ignore */ }
    activeAudioTaskId.current = null
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    setLoading(false); setStatusText('Cancelled')
  }

  function pollAudio(taskId: string) {
    jobStatus(taskId).then(s => {
      setAudioJob(s)
      setStatusText(stageLabel(s))
      if (s.state === 'SUCCESS') {
        setLoading(false)
        const aid = s.result?.article_id as string | undefined
        if (aid) fetchPackage(aid)
        loadHistory()
      } else if (s.state === 'FAILURE') {
        setLoading(false)
        setError(s.error || 'Audio generation failed')
        setStatusText('Failed')
      } else {
        pollTimer.current = window.setTimeout(() => pollAudio(taskId), 2500)
      }
    }).catch(e => {
      setLoading(false); setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    })
  }

  async function fetchPackage(aid: string) {
    try {
      const p = await getArticlePackage(aid)
      // Don't clobber in-progress edits — the save flow will refetch on commit.
      if (scriptEditing) return
      setPkg(p)
      setStatusText('Ready')
    } catch (e) {
      console.warn('getArticlePackage failed', e)
      setStatusText('Ready (package unavailable)')
      addToast('error', 'Could not load article package — open the article from history to retry')
    }
  }

  // ── Video generation ─────────────────────────────────────────────────────

  function _setPlatVideo(platform: string, patch: Partial<PlatVideoState>) {
    setPlatVideos(prev => {
      const cur: PlatVideoState = prev[platform] ?? { taskId: '', loading: false, stage: '', error: '' }
      return { ...prev, [platform]: { ...cur, ...patch } }
    })
  }

  async function startVideo() {
    if (!articleId) return
    const platforms = selectedPlatforms.length ? selectedPlatforms : ['tiktok']
    setPlatVideos({})
    for (const plat of platforms) {
      _setPlatVideo(plat, { loading: true, stage: 'Queueing…', error: '' })
      try {
        const resp = await startGenerateVideo(articleId, true, renderMode, plat, animationPrompt || null)
        activeVideoTaskId.current = resp.task_id
        _setPlatVideo(plat, { taskId: resp.task_id, loading: true, stage: 'Queued' })
        pollVideo(resp.task_id, plat)
      } catch (e: unknown) {
        _setPlatVideo(plat, { loading: false, stage: '', error: String((e as Error)?.message ?? e) })
      }
    }
  }

  async function retryVideo(stage: 'video' | 'images') {
    if (!articleId) return
    const firstPlat = selectedPlatforms[0] ?? 'tiktok'
    _setPlatVideo(firstPlat, { loading: true, stage: stage === 'images' ? 'Regenerating images…' : 'Retrying assembly…', error: '' })
    try {
      const resp = await regenerateStage(articleId, stage, true)
      activeVideoTaskId.current = resp.task_id
      _setPlatVideo(firstPlat, { taskId: resp.task_id })
      pollVideo(resp.task_id, firstPlat)
    } catch (e: unknown) {
      _setPlatVideo(firstPlat, { loading: false, stage: '', error: String((e as Error)?.message ?? e) })
    }
  }

  async function cancelVideo() {
    const tid = activeVideoTaskId.current
    if (!tid) return
    try { await cancelJob(tid) } catch { /* ignore */ }
    activeVideoTaskId.current = null
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setPlatVideos(prev => {
      const next = { ...prev }
      for (const k of Object.keys(next)) next[k] = { ...next[k], loading: false, stage: 'Cancelled' }
      return next
    })
  }

  function pollVideo(taskId: string, platform: string) {
    jobStatus(taskId).then(s => {
      let stage = ''
      if (s.state === 'PROGRESS' && s.meta?.msg) stage = s.meta.msg
      else if (s.state !== 'SUCCESS' && s.state !== 'FAILURE') stage = STATE_LABELS[s.state] ?? s.state
      _setPlatVideo(platform, { stage })
      if (s.state === 'SUCCESS') {
        _setPlatVideo(platform, { loading: false, stage: '' })
        if (articleId) fetchPackage(articleId)
        loadHistory()
      } else if (s.state === 'FAILURE') {
        _setPlatVideo(platform, { loading: false, stage: '', error: s.error || 'Video generation failed' })
      } else {
        videoPollTimer.current = window.setTimeout(() => pollVideo(taskId, platform), 3000)
      }
    }).catch(e => {
      const msg = String((e as Error)?.message ?? e)
      _setPlatVideo(platform, { loading: false, stage: '', error: msg })
      addToast('error', `${platform}: ${msg}`)
    })
  }

  function reset() {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setAudioJob(null); setPkg(null); setError('')
    setPlatVideos({})
    setLoading(false); setPrepareLoading(false); setStatusText('Idle'); setPkgLoading(false)
    setScriptEditing(false); setScriptSaveError('')
    setFlowStep('pick'); setCandidates([]); setCandidatesError('')
    setSelectedCandidate(null)
  }

  // ── Article picker handlers ───────────────────────────────────────────────

  async function handlePullCandidates() {
    setCandidatesError('')
    setCandidates([])
    setCandidatesLoading(true)
    try {
      // sourceId '_all' means fetch across all sources
      const sid = sourceId === '_all' ? undefined : sourceId
      const results = await fetchRssCandidates(sid, 10)
      if (results.length === 0) {
        setCandidatesError('No articles found. Try a different source or check back later.')
      } else {
        setCandidates(results)
      }
    } catch (e: unknown) {
      setCandidatesError(String((e as Error)?.message ?? e))
    } finally {
      setCandidatesLoading(false)
    }
  }

  function handleSelectCandidate(c: RssCandidate) {
    setSelectedCandidate(c)
    setFlowStep('configure')
    setCandidates([])
    setCandidatesError('')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function handleChangeArticle() {
    setSelectedCandidate(null)
    setFlowStep('pick')
    setCandidates([])
    setCandidatesError('')
    setPkg(null)
    setAudioJob(null); setPlatVideos({})
    setError(''); setStatusText('Idle')
    setLoading(false)
  }

  // ── Script preview (prepare_article task) ────────────────────────────────

  async function handlePrepareArticle() {
    if (!selectedCandidate) return
    setPrepareLoading(true)
    setError('')
    setPkg(null)
    setAudioJob(null)
    setStatusText('Generating script preview…')
    try {
      const resp = await prepareArticle({
        source_id: selectedCandidate.source_id,
        article_url: selectedCandidate.url,
        article_title: selectedCandidate.title,
        article_summary: selectedCandidate.summary ?? null,
        article_published_at: selectedCandidate.published_at ?? null,
        n_scenes: nScenes,
        target_seconds: targetSeconds,
        language,
        selected_platforms: selectedPlatforms,
        animation_prompt: animationPrompt || null,
      })
      activeAudioTaskId.current = resp.task_id
      pollPrepare(resp.task_id)
    } catch (e: unknown) {
      setPrepareLoading(false)
      setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    }
  }

  function pollPrepare(taskId: string) {
    jobStatus(taskId).then(s => {
      setAudioJob(s)
      setStatusText(stageLabel(s))
      if (s.state === 'SUCCESS') {
        setPrepareLoading(false)
        setStatusText('Script ready')
        const aid = s.result?.article_id as string | undefined
        if (aid) {
          fetchPackage(aid)
          addToast('success', 'Script generated — review below before generating audio')
        }
        loadHistory()
      } else if (s.state === 'FAILURE') {
        setPrepareLoading(false)
        setStatusText('Failed')
        setError(s.error || 'Script generation failed')
      } else {
        pollTimer.current = window.setTimeout(() => pollPrepare(taskId), 2500)
      }
    }).catch(e => {
      setPrepareLoading(false)
      setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    })
  }

  async function startAudioFromPreview() {
    if (!pkg?.article_id || !selectedCandidate) return
    setError('')
    setLoading(true)
    setAudioJob(null)
    setStatusText('Queueing audio synthesis…')
    try {
      const payload: GenerateReq = {
        source_id: selectedCandidate.source_id,
        article_id: pkg.article_id,
        target_seconds: targetSeconds,
        n_scenes: nScenes,
      }
      const resp = await startGenerate(payload)
      activeAudioTaskId.current = resp.task_id
      setStatusText(`Queued: ${resp.task_id.slice(0, 8)}…`)
      pollAudio(resp.task_id)
    } catch (e: unknown) {
      setLoading(false)
      setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    }
  }

  // ── Side effects ───────────────────────────────────────────────────────────

  // Page title
  useEffect(() => {
    document.title = pkg?.title
      ? `${pkg.title.slice(0, 50)} — Medical Content Generator`
      : 'Medical Content Generator'
  }, [pkg?.title])

  // Cmd/Ctrl+Enter → preview script (only in configure step with a selected article)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter'
          && !loading && !prepareLoading && selectedCandidate && flowStep === 'configure' && !showAuthModal) {
        e.preventDefault()
        handlePrepareArticle()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, prepareLoading, selectedCandidate, flowStep, showAuthModal])

  // Warn before leaving when script has unsaved edits
  useEffect(() => {
    if (!scriptEditing) return
    function onBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [scriptEditing])

  // ── Pin / reorder ──────────────────────────────────────────────────────────

  async function handlePinArticle(id: string, pinned: boolean) {
    try {
      await pinArticle(id, pinned)
      setHistory(h => h.map(a => a.id === id ? { ...a, is_pinned: pinned } : a))
      addToast('success', pinned ? 'Article pinned' : 'Article unpinned')
    } catch { addToast('error', 'Failed to update pin') }
  }

  async function handleReorderScene(fromIdx: number, toIdx: number) {
    if (!pkg?.storyboard || !articleId) return
    const newOrder = pkg.storyboard.scenes.map(s => s.scene_number)
    const [moved] = newOrder.splice(fromIdx, 1)
    newOrder.splice(toIdx, 0, moved)
    try {
      await reorderStoryboard(articleId, newOrder)
      const updated = await getArticlePackage(articleId)
      setPkg(updated)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    }
  }

  // ── Script editing ────────────────────────────────────────────────────────

  function handleEditScript() {
    setScriptDraft(pkg?.script?.text ?? '')
    setScriptEditing(true)
    setScriptSaveError('')
  }

  function handleScriptCancel() {
    setScriptEditing(false)
    setScriptSaveError('')
  }

  async function handleScriptSave() {
    if (!articleId) return
    setScriptSaving(true)
    setScriptSaveError('')
    try {
      await editScript(articleId, scriptDraft)
      const updated = await getArticlePackage(articleId)
      setPkg(updated)
      setScriptEditing(false)
      addToast('success', 'Script saved')
    } catch (e: unknown) {
      setScriptSaveError(String((e as Error)?.message ?? e))
    } finally {
      setScriptSaving(false)
    }
  }

  async function startRegenScript() {
    if (!articleId) return
    setScriptEditing(false)
    setScriptSaveError('')
    setRegenScriptLoading(true)
    setError('')
    setStatusText('Queuing re-summarize…')
    try {
      const resp = await startRegenerateScript(articleId, nScenes)
      pollRegenScript(resp.task_id)
    } catch (e: unknown) {
      setRegenScriptLoading(false)
      setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    }
  }

  function pollRegenScript(taskId: string) {
    jobStatus(taskId).then(s => {
      if (s.state === 'PROGRESS' && s.meta?.msg) setStatusText(s.meta.msg)
      if (s.state === 'SUCCESS') {
        setRegenScriptLoading(false)
        setStatusText('Ready')
        if (articleId) fetchPackage(articleId)
      } else if (s.state === 'FAILURE') {
        setRegenScriptLoading(false)
        setStatusText('Failed')
        setError(s.error || 'Script regeneration failed')
      } else {
        window.setTimeout(() => pollRegenScript(taskId), 2500)
      }
    }).catch(e => {
      setRegenScriptLoading(false)
      setStatusText('Error')
      setError(String((e as Error)?.message ?? e))
    })
  }

  // ── Derived ───────────────────────────────────────────────────────────────

  const articleId = pkg?.article_id ?? (audioJob?.result?.article_id as string | undefined)
  const videoReady = pkg?.video?.status === 'ready'
  const audioDownloadUrl = pkg?.audio?.id ? resolveAudioUrl(pkg.audio.id) : undefined

  const imageMap = useMemo(
    () => Object.fromEntries((pkg?.images ?? []).map(img => [img.scene_number, img])),
    [pkg?.images],
  )

  const keysConfigured = userKeys?.has_openai_key && userKeys?.has_elevenlabs_key

  // ── Render ────────────────────────────────────────────────────────────────

  if (authLoading) {
    return (
      <div className="container" style={{ textAlign: 'center', paddingTop: 80 }}>
        <div className="small">Loading…</div>
      </div>
    )
  }

  return (
    <div className="container">

      {/* Auth modal */}
      {showAuthModal && <AuthModal onSuccess={handleAuthSuccess} />}

      {/* Keys setup modal */}
      {showKeysModal && (
        <KeysModal
          existing={userKeys}
          onSave={keys => { setUserKeys(keys); setShowKeysModal(false) }}
          onSkip={() => setShowKeysModal(false)}
        />
      )}

      {/* Header */}
      <div className="header">
        <div>
          <div className="h1">Medical Content Generator</div>
          <div className="small">RSS → Script → Audio → Storyboard → Video</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div className="status">{statusText}</div>
          {currentUser && (
            <>
              <button
                className="secondary settings-btn"
                onClick={() => setShowKeysModal(true)}
                title="Manage API Keys"
              >
                API Keys {keysConfigured
                  ? <span className="key-dot key-dot-ok" />
                  : <span className="key-dot key-dot-warn" />
                }
              </button>
              <div className="user-chip" title={currentUser.email}>
                {currentUser.email.split('@')[0]}
              </div>
              <button className="secondary settings-btn" onClick={handleLogout}>
                Sign Out
              </button>
            </>
          )}
          <button className="secondary settings-btn" onClick={() => setShowSettings(v => !v)}>
            {showSettings ? 'Close' : 'Settings'}
          </button>
        </div>
      </div>

      {/* Settings panel */}
      {showSettings && (
        <div className="card" style={{ marginBottom: 12 }}>
          <label>
            Server API Key <span className="small">(fallback, sent as X-API-Key — only needed if server requires it)</span>
          </label>
          <input
            type="password"
            placeholder="Leave empty if using account login"
            value={apiKey}
            onChange={e => setApiKeyState(e.target.value)}
            autoComplete="off"
          />
        </div>
      )}

      {/* Keys warning banner */}
      {currentUser && !keysConfigured && (
        <div className="keys-banner">
          <span>API keys not configured — generations will use server defaults.</span>
          <button className="secondary settings-btn" style={{ marginLeft: 12 }} onClick={() => setShowKeysModal(true)}>
            Add Keys
          </button>
        </div>
      )}

      {/* ── Step 1: Article Picker ───────────────────────────────────── */}
      {!pkg && flowStep === 'pick' && (
        <div className="card flow-card">
          <div className="flow-step-header">
            <span className="flow-step-pill">1</span>
            <span className="flow-step-title">Choose an Article</span>
          </div>

          <div className="picker-source-row">
            <div style={{ flex: 1 }}>
              <label>Source</label>
              <select
                value={sourceId}
                onChange={e => setSourceId(e.target.value)}
                disabled={candidatesLoading || !sources.length}
              >
                <option value="_all">All Sources</option>
                {sources.map(s => (
                  <option key={String(s.id)} value={String(s.id)}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="picker-pull-wrap">
              <button
                onClick={handlePullCandidates}
                disabled={candidatesLoading || !sources.length}
                className="pull-btn"
              >
                {candidatesLoading ? (
                  <><span className="spinner" />Fetching articles…</>
                ) : 'Pull Latest Articles'}
              </button>
            </div>
          </div>

          {candidatesError && (
            <div className="error-text" style={{ marginTop: 10 }}>{candidatesError}</div>
          )}

          {candidates.length > 0 && (
            <div className="candidate-list">
              <div className="candidate-list-header small">
                {candidates.length} article{candidates.length !== 1 ? 's' : ''} found — pick one to continue
              </div>
              {candidates.map((c, i) => (
                <ArticleCard
                  key={c.url}
                  candidate={c}
                  rank={i + 1}
                  onSelect={() => handleSelectCandidate(c)}
                />
              ))}
            </div>
          )}

          {candidates.length === 0 && !candidatesLoading && !candidatesError && (
            <div className="picker-empty">
              Select a source above and click <strong>Pull Latest Articles</strong> to browse available content.
            </div>
          )}
        </div>
      )}

      {/* ── Step 2: Selected article + generation settings ───────────── */}
      {!pkg && flowStep === 'configure' && selectedCandidate && (
        <>
          {/* Selected article banner */}
          <div className="selected-article-card">
            <div className="selected-check">✓</div>
            <div className="selected-article-info">
              <div className="selected-article-title">{selectedCandidate.title}</div>
              <div className="selected-article-meta">
                <span className="small">{selectedCandidate.source_name}</span>
                {selectedCandidate.published_at && (
                  <span className="small">{relativeDate(selectedCandidate.published_at)}</span>
                )}
                <a
                  href={selectedCandidate.url}
                  target="_blank"
                  rel="noreferrer"
                  className="small link"
                >
                  {(() => { try { return new URL(selectedCandidate.url).hostname } catch { return selectedCandidate.url.slice(0, 40) } })()}
                </a>
              </div>
            </div>
            <button className="secondary settings-btn change-article-btn" onClick={handleChangeArticle}>
              ← Change
            </button>
          </div>

          {/* Generation settings */}
          <div className="card flow-card">
            <div className="flow-step-header">
              <span className="flow-step-pill">2</span>
              <span className="flow-step-title">Generation Settings</span>
            </div>

            {/* Language + Duration + Scenes */}
            <div className="row" style={{ marginTop: 8 }}>
              <div>
                <label>Language</label>
                <select
                  value={language}
                  onChange={e => setLanguage(e.target.value)}
                  disabled={loading || prepareLoading}
                  className="lang-select"
                >
                  {platformsData
                    ? platformsData.languages.map(l => (
                        <option key={l.code} value={l.code}>{l.label}</option>
                      ))
                    : <option value={language}>{language}</option>
                  }
                </select>
              </div>
              <div>
                <label>Target duration (s)</label>
                <input
                  type="number" min={30} max={600} value={targetSeconds}
                  onChange={e => setTargetSeconds(Number(e.target.value))}
                  disabled={loading || prepareLoading}
                />
              </div>
              <div>
                <label>Storyboard scenes</label>
                <input
                  type="number" min={0} max={20} value={nScenes}
                  onChange={e => setNScenes(Number(e.target.value))}
                  disabled={loading || prepareLoading}
                />
              </div>
            </div>

            {/* Platform selector */}
            <div style={{ marginTop: 14 }}>
              <label style={{ display: 'block', marginBottom: 8 }}>Target platforms</label>
              <div className="platform-chips">
                {(platformsData?.platforms ?? [
                  { id: 'tiktok', name: 'TikTok', aspect_ratio: '9:16' },
                  { id: 'reels', name: 'Reels', aspect_ratio: '9:16' },
                  { id: 'youtube_shorts', name: 'YT Shorts', aspect_ratio: '9:16' },
                  { id: 'youtube', name: 'YouTube', aspect_ratio: '16:9' },
                  { id: 'facebook', name: 'Facebook', aspect_ratio: '9:16' },
                ]).map(p => {
                  const active = selectedPlatforms.includes(p.id)
                  return (
                    <button
                      key={p.id}
                      className={`platform-chip ${active ? 'platform-chip-active' : ''}`}
                      onClick={() => {
                        if (active) {
                          const next = selectedPlatforms.filter(x => x !== p.id)
                          if (next.length) {
                            setSelectedPlatforms(next)
                            // Update duration hint to the new primary platform's default
                            const primary = platformsData?.platforms.find(x => x.id === next[0])
                            if (primary) setTargetSeconds(primary.default_duration)
                          }
                        } else {
                          const next = [...selectedPlatforms, p.id]
                          setSelectedPlatforms(next)
                          // If this is now the only / first platform, suggest its default duration
                          if (next.length === 1) {
                            const pd = platformsData?.platforms.find(x => x.id === p.id)
                            if (pd) setTargetSeconds(pd.default_duration)
                          }
                        }
                      }}
                      disabled={loading || prepareLoading}
                      type="button"
                    >
                      {p.name}
                      <span className="platform-chip-ratio">{p.aspect_ratio}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Animation prompt */}
            <div style={{ marginTop: 14 }}>
              <label>Animation prompt <span className="small" style={{ color: '#9ca3af' }}>used for animated video renders</span></label>
              <textarea
                className="animation-prompt-input"
                value={animationPrompt}
                onChange={e => setAnimationPrompt(e.target.value)}
                disabled={loading || prepareLoading}
                rows={2}
                placeholder="e.g. Smooth, subtle camera movement. Slow cinematic zoom."
              />
            </div>

            <div className="actions" style={{ marginTop: 16 }}>
              <button
                className="generate-btn"
                onClick={handlePrepareArticle}
                disabled={loading || prepareLoading}
                title="Preview Script (⌘/Ctrl + Enter)"
              >
                {prepareLoading ? (
                  <><span className="spinner spinner-light" />Generating script…</>
                ) : 'Preview Script →'}
              </button>
              {(loading || prepareLoading) && (
                <button className="secondary" onClick={() => { cancelAudio(); setPrepareLoading(false) }}>
                  Cancel
                </button>
              )}
            </div>

            {error && <div className="error-text">{error}</div>}
          </div>
        </>
      )}

      {/* History */}
      <div style={{ marginTop: 12 }}>
        <Collapsible title={`History · ${historyTotal} article${historyTotal !== 1 ? 's' : ''}`}>
          {history.length === 0 ? (
            <div className="history-empty">
              No articles yet. Generate your first one above.
            </div>
          ) : (
            <>
              <div className="history-list">
                {history.map(a => (
                  <HistoryRow
                    key={a.id}
                    article={a}
                    sources={sources}
                    onLoad={loadFromHistory}
                    onPin={handlePinArticle}
                  />
                ))}
              </div>
              {historyHasMore && (
                <div style={{ textAlign: 'center', padding: '10px 0' }}>
                  <button
                    className="secondary settings-btn"
                    onClick={loadMoreHistory}
                    disabled={historyLoadingMore}
                  >
                    {historyLoadingMore ? 'Loading…' : `Load More (${historyTotal - history.length} remaining)`}
                  </button>
                </div>
              )}
            </>
          )}
        </Collapsible>
      </div>

      {/* Package loading spinner */}
      {pkgLoading && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="small">Loading article…</div>
        </div>
      )}

      {/* Results toolbar */}
      {pkg && (
        <div className="results-toolbar">
          <button className="secondary settings-btn" onClick={reset}>
            ← New Article
          </button>
          <span className="small" style={{ color: '#9ca3af' }}>
            {pkg.title.length > 60 ? pkg.title.slice(0, 60) + '…' : pkg.title}
          </span>
        </div>
      )}

      {/* Results */}
      {pkg && (
        <div className="results">

          {/* Article title + audio metadata + player */}
          <div className="result-meta card">
            <div className="result-meta-top">
              <div className="result-meta-info">
                <a href={pkg.url} target="_blank" rel="noreferrer" className="article-title link">
                  {pkg.title}
                </a>
                {(pkg.source_name || pkg.published_at) && (
                  <div className="small result-article-meta">
                    {pkg.source_name && <span>{pkg.source_name}</span>}
                    {pkg.published_at && <span>{relativeDate(pkg.published_at)}</span>}
                  </div>
                )}
              </div>
              {pkg.thumbnail_url && (
                <div className="thumbnail-wrap">
                  <a href={resolveThumbnailUrl(pkg.article_id)} target="_blank" rel="noreferrer">
                    <img
                      src={resolveThumbnailUrl(pkg.article_id)}
                      alt="Thumbnail"
                      className="article-thumbnail"
                    />
                  </a>
                  <a
                    href={resolveThumbnailUrl(pkg.article_id)}
                    className="dl-btn dl-btn-sm"
                    download="thumbnail.png"
                    style={{ marginTop: 4 }}
                  >
                    Download
                  </a>
                </div>
              )}
            </div>
            {pkg.audio && (
              <div className="small" style={{ marginTop: 4 }}>
                {fmtSeconds(pkg.audio.duration_seconds)}
                {' · '}{pkg.audio.word_count?.toLocaleString()} words
                {' · voice: '}<span className="code">{pkg.audio.voice_id}</span>
              </div>
            )}
            {audioDownloadUrl && (
              <audio controls className="audio-player">
                <source src={audioDownloadUrl} type="audio/mpeg" />
              </audio>
            )}
            {pkg.analysis && <AnalysisCard analysis={pkg.analysis} />}
            {pkg.cost_summary && <CostPanel cost={pkg.cost_summary} />}
            {pkg.social_captions && pkg.social_captions.length > 0 && (
              <SocialCaptionsPanel captions={pkg.social_captions} />
            )}
            {pkg.storyboard && (
              <div className="download-row" style={{ marginTop: 8 }}>
                <span className="small">Subtitles:</span>
                <a href={resolveCaptionUrl(pkg.article_id, 'srt')} className="dl-btn" download>SRT</a>
                <a href={resolveCaptionUrl(pkg.article_id, 'vtt')} className="dl-btn" download>VTT</a>
              </div>
            )}
            <div className="download-row" style={{ marginTop: 8 }}>
              <span className="small">Export:</span>
              <a href={getExportZipUrl(pkg.article_id)} className="dl-btn" download>ZIP Package</a>
            </div>
          </div>

          {/* Script-preview approval gate — shows when article is prepared but audio not yet generated */}
          {pkg.script && !pkg.audio && (
            <div className="card approve-card">
              <div className="flow-step-header" style={{ marginBottom: 12 }}>
                <span className="flow-step-pill" style={{ background: '#7c3aed' }}>3</span>
                <span className="flow-step-title">Approve &amp; Generate Voiceover</span>
              </div>
              <p className="small" style={{ color: '#4b5563', margin: '0 0 10px' }}>
                Review the script below. Edit if needed, then synthesize the voiceover.
              </p>
              {/* Show configured settings as read-only summary */}
              <div className="approve-settings-summary small">
                <span><strong>Language:</strong> {pkg.language ?? language}</span>
                <span><strong>Duration:</strong> {targetSeconds}s</span>
                <span><strong>Scenes:</strong> {nScenes}</span>
                {(pkg.selected_platforms ?? selectedPlatforms).length > 0 && (
                  <span><strong>Platforms:</strong> {(pkg.selected_platforms ?? selectedPlatforms).join(', ')}</span>
                )}
              </div>
              <div className="actions" style={{ marginTop: 14 }}>
                <button className="generate-btn" onClick={startAudioFromPreview} disabled={loading}>
                  {loading ? 'Generating audio…' : 'Generate Audio →'}
                </button>
                {loading && <button className="secondary" onClick={cancelAudio}>Cancel</button>}
              </div>
              {error && <div className="error-text">{error}</div>}
            </div>
          )}

          {/* Script */}
          {pkg.script && (
            <Collapsible
              title={`Script · ${scriptEditing ? scriptDraft.split(/\s+/).filter(Boolean).length + ' words (editing)' : pkg.script.word_count + ' words'} · ${pkg.script.language}`}
            >
              {!scriptEditing && (
                <div className="script-toolbar">
                  <button className="secondary settings-btn" onClick={handleEditScript}>Edit</button>
                  <button
                    className="secondary settings-btn"
                    onClick={startRegenScript}
                    disabled={regenScriptLoading || loading}
                  >
                    {regenScriptLoading ? 'Re-summarizing…' : 'Re-summarize'}
                  </button>
                  <button
                    className="secondary settings-btn"
                    onClick={() => pkg?.script?.text && navigator.clipboard.writeText(pkg.script.text)
                      .then(() => addToast('success', 'Script copied to clipboard'))
                      .catch(() => addToast('error', 'Clipboard access denied'))}
                    title="Copy script to clipboard"
                  >Copy</button>
                </div>
              )}
              {scriptEditing ? (
                <div>
                  <textarea
                    className="script-editor"
                    value={scriptDraft}
                    onChange={e => setScriptDraft(e.target.value)}
                    rows={14}
                  />
                  <div className="actions" style={{ marginTop: 8 }}>
                    <button onClick={handleScriptSave} disabled={scriptSaving || !scriptDraft.trim()}>
                      {scriptSaving ? 'Saving…' : 'Save Script'}
                    </button>
                    <button className="secondary" onClick={handleScriptCancel}>Cancel</button>
                    <span className="small">
                      {(() => {
                        const wc = scriptDraft.split(/\s+/).filter(Boolean).length
                        const estSec = Math.round(wc / 2.5)
                        return `${wc} words · ~${fmtSeconds(estSec)}`
                      })()}
                    </span>
                  </div>
                  {scriptSaveError && <div className="error-text">{scriptSaveError}</div>}
                </div>
              ) : (
                <pre className="script-pre">{pkg.script.text}</pre>
              )}
            </Collapsible>
          )}

          {/* Storyboard */}
          {pkg.storyboard && pkg.storyboard.scenes.length > 0 && (
            <Collapsible
              title={`Storyboard · ${pkg.storyboard.scenes.length} scenes · ${fmtSeconds(pkg.storyboard.total_duration_estimate)}`}
              defaultOpen
            >
              <div className="scene-list">
                {pkg.storyboard.scenes.map((s, idx) => (
                  <SceneCard
                    key={s.scene_number}
                    scene={s}
                    imageRef={imageMap[s.scene_number]}
                    articleId={articleId}
                    onImageUploaded={() => articleId && fetchPackage(articleId)}
                    onMoveUp={idx > 0 ? () => handleReorderScene(idx, idx - 1) : undefined}
                    onMoveDown={idx < pkg.storyboard!.scenes.length - 1 ? () => handleReorderScene(idx, idx + 1) : undefined}
                    isFirst={idx === 0}
                    isLast={idx === pkg.storyboard!.scenes.length - 1}
                    onToast={addToast}
                  />
                ))}
              </div>
            </Collapsible>
          )}

          {/* Video */}
          <div className="video-section card">
            <div className="video-section-header">
              <strong>Video</strong>
              {videoLoading && videoStage && <span className="small">{videoStage}</span>}
            </div>

            {/* Per-platform video outputs */}
            {pkg.videos && pkg.videos.length > 0 && (
              <div className="platform-video-list">
                {pkg.videos.map(v => (
                  <div key={v.id} className="platform-video-card">
                    <div className="platform-video-meta small">
                      {v.platform && <span className="platform-badge">{v.platform}</span>}
                      {' '}{v.width}×{v.height}
                      {' · '}{fmtSeconds(v.duration_seconds)}
                      {v.has_subtitles ? ' · subtitles' : ''}
                      {v.render_mode === 'animated' ? ' · animated' : ''}
                      {v.status === 'failed' && <span style={{ color: '#ef4444' }}> · failed</span>}
                    </div>
                    {v.status === 'ready' ? (
                      <>
                        <video controls className="video-player">
                          <source src={resolveVideoUrl(v.id)} type="video/mp4" />
                        </video>
                        <div className="actions" style={{ marginTop: 8 }}>
                          <a href={resolveVideoUrl(v.id)} className="dl-btn dl-btn-primary" download>
                            Download MP4
                          </a>
                          {v.has_subtitles && <span className="small">subtitles burned in</span>}
                        </div>
                      </>
                    ) : v.status === 'failed' ? (
                      <div className="error-text" style={{ marginTop: 6, fontSize: 12 }}>{v.error || 'Generation failed'}</div>
                    ) : (
                      <div className="small" style={{ color: '#9ca3af', marginTop: 4 }}>Status: {v.status}</div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Per-platform loading progress */}
            {Object.entries(platVideos).map(([plat, state]) => state.loading && (
              <div key={plat} className="small" style={{ color: '#6b7280', marginTop: 6 }}>
                <span className="platform-badge" style={{ marginRight: 6 }}>{plat}</span>
                <span className="spinner" style={{ width: 10, height: 10, marginRight: 6 }} />
                {state.stage || 'Working…'}
              </div>
            ))}

            {/* Error display per platform */}
            {Object.entries(platVideos).map(([plat, state]) => state.error && (
              <div key={plat} className="error-text" style={{ marginTop: 4 }}>
                <span className="platform-badge" style={{ marginRight: 4 }}>{plat}</span>
                {state.error}
              </div>
            ))}

            {/* Generate section — shown when no video yet or to add more */}
            {(!pkg.videos || pkg.videos.length === 0 || (!videoLoading)) && (
              <div style={{ marginTop: pkg.videos && pkg.videos.length > 0 ? 14 : 0 }}>
                {/* Render mode toggle */}
                {!videoLoading && (
                  <div className="render-mode-toggle">
                    <button
                      className={`render-mode-btn ${renderMode === 'static' ? 'render-mode-active' : ''}`}
                      onClick={() => setRenderMode('static')}
                    >
                      Standard
                      <span className="render-mode-desc">DALL-E images · FFmpeg slideshow</span>
                    </button>
                    <button
                      className={`render-mode-btn ${renderMode === 'animated' ? 'render-mode-active' : ''}`}
                      onClick={() => setRenderMode('animated')}
                    >
                      Animated <span className="pro-badge">PRO</span>
                      <span className="render-mode-desc">AI-animated scene clips</span>
                    </button>
                  </div>
                )}
                {renderMode === 'animated' && !videoLoading && (
                  <div className="animated-info small">
                    Each scene image is animated by the configured provider, then assembled into one video matched to the audio.
                    Falls back to static for any scene that fails.
                  </div>
                )}
                <div className="actions" style={{ marginTop: 12 }}>
                  <button onClick={startVideo} disabled={videoLoading || !articleId}>
                    {videoLoading
                      ? 'Generating…'
                      : renderMode === 'animated'
                        ? `Generate Animated Video${selectedPlatforms.length > 1 ? ` (${selectedPlatforms.length} platforms)` : ''}`
                        : `Generate Video${selectedPlatforms.length > 1 ? ` (${selectedPlatforms.length} platforms)` : ''}`}
                  </button>
                  {videoLoading
                    ? <button className="secondary" onClick={cancelVideo}>Cancel</button>
                    : <span className="small">
                        {renderMode === 'animated'
                          ? 'Animates each scene · ~5–15 min per platform'
                          : `DALL-E images + FFmpeg · ~2–5 min${selectedPlatforms.length > 1 ? ` × ${selectedPlatforms.length}` : ''}`}
                      </span>
                  }
                </div>
                {pkg.videos && pkg.videos.length > 0 && articleId && !videoLoading && (
                  <div className="actions" style={{ marginTop: 8 }}>
                    <button className="secondary" style={{ fontSize: 12, padding: '5px 10px' }}
                      onClick={() => retryVideo('images')} disabled={videoLoading}>
                      Regenerate All Images
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Scene animation status grid (animated renders) */}
            {pkg.scene_videos && pkg.scene_videos.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="small" style={{ marginBottom: 6, color: '#6b7280', fontWeight: 600 }}>
                  Scene clips · {pkg.scene_videos.filter((sv: SceneVideoRef) => sv.status === 'ready').length}/{pkg.scene_videos.length} animated
                </div>
                <div className="scene-clip-grid">
                  {pkg.scene_videos.map((sv: SceneVideoRef) => (
                    <span
                      key={sv.id}
                      className={`scene-clip-dot scene-clip-${sv.status}`}
                      title={`Scene ${sv.scene_number} · ${sv.provider} · ${sv.status}${sv.error ? ': ' + sv.error : ''}${sv.duration_seconds ? ' · ' + fmtSeconds(sv.duration_seconds) : ''}`}
                    >
                      {sv.scene_number}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {pkg.images && pkg.images.length > 0 && (
              <div className="image-status-row small" style={{ marginTop: 10 }}>
                {pkg.images.map(img => (
                  <span
                    key={img.id}
                    className={`img-dot ${img.status === 'ready' ? 'img-dot-ok' : 'img-dot-fail'}`}
                    title={`Scene ${img.scene_number}: ${img.status} — ${img.visual_prompt.slice(0, 60)}`}
                  />
                ))}
                <span>{pkg.images.filter(i => i.status === 'ready').length}/{pkg.images.length} images ready</span>
              </div>
            )}
          </div>

        </div>
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
