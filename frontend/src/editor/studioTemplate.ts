// Adapter between our canonical Edit document (source of truth, persisted by the
// backend) and the Shotstack Studio template format the editor mounts.
//
// Load:  canonicalToStudio()  — build a Studio template with browser-reachable
//        preview URLs (the public/API media routes).
// Save:  mergeStudioIntoCanonical() — fold the editor's timing/text/effect edits
//        back into the canonical doc, preserving each clip's asset identity
//        (asset_kind/asset_id) so the renderer can later resolve public URLs.
//
// Round-trip is reconciled per track by clip index, which covers the Phase 2
// operations (retime/drag, subtitle text + style, motion effect, transitions).
// Adding/removing/reordering clips is a later enhancement.

import { resolveAudioUrl, resolveImageUrl, resolveSceneVideoUrl } from '../api'
import type { EditDocument, TimelineSubtitleClip, TimelineVisualClip } from '../types'

const round = (n: number) => Math.round(n * 1000) / 1000

// Shotstack Studio requires 6-digit hex colors (#RRGGBB) with a separate
// `opacity`; our styles may carry 8-digit #RRGGBBAA. Split the alpha out.
function splitColor(value: string | undefined, fallback: string): { color: string; opacity: number } {
  const v = (value ?? '').trim()
  const m8 = /^#([A-Fa-f0-9]{6})([A-Fa-f0-9]{2})$/.exec(v)
  if (m8) return { color: `#${m8[1]}`, opacity: round(parseInt(m8[2], 16) / 255) }
  const m6 = /^#([A-Fa-f0-9]{6})$/.exec(v)
  if (m6) return { color: `#${m6[1]}`, opacity: 1 }
  return { color: fallback, opacity: 1 }
}

function assetUrl(kind: string, id: string): string {
  if (kind === 'video') return resolveSceneVideoUrl(id)
  if (kind === 'audio') return resolveAudioUrl(id)
  return resolveImageUrl(id)
}

function subtitleToStudio(c: TimelineSubtitleClip) {
  const style = (c.style ?? {}) as Record<string, any>
  const fg = splitColor(style.color, '#FFFFFF')
  const bg = splitColor(style.background, '#000000')
  return {
    asset: {
      type: 'text',
      text: c.text ?? '',
      font: {
        family: style.font_family ?? 'Montserrat ExtraBold',
        size: Number(style.font_size ?? 48),
        color: fg.color,
        opacity: fg.opacity,
        weight: 700,
      },
      background: { color: bg.color, opacity: bg.opacity, padding: 12 },
      alignment: { horizontal: style.align ?? 'center', vertical: 'bottom' },
    },
    start: c.start,
    length: c.length,
    position: style.position ?? 'bottom',
  }
}

function visualToStudio(c: TimelineVisualClip) {
  const url = c.asset_id ? assetUrl(c.asset_kind, c.asset_id) : null
  const clip: any = {
    asset: { type: c.asset_kind === 'video' ? 'video' : 'image', src: url },
    start: c.start,
    length: c.length,
    fit: c.fit ?? 'cover',
  }
  if (c.animate?.mode === 'effect' && c.animate.effect) clip.effect = c.animate.effect
  const t: any = {}
  if (c.transition?.in) t.in = c.transition.in
  if (c.transition?.out) t.out = c.transition.out
  if (Object.keys(t).length) clip.transition = t
  return clip
}

/** Canonical Edit document → Shotstack Studio template (for editing/preview). */
export function canonicalToStudio(edit: EditDocument): any {
  const out = edit.output ?? { width: 1080, height: 1920, fps: 30, format: 'mp4' }
  const tracks: any[] = []
  for (const track of edit.tracks ?? []) {
    if (track.type === 'subtitles') {
      const clips = track.clips.map(subtitleToStudio)
      if (clips.length) tracks.push({ clips })
    } else {
      // Only clips that actually have a rendered asset can be previewed.
      const clips = track.clips.filter(c => c.asset_id).map(visualToStudio)
      if (clips.length) tracks.push({ clips })
    }
  }

  const template: any = {
    timeline: { background: '#000000', tracks },
    output: { format: out.format ?? 'mp4', size: { width: out.width, height: out.height }, fps: out.fps ?? 30 },
  }
  if (edit.soundtrack?.asset_id) {
    template.timeline.soundtrack = { src: assetUrl('audio', edit.soundtrack.asset_id), effect: 'fadeOut' }
  }
  return template
}

/** Fold the Studio editor's snapshot back into the canonical document. */
export function mergeStudioIntoCanonical(canonical: EditDocument, studio: any): EditDocument {
  const next: EditDocument = structuredClone(canonical)
  const sTracks: any[] = studio?.timeline?.tracks ?? []

  // canonicalToStudio emits non-empty tracks in canonical order; walk in lockstep.
  let si = 0
  for (const ctrack of next.tracks) {
    const emitted =
      ctrack.type === 'subtitles'
        ? ctrack.clips
        : (ctrack.clips as TimelineVisualClip[]).filter(c => c.asset_id)
    if (emitted.length === 0) continue
    const strack = sTracks[si]
    si += 1
    if (!strack?.clips) continue

    emitted.forEach((c: any, i: number) => {
      const s = strack.clips[i]
      if (!s) return
      c.start = round(Number(s.start ?? c.start))
      c.length = round(Number(s.length ?? c.length))
      if (ctrack.type === 'subtitles') {
        const text = s.asset?.text
        if (typeof text === 'string') c.text = text
      } else {
        if (s.effect) c.animate = { ...c.animate, mode: 'effect', effect: s.effect }
        if (s.transition) {
          c.transition = { in: s.transition.in ?? null, out: s.transition.out ?? null }
        }
      }
    })
  }

  return next
}
