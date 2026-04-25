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
