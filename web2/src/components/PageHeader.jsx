import { cn } from '@/lib/utils'

// One header for every screen. The title is the only 20px text in the product,
// the subtitle says what the screen is for, and actions sit on the right and
// wrap beneath the title before they crowd it. `children` carries a screen's
// own strip — Connect's sub-tabs — so that strip stays part of the header
// rather than becoming a second one.
export function PageHeader({ title, subtitle, actions, children, className }) {
  return (
    <header className={cn('shrink-0 border-b border-border px-6 py-4', className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-[-0.02em]">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </header>
  )
}
