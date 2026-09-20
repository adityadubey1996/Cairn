import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Clock3, Pause, Play, RefreshCw, RotateCcw, Settings2, Square, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { Button } from '@/components/ui/button'
import { ConnectorIcon } from '@/components/ConnectorIcon'
import { SkeletonList } from '@/components/SkeletonList'
import { Progress } from '@/components/ui/progress'

const ACTIVE = new Set(['queued', 'running', 'cancelling'])
const fieldClass = 'w-full rounded-md border border-border bg-background px-2.5 py-2 text-[13px] outline-none focus:border-primary'
const dateLabel = (value) => value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Not scheduled'
const defaultTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'

function ConnectionAutomation({ connection, onRun }) {
  const [policy, setPolicy] = useState(null)
  const [draft, setDraft] = useState(null)
  const [schedule, setSchedule] = useState('manual')
  const [editing, setEditing] = useState(false)
  const editingRef = useRef(false)
  editingRef.current = editing
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)
  const [reload, setReload] = useState(0)

  const adopt = (next, preserveDraft = false) => {
    setPolicy(next)
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
      const next = await api.saveConnectionPolicy(connection.id, {
        sync_enabled: schedule !== 'manual',
        schedule_minutes: Number(draft.schedule_minutes || 60),
        cron_expression: schedule === 'cron' ? draft.cron_expression?.trim() || ''
          : schedule === 'manual' ? policy.cron_expression || '' : '',
        timezone: draft.timezone,
        auto_absorb: !!draft.auto_absorb,
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
            <span className={policy.auto_absorb ? 'text-success' : 'text-muted-foreground'}>
              {policy.auto_absorb ? 'New and changed files absorb automatically' : 'Review files before absorption'}
            </span>
          </div>}
          {policy?.sync_enabled && hasSchedule && <p className="mt-1 text-[11px] text-muted-foreground">Next run: {dateLabel(policy.next_run_at)}</p>}
          {(policy?.last_run_at || connection.lastSyncAt) && <p className="mt-1 text-[11px] text-muted-foreground">Last run: {ago(policy?.last_run_at || connection.lastSyncAt)}</p>}
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
            <span className="text-[11px]">Minute, hour, day, month, weekday. Example: weekdays at 09:00.</span>
          </label>}
          {schedule !== 'manual' && <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Timezone
            <input required className={fieldClass} list="pipeline-timezones" value={draft.timezone}
              onChange={(e) => setDraft((d) => ({ ...d, timezone: e.target.value }))} />
          </label>}
        </div>
        <label className="mt-4 flex items-start gap-2.5 text-[13px]">
          <input type="checkbox" checked={!!draft.auto_absorb} className="mt-1 size-4 accent-[var(--primary)]"
            onChange={(e) => setDraft((d) => ({ ...d, auto_absorb: e.target.checked }))} />
          <span>Automatically absorb new and changed files
            <span className="mt-1 block max-w-2xl text-xs leading-relaxed text-muted-foreground">After a successful sync, the worker updates your wiki using the configured model. Files marked manual or excluded keep their own policy. Turn this off to review and queue files yourself.</span>
          </span>
        </label>
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
    <pre className="max-h-64 overflow-auto rounded-md border border-border bg-card p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">{lines.length ? lines.map((line) => line.line).join('\n') : running ? 'Waiting for the worker to report progress…' : 'No log output for this run.'}</pre>
  </div>
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
          <h2 className="text-[15px] font-semibold">Connector automation</h2>
          <span className="text-xs text-muted-foreground">Schedules run while the Cairn server is running.</span>
        </div>
        {!connections.length && <div className="py-8">
          <p className="text-sm">Connect your first source to create a schedule.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => onNavigate?.('connect')}>Choose a connector</Button>
        </div>}
        {connections.map((c) => <ConnectionAutomation key={c.id} connection={c} onRun={onRun} />)}
      </section>
      <section>
        <div className="flex items-center gap-3 border-b border-border pb-3">
          <h2 className="text-[15px] font-semibold">Run history</h2>
          <span className="text-xs text-muted-foreground">Live progress and logs</span>
          {status?.queue?.failed > 0 && <Button variant="outline" size="xs" className="ml-auto" disabled={busy} onClick={retry}>
            <RotateCcw size={12} aria-hidden />Requeue {status.queue.failed} failed
          </Button>}
        </div>
        {!runs.length && <div className="flex items-start gap-3 py-7 text-muted-foreground">
          <Clock3 size={18} aria-hidden /><p className="text-[13px]">No runs yet. Run a connector to see extraction, absorption, and errors here.</p>
        </div>}
        {runs.map((run) => {
          const units = run.units || {}
          const isOpen = open === run.id
          const name = connections.find((c) => c.id === run.connection_id)?.name ?? run.connector_id
          return <div key={run.id} className="border-b border-border/70">
            <button className="flex w-full flex-wrap items-center gap-2 py-3 text-left" type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : run.id)}>
              {isOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
              <span className="text-[13px] font-medium">{name || 'Wiki absorption'}</span>
              <span className={cn('rounded-md px-1.5 py-px text-[11px]', run.status === 'ok' ? 'bg-success/10 text-success' : ACTIVE.has(run.status) ? 'bg-primary/10 text-primary' : 'bg-warning/10 text-warning')}>
                {runLabel[run.status] || run.status}
              </span>
              <span className="text-xs text-muted-foreground">{run.phase || ''}{units.done ? ` · ${units.done} absorbed` : ''}{units.failed ? ` · ${units.failed} failed` : ''}{units.pending ? ` · ${units.pending} pending` : ''}</span>
              <span className="ml-auto text-[11px] text-muted-foreground">{ago(run.started_at)}</span>
            </button>
            {isOpen && <RunDetail id={run.id} onFinish={refresh} onRun={onRun} />}
          </div>
        })}
      </section>
    </>}
  </div>
}
