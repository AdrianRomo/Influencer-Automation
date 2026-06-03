import type { JobStatus } from './types'

export function fmtSeconds(s: number | null | undefined): string {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

export function assetBadgeClass(type: string): string {
  if (type === 'title-card') return 'badge badge-title'
  if (type === 'outro') return 'badge badge-outro'
  return 'badge badge-broll'
}

export function relativeDate(iso: string): string {
  const d = new Date(iso)
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.floor(days / 7)}w ago`
  return d.toLocaleDateString()
}

export const STATE_LABELS: Record<string, string> = {
  PENDING: 'Queued…',
  RECEIVED: 'Starting…',
  STARTED: 'Running…',
  RETRY: 'Retrying…',
  SUCCESS: 'Done',
  FAILURE: 'Failed',
  REVOKED: 'Cancelled',
}

export function stageLabel(s: JobStatus): string {
  if (s.state === 'PROGRESS' && s.meta?.msg) return s.meta.msg
  return STATE_LABELS[s.state] ?? s.state
}

// ── Named video-generation stages ──────────────────────────────────────────
// An ordered, user-facing pipeline used by <GenerationProgress>. We don't get
// a clean stage enum from the backend — only free-form PROGRESS `meta.msg`
// strings — so we map those messages onto these stages by keyword. Anything we
// can't classify keeps the previous stage active rather than jumping around.

export type StageId =
  | 'script' | 'audio' | 'subtitles' | 'scenes'
  | 'clips' | 'normalize' | 'combine' | 'burn' | 'export'

export type VideoStage = { id: StageId; label: string }

// Static slideshow renders skip the per-clip animation stages; the animated
// pipeline uses the full list. `videoStages(mode)` returns the right subset.
const ALL_STAGES: VideoStage[] = [
  { id: 'script',    label: 'Preparing script' },
  { id: 'audio',     label: 'Generating audio' },
  { id: 'subtitles', label: 'Creating subtitles' },
  { id: 'scenes',    label: 'Planning scenes' },
  { id: 'clips',     label: 'Generating visual clips' },
  { id: 'normalize', label: 'Normalizing clips' },
  { id: 'combine',   label: 'Combining video' },
  { id: 'burn',      label: 'Adding subtitles' },
  { id: 'export',    label: 'Exporting final MP4' },
]

export function videoStages(mode: 'static' | 'animated'): VideoStage[] {
  if (mode === 'animated') return ALL_STAGES
  // Static slideshow has no per-clip animation/normalize step.
  return ALL_STAGES.filter(s => s.id !== 'clips' && s.id !== 'normalize')
}

// Keyword → stage classification, checked in order. Lowercased substring match.
const STAGE_KEYWORDS: [StageId, string[]][] = [
  ['script',    ['script', 'summar', 'narrat']],
  ['audio',     ['audio', 'voice', 'tts', 'synth', 'speech']],
  ['subtitles', ['caption', 'subtitle', 'srt']],
  ['scenes',    ['scene plan', 'storyboard', 'planning', 'image', 'dall']],
  ['clips',     ['clip', 'animat', 'provider', 'render scene']],
  ['normalize', ['normaliz', 'trim', 'pad']],
  ['combine',   ['combin', 'assembl', 'concat', 'ffmpeg', 'stitch']],
  ['burn',      ['burn', 'overlay']],
  ['export',    ['export', 'final', 'encod', 'faststart']],
]

/** Classify a free-form progress message into a StageId, or null if unknown. */
export function classifyStage(msg: string | undefined | null): StageId | null {
  if (!msg) return null
  const m = msg.toLowerCase()
  for (const [id, words] of STAGE_KEYWORDS) {
    if (words.some(w => m.includes(w))) return id
  }
  return null
}

// ── Friendly error mapping ──────────────────────────────────────────────────
// Raw errors ("FFmpeg error", "500 Internal Server Error", provider stack
// traces) are useless to creators. Map them to a plain-language message, a
// likely cause, and a recovery hint. `raw` is preserved for a "details" toggle.

export type FriendlyError = {
  title: string
  detail: string
  /** Which retry action, if any, the recovery card should offer. */
  action?: 'retry-video' | 'retry-images' | 'add-keys' | 'reload'
  raw: string
}

export function friendlyError(raw: unknown): FriendlyError {
  const text = String((raw as Error)?.message ?? raw ?? '').trim()
  const t = text.toLowerCase()

  if (t.includes('api key') || t.includes('unauthorized') || t.includes('401') || t.includes('missing key')) {
    return {
      title: 'API key problem',
      detail: 'A required API key is missing or invalid. Add your keys in settings, or switch back to the standard (black background) video mode.',
      action: 'add-keys',
      raw: text,
    }
  }
  if (t.includes('provider') || t.includes('clip') || t.includes('scene')) {
    return {
      title: 'A visual clip failed to generate',
      detail: 'The AI video provider could not create one or more scenes. Your video can still be exported using the fallback background — or retry just the visual background.',
      action: 'retry-images',
      raw: text,
    }
  }
  if (t.includes('ffmpeg') || t.includes('assembl') || t.includes('render') || t.includes('video')) {
    return {
      title: 'Video assembly hit a snag',
      detail: 'Something went wrong while combining your clips into the final MP4. This is usually temporary — retry the video and it should complete.',
      action: 'retry-video',
      raw: text,
    }
  }
  if (t.includes('rate limit') || t.includes('429') || t.includes('capacity')) {
    return {
      title: 'Too many jobs at once',
      detail: 'You have hit a temporary rate limit. Wait a moment and try again.',
      action: 'retry-video',
      raw: text,
    }
  }
  if (t.includes('network') || t.includes('failed to fetch') || t.includes('timeout') || t.includes('500') || t.includes('502') || t.includes('503')) {
    return {
      title: 'Connection problem',
      detail: 'We could not reach the server. Check your connection and try again.',
      action: 'reload',
      raw: text,
    }
  }
  return {
    title: 'Something went wrong',
    detail: text || 'An unexpected error occurred. Please try again.',
    action: 'retry-video',
    raw: text,
  }
}
