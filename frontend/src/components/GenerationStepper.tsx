/**
 * Horizontal progress stepper showing where the user is in the create flow.
 * Purely presentational — the parent derives `current` from real package state
 * so the stepper never claims progress the data doesn't support.
 *
 * `current` is the active step index; everything before it is treated as done.
 */
export function GenerationStepper({
  steps,
  current,
  onStepClick,
}: {
  steps: string[]
  current: number
  /** Optional: allow clicking a completed step to jump back. */
  onStepClick?: (index: number) => void
}) {
  return (
    <nav className="stepper" aria-label="Progress">
      <ol className="stepper-list">
        {steps.map((label, i) => {
          const state = i < current ? 'done' : i === current ? 'active' : 'pending'
          const clickable = !!onStepClick && i < current
          return (
            <li key={label} className={`stepper-item stepper-${state}`}>
              <button
                type="button"
                className="stepper-node"
                onClick={clickable ? () => onStepClick!(i) : undefined}
                disabled={!clickable}
                aria-current={state === 'active' ? 'step' : undefined}
              >
                <span className="stepper-dot" aria-hidden>
                  {state === 'done' ? '✓' : i + 1}
                </span>
                <span className="stepper-label">{label}</span>
              </button>
              {i < steps.length - 1 && <span className="stepper-bar" aria-hidden />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
