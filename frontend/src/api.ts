import type { Source, GenerateReq, GenerateResp, JobStatus } from './types'

const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined
// If VITE_API_BASE_URL is empty, we fall back to same-origin relative requests.
const API_BASE = (rawBase && rawBase.trim().length > 0) ? rawBase.replace(/\/$/, '') : ''

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  // Some endpoints might return empty responses; but for this app we always expect JSON here.
  return res.json() as Promise<T>
}

// Your backend contract (as shared):
// - GET  /sources
// - POST /generate  (body: GenerateReq) -> { task_id, status }
// - GET  /jobs/{task_id} -> JobStatus
// - GET  /audio/{audio_id} -> mp3 file
// - GET  /articles/{article_id} (optional for UI links)

export function listSources(): Promise<Source[]> {
  return http<Source[]>('/sources')
}

export function startGenerate(req: GenerateReq): Promise<GenerateResp> {
  return http<GenerateResp>('/generate', { method: 'POST', body: JSON.stringify(req) })
}

export function jobStatus(taskId: string): Promise<JobStatus> {
  return http<JobStatus>(`/jobs/${encodeURIComponent(taskId)}`)
}

export function resolveDownloadUrl(status: JobStatus): string | undefined {
  const r = status.result
  if (!r) return undefined

  // your API adds a friendly relative audio_url when SUCCESS (e.g. "/audio/{audio_id}")
  if (typeof r.audio_url === 'string' && r.audio_url.length) {
    // If audio_url is already absolute, use it; else prefix with API_BASE.
    if (/^https?:\/\//i.test(r.audio_url)) return r.audio_url
    return `${API_BASE}${r.audio_url}`
  }

  if (typeof r.audio_id === 'string' && r.audio_id.length) {
    return `${API_BASE}/audio/${r.audio_id}`
  }

  return undefined
}
