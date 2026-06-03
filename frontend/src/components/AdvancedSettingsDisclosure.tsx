import { Collapsible } from './Collapsible'

/**
 * Progressive-disclosure wrapper for power-user knobs (scene count, duration,
 * render-mode override, custom animation prompt). Hidden by default so the
 * main flow stays jargon-free. Thin styling wrapper over <Collapsible>.
 */
export function AdvancedSettingsDisclosure({
  children,
  title = 'Advanced settings',
  defaultOpen = false,
}: {
  children: React.ReactNode
  title?: string
  defaultOpen?: boolean
}) {
  return (
    <div className="advanced-disclosure">
      <Collapsible title={title} defaultOpen={defaultOpen}>
        {children}
      </Collapsible>
    </div>
  )
}
