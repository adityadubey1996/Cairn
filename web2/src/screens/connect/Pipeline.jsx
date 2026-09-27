import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Clock3, Pause, Play, RefreshCw, RotateCcw, Settings2, Square, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { localZone, nextRuns, runLabel, sameZone } from '@/lib/cron'
import { Button } from '@/components/ui/button'
import { ConnectorIcon } from '@/components/ConnectorIcon'
import { SkeletonList } from '@/components/SkeletonList'
import { Progress } from '@/components/ui/progress'

const ACTIVE = new Set(['queued', 'running', 'cancelling'])

// The absorption plan in one line, including its ceiling — a budget nobody can
// see from the row is a budget nobody remembers setting.
function absorbLabel(policy) {
  const trigger = policy.absorb_trigger ?? (policy.auto_absorb ? 'sync' : 'manual')
  const cap = policy.absorb_limit_units
    ? ` · up to ${policy.absorb_limit_units} units a run` : ''
  if (trigger === 'sync') return `Absorbs after every sync${cap}`
  if (trigger === 'schedule') return `Absorbs on ${policy.absorb_cron || 'a schedule'}${cap}`
  return 'Review files before absorption'
}
const fieldClass = 'w-full rounded-md border border-border bg-background px-2.5 py-2 text-sm outline-none focus:border-primary'
const dateLabel = (value) => value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Not scheduled'
const defaultTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'

