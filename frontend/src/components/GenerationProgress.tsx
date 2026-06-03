import { classifyStage, videoStages, type StageId } from '../utils'

type StageState = 'done' | 'active' | 'pending' | 'error'

/**
 * Named, ordered stage list for a long-running render — replaces the bare
 * spinner + free-form status string.
 *
 * The backend only emits free-form PROGRESS messages, so we classify the
 * current message into one of our known stages (`classifyStage`) and treat
 * every earlier stage as done. `currentMessage` is shown verbatim under the
 * active stage so nothing is lost when classification can't place it.
 *
 * Announced via aria-live so screen readers hear stage changes.
 */
export function GenerationProgress({
  mode,
  currentMessage,
  active,
  errored = false,
}: {
  mode: 'static' | 'animated'
  /** Latest free-form progress message from the polling loop. */
  currentMessage?: string | null
  /** Whether a job is currently running. */
  active: boolean
  /** True when the job failed — marks the current stage as errored. */
  errored?: boolean
}) {
  const stages = videoStages(mode)
  const current: StageId | null = classifyStage(currentMessage) ?? (active ? stages[0].id : null)
  const currentIdx = current ? stages.findIndex(s => s.id === current) : -1

  function stateFor(idx: number): StageState {
    if (currentIdx < 0) return 'pending'
    if (idx < currentIdx) return 'done'
    if (idx === currentIdx) return errored ? 'error' : 'active'
    return 'pending'
  }

  return (
    <div className="gen-progress" role="status" aria-live="polite">
      <ol className="gen-stage-list">
        {stages.map((s, idx) => {
          const st = stateFor(idx)
          return (
            <li key={s.id} className={`gen-stage gen-stage-${st}`}>
              <span className="gen-stage-marker" aria-hidden>
                {st === 'done' ? '✓' : st === 'active' ? <span className="spinner spinner-sm" /> : st === 'error' ? '×' : ''}
              </span>
              <span className="gen-stage-label">{s.label}</span>
              {st === 'active' && currentMessage && classifyStage(currentMessage) && (
                <span className="gen-stage-msg">{currentMessage}</span>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
