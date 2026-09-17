import { useCallback, useEffect, useRef, useState } from 'react'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { ChevronDown, ChevronRight, Play, RotateCcw, Square, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { Button } from '@/components/ui/button'

// Only the two Google feeders have a phased runner behind them. The rest sync
// through /api/connections and show up under Health instead.
const CONNECTORS = [
  { id: 'gdrive', name: 'Google Drive' },
  { id: 'gchat', name: 'Google Chat' },
]

function duration(seconds) {
  if (!seconds) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${Math.round(seconds)}s`
}

const compact = (n) => (n ?? 0).toLocaleString()

function Phases({ phases }) {
  const keys = Object.keys(phases || {})
  if (!keys.length) return null
  return (
    <table className="mt-3 w-full text-[12.5px]">
      <thead className="text-muted-foreground">
        <tr className="text-left">
          <th className="pb-1 font-medium">phase</th>
          <th className="pb-1 font-medium">in</th>
          <th className="pb-1 font-medium">out</th>
          <th className="pb-1 font-medium">tokens</th>
          <th className="pb-1 font-medium">time</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const p = phases[k]
          const tok = (p.tokens_in || 0) + (p.tokens_out || 0)
          return (
            <tr key={k} className="border-t border-border/60">
              <td className="py-1">{k}</td>
              <td className="py-1">{p.seen ?? p.queued ?? '—'}</td>
              <td className="py-1">{p.written ?? p.files ?? '—'}</td>
              <td className="py-1">{tok ? compact(tok) : '—'}</td>
              <td className="py-1">{duration(p.seconds)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function Log({ lines }) {
  const box = useRef(null)
  useEffect(() => { box.current?.scrollTo(0, box.current.scrollHeight) }, [lines])
  if (!lines.length) return null
  return (
    <pre
      ref={box}
      className="mt-3 max-h-72 overflow-y-auto rounded-md bg-muted/40 p-2.5 font-mono text-[11.5px] leading-relaxed text-muted-foreground"
    >
      {lines.map((l) => l.line).join('\n')}
    </pre>
  )
}

function Runner({ connector, allowPaid }) {
  const [est, setEst] = useState(null)
  const [runId, setRunId] = useState(null)
  const [startError, setStartError] = useState(null)

  const loadEstimate = useCallback(() => {
    api.pipelineEstimate(connector.id).then(setEst).catch(() => setEst(null))
  }, [connector.id])

  useEffect(loadEstimate, [loadEstimate])

  // The estimate is stale the moment a run finishes — it counts what is still
  // queued — so refetch it then rather than leaving the old number up.
  const { run, lines, error: pollError, running, pct } =
    usePipelineRun(runId, { onFinish: loadEstimate })
  const error = startError || pollError

  async function start(skipAbsorb) {
    setStartError(null)
    try {
      const { run_id } = await api.startPipelineRun(connector.id, { skipAbsorb })
      setRunId(run_id)
    } catch (e) { setStartError(String(e.message || e)) }
  }

  async function stop() {
    try { await api.stopPipelineRun(runId) }
    catch (e) { setStartError(String(e.message || e)) }
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 p-3.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium">{connector.name}</span>
        {running && (
          <span className="text-[12px] text-muted-foreground">
            {run.phase ?? run.status} · {run.items_written}/{run.items_seen} ({pct}%)
          </span>
        )}
      </div>

      {!running && est && (
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          {compact(est.queued)} queued · ~{compact(est.tokens)} tokens · ~{duration(est.seconds)}
          {!est.measured && ' (estimate, no run measured yet)'}
        </p>
      )}

      {run && !running && run.status !== 'ok' && (
        <p className="mt-1 text-[12.5px] text-muted-foreground">run {run.status}</p>
      )}

      <Phases phases={run?.phases} />

      {(error || run?.error) && (
        <p className="mt-2 flex items-start gap-1.5 text-[12.5px] text-destructive">
          <TriangleAlert size={14} className="mt-[2px] shrink-0" aria-hidden />
          <span>{error || run.error}</span>
        </p>
      )}

      <Log lines={lines} />

      <div className="mt-3 flex gap-2">
        {running ? (
          <Button variant="outline" size="sm" onClick={stop}>
            <Square size={13} aria-hidden /> Stop
          </Button>
        ) : (
          <>
            <Button size="sm" onClick={() => start(true)}>
              <Play size={13} aria-hidden /> Scrape only · free
            </Button>
            {/* The only control here that spends money, so it is the only one
                still behind DEV_UI. Watching a free scrape is not a developer
                feature. */}
            {allowPaid && (
              <Button variant="outline" size="sm" onClick={() => start(false)}>
                Write up · paid
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// Free is the primary action: scrape and ingest cost nothing, and the write-up
// is the only phase that spends money — so the paid button is the secondary one
// here, the reverse of how a "refresh" usually reads.
// The four states a file can be in, in the order they happen. "In progress"
// is the one that did not exist before: a run that died used to leave no trace
// of the unit it was working on.
const STATES = [
  { key: 'done', label: 'Done', tone: 'text-success' },
  { key: 'running', label: 'In progress', tone: 'text-primary' },
  { key: 'pending', label: 'Not started', tone: 'text-muted-foreground' },
  { key: 'failed', label: 'Failed', tone: 'text-destructive' },
]

function UnitStates({ projectId }) {
  const [summary, setSummary] = useState(null)
  const [rows, setRows] = useState([])
  const [filter, setFilter] = useState(null)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    api.ingestUnits({ projectId, state: filter })
      .then((got) => {
        if (cancelled) return
        setSummary(got.summary)
        setRows(got.rows)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, filter, reload])

  // While something is running the counts move on their own, so the panel has
  // to as well — otherwise it reads as stuck.
  useEffect(() => {
    if (!summary?.running) return
    const id = setInterval(() => setReload((n) => n + 1), 4000)
    return () => clearInterval(id)
  }, [summary?.running])

  const retry = async () => {
    setBusy(true)
    try {
      await api.retryUnits(projectId)
      setReload((n) => n + 1)
    } finally {
      setBusy(false)
    }
  }

  if (!summary) return null

  return (
    <div className="rounded-[10px] border border-border bg-card p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">Files</span>
        {STATES.map(({ key, label, tone }) => (
          <button
            key={key} type="button" aria-pressed={filter === key}
            onClick={() => setFilter(filter === key ? null : key)}
            className={cn(
              'rounded-[9px] border px-2 py-px text-[11.5px] transition-colors',
              filter === key ? 'border-primary' : 'border-border hover:border-primary/60',
            )}
          >
            <span className={tone}>{(summary[key] ?? 0).toLocaleString()}</span> {label}
          </button>
        ))}
        {summary.failed > 0 && (
          <Button size="xs" variant="outline" className="ml-auto" disabled={busy}
                  onClick={retry}>
            <RotateCcw size={12} aria-hidden /> Retry {summary.failed} failed
          </Button>
        )}
      </div>

      {filter && (
        <div className="mt-2.5 max-h-[320px] overflow-y-auto">
          {rows.length === 0 && (
            <p className="py-2 text-[12.5px] text-muted-foreground">
              Nothing in this state.
            </p>
          )}
          {rows.map((r) => (
            <div key={r.unitId} className="flex items-start gap-2 border-b border-border/60 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px]">{r.name}</span>
                {r.error && <span className="block text-[11.5px] text-destructive">{r.error}</span>}
                {r.article && <span className="block text-[11.5px] text-muted-foreground">{r.article}</span>}
              </span>
              {r.attempts > 1 && (
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {r.attempts} attempts
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// Every run ever started, with what each FILE ended up as — not the run's own
// items_written, which is a live counter that stops wherever the process did.
// A run that was killed reads as "wrote 3" there, with no way to see the 9 that
// never started.
function RunHistory({ projectId }) {
  const [runs, setRuns] = useState(null)
  const [open, setOpen] = useState(null)
  const [log, setLog] = useState([])
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    api.pipelineRuns({ projectId })
      .then((got) => !cancelled && setRuns(got.runs))
      .catch(() => !cancelled && setRuns([]))
    return () => { cancelled = true }
  }, [projectId, reload])

  // A run still going keeps changing, so the list has to refresh itself or it
  // reads as frozen.
  const anyRunning = runs?.some((r) => r.status === 'running')
  useEffect(() => {
    if (!anyRunning) return
    const id = setInterval(() => setReload((n) => n + 1), 5000)
    return () => clearInterval(id)
  }, [anyRunning])

  useEffect(() => {
    if (!open) { setLog([]); return }
    let cancelled = false
    api.pipelineRunLog(open, 0)
      .then((got) => !cancelled && setLog(got.lines ?? []))
      .catch(() => {})
    return () => { cancelled = true }
  }, [open])

  if (!runs?.length) return null

  return (
    <div className="rounded-[10px] border border-border bg-card p-3.5">
      <div className="pb-1.5 text-[13px] font-semibold">Run history</div>
      {runs.map((r) => {
        const u = r.units || {}
        const isOpen = open === r.id
        return (
          <div key={r.id} className="border-t border-border/60 first:border-t-0">
            <button
              type="button" onClick={() => setOpen(isOpen ? null : r.id)}
              className="flex w-full items-center gap-2 py-2 text-left"
            >
              {isOpen
                ? <ChevronDown size={13} className="shrink-0 text-muted-foreground" aria-hidden />
                : <ChevronRight size={13} className="shrink-0 text-muted-foreground" aria-hidden />}
              <span className="text-[12.5px]">{r.connector_id}</span>
              <span className={cn(
                'rounded-[9px] border px-2 py-px text-[11px]',
                r.status === 'ok' ? 'border-success/40 text-success'
                  : r.status === 'running' ? 'border-primary/40 text-primary'
                    : 'border-destructive/40 text-destructive',
              )}>
                {r.status}
              </span>
              {/* The per-file breakdown, which is the question "how many went
                  down, how many are pending" actually being answered. */}
              <span className="flex flex-wrap gap-2 text-[11.5px] text-muted-foreground">
                {u.done ? <span className="text-success">{u.done} done</span> : null}
                {u.running ? <span className="text-primary">{u.running} running</span> : null}
                {u.pending ? <span>{u.pending} not started</span> : null}
                {u.failed ? <span className="text-destructive">{u.failed} failed</span> : null}
                {!u.done && !u.running && !u.pending && !u.failed
                  ? <span>{r.items_written ?? 0}/{r.items_seen ?? 0}</span> : null}
              </span>
              <span className="ml-auto shrink-0 text-[11.5px] text-muted-foreground">
                {ago(r.started_at)}
              </span>
            </button>
            {isOpen && (
              <div className="pb-2 pl-5">
                {r.error && (
                  <p className="mb-1.5 text-[11.5px] text-destructive">{r.error}</p>
                )}
                <pre className="max-h-[220px] overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {log.length ? log.map((l) => l.line).join('\n') : 'no log lines'}
                </pre>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function Pipeline({ projectId, allowPaid = false }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12.5px] text-muted-foreground">
        Scrape, ingest and write up Drive and Chat. Writing up costs money and can
        run for hours; a run survives a server restart. To write up a chosen set of
        files instead of a whole connector, tick them on the Sources tab.
      </p>
      <UnitStates projectId={projectId} />
      {CONNECTORS.map((c) => <Runner key={c.id} connector={c} allowPaid={allowPaid} />)}
      <RunHistory projectId={projectId} />
    </div>
  )
}
