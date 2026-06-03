import {
  COST_MODES, STYLE_PRESETS, FORMATS,
  type CostMode, type StylePresetId, type FormatId,
} from '../presets'

/**
 * Cost/quality mode — the primary, jargon-free quality control. Selecting a
 * mode applies a preset (render mode + scene count) in the parent.
 */
export function CostModeSelector({
  value, onChange, disabled,
}: {
  value: CostMode
  onChange: (m: CostMode) => void
  disabled?: boolean
}) {
  return (
    <div className="seg-group" role="radiogroup" aria-label="Cost mode">
      {COST_MODES.map(m => {
        const active = m.id === value
        return (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={active}
            className={`seg-card${active ? ' seg-card-active' : ''}`}
            onClick={() => onChange(m.id)}
            disabled={disabled}
          >
            <span className="seg-card-title">{m.label}</span>
            <span className="seg-card-blurb">{m.blurb}</span>
            <span className="seg-card-hint">{m.hint}</span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * Visual style preset — sets the animation prompt for animated renders.
 * "Custom" reveals the free-text box (rendered by the parent).
 */
export function StylePresetSelector({
  value, onChange, disabled,
}: {
  value: StylePresetId
  onChange: (id: StylePresetId) => void
  disabled?: boolean
}) {
  return (
    <div className="chip-group" role="radiogroup" aria-label="Visual style">
      {STYLE_PRESETS.map(p => {
        const active = p.id === value
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={active}
            className={`style-chip${active ? ' style-chip-active' : ''}`}
            onClick={() => onChange(p.id)}
            disabled={disabled}
          >
            {p.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * Format / aspect-ratio selector. Maps to the platform ids the backend renders.
 */
export function FormatSelector({
  value, onChange, disabled,
}: {
  value: FormatId
  onChange: (id: FormatId) => void
  disabled?: boolean
}) {
  return (
    <div className="seg-group" role="radiogroup" aria-label="Format">
      {FORMATS.map(f => {
        const active = f.id === value
        return (
          <button
            key={f.id}
            type="button"
            role="radio"
            aria-checked={active}
            className={`seg-card seg-card-format${active ? ' seg-card-active' : ''}`}
            onClick={() => onChange(f.id)}
            disabled={disabled}
          >
            <span className="seg-card-aspect" aria-hidden>
              <span className={`aspect-box aspect-${f.id}`} />
            </span>
            <span className="seg-card-title">{f.label}</span>
            <span className="seg-card-blurb">{f.sub}</span>
            <span className="seg-card-hint">{f.aspect}</span>
          </button>
        )
      })}
    </div>
  )
}
