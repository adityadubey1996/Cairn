import { cn } from '@/lib/utils'

// Determinate only. There is deliberately no indeterminate variant: every
// caller here knows its own total, and "something is happening but I cannot
// say how far along" is what Spinner already says.
export function Progress({ value = 0, label, className }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      role="progressbar" aria-label={label}
      aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
      className={cn('h-1 w-full overflow-hidden rounded-full bg-muted', className)}
    >
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-200"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
