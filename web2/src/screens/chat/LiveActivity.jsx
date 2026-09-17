import { useState } from 'react'
import { Check, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Spinner } from '@/components/ui/spinner'

// While generating this is an open panel of steps; once the answer lands it
// collapses to one line. Same pattern as V1, which users already read fluently.
export function LiveActivity({ stage, done }) {
  const [open, setOpen] = useState(false)
  const articles = stage?.articles ?? []

  if (done) {
    return (
      <div className="text-xs text-muted-foreground">
        <button
          type="button" onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 hover:text-foreground"
          aria-expanded={open}
        >
          <ChevronRight size={12} className={cn('shrink-0 transition-transform', open && 'rotate-90')} aria-hidden />
          searched the wiki · read {articles.length} article{articles.length === 1 ? '' : 's'}
        </button>
        {open && (
          <div className="mt-1.5 flex flex-col gap-1.5 pl-4">
            {stage?.query && <div>query: “{stage.query}”</div>}
            <div className="flex flex-wrap gap-1.5">
              {articles.map((a) => (
                <span key={a.path} className="rounded-full border border-border bg-card px-2 py-0.5">{a.title}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  const steps = []
  if (stage?.query) steps.push(['done', `Query: “${stage.query}”`])
  if (stage?.stage === 'searching') steps.push(['spin', 'Searching the knowledge base…'])
  if (stage?.hits) steps.push(['done', `Index search — ${stage.hits} hits`])
  if (articles.length) steps.push(['done', `Expanded wikilinks — reading ${articles.length} articles`])
  if (stage?.stage === 'generating') steps.push(['spin', 'Generating the answer…'])
  if (!steps.length) steps.push(['spin', 'Thinking…'])

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-card px-3 py-2.5 text-[13px]">
      {steps.map(([kind, label], i) => (
        <div key={i} className={cn('flex items-center gap-2.5', kind === 'spin' ? 'text-muted-foreground' : 'text-foreground')}>
          {kind === 'spin' ? <Spinner className="size-3.5" /> : <Check size={14} className="text-success" aria-hidden />}
          <span className={kind === 'spin' ? 'animate-pulse' : undefined}>{label}</span>
        </div>
      ))}
      {articles.length > 0 && (
        <div className="mt-0.5 flex flex-wrap gap-1.5 pl-6">
          {articles.map((a) => (
            <span key={a.path} className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground">
              {a.title}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
