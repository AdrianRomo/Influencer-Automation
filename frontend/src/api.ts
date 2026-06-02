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

// ── Catalog-to-ad (B2B) API ──────────────────────────────────────────────────

import type {
  Workspace, Brand, Product, Catalog, IngestReport, Campaign, AdConcept,
} from './types'

// Auth header from current module state (mirrors http<T>); used by raw fetch
// paths (multipart upload, blob download) that can't go through http<T>.
function _authHeaders(): Record<string, string> {
  if (_jwtToken) return { Authorization: `Bearer ${_jwtToken}` }
  if (_apiKey) return { 'X-API-Key': _apiKey }
  return {}
}

export function listWorkspaces(): Promise<{ workspaces: Workspace[] }> {
  return http('/workspaces')
}
export function createWorkspace(name: string): Promise<Workspace> {
  return http('/workspaces', { method: 'POST', body: JSON.stringify({ name }) })
}

export function listBrands(workspaceId: string): Promise<{ brands: Brand[] }> {
  return http(`/workspaces/${workspaceId}/brands`)
}
export function createBrand(workspaceId: string, body: Partial<Brand> & { name: string }): Promise<Brand> {
  return http(`/workspaces/${workspaceId}/brands`, { method: 'POST', body: JSON.stringify(body) })
}
export function updateBrand(brandId: string, body: Partial<Brand>): Promise<Brand> {
  return http(`/brands/${brandId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function listCatalogs(workspaceId: string): Promise<{ catalogs: Catalog[] }> {
  return http(`/workspaces/${workspaceId}/catalogs`)
}

export async function uploadCsvCatalog(
  workspaceId: string, file: File, brandId?: string,
): Promise<IngestReport> {
  const form = new FormData()
  form.append('file', file)
  if (brandId) form.append('brand_id', brandId)
  // No Content-Type header — the browser sets the multipart boundary.
  const res = await fetch(`${API_BASE}/workspaces/${workspaceId}/catalogs/csv`, {
    method: 'POST', credentials: 'include', headers: _authHeaders(), body: form,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${text ? `: ${text}` : ''}`)
  }
  return res.json() as Promise<IngestReport>
}

export function ingestUrls(
  workspaceId: string, urls: string[], brandId?: string,
): Promise<IngestReport> {
  return http(`/workspaces/${workspaceId}/catalogs/url`, {
    method: 'POST', body: JSON.stringify({ urls, brand_id: brandId ?? null }),
  })
}

export function listProducts(workspaceId: string): Promise<{ products: Product[] }> {
  return http(`/workspaces/${workspaceId}/products`)
}
export function getProduct(productId: string): Promise<Product> {
  return http(`/products/${productId}`)
}
export function analyzeProduct(productId: string): Promise<GenerateResp> {
  return http(`/products/${productId}/analyze`, { method: 'POST' })
}
export function listProductConcepts(productId: string): Promise<{ concepts: AdConcept[] }> {
  return http(`/products/${productId}/concepts`)
}

export function listCampaigns(workspaceId: string): Promise<{ campaigns: Campaign[] }> {
  return http(`/workspaces/${workspaceId}/campaigns`)
}
export function createCampaign(
  workspaceId: string,
  body: { name: string; goal: string; platforms: string[]; product_ids: string[]; brand_id?: string | null },
): Promise<Campaign> {
  return http(`/workspaces/${workspaceId}/campaigns`, { method: 'POST', body: JSON.stringify(body) })
}
export function generateCampaign(
  campaignId: string, nVariants: number, productIds?: string[],
): Promise<{ campaign_id: string; tasks: { product_id: string; task_id: string }[]; count: number }> {
  return http(`/campaigns/${campaignId}/generate`, {
    method: 'POST',
    body: JSON.stringify({ n_variants: nVariants, product_ids: productIds ?? null }),
  })
}
export function listCampaignConcepts(campaignId: string): Promise<{ concepts: AdConcept[] }> {
  return http(`/campaigns/${campaignId}/concepts`)
}

// Download the creative-library export as a Blob (json | csv | zip).
export async function downloadCampaignExport(
  campaignId: string, format: 'json' | 'csv' | 'zip',
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/campaigns/${campaignId}/export?format=${format}`, {
    credentials: 'include', headers: _authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${text ? `: ${text}` : ''}`)
  }
  return res.blob()
}

export function getConcept(conceptId: string): Promise<AdConcept> {
  return http(`/concepts/${conceptId}`)
}
export function updateConcept(
  conceptId: string,
  body: Partial<Pick<AdConcept, 'angle' | 'hook' | 'headline' | 'cta' | 'on_screen_text' | 'script_json' | 'captions_json'>> & { recheck?: boolean },
): Promise<AdConcept> {
  return http(`/concepts/${conceptId}`, { method: 'PATCH', body: JSON.stringify(body) })
}
export function regenerateConcept(conceptId: string, angle?: string): Promise<GenerateResp> {
  return http(`/concepts/${conceptId}/regenerate`, {
    method: 'POST', body: JSON.stringify({ angle: angle ?? null }),
  })
}
export function deleteConcept(conceptId: string): Promise<{ deleted: string }> {
  return http(`/concepts/${conceptId}`, { method: 'DELETE' })
}

export function generateConceptVideo(
  conceptId: string, platform: string, burnSubtitles = true,
): Promise<GenerateResp> {
  return http(`/concepts/${conceptId}/video`, {
    method: 'POST', body: JSON.stringify({ platform, burn_subtitles: burnSubtitles }),
  })
}

export async function downloadConceptVideo(conceptId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/concepts/${conceptId}/video`, {
    credentials: 'include', headers: _authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${text ? `: ${text}` : ''}`)
  }
  return res.blob()
}

export function getWorkspaceCredits(workspaceId: string): Promise<{
  balance: number
  costs: Record<string, number>
  transactions: { id: string; amount: number; reason: string; balance_after: number; created_at?: string }[]
}> {
  return http(`/workspaces/${workspaceId}/credits`)
}

export function getCreditPacks(): Promise<{
  configured: boolean
  packs: { id: string; credits: number; label: string; price_configured: boolean }[]
}> {
  return http('/billing/packs')
}
export function createCheckout(workspaceId: string, pack: string): Promise<{ url: string; session_id: string }> {
  return http(`/workspaces/${workspaceId}/billing/checkout`, {
    method: 'POST', body: JSON.stringify({ pack }),
  })
}

export function updateProduct(
  productId: string,
  body: Partial<Pick<Product, 'title' | 'description' | 'price' | 'currency' | 'category' | 'primary_image_url'>>,
): Promise<Product> {
  return http(`/products/${productId}`, { method: 'PATCH', body: JSON.stringify(body) })
}
export function deleteProduct(productId: string): Promise<{ deleted: string }> {
  return http(`/products/${productId}`, { method: 'DELETE' })
}

export function getCatalog(catalogId: string): Promise<Catalog> {
  return http(`/catalogs/${catalogId}`)
}
export function updateCatalog(catalogId: string, name: string): Promise<Catalog> {
  return http(`/catalogs/${catalogId}`, { method: 'PATCH', body: JSON.stringify({ name }) })
}
export function deleteCatalog(catalogId: string): Promise<{ deleted: string; items_removed: number }> {
  return http(`/catalogs/${catalogId}`, { method: 'DELETE' })
}
