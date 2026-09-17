import { Search, Sparkles, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'
import { SourceResultRow } from '@/components/SourceResultRow'

export function SearchPane({ state, query, rows, starters, onStarter, onAskInstead, onRetry }) {
  if (state === 'loading') return <div className="pt-4"><SkeletonList rows={5} /></div>

  if (state === 'error') {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Search is unavailable right now."
        detail="Nothing was lost — your sources are still indexed."
        action={<Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={onRetry}>Try again</Button>}
      />
    )
  }

  // No query yet: say what Search is for, and that it is the free one.
  if (!query.trim()) {
    return (
      <EmptyState
        icon={Search}
        title="Search everything you’ve connected — no LLM cost."
        action={
          <div className="mt-1 flex flex-wrap justify-center gap-1.5">
            {starters.map((s) => (
              <button
                key={s} type="button" onClick={() => onStarter?.(s)}
                className="rounded-full border border-border bg-card px-3 py-1 text-xs text-foreground hover:border-primary"
              >
                {s}
              </button>
            ))}
          </div>
        }
      />
    )
  }

  if (!rows.length) {
    return (
      <EmptyState
        icon={Search}
        title="Nothing matched. Try a different term, or switch to Ask if you want the assistant to reason about it instead."
        action={
          <Button variant="outline" size="sm" onClick={onAskInstead}>
            <Sparkles size={12} aria-hidden />Run “{query}” in Ask
          </Button>
        }
      />
    )
  }

  return (
    <>
      <div className="flex items-center justify-between py-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
          {rows.length} match{rows.length === 1 ? '' : 'es'} · no LLM cost
        </span>
      </div>
      {rows.map((r) => <SourceResultRow key={r.id} row={r} />)}
    </>
  )
}
