import type { ArticleSummary, Source, GenerateReq, GenerateResp, JobStatus, ContentPackage } from './types'

const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined
const API_BASE = (rawBase && rawBase.trim().length > 0) ? rawBase.replace(/\/$/, '') : ''

// Module-level key set by the app on init and whenever the user updates it
let _apiKey = ''
export function setApiKey(k: string) { _apiKey = k }

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (_apiKey) headers['X-API-Key'] = _apiKey
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return res.json() as Promise<T>
}

export function listSources(): Promise<Source[]> {
  return http<Source[]>('/sources')
}

export function listArticles(params?: { source_id?: string; limit?: number; offset?: number }): Promise<ArticleSummary[]> {
  const q = new URLSearchParams()
  if (params?.source_id) q.set('source_id', params.source_id)
  if (params?.limit != null) q.set('limit', String(params.limit))
  if (params?.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return http<ArticleSummary[]>(`/articles${qs ? `?${qs}` : ''}`)
}

export function startGenerate(req: GenerateReq): Promise<GenerateResp> {
  return http<GenerateResp>('/generate', { method: 'POST', body: JSON.stringify(req) })
}

export function jobStatus(taskId: string): Promise<JobStatus> {
  return http<JobStatus>(`/jobs/${encodeURIComponent(taskId)}`)
}

export function getArticlePackage(articleId: string): Promise<ContentPackage> {
  return http<ContentPackage>(`/articles/${encodeURIComponent(articleId)}/package`)
}

export function startGenerateVideo(
  articleId: string,
  burnSubtitles: boolean = true,
): Promise<GenerateResp> {
  return http<GenerateResp>('/generate-video', {
    method: 'POST',
    body: JSON.stringify({ article_id: articleId, burn_subtitles: burnSubtitles }),
  })
}

export function resolveAudioUrl(audioId: string): string {
  return `${API_BASE}/audio/${audioId}`
}

export function resolveImageUrl(imageId: string): string {
  return `${API_BASE}/image/${imageId}`
}

export function resolveVideoUrl(videoId: string): string {
  return `${API_BASE}/video/${videoId}`
}

export function resolveCaptionUrl(articleId: string, format: 'srt' | 'vtt'): string {
  return `${API_BASE}/articles/${articleId}/captions.${format}`
}

// Legacy helper kept for backward-compat with places that still have a JobStatus
export function resolveDownloadUrl(status: JobStatus): string | undefined {
  const r = status.result
  if (!r) return undefined
  if (typeof r.audio_url === 'string' && r.audio_url.length) {
    return /^https?:\/\//i.test(r.audio_url) ? r.audio_url : `${API_BASE}${r.audio_url}`
  }
  if (typeof r.audio_id === 'string' && r.audio_id.length) {
    return resolveAudioUrl(r.audio_id)
  }
  return undefined
}