// What the expression actually does, as times rather than prose. The server
// fires these through croniter and remains the authority: an expression this
// cannot read gets no preview instead of an error, so a schedule the server
// would accept is never blocked here.
function CronPreview({ expression, zone }) {
  const where = zone || localZone()
  const runs = useMemo(() => nextRuns(expression, 3, new Date(), where), [expression, where])
  const typed = (expression || '').trim()
  if (!typed) return null
  const elsewhere = !sameZone(where, localZone())

  return (
    <div className="mt-4 rounded-lg border border-border bg-background p-3">
      {runs === null ? (
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <TriangleAlert size={13} className="mt-px shrink-0" aria-hidden />
          No preview for this expression. Five fields, like <span className="font-mono">0 9 * * 1-5</span>.
          It is checked again when you save.
        </p>
      ) : runs.length === 0 ? (
        <p className="flex items-start gap-2 text-xs text-warning">
          <TriangleAlert size={13} className="mt-px shrink-0" aria-hidden />
          Nothing matches this in the next 90 days. A date like 31 February never comes round.
        </p>
      ) : (
        <>
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-[0.03em] text-muted-foreground">
            <Clock3 size={12} aria-hidden /> Next runs
          </div>
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            {runs.map((run) => (
              <li key={run.toISOString()} className="flex items-center gap-2 text-sm tabular-nums">
                <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                {runLabel(run)}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            {elsewhere
              ? <>Times in {where}, where this schedule runs — not your own clock.</>
              : <>Times shown in {where}.</>}
          </p>
        </>
      )}
    </div>
  )
}

const TRIGGERS = [
  { id: 'manual', label: 'Only when I ask',
    detail: 'Nothing is absorbed until you press Absorb.' },
  { id: 'sync', label: 'After every sync',
    detail: 'The queue drains as soon as new content lands.' },
  { id: 'schedule', label: 'On its own schedule',
    detail: 'Sync through the day, write the queue up at a quiet hour.' },
]

// Syncing is free; absorbing is the only step that spends the model. That is
// why this is its own section with its own schedule rather than a checkbox on
// the sync one — they answer different questions.
function AbsorptionPlan({ draft, onChange }) {
  const trigger = draft.absorb_trigger ?? (draft.auto_absorb ? 'sync' : 'manual')
  const number = (value) => (value ? String(value) : '')

  return (
    <fieldset className="mt-5 rounded-lg border border-border p-4">
      <legend className="px-1.5 text-xs font-medium uppercase tracking-[0.03em] text-muted-foreground">
        Absorption
      </legend>
      <p className="mb-3 max-w-2xl text-xs leading-relaxed text-muted-foreground">
        Absorbing is the only step that uses your model. Files marked manual or excluded
        keep their own policy whatever this says.
      </p>

      <div className="grid gap-2 sm:grid-cols-3">
        {TRIGGERS.map((t) => (
          <label
            key={t.id}
            className={cn('flex cursor-pointer items-start gap-2 rounded-lg border p-2.5',
              trigger === t.id ? 'border-primary/50 bg-primary/10' : 'border-border')}
          >
            <input
              type="radio" name="absorb-trigger" value={t.id} checked={trigger === t.id}
              className="mt-0.5 size-3.5 shrink-0 accent-[var(--primary)]"
              onChange={() => onChange({ absorb_trigger: t.id, auto_absorb: t.id === 'sync' })}
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium">{t.label}</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{t.detail}</span>
            </span>
          </label>
        ))}
      </div>

      {trigger === 'schedule' && (
        <div className="mt-3">
          <label className="flex flex-col gap-1.5 text-xs text-muted-foreground" htmlFor="absorb-cron">
            When to absorb
            <input
              id="absorb-cron" required placeholder="0 2 * * *"
              className={`${fieldClass} font-mono sm:max-w-xs`}
              value={draft.absorb_cron || ''}
              onChange={(e) => onChange({ absorb_cron: e.target.value })}
            />
          </label>
          <CronPreview expression={draft.absorb_cron} zone={draft.timezone} />
        </div>
      )}

      {trigger !== 'manual' && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
            Most units per run
            <input
              type="number" min="0" max="100000" className={fieldClass} placeholder="No limit"
              value={number(draft.absorb_limit_units)}
              onChange={(e) => onChange({ absorb_limit_units: Number(e.target.value) || 0 })}
            />
            <span>Unspent units stay queued for the next run.</span>
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
            Token ceiling per run
            <input
              type="number" min="0" step="100000" className={fieldClass} placeholder="Pipeline default"
              value={number(draft.absorb_max_tokens)}
              onChange={(e) => onChange({ absorb_max_tokens: Number(e.target.value) || 0 })}
            />
            {/* A unit limit cannot bound cost: one long document is a single
                unit and many model calls. This is the ceiling that can. */}
            <span>What actually bounds one very long document.</span>
          </label>
        </div>
      )}

      <label className="mt-4 flex flex-col gap-1.5 text-xs text-muted-foreground sm:max-w-xs">
        Ask before a run larger than
        <input
          type="number" min="0" max="1000000" className={fieldClass} placeholder="Never ask"
          value={number(draft.absorb_guardrail_units)}
          onChange={(e) => onChange({ absorb_guardrail_units: Number(e.target.value) || 0 })}
        />
        <span>Catches a scope widened by one tick, before the run starts rather than after.</span>
      </label>
    </fieldset>
  )
}

function ConnectionAutomation({ connection, onRun, onPolicy }) {
  const [policy, setPolicy] = useState(null)
  const [draft, setDraft] = useState(null)
  const [schedule, setSchedule] = useState('manual')
  const [editing, setEditing] = useState(false)
  const editingRef = useRef(false)
  editingRef.current = editing
  const [busy, setBusy] = useState(false)
  const [policies, setPolicies] = useState({})
  const notePolicy = useCallback((id, policy) => setPolicies((all) => ({ ...all, [id]: policy })), [])
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)
  const [reload, setReload] = useState(0)

  const adopt = (next, preserveDraft = false) => {
    setPolicy(next)
    onPolicy?.(connection.id, next)
    if (preserveDraft) return
    setDraft({ ...next, timezone: next.timezone || defaultTimezone })
    setSchedule(!next.sync_enabled ? 'manual' : next.cron_expression ? 'cron' : 'interval')
  }
  useEffect(() => {
    let alive = true
    api.connectionPolicy(connection.id).then((p) => { if (alive) adopt(p, editingRef.current) })
      .catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [connection.id, connection.lastSyncAt, reload])

  const mutate = async (operation) => {
    setBusy(true); setError(null); setSaved(false)
    try { await operation() }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  const save = (e) => {
    e.preventDefault()
    mutate(async () => {
      const trigger = draft.absorb_trigger ?? (draft.auto_absorb ? 'sync' : 'manual')
      const next = await api.saveConnectionPolicy(connection.id, {
        sync_enabled: schedule !== 'manual',
        schedule_minutes: Number(draft.schedule_minutes || 60),
        cron_expression: schedule === 'cron' ? draft.cron_expression?.trim() || ''
          : schedule === 'manual' ? policy.cron_expression || '' : '',
        timezone: draft.timezone,
        absorb_trigger: trigger,
        absorb_cron: trigger === 'schedule' ? draft.absorb_cron?.trim() || '' : '',
        absorb_limit_units: Number(draft.absorb_limit_units || 0),
        absorb_max_tokens: Number(draft.absorb_max_tokens || 0),
        absorb_guardrail_units: Number(draft.absorb_guardrail_units || 0),
      })
      adopt(next); setSaved(true); setEditing(false)
    })
  }
  const runNow = () => mutate(async () => {
    const result = await api.syncConnection(connection.id)
    onRun(result.runId ?? result.run_id)
  })
  const toggle = () => mutate(async () => {
    adopt(await api.saveConnectionPolicy(connection.id, { sync_enabled: !policy.sync_enabled }))
  })

  const hasSchedule = !!(policy?.schedule_minutes || policy?.cron_expression)
  return (
    <section className="border-b border-border py-4 last:border-b-0">
      <div className="flex flex-wrap items-start gap-3">
        <ConnectorIcon kind={connection.kind} size={19} className="mt-1 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{connection.name}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{connection.detail || connection.kind}</p>
          {policy && <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className={policy.sync_enabled && hasSchedule ? 'text-primary' : 'text-muted-foreground'}>
              {hasSchedule ? policy.sync_enabled ? policy.cron_expression ? `${policy.cron_expression} · ${policy.timezone}` : `Every ${policy.schedule_minutes} minutes` : 'Schedule paused' : 'Manual sync'}
            </span>
            <span className={policy.absorb_trigger === 'manual' || !policy.absorb_trigger
              ? 'text-muted-foreground' : 'text-success'}>{absorbLabel(policy)}</span>
          </div>}
          {policy?.sync_enabled && hasSchedule && <p className="mt-1 text-xs text-muted-foreground">Next run: {dateLabel(policy.next_run_at)}</p>}
          {(policy?.last_run_at || connection.lastSyncAt) && <p className="mt-1 text-xs text-muted-foreground">Last run: {ago(policy?.last_run_at || connection.lastSyncAt)}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {hasSchedule && <Button variant="ghost" size="sm" disabled={busy} onClick={toggle}>
            {policy.sync_enabled ? <Pause size={12} aria-hidden /> : <Play size={12} aria-hidden />}
            {policy.sync_enabled ? 'Pause' : 'Resume'}
          </Button>}
          <Button variant="outline" size="sm" disabled={busy || !policy} aria-expanded={editing} onClick={() => { setEditing((v) => !v); setSaved(false) }}>
            <Settings2 size={12} aria-hidden /> Automation
          </Button>
          <Button variant="outline" size="sm" disabled={busy || ACTIVE.has(connection.status)} onClick={runNow}>
            <Play size={12} aria-hidden /> {busy ? 'Working…' : 'Run now'}
          </Button>
        </div>
      </div>
      {!policy && !error && <p className="mt-2 text-xs text-muted-foreground">Loading automation policy…</p>}
      {error && <div role="alert" className="mt-3 flex items-center gap-2 text-xs text-destructive">
        <TriangleAlert size={13} aria-hidden />{error}
        {!policy && <Button variant="ghost" size="xs" onClick={() => { setError(null); setReload((n) => n + 1) }}>Retry</Button>}
      </div>}
      {saved && <p role="status" className="mt-2 flex items-center gap-1.5 text-xs text-success"><Check size={12} aria-hidden /> Automation saved.</p>}
      {editing && draft && <form onSubmit={save} className="mt-4 rounded-lg border border-border bg-card p-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Sync schedule
            <select className={fieldClass} value={schedule} onChange={(e) => setSchedule(e.target.value)}>
              <option value="manual">Only when I run it</option>
              <option value="interval">Every interval</option>
              <option value="cron">Custom cron schedule</option>
            </select>
          </label>
          {schedule === 'interval' && <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Interval in minutes
            <input type="number" min="1" max="43200" required className={fieldClass} value={draft.schedule_minutes || 60}
              onChange={(e) => setDraft((d) => ({ ...d, schedule_minutes: e.target.value }))} />
          </label>}
          {schedule === 'cron' && <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Cron expression
            <input required className={`${fieldClass} font-mono`} placeholder="0 9 * * 1-5" value={draft.cron_expression || ''}
              onChange={(e) => setDraft((d) => ({ ...d, cron_expression: e.target.value }))} />
            <span className="text-xs">Minute, hour, day, month, weekday. Example: weekdays at 09:00.</span>
          </label>}
          {schedule !== 'manual' && <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Timezone
            <input required className={fieldClass} list="pipeline-timezones" value={draft.timezone}
              onChange={(e) => setDraft((d) => ({ ...d, timezone: e.target.value }))} />
          </label>}
        </div>
        {schedule === 'cron' && <CronPreview expression={draft.cron_expression} zone={draft.timezone} />}
        <AbsorptionPlan draft={draft} onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))} />
        <div className="mt-4 flex items-center gap-2">
          <Button type="submit" size="sm" disabled={busy}>{busy ? 'Saving…' : 'Save automation'}</Button>
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { adopt(policy); setEditing(false) }}>Cancel</Button>
        </div>
      </form>}
    </section>
  )
}

