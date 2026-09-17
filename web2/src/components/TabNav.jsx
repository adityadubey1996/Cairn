import { cn } from '@/lib/utils'

// Two levels of one pattern. TabNav is the sidebar's four primary tabs;
// SubTabNav is Connect's secondary strip and must never carry the same
// visual weight — smaller, no icons, no fill.
export function TabNav({ tabs, value, onChange, collapsed = false }) {
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Sections">
      {tabs.map(({ id, label, icon: Icon }) => {
        const active = value === id
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange?.(id)}
            aria-current={active ? 'page' : undefined}
            title={collapsed ? label : undefined}
            className={cn(
              'flex items-center gap-2.5 rounded-md text-left text-sm font-medium transition-colors',
              collapsed ? 'justify-center px-2 py-2.5' : 'px-2.5 py-2',
              active ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
            )}
          >
            <Icon size={16} className={cn('shrink-0', active && 'text-primary')} aria-hidden />
            {!collapsed && label}
          </button>
        )
      })}
    </nav>
  )
}

export function SubTabNav({ tabs, value, onChange, className }) {
  return (
    <nav className={cn('flex gap-1 overflow-x-auto', className)} aria-label="Views">
      {tabs.map(({ id, label }) => {
        const active = value === id
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange?.(id)}
            aria-current={active ? 'true' : undefined}
            className={cn(
              'shrink-0 rounded-lg border px-3 py-1 text-[12.5px] transition-colors',
              active ? 'border-border bg-card text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        )
      })}
    </nav>
  )
}
