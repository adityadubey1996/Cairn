import { cn } from '@/lib/utils'
import { DOT, statusOf } from '@/lib/status'

export function StatusDot({ status, className, label }) {
  const { tone, label: fallback } = statusOf(status)
  return (
    <span
      className={cn('size-2 shrink-0 rounded-full', DOT[tone], className)}
      role="img"
      aria-label={label ?? fallback}
    />
  )
}