function RunDetail({ id, onFinish, onRun }) {
  const { run, lines, error, running, pct } = usePipelineRun(id, { onFinish })
  const [stopError, setStopError] = useState(null)
  const [stopping, setStopping] = useState(false)
  const stop = async () => {
    setStopping(true); setStopError(null)
    try { await api.stopPipelineRun(id) }
    catch (e) { setStopError(e.message) }
    finally { setStopping(false) }
  }
  const retry = async () => {
    setStopping(true); setStopError(null)
    try { const result = await api.retryPipelineRun(id); onRun(result.run_id ?? result.runId) }
    catch (e) { setStopError(e.message) }
    finally { setStopping(false) }
  }
  return <div className="pb-4 pl-6">
    {running && <div className="mb-3 flex items-center gap-3">
      <span className="text-xs text-muted-foreground">{run?.phase || 'Waiting to start'} · {run?.items_written ?? 0}/{run?.items_seen ?? 0} files</span>
      <Button size="xs" variant="outline" disabled={stopping || run?.status === 'cancelling'} onClick={stop}><Square size={11} aria-hidden />{stopping || run?.status === 'cancelling' ? 'Stopping…' : 'Stop run'}</Button>
    </div>}
    {running && !!run?.items_seen && <Progress className="mb-3 max-w-md" value={pct} label="Run progress" />}
    {(error || stopError || run?.error) && <p role="alert" className="mb-2 text-xs text-destructive">{stopError || error || run.error}</p>}
    {!running && run && run.status !== 'ok' && <Button className="mb-3" variant="outline" size="xs" disabled={stopping} onClick={retry}>
      <RotateCcw size={12} aria-hidden />{stopping ? 'Retrying…' : 'Retry run'}
    </Button>}
    <pre className="max-h-64 overflow-auto rounded-md border border-border bg-card p-3 font-mono text-xs leading-relaxed text-muted-foreground">{lines.length ? lines.map((line) => line.line).join('\n') : running ? 'Waiting for the worker to report progress…' : 'No log output for this run.'}</pre>
  </div>
}

