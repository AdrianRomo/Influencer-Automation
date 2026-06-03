import { useState } from 'react'

export function Collapsible({ title, children, defaultOpen = false }: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="collapsible">
      <button className="collapsible-header" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span>{title}</span>
        <span className="chevron" aria-hidden>{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}
