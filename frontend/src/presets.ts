// Creator-facing presets that map friendly choices onto the real backend
// parameters (render mode, scene count, animation prompt, platform/aspect).
// Keeping the mapping here means the UI never exposes raw provider/FFmpeg
// details while still driving the existing /generate-video contract.

import type { RenderMode } from './types'

// ── Cost / quality mode ─────────────────────────────────────────────────────
export type CostMode = 'low' | 'balanced' | 'high'

export type CostModePreset = {
  id: CostMode
  label: string
  blurb: string
  hint: string
  renderMode: RenderMode
  nScenes: number
}

export const COST_MODES: CostModePreset[] = [
  { id: 'low',      label: 'Low Cost',     blurb: 'Fastest & cheapest',  hint: '~2 min · still images',      renderMode: 'static',   nScenes: 5 },
  { id: 'balanced', label: 'Balanced',     blurb: 'Recommended',         hint: '~3–5 min · still images',    renderMode: 'static',   nScenes: 8 },
  { id: 'high',     label: 'High Quality', blurb: 'AI-animated scenes',  hint: '~5–15 min · motion clips',   renderMode: 'animated', nScenes: 8 },
]

export function costModeFor(renderMode: RenderMode, nScenes: number): CostMode {
  if (renderMode === 'animated') return 'high'
  if (nScenes <= 6) return 'low'
  return 'balanced'
}

// ── Visual style preset ─────────────────────────────────────────────────────
// Feeds the animation prompt used by animated renders. `custom` reveals the
// free-text box. Prompts are deliberately motion-focused (they describe camera
// movement, not subject matter — subject comes from the storyboard).
export type StylePresetId = 'cinematic' | 'realistic' | 'animated' | 'documentary' | 'minimal' | 'custom'

export type StylePreset = {
  id: StylePresetId
  label: string
  prompt: string  // '' for custom
}

export const STYLE_PRESETS: StylePreset[] = [
  { id: 'cinematic',   label: 'Cinematic',   prompt: 'Smooth cinematic camera movement, slow dramatic zoom, shallow depth of field, subtle film grain.' },
  { id: 'realistic',   label: 'Realistic',   prompt: 'Natural lifelike motion, subtle realistic camera movement, grounded handheld feel.' },
  { id: 'animated',    label: 'Animated',    prompt: 'Playful animated motion, lively energetic movement, vibrant and dynamic.' },
  { id: 'documentary', label: 'Documentary', prompt: 'Steady documentary framing, gentle slow pans, calm and informative.' },
  { id: 'minimal',     label: 'Minimal',     prompt: 'Very subtle calm motion, minimal slow drift, clean and modern.' },
  { id: 'custom',      label: 'Custom',      prompt: '' },
]

export function stylePresetFor(animationPrompt: string): StylePresetId {
  const match = STYLE_PRESETS.find(p => p.id !== 'custom' && p.prompt === animationPrompt.trim())
  return match ? match.id : 'custom'
}

// ── Format / aspect ratio ───────────────────────────────────────────────────
// Maps an aspect choice onto the platform ids the backend understands. Vertical
// covers the short-form trio; the others are single-platform.
export type FormatId = 'vertical' | 'horizontal'

export type FormatOption = {
  id: FormatId
  label: string
  sub: string
  aspect: string
  platforms: string[]   // platform ids to select
}

// Only formats backed by a real platform profile (the backend has no 1:1
// profile, so we don't offer Square and silently produce the wrong aspect).
export const FORMATS: FormatOption[] = [
  { id: 'vertical',   label: 'Vertical',   sub: 'TikTok · Reels · Shorts', aspect: '9:16', platforms: ['tiktok'] },
  { id: 'horizontal', label: 'Horizontal', sub: 'YouTube',                 aspect: '16:9', platforms: ['youtube'] },
]

export function formatFor(platforms: string[]): FormatId {
  // Horizontal only if every selected platform is a 16:9 one.
  const horizontalIds = new Set(['youtube'])
  return platforms.length > 0 && platforms.every(p => horizontalIds.has(p)) ? 'horizontal' : 'vertical'
}
