import { cn } from '@/lib/utils'

// Real row height and layout, not a spinner — the list should not jump when
// the data lands. Widths vary so it reads as content, not as a progress bar.
const WIDTHS = ['76%', '62%', '70%', '55%', '68%', '58%']

export function SkeletonList({ rows = 4, icon = true, avatar = false, className }) {
  return (
    <div className={cn('flex flex-col gap-3', className)} aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-2.5 py-1">
          {avatar && <div className="size-7 shrink-0 rounded-full bg-accent" />}
          {icon && !avatar && <div className="size-4.5 shrink-0 rounded-[5px] bg-accent" />}
          <div className="min-w-0 flex-1">
            <div className="h-2.5 rounded bg-accent" style={{ width: WIDTHS[i % WIDTHS.length] }} />
            <div className="mt-1.5 h-[7px] rounded bg-accent/60" style={{ width: `calc(${WIDTHS[i % WIDTHS.length]} - 24%)` }} />
          </div>
          <div className="h-2 w-10 shrink-0 rounded bg-accent" />
        </div>
      ))}
    </div>
  )
}
