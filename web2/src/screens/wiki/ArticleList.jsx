import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, Search, TriangleAlert, X } from 'lucide-react'
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

// One row, one group heading. Fixed because the list is windowed: at 3,500
// articles React cannot keep every row mounted and still scroll at 60fps, and
// windowing needs to know a row's height without measuring it.
const ROW_H = 30
const HEAD_H = 26
const OVERSCAN = 12

const matches = (node, needle) =>
  node.title.toLowerCase().includes(needle) || node.path.toLowerCase().includes(needle)

// Grouped by type, which is the only grouping the graph payload carries a field
// for — see the report note about recency. Flattened into one array of rows so
// the windowing below can index straight into it: headings and articles are
// the same list, which is what makes a sticky heading possible.
function buildRows(nodes, query) {
  const needle = query.trim().toLowerCase()
  const byType = new Map()
  for (const node of nodes) {
    if (needle && !matches(node, needle)) continue
    let group = byType.get(node.type)
    if (!group) byType.set(node.type, (group = []))
    group.push(node)
  }
  const rows = []
  for (const [type, group] of [...byType.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    rows.push({ kind: 'head', id: `head:${type}`, type, count: group.length })
    for (const node of group.sort((a, b) => a.title.localeCompare(b.title))) {
      rows.push({ kind: 'item', id: node.id, node })
    }
  }
  return rows
}

export function ArticleList({ nodes, state, selected, onSelect, onRetry }) {
  const [query, setQuery] = useState('')
  // Typing into a filter over thousands of rows should not block the keystroke.
  const deferredQuery = useDeferredValue(query)
  const rows = useMemo(() => buildRows(nodes, deferredQuery), [nodes, deferredQuery])

  const scrollRef = useRef(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(600)

  // Offsets are cumulative because headings and rows are different heights.
  const { offsets, total } = useMemo(() => {
    const out = new Array(rows.length + 1)
    let y = 0
    for (let i = 0; i < rows.length; i++) {
      out[i] = y
      y += rows[i].kind === 'head' ? HEAD_H : ROW_H
    }
    out[rows.length] = y
    return { offsets: out, total: y }
  }, [rows])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setViewport(entry.contentRect.height))
    ro.observe(el)
    return () => ro.disconnect()
  }, [state])

  // A new filter should put you back at the top of its results.
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); setScrollTop(0) }, [deferredQuery])

  const findRow = (y) => {
    let lo = 0, hi = rows.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (offsets[mid] <= y) lo = mid; else hi = mid - 1
    }
    return lo
  }

  const first = rows.length ? Math.max(0, findRow(scrollTop) - OVERSCAN) : 0
  const last = rows.length ? Math.min(rows.length, findRow(scrollTop + viewport) + OVERSCAN + 1) : 0
  const visible = rows.slice(first, last)

  // The heading of whatever group the top of the viewport is inside, pinned
  // above the list so a scroll through 1,600 boundary articles still says
  // "boundary".
  const stuck = useMemo(() => {
    if (!rows.length) return null
    for (let i = Math.min(findRow(scrollTop), rows.length - 1); i >= 0; i--) {
      if (rows[i].kind === 'head') return rows[i]
    }
    return null
  }, [rows, scrollTop, offsets])

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

  const Heading = ({ row, className }) => (
    <div className={cn(
      'flex items-center gap-1.5 px-2 text-xs font-medium uppercase tracking-[0.04em] text-muted-foreground',
      className,
    )} style={{ height: HEAD_H }}>
      <span className="size-1.5 shrink-0 rounded-full" style={{ background: typeColor(row.type) }} aria-hidden />
      {row.type}
      <span className="opacity-70 tabular-nums">{row.count}</span>
    </div>
  )

  return (
    <>
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 focus-within:border-primary">
        <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Filter ${nodes.length.toLocaleString()} articles…`}
          aria-label="Filter articles"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
        {query && (
          <button
            type="button" aria-label="Clear filter" onClick={() => setQuery('')}
            className="shrink-0 rounded text-muted-foreground hover:text-foreground"
          >
            <X size={13} aria-hidden />
          </button>
        )}
      </div>

      {query.trim() && (
        <p className="px-1 pt-1.5 text-xs text-muted-foreground" role="status">
          {rows.filter((r) => r.kind === 'item').length.toLocaleString()} of {nodes.length.toLocaleString()} match “{query.trim()}”
        </p>
      )}

      <div className="relative min-h-0 flex-1 pt-1">
        {stuck && (
          <Heading row={stuck} className="pointer-events-none absolute left-0 right-0 top-1 z-10 border-b border-border bg-background" />
        )}
        {/* No top padding: the first heading sits at y=0 directly beneath the
            pinned copy, so the two never both read as rows. */}
        <div
          ref={scrollRef}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          className="h-full overflow-y-auto overflow-x-hidden"
        >
          {rows.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              Nothing matched “{query.trim()}”.
            </p>
          )}

          <div style={{ height: total, position: 'relative' }}>
            {visible.map((row, i) => {
              const top = offsets[first + i]
              if (row.kind === 'head') {
                return (
                  <div key={row.id} className="absolute left-0 right-0" style={{ top }}>
                    <Heading row={row} />
                  </div>
                )
              }
              const { node } = row
              const active = node.path === selected
              return (
                <div key={row.id} className="absolute left-0 right-0 px-0" style={{ top, height: ROW_H }}>
                  <button
                    type="button"
                    onClick={() => onSelect?.(node.path)}
                    aria-current={active ? 'true' : undefined}
                    title={node.title}
                    className={cn(
                      'flex h-full w-full items-center gap-2 rounded-md px-2.5 text-left text-sm',
                      active
                        ? 'bg-accent text-foreground'
                        : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground',
                    )}
                  >
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: typeColor(node.type) }}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1 truncate">{node.title}</span>
                    {node.stale && <span className="shrink-0 text-xs text-warning">stale</span>}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </>
  )
}
