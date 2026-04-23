// ── Auth types ─────────────────────────────────────────────────────────────

export type TokenResp = {
  access_token: string
  refresh_token?: string
  token_type: string
  user_id: string
  email: string
}

export type UserResp = {
  id: string
  email: string
  created_at: string
  has_keys: boolean
}

export type UserKeysIn = {
  openai_key?: string | null
  elevenlabs_key?: string | null
  elevenlabs_voice_id?: string | null
  elevenlabs_model_id?: string | null
}

export type UserKeysOut = {
  has_openai_key: boolean
  has_elevenlabs_key: boolean
  elevenlabs_voice_id?: string | null
  elevenlabs_model_id?: string | null
}

export type Source = {
  id: string
  name: string
  rss_url: string
  language_hint?: string | null
}

export type ArticleSummary = {
  id: string
  title: string
  url: string
  source_id: string
  created_at: string
  has_audio: boolean
  has_video: boolean
  is_pinned?: boolean
  analysis_sentiment?: string | null
  analysis_impact?: number | null
}

export type PaginatedArticles = {
  items: ArticleSummary[]
  total: number
  has_more: boolean
}

export type RssCandidate = {
  title: string
  url: string
  summary?: string | null
  published_at?: string | null
  score: number
  source_id: string
  source_name: string
}

export type PlatformProfile = {
  id: string
  name: string
  width: number
  height: number
  aspect_ratio: string
  default_duration: number
  min_duration: number
  max_duration: number
  short_form: boolean
  description: string
}

export type LanguageOption = {
  code: string
  label: string
}

export type PlatformsResp = {
  platforms: PlatformProfile[]
  languages: LanguageOption[]
  defaults: {
    platform: string
    language: string
    animation_prompt: string
  }
}

export type PrepareArticleReq = {
  source_id: string
  article_url: string
  article_title?: string
  article_summary?: string | null
  article_published_at?: string | null
  n_scenes?: number
  target_seconds?: number
  language?: string
  selected_platforms?: string[]
  animation_prompt?: string | null
}

export type GenerateReq = {
  source_id: string
  voice_id?: string | null
  target_seconds: number
  n_scenes: number
  article_url?: string | null
  article_title?: string | null
  article_summary?: string | null
  article_published_at?: string | null
  article_id?: string | null
}

export type GenerateResp = {
  task_id: string
  status: string
}

export type JobState = 'PENDING' | 'RECEIVED' | 'STARTED' | 'RETRY' | 'FAILURE' | 'SUCCESS' | 'PROGRESS'

export type JobStatus = {
  task_id: string
  state: JobState | string
  meta?: { stage?: string; msg?: string; progress?: number }
  result?: {
    audio_id?: string
    audio_url?: string
    article_id?: string
    video_id?: string
    [k: string]: unknown
  }
  error?: string
}

// ── Content package types ──────────────────────────────────────────────────

export type StoryboardScene = {
  scene_number: number
  start_time_estimate: number
  duration_estimate: number
  narration: string
  visual_prompt: string
  on_screen_text: string
  transition: string
  asset_type: string
}

export type Storyboard = {
  scenes: StoryboardScene[]
  total_duration_estimate: number
}

export type CaptionEntry = {
  index: number
  start_time: number
  end_time: number
  text: string
}

export type VisualPromptEntry = {
  scene_number: number
  visual_prompt: string
  on_screen_text: string
  asset_type: string
  start_time_estimate: number
}

export type AudioAssetRef = {
  id: string
  download_url: string
  duration_seconds?: number | null
  format: string
  word_count?: number | null
  voice_id: string
  model_id: string
}

export type ScriptAsset = {
  text: string
  language: string
  word_count: number
  estimated_duration_seconds?: number | null
  model?: string | null
}

export type ImageAssetRef = {
  id: string
  scene_number: number
  visual_prompt: string
  status: string
}

export type VideoAssetRef = {
  id: string
  download_url: string
  duration_seconds?: number | null
  width: number
  height: number
  has_subtitles: boolean
  status: string
  error?: string | null
  render_mode: 'static' | 'animated'
  platform?: string | null
}

export type SceneVideoRef = {
  id: string
  scene_number: number
  provider: string
  status: 'pending' | 'processing' | 'ready' | 'failed' | 'fallback' | string
  duration_seconds?: number | null
  error?: string | null
}

export type RenderMode = 'static' | 'animated'

export type AnalysisResult = {
  sentiment: 'positive' | 'neutral' | 'cautionary' | 'urgent'
  impact_score: number
  medical_urgency: 'routine' | 'informational' | 'important' | 'critical'
  key_claims: string[]
  audience_relevance?: string | null
}

export type UsageEvent = {
  id: string
  provider: string
  operation: string
  model?: string | null
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  cached_input_tokens?: number | null
  character_count?: number | null
  image_count?: number | null
  image_size?: string | null
  image_quality?: string | null
  estimated_cost_usd?: number | null
  external_request_id?: string | null
  created_at?: string | null
}

export type CostSummary = {
  article_id: string
  total_estimated_usd: number
  by_provider: Record<string, number>
  by_stage: Record<string, number>
  total_tokens: number
  total_characters: number
  event_count: number
  events: UsageEvent[]
  pricing_note: string
}

export type SocialCaption = {
  platform: string
  caption: string
  hashtags: string[]
}

export type ContentPackage = {
  article_id: string
  title: string
  url: string
  source_id: string
  source_name?: string | null
  published_at?: string | null
  generated_at: string
  language?: string
  selected_platforms?: string[] | null
  animation_prompt?: string | null
  thumbnail_url?: string | null
  script?: ScriptAsset | null
  audio?: AudioAssetRef | null
  storyboard?: Storyboard | null
  captions?: CaptionEntry[] | null
  visual_prompts?: VisualPromptEntry[] | null
  images?: ImageAssetRef[] | null
  video?: VideoAssetRef | null
  videos?: VideoAssetRef[] | null
  scene_videos?: SceneVideoRef[] | null
  analysis?: AnalysisResult | null
  social_captions?: SocialCaption[] | null
  cost_summary?: CostSummary | null
}
