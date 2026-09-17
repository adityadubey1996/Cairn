import { cn } from '@/lib/utils'
import { GradeBadgePills } from './GradeBadgePills'

// Read-only. The pills are inspectable on hover, never click-through.
export function TrustLine({ grades, articleCount = 0, projectName, heads, uncited, className }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5 border-t border-border pt-2', className)}>
      {uncited && (
        <span className="rounded-full bg-warning/15 px-2 py-0.5 text-[11px] text-warning">
          no wiki coverage — uncited answer
        </span>
      )}
      <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
        context <b className="font-semibold text-foreground tabular-nums">{articleCount}</b> articles
      </span>
      <GradeBadgePills grades={grades} />
      {projectName && (
        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
          {projectName}
        </span>
      )}
      {heads && <span className="font-mono text-[11px] text-muted-foreground">{heads}</span>}
    </div>
  )
}
