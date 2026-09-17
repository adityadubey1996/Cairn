import { useMemo, useState } from 'react'
import { BookOpen, Search, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'

// Ported verbatim from web/src/Wiki.jsx. An article's *type* is its own
// vocabulary and is deliberately not the Green/Amber/Red status vocabulary —
// those two mean different things and must never be read as the same signal.
// One colour per type, inline by nature: these are data values, not tokens.
export const TYPE_COLOR = {
  system: '#7ee2a8', domain: '#f0c98c', flow: '#e8b46e',
  boundary: '#c9d16e', runtime: '#b5f08c', decision: '#8cf0c9',
  conflict: '#d98cf0', unknown: '#e07a7a', idea: '#8ce0f0',
  outcome: '#a0a8f0',
}

export const typeColor = (type) => TYPE_COLOR[type] ?? '#676d79' // Pencil

const matches = (node, needle) =>
  node.title.toLowerCase().includes(needle) || node.path.toLowerCase().includes(needle)

// Grouped by type, which is the only grouping the graph payload carries a field
// for — see the report note about recency.
function groupByType(nodes, query) {
  const needle = query.trim().toLowerCase()
  const byType = new Map()
  for (const node of nodes) {
    if (needle && !matches(node, needle)) continue
    let group = byType.get(node.type)
    if (!group) byType.set(node.type, (group = []))
    group.push(node)
  }
  return [...byType.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([type, group]) => [type, group.sort((a, b) => a.title.localeCompare(b.title))])
}

export function ArticleList({ nodes, state, selected, onSelect, onRetry }) {
  const [query, setQuery] = useState('')
  const groups = useMemo(() => groupByType(nodes, query), [nodes, query])

  if (state === 'loading') return <div className="px-1 pt-2"><SkeletonList rows={6} icon={false} /></div>

  if (state === 'error') {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Couldn’t load the wiki."
        detail="Your articles are still there — this is only the read that failed."
        action={
          <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={onRetry}>
            Try again
          </Button>
        }
      />
    )
  }

  if (!nodes.length) return <EmptyState icon={BookOpen} title="No articles yet." />

  return (
    <>
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 focus-within:border-primary">
        <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter articles…"
          aria-label="Filter articles"
          className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pt-1">
        {groups.length === 0 && (
          <p className="px-2 py-6 text-center text-[12.5px] text-muted-foreground">
            Nothing matched “{query.trim()}”.
          </p>
        )}

        {groups.map(([type, group]) => (
          <div key={type}>
            <div className="px-2 pt-2.5 pb-1 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
              {type}<span className="ml-1 opacity-70">{group.length}</span>
            </div>
            {group.map((node) => (
              <button
                key={node.id}
                type="button"
                onClick={() => onSelect?.(node.path)}
                aria-current={node.path === selected ? 'true' : undefined}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px]',
                  node.path === selected
                    ? 'bg-accent text-foreground'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                )}
              >
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: typeColor(node.type) }}
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate">{node.title}</span>
                {node.stale && <span className="shrink-0 text-[11px] text-warning">stale</span>}
              </button>
            ))}
          </div>
        ))}
      </div>
    </>
  )
}
