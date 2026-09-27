import { cn } from '@/lib/utils'

const ORDER = ['verified', 'code', 'doc', 'conflict', 'gap']

// The same five counts appear beneath a chat answer (TrustLine) and in a wiki
// article header. One component, so the vocabulary can never drift apart.
export function GradeBadgePills({ grades = {}, className }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {ORDER.map((label) => (
        <span
          key={label}
          title={`${grades[label] ?? 0} ${label}`}
          className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground"
        >
          {label} <b className="font-semibold text-foreground tabular-nums">{grades[label] ?? 0}</b>
        </span>
      ))}
    </div>
  )
}
