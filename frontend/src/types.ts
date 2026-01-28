export type Source = {
  id: string
  name: string
  rss_url: string
  language_hint?: string | null
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

export type JobState = 'PENDING' | 'RECEIVED' | 'STARTED' | 'RETRY' | 'FAILURE' | 'SUCCESS'

export type JobStatus = {
  task_id: string
  state: JobState | string
  result?: {
    audio_id?: string
    audio_url?: string
    article_id?: string
    [k: string]: unknown
  }
  error?: string
}
