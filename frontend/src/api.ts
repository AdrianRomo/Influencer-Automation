import type {
  ArticleSummary, Source, GenerateReq, GenerateResp, JobStatus, ContentPackage,
  TokenResp, UserResp, UserKeysIn, UserKeysOut,
} from './types'

const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined
const API_BASE = (rawBase && rawBase.trim().length > 0) ? rawBase.replace(/\/$/, '') : ''

// Module-level auth state — set by the app on init and whenever auth changes
let _apiKey = ''
let _jwtToken = ''

export function setApiKey(k: string) { _apiKey = k }
export function setAuthToken(token: string) { _jwtToken = token }
export function clearAuthToken() { _jwtToken = '' }

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (_jwtToken) {
    headers['Authorization'] = `Bearer ${_jwtToken}`
  } else if (_apiKey) {
    headers['X-API-Key'] = _apiKey
  }
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

// ── Auth ───────────────────────────────────────────────────────────────────

export function register(email: string, password: string): Promise<TokenResp> {
  return http<TokenResp>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function login(email: string, password: string): Promise<TokenResp> {
  return http<TokenResp>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function getMe(): Promise<UserResp> {
  return http<UserResp>('/users/me')
}

export function getUserKeys(): Promise<UserKeysOut> {
  return http<UserKeysOut>('/users/me/keys')
}

export function saveUserKeys(keys: UserKeysIn): Promise<UserKeysOut> {
  return http<UserKeysOut>('/users/me/keys', { method: 'PUT', body: JSON.stringify(keys) })
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
export function cancelJob(taskId: string): Promise<{ cancelled: string }> {
  return http<{ cancelled: string }>(`/jobs/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export function regenerateStage(
  articleId: string,
  stage: 'video' | 'images',
  burnSubtitles = true,
): Promise<{ task_id: string; status: string; stage: string }> {
  return http(`/articles/${encodeURIComponent(articleId)}/regenerate`, {
    method: 'POST',
    body: JSON.stringify({ stage, burn_subtitles: burnSubtitles }),
  })
}

export function editScript(
  articleId: string,
  text: string,
): Promise<{ article_id: string; word_count: number }> {
  return http(`/articles/${encodeURIComponent(articleId)}/script`, {
    method: 'PATCH',
    body: JSON.stringify({ text }),
  })
}

export function startRegenerateScript(
  articleId: string,
  nScenes = 8,
): Promise<{ task_id: string; status: string }> {
  return http(`/articles/${encodeURIComponent(articleId)}/regenerate-script`, {
    method: 'POST',
    body: JSON.stringify({ n_scenes: nScenes }),
  })
}

export function getExportZipUrl(articleId: string): string {
  return `${API_BASE}/articles/${encodeURIComponent(articleId)}/export.zip`
}

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
