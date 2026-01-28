import { useEffect, useMemo, useRef, useState } from 'react'
import type { GenerateReq, JobStatus, Source } from './types'
import { jobStatus, listSources, resolveDownloadUrl, startGenerate } from './api'

export default function App() {
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState<string>('')
  const [voiceId, setVoiceId] = useState<string>('')
  const [targetSeconds, setTargetSeconds] = useState<number>(180)
  const [nScenes, setNScenes] = useState<number>(8)

  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('Idle')
  const [job, setJob] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string>('')

  const pollTimer = useRef<number | null>(null)

  const downloadUrl = useMemo(() => (job ? resolveDownloadUrl(job) : undefined), [job])
  const articleUrl = useMemo(() => {
    const articleId = job?.result?.article_id
    if (!articleId) return undefined
    const base = import.meta.env.VITE_API_BASE_URL ? String(import.meta.env.VITE_API_BASE_URL).replace(/\/$/, '') : ''
    return `${base}/articles/${articleId}`
  }, [job])

  async function load() {
    setError('')
    setStatusText('Loading sources…')
    try {
      const s = await listSources()
      setSources(s)
      if (s.length) setSourceId(String(s[0].id))
      setStatusText('Ready')
    } catch (e: any) {
      setStatusText('Error')
      setError(e?.message ?? String(e))
    }
  }

  useEffect(() => {
    load()
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current)
    }
  }, [])

  async function start() {
    if (!sourceId) return
    setError('')
    setLoading(true)
    setJob(null)
    setStatusText('Queueing job…')

    const payload: GenerateReq = {
      source_id: sourceId,
      voice_id: voiceId.trim().length ? voiceId.trim() : null,
      target_seconds: targetSeconds,
      n_scenes: nScenes,
    }

    try {
      const resp = await startGenerate(payload)
      setStatusText(`Queued: ${resp.task_id}`)
      poll(resp.task_id)
    } catch (e: any) {
      setStatusText('Error')
      setError(e?.message ?? String(e))
      setLoading(false)
    }
  }

  async function poll(taskId: string) {
    try {
      const s = await jobStatus(taskId)
      setJob(s)
      setStatusText(`State: ${s.state}`)

      if (s.state === 'SUCCESS' || s.state === 'FAILURE') {
        setLoading(false)
        if (s.state === 'FAILURE') setError(s.error || 'Job failed (no error message provided).')
        return
      }

      pollTimer.current = window.setTimeout(() => poll(taskId), 2000)
    } catch (e: any) {
      setLoading(false)
      setStatusText('Error')
      setError(e?.message ?? String(e))
    }
  }

  function reset() {
    setJob(null)
    setError('')
    setLoading(false)
    setStatusText('Ready')
  }

  const selectedSource = useMemo(() => sources.find(s => String(s.id) === String(sourceId)) ?? null, [sources, sourceId])

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="h1">Medical RSS → Summary → ElevenLabs (MVP)</div>
          <div className="small">Select source → Generate → Download MP3 when ready</div>
        </div>
        <div className="status">{statusText}</div>
      </div>

      <div className="card">
        <div className="row">
          <div>
            <label>Source</label>
            <select
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              disabled={sources.length === 0 || loading}
            >
              {sources.map(s => (
                <option key={String(s.id)} value={String(s.id)}>
                  {s.name}
                </option>
              ))}
            </select>
            {selectedSource && (
              <div className="small" style={{ marginTop: 6 }}>
                RSS: <span className="code">{selectedSource.rss_url}</span>
                {selectedSource.language_hint ? <> · Lang: <span className="code">{selectedSource.language_hint}</span></> : null}
              </div>
            )}
          </div>

          <div>
            <label>Voice ID (optional)</label>
            <input
              placeholder="ElevenLabs voice_id (optional)"
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
              disabled={loading}
            />
            <div className="small" style={{ marginTop: 6 }}>
              Leave empty to use backend default.
            </div>
          </div>
        </div>

        <hr />

        <div className="row">
          <div>
            <label>Target seconds</label>
            <input
              type="number"
              min={30}
              max={600}
              value={targetSeconds}
              onChange={(e) => setTargetSeconds(Number(e.target.value))}
              disabled={loading}
            />
          </div>
          <div>
            <label>Storyboard scenes</label>
            <input
              type="number"
              min={0}
              max={20}
              value={nScenes}
              onChange={(e) => setNScenes(Number(e.target.value))}
              disabled={loading}
            />
          </div>
        </div>

        <div className="actions" style={{ marginTop: 14 }}>
          <button onClick={start} disabled={loading || !sourceId}>
            {loading ? 'Working…' : 'Generate MP3'}
          </button>

          <button className="secondary" onClick={reset} disabled={loading}>
            Reset
          </button>

          {downloadUrl && (
            <a href={downloadUrl} className="link" download>
              Download MP3
            </a>
          )}
        </div>

        {job?.result?.audio_id && (
          <div className="small" style={{ marginTop: 10 }}>
            audio_id: <span className="code">{String(job.result.audio_id)}</span>
          </div>
        )}

        {articleUrl && (
          <div className="small" style={{ marginTop: 6 }}>
            article: <a className="link" href={articleUrl} target="_blank" rel="noreferrer">{articleUrl}</a>
          </div>
        )}

        {error && (
          <div className="small" style={{ marginTop: 10 }}>
            <b>Error:</b> {error}
          </div>
        )}
      </div>

      <div className="small" style={{ marginTop: 14 }}>
        Config: set <span className="code">VITE_API_BASE_URL</span> to your FastAPI base URL (example: <span className="code">http://localhost:8000</span>).
      </div>
    </div>
  )
}