// Budgets and schedules are set per connection because scope is. The cost of
// that is nobody sees the total: three connections each absorbing on every
// sync is three model bills, and no card says so. This line does.
function AutomationSummary({ connections, policies }) {
  const rows = connections.map((c) => policies[c.id]).filter(Boolean)
  if (rows.length < 2) return null

  const scheduled = rows.filter((p) => p.sync_enabled && (p.schedule_minutes || p.cron_expression))
  const absorbing = rows.filter((p) => (p.absorb_trigger ?? (p.auto_absorb ? 'sync' : 'manual')) !== 'manual')
  const next = scheduled.map((p) => p.next_run_at).filter(Boolean).sort()[0]

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-card px-3.5 py-2.5 text-xs">
      <span className="text-muted-foreground">
        <b className="text-foreground">{scheduled.length}</b> of {rows.length} connections sync on a schedule
      </span>
      <span className={cn(absorbing.length ? 'text-warning' : 'text-muted-foreground')}>
        <b className={absorbing.length ? undefined : 'text-foreground'}>{absorbing.length}</b>
        {absorbing.length === 1 ? ' absorbs' : ' absorb'} automatically
        {!!absorbing.length && ' — each one uses your model'}
      </span>
      {next && <span className="text-muted-foreground">Next: {dateLabel(next)}</span>}
    </div>
  )
}

