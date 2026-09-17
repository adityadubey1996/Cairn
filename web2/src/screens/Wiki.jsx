import { useCallback, useEffect, useMemo, useState } from 'react'
import { PanelLeft, X } from 'lucide-react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { cn } from '@/lib/utils'
import { useNarrow } from '@/Shell'
import { Button } from '@/components/ui/button'
import { SubTabNav } from '@/components/TabNav'
import { ArticleList } from './wiki/ArticleList'
import { GraphView } from './wiki/GraphView'
import { Reader } from './wiki/Reader'

const EMPTY_GRAPH = { nodes: [], edges: [] }

// List first, always. A graph of three articles tells you nothing a list does
// not, so Graph is an explicit opt-in rather than the landing view.
const VIEWS = [{ id: 'list', label: 'List' }, { id: 'graph', label: 'Graph' }]

export function Wiki({ projectId, projectName, target, onNavigate }) {
  const [view, setView] = useState('list')
  const [graph, setGraph] = useState(EMPTY_GRAPH)
  const [absorbedAt, setAbsorbedAt] = useState(() => new Map())
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [sheetOpen, setSheetOpen] = useState(false)
  const narrow = useNarrow()
  const forced = forcedState()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setSelected(null)
    api.wikiGraph({ projectId })
      .then((g) => !cancelled && setGraph(g ?? EMPTY_GRAPH))
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, reload])

  // When an article was absorbed is an article-level fact that lives only on
  // the timeline's absorb events, so the sources panel reads it from there.
  // Decorative: a failure leaves the line out rather than breaking the screen.
  useEffect(() => {
    let cancelled = false
    api.listTimeline({ projectId })
      .then((rows) => {
        if (cancelled) return
        const latest = new Map()
        for (const row of rows) {
          if (row.kind !== 'absorb') continue
          for (const link of row.links ?? []) {
            const known = latest.get(link.path)
            if (!known || row.at > known) latest.set(link.path, row.at)
          }
        }
        setAbsorbedAt(latest)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId])

  useEffect(() => {
    if (!sheetOpen) return
    const onKey = (e) => { if (e.key === 'Escape') setSheetOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sheetOpen])

  useEffect(() => { if (!narrow) setSheetOpen(false) }, [narrow])

  const data = useMemo(() => (forced === 'empty' ? EMPTY_GRAPH : graph), [forced, graph])
  const state = forced ?? (loading ? 'loading' : error ? 'error' : 'ready')
  const retry = () => setReload((n) => n + 1)

  // Arriving from another screen with an article in hand. Runs after the graph
  // effect that clears `selected`, so the target is not wiped by the load it
  // was requested alongside.
  useEffect(() => { if (target) setSelected(target) }, [target, graph])

  const pick = useCallback((path) => {
    setSelected(path)
    setSheetOpen(false)
  }, [])

  const pane = (
    <>
      <div className="flex items-center justify-between gap-2 pb-2">
        <span className="px-1 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
          Articles<span className="ml-1 opacity-70">{data.nodes.length}</span>
        </span>
        <SubTabNav tabs={VIEWS} value={view} onChange={setView} />
      </div>

      {/* Loading, empty and error are one list-shaped treatment in both views —
          there is no graph to draw in any of them. */}
      {view === 'graph' && state === 'ready' && data.nodes.length > 0 ? (
        <GraphView nodes={data.nodes} edges={data.edges} selected={selected} onSelect={pick} />
      ) : (
        <ArticleList nodes={data.nodes} state={state} selected={selected} onSelect={pick} onRetry={retry} />
      )}
    </>
  )

  return (
    <div className="flex min-h-0 flex-1">
      <aside
        className={cn(
          'hidden shrink-0 flex-col border-r border-border bg-background p-2.5 md:flex',
          view === 'graph' ? 'w-[420px]' : 'w-[288px]',
        )}
      >
        {pane}
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2.5 px-5 pt-4 md:hidden">
          <Button variant="outline" size="sm" onClick={() => setSheetOpen(true)}>
            <PanelLeft size={13} aria-hidden />
            Articles<span className="text-muted-foreground">{data.nodes.length}</span>
          </Button>
          <span className="truncate text-[12.5px] text-muted-foreground">{projectName ?? '—'}</span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="mx-auto w-full max-w-[860px]">
            <Reader
              path={selected}
              nodes={data.nodes}
              state={state}
              absorbedAt={absorbedAt}
              onNavigate={pick}
              onRetry={retry}
              onConnect={onNavigate && (() => onNavigate('connect'))}
              onDeleted={() => { setSelected(null); setReload((n) => n + 1) }}
            />
          </div>
        </div>
      </div>

      {narrow && sheetOpen && (
        <div className="fixed inset-0 z-40 flex" role="dialog" aria-modal="true" aria-label="Articles">
          <button
            type="button"
            aria-label="Close articles"
            className="absolute inset-0 bg-background/80"
            onClick={() => setSheetOpen(false)}
          />
          <aside className="relative flex h-full w-[86%] max-w-[340px] flex-col border-r border-border bg-card p-2.5">
            <div className="flex justify-end">
              <Button variant="ghost" size="icon-xs" aria-label="Close articles" onClick={() => setSheetOpen(false)}>
                <X size={14} aria-hidden />
              </Button>
            </div>
            {pane}
          </aside>
        </div>
      )}
    </div>
  )
}
