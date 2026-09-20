import { Search, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

const MODES = [
  { id: 'ask', label: 'Ask', icon: Sparkles, hint: 'Your configured model reasons over the wiki' },
  { id: 'search', label: 'Search', icon: Search, hint: 'Find text in connected sources without a model' },
]

// Used exactly once in this product. It is the headline difference between
// V1 and V2, so it sits on the composer, never in a menu.
export function ModeToggle({ value = 'search', onChange, className }) {
  return (
    <div
      role="tablist"
      aria-label="Chat mode"
      className={cn('inline-flex rounded-full border border-border bg-card p-0.5', className)}
    >
      {MODES.map(({ id, label, icon: Icon, hint }) => {
        const active = value === id
        return (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={active}
            title={hint}
            onClick={() => onChange?.(id)}
            className={cn(
              'flex items-center gap-1.5 rounded-full px-4 py-1 text-[12.5px] transition-colors',
              active
                ? 'bg-primary font-semibold text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon size={13} aria-hidden />
            {label}
          </button>
        )
      })}
    </div>
  )
}
