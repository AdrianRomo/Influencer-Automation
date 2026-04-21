// ── Auth types ─────────────────────────────────────────────────────────────

export type TokenResp = {
  access_token: string
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
}

export type GenerateReq = {
  source_id: string
  voice_id?: string | null
  target_seconds: number
  n_scenes: number
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
}

export type ContentPackage = {
  article_id: string
  title: string
  url: string
  source_id: string
  generated_at: string
  script?: ScriptAsset | null
  audio?: AudioAssetRef | null
  storyboard?: Storyboard | null
  captions?: CaptionEntry[] | null
  visual_prompts?: VisualPromptEntry[] | null
  images?: ImageAssetRef[] | null
  video?: VideoAssetRef | null
}
