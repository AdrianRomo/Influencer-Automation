import type {
  SubtitleStyle, SubtitlePosition, SubtitleSize, SubtitlePreset,
} from '../types'

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  position: 'bottom',
  size: 'medium',
  preset: 'boxed',
}

const POSITIONS: { id: SubtitlePosition; label: string }[] = [
  { id: 'bottom', label: 'Bottom' },
  { id: 'center', label: 'Center' },
  { id: 'top', label: 'Top' },
]
const SIZES: { id: SubtitleSize; label: string }[] = [
  { id: 'small', label: 'Small' },
  { id: 'medium', label: 'Medium' },
  { id: 'large', label: 'Large' },
]
const PRESETS: { id: SubtitlePreset; label: string }[] = [
  { id: 'boxed', label: 'Boxed' },
  { id: 'outline', label: 'Outline' },
  { id: 'bold', label: 'Bold' },
]

function Segmented<T extends string>({
  label, value, options, onChange, disabled,
}: {
  label: string
  value: T
  options: { id: T; label: string }[]
  onChange: (v: T) => void
  disabled?: boolean
}) {
  return (
    <div className="sub-field">
      <span className="field-label">{label}</span>
      <div className="seg-pills" role="radiogroup" aria-label={label}>
        {options.map(o => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={o.id === value}
            className={`seg-pill${o.id === value ? ' seg-pill-active' : ''}`}
            onClick={() => onChange(o.id)}
            disabled={disabled}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** Subtitle style controls — position / size / look. */
export function SubtitleControls({
  value, onChange, enabled, onToggle, disabled,
}: {
  value: SubtitleStyle
  onChange: (s: SubtitleStyle) => void
  enabled: boolean
  onToggle: (on: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className="sub-controls">
      <label className="sub-toggle">
        <input
          type="checkbox"
          checked={enabled}
          onChange={e => onToggle(e.target.checked)}
          disabled={disabled}
        />
        <span>Burn subtitles into the video</span>
      </label>
      {enabled && (
        <div className="sub-fields">
          <Segmented label="Position" value={value.position} options={POSITIONS}
            onChange={v => onChange({ ...value, position: v })} disabled={disabled} />
          <Segmented label="Size" value={value.size} options={SIZES}
            onChange={v => onChange({ ...value, size: v })} disabled={disabled} />
          <Segmented label="Style" value={value.preset} options={PRESETS}
            onChange={v => onChange({ ...value, preset: v })} disabled={disabled} />
        </div>
      )}
    </div>
  )
}

/**
 * Live caption preview. Renders a sample subtitle with CSS that approximates
 * the FFmpeg force_style so creators can judge position/size/look before
 * committing to a multi-minute render. Can be dropped over any background
 * (e.g. inside the 9:16 phone frame) — pass `sample` to change the text.
 */
export function SubtitleStylePreview({
  style, sample = 'Your subtitles will look like this',
}: {
  style: SubtitleStyle
  sample?: string
}) {
  const posClass = `sub-prev-${style.position}`
  const sizeClass = `sub-prev-${style.size}`
  const presetClass = `sub-prev-preset-${style.preset}`
  return (
    <div className={`sub-prev ${posClass}`} aria-hidden>
      <span className={`sub-prev-text ${sizeClass} ${presetClass}`}>{sample}</span>
    </div>
  )
}
