export type ToastKind = 'success' | 'error' | 'info'
export type Toast = { id: number; kind: ToastKind; msg: string }

// Module-scoped counter so callers across files don't need to share state.
let _toastSeq = 0
export function nextToastId(): number {
  return ++_toastSeq
}

export function ToastContainer({ toasts, onDismiss }: {
  toasts: Toast[]
  onDismiss: (id: number) => void
}) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => onDismiss(t.id)}>
          {t.msg}
        </div>
      ))}
    </div>
  )
}
