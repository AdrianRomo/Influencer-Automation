import { useCallback, useEffect, useRef, useState } from 'react'
import { Canvas, Controls, Edit, Timeline } from '@shotstack/shotstack-studio'

import {
  getTimeline, pollTimelineRender, resolveVideoUrl, saveTimeline, startTimelineRender,
} from '../api'
import type { EditDocument, TimelineVisualClip } from '../types'
import { canonicalToStudio, mergeStudioIntoCanonical } from './studioTemplate'

const MOTION_EFFECTS: { value: string; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'zoomIn', label: 'Ken Burns — Zoom in' },
  { value: 'zoomOut', label: 'Ken Burns — Zoom out' },
  { value: 'slideLeft', label: 'Pan left' },
  { value: 'slideRight', label: 'Pan right' },
  { value: 'slideUp', label: 'Pan up' },
  { value: 'slideDown', label: 'Pan down' },
]

type RenderState = { phase: 'idle' | 'rendering' | 'done' | 'error'; message?: string; videoId?: string }

export function VideoEditor({ articleId, onClose }: { articleId: string; onClose: () => void }) {
  const canvasHostRef = useRef<HTMLDivElement | null>(null)
  const timelineHostRef = useRef<HTMLDivElement | null>(null)
  const editRef = useRef<any>(null)
  const canonicalRef = useRef<EditDocument | null>(null)
  const pollRef = useRef<number | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>('')
  const [version, setVersion] = useState<number | undefined>(undefined)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [render, setRender] = useState<RenderState>({ phase: 'idle' })
  // Visual clips that can be animated (those backed by an asset), for the side panel.
  const [visualClips, setVisualClips] = useState<TimelineVisualClip[]>([])

  const visualTrackIndex = useCallback((doc: EditDocument): number => {
    // canonicalToStudio emits subtitles first (if any), then visuals.
    const subs = doc.tracks.find(t => t.type === 'subtitles')
    return subs && subs.clips.length ? 1 : 0
  }, [])

  // ── Mount the Studio editor ────────────────────────────────────────────────
  useEffect(() => {
    let disposed = false
    let canvas: any, timeline: any, controls: any

    async function mount() {
      try {
        const resp = await getTimeline(articleId)
        if (disposed) return
        canonicalRef.current = resp.edit
        setVersion(resp.version)
        setVisualClips(
          (resp.edit.tracks.find(t => t.type === 'visual')?.clips ?? [])
            .filter((c: any) => c.asset_id) as TimelineVisualClip[],
        )

        const template = canonicalToStudio(resp.edit)
        const edit = new Edit(template)
        editRef.current = edit
        canvas = new Canvas(edit)
        await canvas.load()
        await edit.load()

        if (timelineHostRef.current) {
          timeline = new Timeline(edit, timelineHostRef.current)
          await timeline.load()
        }
        controls = new Controls(edit)
        await controls.load()

        edit.events.on('edit:changed', () => setDirty(true))
        edit.events.on('clip:updated', () => setDirty(true))

        if (!disposed) setLoading(false)
      } catch (e: any) {
        if (!disposed) {
          setError(e?.message ?? 'Failed to load the editor')
          setLoading(false)
        }
      }
    }
    mount()

    return () => {
      disposed = true
      if (pollRef.current) window.clearInterval(pollRef.current)
      try { timeline?.dispose?.() } catch { /* noop */ }
      try { canvas?.dispose?.() } catch { /* noop */ }
      editRef.current = null
    }
  }, [articleId])

  // ── Save ───────────────────────────────────────────────────────────────────
  const handleSave = useCallback(async (): Promise<EditDocument | null> => {
    const edit = editRef.current
    const canonical = canonicalRef.current
    if (!edit || !canonical) return null
    setSaving(true)
    setError('')
    try {
      const snapshot = edit.getEdit()
      const merged = mergeStudioIntoCanonical(canonical, snapshot)
      const resp = await saveTimeline(articleId, merged, version)
      canonicalRef.current = resp.edit
      setVersion(resp.version)
      setDirty(false)
      return resp.edit
    } catch (e: any) {
      setError(e?.message ?? 'Save failed')
      return null
    } finally {
      setSaving(false)
    }
  }, [articleId, version])

  // ── Animate (free motion-effect tier) ──────────────────────────────────────
  const applyEffect = useCallback((sceneNumber: number, effect: string) => {
    const edit = editRef.current
    const canonical = canonicalRef.current
    if (!edit || !canonical) return

    const visual = canonical.tracks.find(t => t.type === 'visual')
    if (!visual) return
    const emitted = (visual.clips as TimelineVisualClip[]).filter(c => c.asset_id)
    const clipIndex = emitted.findIndex(c => c.scene_number === sceneNumber)
    if (clipIndex < 0) return

    // Update canonical (source of truth) + live preview.
    emitted[clipIndex].animate = {
      ...emitted[clipIndex].animate,
      mode: effect ? 'effect' : 'none',
      effect: effect || null,
    }
    setVisualClips([...emitted])
    setDirty(true)
    try {
      edit.updateClip(visualTrackIndex(canonical), clipIndex, { effect: effect || null })
    } catch { /* preview update is best-effort */ }
  }, [visualTrackIndex])

  // ── Render ───────────────────────────────────────────────────────────────────
  const handleRender = useCallback(async () => {
    const saved = await handleSave()
    if (!saved) return
    setRender({ phase: 'rendering', message: 'Submitting render…' })
    try {
      const start = await startTimelineRender(articleId)
      pollRef.current = window.setInterval(async () => {
        try {
          const st = await pollTimelineRender(articleId, start.render_job_id)
          if (st.status === 'done' && st.video_asset_id) {
            if (pollRef.current) window.clearInterval(pollRef.current)
            setRender({ phase: 'done', videoId: st.video_asset_id })
          } else if (st.status === 'failed') {
            if (pollRef.current) window.clearInterval(pollRef.current)
            setRender({ phase: 'error', message: st.error ?? 'Render failed' })
          } else {
            setRender({ phase: 'rendering', message: `Rendering… (${st.status})` })
          }
        } catch (e: any) {
          if (pollRef.current) window.clearInterval(pollRef.current)
          setRender({ phase: 'error', message: e?.message ?? 'Render polling failed' })
        }
      }, 4000)
    } catch (e: any) {
      setRender({ phase: 'error', message: e?.message ?? 'Could not start render' })
    }
  }, [articleId, handleSave])

  return (
    <div className="editor-overlay">
      <header className="editor-bar">
        <button className="secondary" onClick={onClose}>← Back</button>
        <span className="editor-title">Video editor {dirty && <em className="small">· unsaved</em>}</span>
        <div className="editor-actions">
          <button className="secondary" onClick={handleSave} disabled={saving || loading}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button onClick={handleRender} disabled={loading || render.phase === 'rendering'}>
            {render.phase === 'rendering' ? 'Rendering…' : 'Generate video'}
          </button>
        </div>
      </header>

      {error && <div className="editor-error">{error}</div>}
      {render.phase === 'error' && <div className="editor-error">Render: {render.message}</div>}
      {render.phase === 'rendering' && <div className="editor-status">{render.message}</div>}
      {render.phase === 'done' && render.videoId && (
        <div className="editor-status">
          Render complete — <a href={resolveVideoUrl(render.videoId)} download>download MP4</a>
        </div>
      )}

      <div className="editor-body">
        <div className="editor-stage">
          {loading && <div className="editor-loading">Loading editor…</div>}
          {/* Shotstack Studio mounts the preview canvas here. */}
          <div data-shotstack-studio ref={canvasHostRef} className="editor-canvas" />
          <div data-shotstack-timeline ref={timelineHostRef} className="editor-timeline" />
        </div>

        <aside className="editor-sidebar">
          <h4>Animate scenes</h4>
          <p className="small">
            Motion effects render instantly and free. AI image-to-video (provider-generated
            motion) arrives in a later phase.
          </p>
          {visualClips.length === 0 && <p className="small">No scene assets yet — generate images first.</p>}
          {visualClips.map(c => (
            <div key={c.id} className="animate-row">
              <label className="small">
                Scene {c.scene_number}
                {c.asset_kind === 'video' && <span className="badge"> AI clip</span>}
              </label>
              <select
                value={c.animate?.effect ?? ''}
                disabled={c.asset_kind === 'video'}
                onChange={e => applyEffect(c.scene_number, e.target.value)}
              >
                {MOTION_EFFECTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          ))}
        </aside>
      </div>
    </div>
  )
}
