import type {
  ArticleSummary, PaginatedArticles, PrepareArticleReq, RssCandidate, Source,
  GenerateReq, GenerateResp, JobStatus, ContentPackage, RenderMode,
  TokenResp, UserResp, UserKeysIn, UserKeysOut, CostSummary, PlatformsResp,
} from './types'

const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined
const API_BASE = (rawBase && rawBase.trim().length > 0) ? rawBase.replace(/\/$/, '') : ''

// Module-level auth state — set by the app on init and whenever auth changes
let _apiKey = ''
let _jwtToken = ''
let _refreshToken = ''

export function setApiKey(k: string) { _apiKey = k }
export function setAuthToken(token: string) { _jwtToken = token }
export function clearAuthToken() { _jwtToken = '' }
export function setRefreshToken(t: string) {
  _refreshToken = t
  if (t) localStorage.setItem('refresh_token', t)
  else localStorage.removeItem('refresh_token')
}
export function getStoredRefreshToken(): string {
  if (!_refreshToken) _refreshToken = localStorage.getItem('refresh_token') ?? ''
  return _refreshToken
}

// In-flight refresh deduplication: only one refresh request at a time
let _refreshInFlight: Promise<TokenResp> | null = null

async function _tryRefresh(): Promise<TokenResp | null> {
  const bodyToken = getStoredRefreshToken() || undefined
  if (!bodyToken) return null

  if (!_refreshInFlight) {
    _refreshInFlight = fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: bodyToken }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(`Refresh ${r.status}`)
        return r.json() as Promise<TokenResp>
      })
      .finally(() => { _refreshInFlight = null })
  }
  try {
    const tokens = await _refreshInFlight
    _jwtToken = tokens.access_token
    localStorage.setItem('jwt_token', tokens.access_token)
    if (tokens.refresh_token) {
      _refreshToken = tokens.refresh_token
      localStorage.setItem('refresh_token', tokens.refresh_token)
    }
    return tokens
  } catch {
    return null
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (_jwtToken) {
    headers['Authorization'] = `Bearer ${_jwtToken}`
  } else if (_apiKey) {
    headers['X-API-Key'] = _apiKey
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: { ...headers, ...(init?.headers ?? {}) },
  })
  // On 401 from non-auth endpoints, attempt transparent token refresh + retry
  if (res.status === 401 && !path.startsWith('/auth/')) {
    const tokens = await _tryRefresh()
    if (tokens) {
      const retryHeaders: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${_jwtToken}`,
      }
      const retry = await fetch(`${API_BASE}${path}`, {
        ...init,
        credentials: 'include',
        headers: { ...retryHeaders, ...(init?.headers ?? {}) },
      })
      if (!retry.ok) {
        const text = await retry.text().catch(() => '')
        throw new Error(`HTTP ${retry.status} ${retry.statusText}${text ? `: ${text}` : ''}`)
      }
      return retry.json() as Promise<T>
    }
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return res.json() as Promise<T>
}

// ── Auth ───────────────────────────────────────────────────────────────────

function _storeTokens(resp: TokenResp): TokenResp {
  if (resp.refresh_token) {
    _refreshToken = resp.refresh_token
    localStorage.setItem('refresh_token', resp.refresh_token)
  }
  return resp
}

export function register(email: string, password: string): Promise<TokenResp> {
  return http<TokenResp>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  }).then(_storeTokens)
}

export function login(email: string, password: string): Promise<TokenResp> {
  return http<TokenResp>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  }).then(_storeTokens)
}

export function refreshAccessToken(): Promise<TokenResp> {
  return fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: getStoredRefreshToken() || undefined }),
  }).then(async r => {
    if (!r.ok) throw new Error(`Token refresh failed: ${r.status}`)
    return r.json() as Promise<TokenResp>
  }).then(_storeTokens)
}

export async function logout(): Promise<void> {
  const rt = getStoredRefreshToken()
  await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: rt || undefined }),
  }).catch(() => {})
  _jwtToken = ''
  _refreshToken = ''
  localStorage.removeItem('jwt_token')
  localStorage.removeItem('refresh_token')
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

export function getPlatforms(): Promise<PlatformsResp> {
  return http<PlatformsResp>('/platforms')
}

export function fetchRssCandidates(sourceId?: string, limit = 10): Promise<RssCandidate[]> {
  const q = new URLSearchParams()
  if (sourceId) q.set('source_id', sourceId)
  q.set('limit', String(limit))
  return http<RssCandidate[]>(`/rss/candidates?${q.toString()}`)
}

export function listArticles(params?: { source_id?: string; limit?: number; offset?: number }): Promise<PaginatedArticles> {
  const q = new URLSearchParams()
  if (params?.source_id) q.set('source_id', params.source_id)
  if (params?.limit != null) q.set('limit', String(params.limit))
  if (params?.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return http<PaginatedArticles>(`/articles${qs ? `?${qs}` : ''}`)
}

export function prepareArticle(req: PrepareArticleReq): Promise<GenerateResp> {
  return http<GenerateResp>('/articles/prepare', { method: 'POST', body: JSON.stringify(req) })
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
  renderMode: RenderMode = 'static',
  platform: string = 'tiktok',
  animationPrompt?: string | null,
): Promise<GenerateResp> {
  return http<GenerateResp>('/generate-video', {
    method: 'POST',
    body: JSON.stringify({
      article_id: articleId,
      burn_subtitles: burnSubtitles,
      render_mode: renderMode,
      platform,
      animation_prompt: animationPrompt ?? null,
    }),
  })
}

export function resolveSceneVideoUrl(sceneVideoId: string): string {
  return `${API_BASE}/scene-videos/${sceneVideoId}`
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

export function resolveThumbnailUrl(articleId: string): string {
  return `${API_BASE}/thumbnail/${articleId}`
}

export function startGenerateThumbnail(
  articleId: string,
  prompt?: string | null,
): Promise<{ task_id: string; status: string }> {
  return http(`/articles/${encodeURIComponent(articleId)}/thumbnail`, {
    method: 'POST',
    body: JSON.stringify({ prompt: prompt?.trim() || null }),
  })
}

export async function uploadThumbnail(
  articleId: string,
  file: File,
): Promise<{ article_id: string; thumbnail_url: string }> {
  const headers: Record<string, string> = {}
  if (_jwtToken) headers['Authorization'] = `Bearer ${_jwtToken}`
  else if (_apiKey) headers['X-API-Key'] = _apiKey

  const form = new FormData()
  form.append('file', file)

  const res = await fetch(
    `${API_BASE}/articles/${encodeURIComponent(articleId)}/thumbnail/upload`,
    { method: 'POST', credentials: 'include', headers, body: form },
  )
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return res.json()
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

export function pinArticle(
  articleId: string,
  pinned: boolean,
): Promise<{ article_id: string; is_pinned: boolean }> {
  return http(`/articles/${encodeURIComponent(articleId)}/pin`, {
    method: 'PATCH',
    body: JSON.stringify({ pinned }),
  })
}

export function reorderStoryboard(
  articleId: string,
  sceneOrder: number[],
): Promise<{ article_id: string; scene_count: number }> {
  return http(`/articles/${encodeURIComponent(articleId)}/storyboard`, {
    method: 'PATCH',
    body: JSON.stringify({ scene_order: sceneOrder }),
  })
}

export async function uploadSceneImage(
  articleId: string,
  sceneNumber: number,
  file: File,
): Promise<{ id: string; scene_number: number; status: string; url: string }> {
  // FormData upload — cannot use http() helper (no Content-Type header; browser sets multipart boundary)
  const headers: Record<string, string> = {}
  if (_jwtToken) headers['Authorization'] = `Bearer ${_jwtToken}`
  else if (_apiKey) headers['X-API-Key'] = _apiKey

  const form = new FormData()
  form.append('file', file)

  const res = await fetch(
    `${API_BASE}/articles/${encodeURIComponent(articleId)}/scenes/${sceneNumber}/image`,
    { method: 'POST', credentials: 'include', headers, body: form },
  )
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return res.json()
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

export function getArticleCosts(articleId: string): Promise<CostSummary> {
  return http<CostSummary>(`/articles/${encodeURIComponent(articleId)}/costs`)
}

export function deleteArticle(articleId: string): Promise<{ deleted: string }> {
  return http(`/articles/${encodeURIComponent(articleId)}`, { method: 'DELETE' })
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