export function Pipeline({ projectId, onNavigate }) {
  const [connections, setConnections] = useState([])
  const [runs, setRuns] = useState([])
  const [status, setStatus] = useState(null)
  const [open, setOpen] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [policies, setPolicies] = useState({})
  const notePolicy = useCallback((id, policy) => setPolicies((all) => ({ ...all, [id]: policy })), [])
  useEffect(() => {
    let alive = true
    Promise.all([api.listConnections(projectId), api.pipelineRuns({ projectId }), api.pipelineStatus(projectId)])
      .then(([cs, history, overview]) => {
        if (!alive) return
        setConnections(cs); setRuns(history.runs ?? []); setStatus(overview); setError(null)
      })
      .catch((e) => { if (alive) setError(e.message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId, reload])
  useEffect(() => {
    const timer = setInterval(() => setReload((n) => n + 1), 5000)
    return () => clearInterval(timer)
  }, [projectId])
  const refresh = () => setReload((n) => n + 1)
  const onRun = (id) => { if (id) setOpen(id); refresh() }
  const retry = async () => {
    setBusy(true)
    try { const result = await api.retryUnits(projectId); onRun(result.run_id ?? result.runId) }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  const active = runs.filter((r) => ACTIVE.has(r.status))
  const sourceCounts = status?.sources
  const runLabel = { queued: 'Queued', running: 'Running', cancelling: 'Stopping', ok: 'Complete', partial: 'Partial', failed: 'Failed', error: 'Failed', stopped: 'Stopped', interrupted: 'Interrupted' }

  return <div className="mx-auto w-full max-w-5xl space-y-7">
    <datalist id="pipeline-timezones"><option value="Asia/Kolkata" /><option value="UTC" /><option value="America/New_York" /><option value="Europe/London" /></datalist>
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className={cn('inline-flex items-center gap-1.5', status?.worker_running ? 'text-success' : 'text-warning')}>
        <span className="size-1.5 rounded-full bg-current" />{status ? status.worker_running ? 'Worker online' : 'Worker offline' : 'Checking worker…'}
      </span>
      <span>·</span><span>{active.length} active {active.length === 1 ? 'run' : 'runs'}</span>
      {sourceCounts && <><span>·</span><span>{sourceCounts.queued ?? 0} queued files</span><span>·</span><span>{sourceCounts.absorbed ?? 0} absorbed</span></>}
      {!!sourceCounts?.failed && <><span>·</span><span className="text-destructive">{sourceCounts.failed} failed files</span></>}
      <Button variant="ghost" size="xs" className="ml-auto" onClick={refresh}><RefreshCw size={12} aria-hidden />Refresh</Button>
    </div>
    {error && <p role="alert" className="flex items-start gap-2 text-sm text-destructive"><TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden />{error}</p>}
    {loading ? <SkeletonList rows={5} /> : <>
      <section>
        <div className="flex flex-wrap items-baseline gap-2 border-b border-border pb-3">
          <h2 className="text-base font-semibold">Connector automation</h2>
          <span className="text-xs text-muted-foreground">Schedules run while the Cairn server is running.</span>
        </div>
        {connections.length > 1 && <div className="pt-3">
          <AutomationSummary connections={connections} policies={policies} />
        </div>}
        {!connections.length && <div className="py-8">
          <p className="text-sm">Connect your first source to create a schedule.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => onNavigate?.('connect')}>Choose a connector</Button>
        </div>}
        {connections.map((c) => <ConnectionAutomation key={c.id} connection={c} onRun={onRun} onPolicy={notePolicy} />)}
      </section>
      <section>
        <div className="flex items-center gap-3 border-b border-border pb-3">
          <h2 className="text-base font-semibold">Run history</h2>
          <span className="text-xs text-muted-foreground">Live progress and logs</span>
          {status?.queue?.failed > 0 && <Button variant="outline" size="xs" className="ml-auto" disabled={busy} onClick={retry}>
            <RotateCcw size={12} aria-hidden />Requeue {status.queue.failed} failed
          </Button>}
        </div>
        {!runs.length && <div className="flex items-start gap-3 py-7 text-muted-foreground">
          <Clock3 size={18} aria-hidden /><p className="text-sm">No runs yet. Run a connector to see extraction, absorption, and errors here.</p>
        </div>}
        {runs.map((run) => {
          const units = run.units || {}
          const isOpen = open === run.id
          const name = connections.find((c) => c.id === run.connection_id)?.name ?? run.connector_id
          return <div key={run.id} className="border-b border-border/70">
            <button className="flex w-full flex-wrap items-center gap-2 py-3 text-left" type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : run.id)}>
              {isOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
              <span className="text-sm font-medium">{name || 'Wiki absorption'}</span>
              <span className={cn('rounded-md px-1.5 py-px text-xs', run.status === 'ok' ? 'bg-success/10 text-success' : ACTIVE.has(run.status) ? 'bg-primary/10 text-primary' : 'bg-warning/10 text-warning')}>
                {runLabel[run.status] || run.status}
              </span>
              <span className="text-xs text-muted-foreground">{run.phase || ''}{units.done ? ` · ${units.done} absorbed` : ''}{units.failed ? ` · ${units.failed} failed` : ''}{units.pending ? ` · ${units.pending} pending` : ''}</span>
              <span className="ml-auto text-xs text-muted-foreground">{ago(run.started_at)}</span>
            </button>
            {isOpen && <RunDetail id={run.id} onFinish={refresh} onRun={onRun} />}
          </div>
        })}
      </section>
    </>}
  </div>
}
