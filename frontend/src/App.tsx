import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  ArticleSummary, ContentPackage, GenerateReq, ImageAssetRef,
  JobStatus, Source, StoryboardScene, UserResp, UserKeysOut,
} from './types'
import {
  cancelJob, getArticlePackage, jobStatus, listArticles, listSources, regenerateStage,
  resolveAudioUrl, resolveCaptionUrl, resolveImageUrl, resolveVideoUrl, setApiKey,
  startGenerate, startGenerateVideo,
  register, login, getMe, getUserKeys, saveUserKeys,
  setAuthToken, clearAuthToken,
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

// ── Auth Modal ─────────────────────────────────────────────────────────────

function AuthModal({ onSuccess }: { onSuccess: (token: string, user: UserResp) => void }) {
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
      const user: UserResp = { id: resp.user_id, email: resp.email, created_at: '', has_keys: false }
      onSuccess(resp.access_token, user)
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

function SceneCard({ scene, imageRef }: { scene: StoryboardScene; imageRef?: ImageAssetRef }) {
  return (
    <div className="scene-card">
      <div className="scene-card-header">
        <span className="scene-num">Scene {scene.scene_number}</span>
        <span className={assetBadgeClass(scene.asset_type)}>{scene.asset_type}</span>
        <span className="scene-time">{fmtSeconds(scene.start_time_estimate)}</span>
        <span className="scene-dur">{fmtSeconds(scene.duration_estimate)}</span>
      </div>
      <div className="scene-card-content">
        <div className="scene-card-text">
          {scene.on_screen_text && (
            <div className="scene-overlay">"{scene.on_screen_text}"</div>
          )}
          <div className="scene-narration">{scene.narration}</div>
          <div className="scene-visual small">{scene.visual_prompt}</div>
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

function HistoryRow({ article, sources, onLoad }: {
  article: ArticleSummary
  sources: Source[]
  onLoad: (id: string) => void
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

  // ── Form state ─────────────────────────────────────────────────────────
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [targetSeconds, setTargetSeconds] = useState(180)
  const [nScenes, setNScenes] = useState(8)

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
  const [videoLoading, setVideoLoading] = useState(false)
  const [videoJob, setVideoJob] = useState<JobStatus | null>(null)
  const [videoError, setVideoError] = useState('')
  const [videoStage, setVideoStage] = useState('')

  // ── History ─────────────────────────────────────────────────────────────
  const [history, setHistory] = useState<ArticleSummary[]>([])

  // ── Bootstrap: validate stored JWT, then load user + keys ──────────────
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
      .catch(() => {
        // Token expired or invalid
        localStorage.removeItem('jwt_token')
        clearAuthToken()
        setAuthTokenState(null)
        setAuthLoading(false)
        setShowAuthModal(true)
      })
  }, [])

  // Sync legacy API key
  useEffect(() => {
    setApiKey(apiKey)
    localStorage.setItem('api_key', apiKey)
  }, [apiKey])

  // Load sources + history once authenticated
  useEffect(() => {
    if (authLoading || showAuthModal) return
    listSources()
      .then(s => { setSources(s); if (s.length) setSourceId(String(s[0].id)) })
      .catch(e => setError(String((e as Error)?.message ?? e)))
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

  // Refresh content package every 8s during video generation
  useEffect(() => {
    const aid = pkg?.article_id
    if (!videoLoading || !aid) return
    const id = window.setInterval(async () => {
      try { setPkg(await getArticlePackage(aid)) } catch { /* ignore */ }
    }, 8000)
    return () => window.clearInterval(id)
  }, [videoLoading, pkg?.article_id])

  // ── Auth handlers ───────────────────────────────────────────────────────

  function handleAuthSuccess(token: string, user: UserResp) {
    setAuthTokenState(token)
    setCurrentUser(user)
    setShowAuthModal(false)
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

  function handleLogout() {
    localStorage.removeItem('jwt_token')
    clearAuthToken()
    setAuthTokenState(null)
    setCurrentUser(null)
    setUserKeys(null)
    setHistory([])
    setPkg(null)
    setAudioJob(null)
    setVideoJob(null)
    setError('')
    setStatusText('Idle')
    setShowAuthModal(true)
  }

  async function loadHistory() {
    try {
      const articles = await listArticles({ limit: 30 })
      setHistory(articles)
    } catch {
      // History is best-effort
    }
  }

  async function loadFromHistory(articleId: string) {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setAudioJob(null); setVideoJob(null); setVideoError(''); setVideoStage('')
    setError(''); setLoading(false); setVideoLoading(false)
    setPkg(null); setPkgLoading(true); setStatusText('Loading…')
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
    if (!sourceId) return
    setError(''); setLoading(true); setAudioJob(null); setPkg(null)
    setVideoJob(null); setVideoError(''); setVideoStage('')
    setStatusText('Queueing…')
    try {
      const payload: GenerateReq = {
        source_id: sourceId,
        voice_id: voiceId.trim() || null,
        target_seconds: targetSeconds,
        n_scenes: nScenes,
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
      setPkg(p)
      setStatusText('Ready')
    } catch {
      setStatusText('Ready (package unavailable)')
    }
  }

  // ── Video generation ─────────────────────────────────────────────────────

  async function startVideo() {
    if (!articleId) return
    setVideoError(''); setVideoLoading(true); setVideoJob(null); setVideoStage('Queueing…')
    try {
      const resp = await startGenerateVideo(articleId, true)
      activeVideoTaskId.current = resp.task_id
      pollVideo(resp.task_id)
    } catch (e: unknown) {
      setVideoLoading(false); setVideoStage('')
      setVideoError(String((e as Error)?.message ?? e))
    }
  }

  async function retryVideo(stage: 'video' | 'images') {
    if (!articleId) return
    setVideoError(''); setVideoLoading(true); setVideoJob(null)
    setVideoStage(stage === 'images' ? 'Regenerating all images…' : 'Retrying video assembly…')
    try {
      const resp = await regenerateStage(articleId, stage, true)
      activeVideoTaskId.current = resp.task_id
      pollVideo(resp.task_id)
    } catch (e: unknown) {
      setVideoLoading(false); setVideoStage('')
      setVideoError(String((e as Error)?.message ?? e))
    }
  }

  async function cancelVideo() {
    const tid = activeVideoTaskId.current
    if (!tid) return
    try { await cancelJob(tid) } catch { /* ignore */ }
    activeVideoTaskId.current = null
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setVideoLoading(false); setVideoStage('Cancelled')
  }

  function pollVideo(taskId: string) {
    jobStatus(taskId).then(s => {
      setVideoJob(s)
      if (s.state === 'PROGRESS' && s.meta?.msg) {
        setVideoStage(s.meta.msg)
      } else if (s.state !== 'SUCCESS' && s.state !== 'FAILURE') {
        setVideoStage(STATE_LABELS[s.state] ?? s.state)
      }
      if (s.state === 'SUCCESS') {
        setVideoLoading(false); setVideoStage('')
        if (articleId) fetchPackage(articleId)
        loadHistory()
      } else if (s.state === 'FAILURE') {
        setVideoLoading(false); setVideoStage('')
        setVideoError(s.error || 'Video generation failed')
      } else {
        videoPollTimer.current = window.setTimeout(() => pollVideo(taskId), 3000)
      }
    }).catch(e => {
      setVideoLoading(false); setVideoStage('')
      setVideoError(String((e as Error)?.message ?? e))
    })
  }

  function reset() {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    if (videoPollTimer.current) window.clearTimeout(videoPollTimer.current)
    setAudioJob(null); setPkg(null); setError('')
    setVideoJob(null); setVideoError(''); setVideoLoading(false); setVideoStage('')
    setLoading(false); setStatusText('Idle'); setPkgLoading(false)
  }

  // ── Derived ───────────────────────────────────────────────────────────────

  const selectedSource = useMemo(
    () => sources.find(s => String(s.id) === String(sourceId)) ?? null,
    [sources, sourceId],
  )

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

      {/* Generation form */}
      <div className="card">
        <div className="row">
          <div>
            <label>Source</label>
            <select
              value={sourceId}
              onChange={e => setSourceId(e.target.value)}
              disabled={!sources.length || loading}
            >
              {sources.map(s => (
                <option key={String(s.id)} value={String(s.id)}>{s.name}</option>
              ))}
            </select>
            {selectedSource && (
              <div className="small" style={{ marginTop: 6 }}>
                {selectedSource.language_hint && (
                  <><span className="code">{selectedSource.language_hint}</span> · </>
                )}
                <span className="code">{selectedSource.rss_url}</span>
              </div>
            )}
          </div>
          <div>
            <label>
              Voice ID <span className="small">
                {userKeys?.elevenlabs_voice_id
                  ? `(using account voice: ${userKeys.elevenlabs_voice_id})`
                  : '(optional — overrides account default)'}
              </span>
            </label>
            <input
              placeholder={userKeys?.elevenlabs_voice_id ?? 'ElevenLabs voice_id — leave empty for default'}
              value={voiceId}
              onChange={e => setVoiceId(e.target.value)}
              disabled={loading}
            />
          </div>
        </div>

        <hr />

        <div className="row">
          <div>
            <label>Target duration (seconds)</label>
            <input
              type="number" min={30} max={600} value={targetSeconds}
              onChange={e => setTargetSeconds(Number(e.target.value))}
              disabled={loading}
            />
          </div>
          <div>
            <label>Storyboard scenes</label>
            <input
              type="number" min={0} max={20} value={nScenes}
              onChange={e => setNScenes(Number(e.target.value))}
              disabled={loading}
            />
          </div>
        </div>

        <div className="actions" style={{ marginTop: 14 }}>
          <button onClick={startAudio} disabled={loading || !sourceId}>
            {loading ? 'Generating…' : 'Generate Audio'}
          </button>
          {loading
            ? <button className="secondary" onClick={cancelAudio}>Cancel</button>
            : <button className="secondary" onClick={reset}>Reset</button>
          }
        </div>

        {error && <div className="error-text">{error}</div>}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Collapsible title={`History · ${history.length} articles`}>
            <div className="history-list">
              {history.map(a => (
                <HistoryRow key={a.id} article={a} sources={sources} onLoad={loadFromHistory} />
              ))}
            </div>
          </Collapsible>
        </div>
      )}

      {/* Package loading spinner */}
      {pkgLoading && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="small">Loading article…</div>
        </div>
      )}

      {/* Results */}
      {pkg && (
        <div className="results">

          {/* Article title + audio metadata + player */}
          <div className="result-meta card">
            <a href={pkg.url} target="_blank" rel="noreferrer" className="article-title link">
              {pkg.title}
            </a>
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
            {pkg.storyboard && (
              <div className="download-row" style={{ marginTop: 8 }}>
                <span className="small">Subtitles:</span>
                <a href={resolveCaptionUrl(pkg.article_id, 'srt')} className="dl-btn" download>SRT</a>
                <a href={resolveCaptionUrl(pkg.article_id, 'vtt')} className="dl-btn" download>VTT</a>
              </div>
            )}
          </div>

          {/* Script */}
          {pkg.script && (
            <Collapsible title={`Script · ${pkg.script.word_count} words · ${pkg.script.language}`}>
              <pre className="script-pre">{pkg.script.text}</pre>
            </Collapsible>
          )}

          {/* Storyboard */}
          {pkg.storyboard && pkg.storyboard.scenes.length > 0 && (
            <Collapsible
              title={`Storyboard · ${pkg.storyboard.scenes.length} scenes · ${fmtSeconds(pkg.storyboard.total_duration_estimate)}`}
              defaultOpen
            >
              <div className="scene-list">
                {pkg.storyboard.scenes.map(s => (
                  <SceneCard key={s.scene_number} scene={s} imageRef={imageMap[s.scene_number]} />
                ))}
              </div>
            </Collapsible>
          )}

          {/* Video */}
          <div className="video-section card">
            <div className="video-section-header">
              <strong>Video</strong>
              {videoStage && <span className="small">{videoStage}</span>}
              {!videoStage && videoReady && pkg.video && (
                <span className="small">
                  {fmtSeconds(pkg.video.duration_seconds)} · {pkg.video.width}×{pkg.video.height}
                  {pkg.video.has_subtitles ? ' · subtitles' : ''}
                </span>
              )}
            </div>

            {videoReady && pkg.video ? (
              <div>
                <video controls className="video-player">
                  <source src={resolveVideoUrl(pkg.video.id)} type="video/mp4" />
                </video>
                <div className="actions" style={{ marginTop: 10 }}>
                  <a href={resolveVideoUrl(pkg.video.id)} className="dl-btn dl-btn-primary" download>
                    Download MP4
                  </a>
                  {pkg.video.has_subtitles && <span className="small">subtitles burned in</span>}
                  {articleId && (
                    <button className="secondary" style={{ fontSize: 12, padding: '5px 10px' }}
                      onClick={() => retryVideo('images')} disabled={videoLoading}>
                      Regenerate Images
                    </button>
                  )}
                </div>
              </div>
            ) : pkg.video?.status === 'failed' ? (
              <div>
                {pkg.video.error && (
                  <div className="error-text" style={{ marginBottom: 10 }}>{pkg.video.error}</div>
                )}
                <div className="actions">
                  <button onClick={() => retryVideo('video')} disabled={videoLoading || !articleId}>
                    {videoLoading ? 'Retrying…' : 'Retry Video Assembly'}
                  </button>
                  <button className="secondary" onClick={() => retryVideo('images')}
                    disabled={videoLoading || !articleId}>
                    Regenerate All Images
                  </button>
                  {videoLoading && (
                    <button className="secondary" onClick={cancelVideo}>Cancel</button>
                  )}
                </div>
              </div>
            ) : (
              <div className="actions">
                <button onClick={startVideo} disabled={videoLoading || !articleId}>
                  {videoLoading ? 'Generating…' : 'Generate Video'}
                </button>
                {videoLoading
                  ? <button className="secondary" onClick={cancelVideo}>Cancel</button>
                  : <span className="small">DALL-E scene images + FFmpeg assembly · ~2–5 min</span>
                }
              </div>
            )}

            {videoError && <div className="error-text">{videoError}</div>}

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

    </div>
  )
}
