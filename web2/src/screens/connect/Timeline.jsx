import { useEffect, useMemo, useState } from 'react'
import { Activity, LayoutGrid, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { EventRow } from '@/components/EventRow'
import { SkeletonList } from '@/components/SkeletonList'

const FILTERS = [
  { id: null, label: 'All activity' },
  { id: 'sync', label: 'Syncs' },
  { id: 'absorb', label: 'Absorbs' },
  { id: 'error', label: 'Failures' },
]

// ponytail: the tail is capped at 500 lines. An absorb writes for hours, and
// an open <pre> that only ever grows is the one unbounded thing on this screen.
const MAX_LOG_LINES = 500

// Owns its own fetching rather than taking lines from Timeline: a live run's
// log has to poll, and polling from the parent would mean an effect whose own
// setState retriggers it. Unmounts when the row collapses, which is what frees
// the tail — there is no cache of closed logs.
function RunLog({ runId, live }) {
  const [state, setState] = useState('loading')
  const [lines, setLines] = useState([])

  useEffect(() => {
    let cancelled = false
    let timer
    let after = 0
    // Deps changed — which happens when the run stops being live — so re-read
    // from the top rather than appending a second copy onto what is already here.
    setLines([])
    const tick = () => {
      api.pipelineRunLog(runId, after)
        .then(({ lines: fresh, last }) => {
          if (cancelled) return
          after = last
          if (fresh.length) setLines((prev) => [...prev, ...fresh].slice(-MAX_LOG_LINES))
          setState('ready')
          if (live) timer = setTimeout(tick, 5000)
        })
        .catch(() => { if (!cancelled) setState('error') })
    }
    tick()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [runId, live])

  if (state === 'loading') return <p className="mt-2 text-xs text-muted-foreground">Loading log…</p>
  if (state === 'error') return <p className="mt-2 text-xs text-destructive">Couldn’t load this run’s log.</p>
  if (!lines.length) {
    return (
      <p className="mt-2 text-xs text-muted-foreground">
        No log for this run — it predates the pipeline runner, which is what writes one.
      </p>
    )
  }
  return (
    <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">
      {lines.map((l) => l.line).join('\n')}
    </pre>
  )
}

export function Timeline({ projectId, projectName, forced }) {
  const [events, setEvents] = useState([])
  const [kind, setKind] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [openId, setOpenId] = useState(null)

  // No setLoading(true) here: a refresh must not swap the list for the skeleton
  // and back, because that unmounts every row — including the open log, which
  // then restarts from line 0 on every tick.
  useEffect(() => {
    let cancelled = false
    api.listTimeline({ projectId })
      .then((rows) => !cancelled && setEvents(rows))
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, reload])

  // Switching projects is the one case that does want the skeleton back, rather
  // than the previous project's activity sitting there while this one loads.
  useEffect(() => { setEvents([]); setLoading(true) }, [projectId])

  // A run in flight moves through phases, so the feed describing it has to
  // move with it. Stops on its own once nothing is running.
  useEffect(() => {
    if (!events.some((e) => e.status === 'running')) return
    const t = setTimeout(() => setReload((n) => n + 1), 5000)
    return () => clearTimeout(t)
  }, [events])

  const retry = () => { setError(null); setReload((n) => n + 1) }

  const state = forced ?? (loading ? 'loading' : error ? 'error' : 'ready')
  const rows = useMemo(() => {
    const all = forced === 'empty' ? [] : events
    const shown = kind ? all.filter((e) => e.kind === kind) : all
    return [...shown].sort((a, b) => new Date(b.at) - new Date(a.at))
  }, [events, kind, forced])

  return (
    <div className="mx-auto w-full max-w-3xl">
      {/* Scope first, then the filter. The project is fixed by the sidebar —
          this row says which project's activity you are reading, and narrows
          it by event type. */}
      <div className="flex gap-1.5 overflow-x-auto pb-3" role="group" aria-label="Filter activity">
        <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11.5px] text-muted-foreground">
          <LayoutGrid size={11} aria-hidden />
          {projectName ?? '—'}
        </span>
        {FILTERS.map((f) => (
          <button
            key={f.label} type="button" onClick={() => setKind(f.id)} aria-pressed={kind === f.id}
            className={cn(
              'shrink-0 rounded-full border px-2.5 py-1 text-[11.5px] transition-colors',
              kind === f.id
                ? 'border-border bg-card text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {state === 'loading' && <SkeletonList rows={6} />}

      {state === 'error' && (
        <EmptyState
          icon={TriangleAlert}
          title="Couldn’t load the activity feed."
          detail="Your syncs and absorbs still ran — only this list failed."
          action={
            <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={retry}>
              Try again
            </Button>
          }
        />
      )}

      {state === 'ready' && !rows.length && (
        kind ? (
          <EmptyState
            icon={Activity}
            title="Nothing of that kind has happened in this project yet."
            action={<Button variant="outline" size="sm" onClick={() => setKind(null)}>Show all activity</Button>}
          />
        ) : (
          <EmptyState
            icon={Activity}
            title="Activity will show up here once you’ve synced or absorbed something."
          />
        )
      )}

      {state === 'ready' && rows.map((e) => (
        <EventRow
          key={e.id} event={e} variant="timeline"
          expanded={openId === e.id}
          onToggle={() => setOpenId((id) => (id === e.id ? null : e.id))}
        >
          {openId === e.id && (
            <RunLog key={e.id} runId={e.id} live={e.status === 'running'} />
          )}
        </EventRow>
      ))}
    </div>
  )
}
