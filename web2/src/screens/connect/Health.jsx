import { useEffect, useState } from 'react'
import { Globe, Plug, Plus, TriangleAlert, X } from 'lucide-react'
import * as api from '@/api'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { ConnectorPicker } from './ConnectorPicker'
import { Button } from '@/components/ui/button'
import { ConnectorCard } from '@/components/ConnectorCard'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'

const GRID = 'grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3'

function AddConnectorCard({ onAdd }) {
  return (
    <button
      type="button" onClick={onAdd}
      className="flex min-h-[128px] flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border bg-card/40 p-3.5 text-muted-foreground transition-colors hover:border-primary hover:text-primary"
    >
      <Plus size={18} aria-hidden />
      <span className="text-[12.5px] font-medium">Add connector</span>
    </button>
  )
}

// A card that follows its own sync. The hook cannot be called in a loop over
// connections (rules of hooks), so each card owns one — which also means two
// connections can sync at once without sharing a timer.
//
// `note` and `status` are existing ConnectorCard props, so live progress needed
// no change to the card itself.
function ConnectionCard({ connection, runId, onSync, onRemove, onFinish }) {
  const { run, running, pct } = usePipelineRun(runId, { onFinish })

  const progress = running && run
    ? `${run.phase ?? 'starting'}`
      + (run.items_seen ? ` · ${run.items_written}/${run.items_seen}` : '')
    : null

  return (
    <ConnectorCard
      connection={{
        ...connection,
        status: running ? 'running' : connection.status,
        note: progress ?? connection.note,
      }}
      progress={running && run?.items_seen ? pct : null}
      onSync={onSync}
      onRemove={onRemove}
    />
  )
}


// One card per CONNECTION, never per connector type — two GitHub repos are
// two rows in connector_connections and two cards here.
export function Health({ projectId, projectName, forced, onGoFiles }) {
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [picking, setPicking] = useState(false)
  // connection id -> the run it started. Cleared when that run finishes, which
  // is also when the connection list is refetched so status and item counts
  // come from the server rather than being guessed at here.
  const [runIds, setRunIds] = useState({})
  const [linksBusy, setLinksBusy] = useState(false)

  const fetchLinks = async () => {
    setLinksBusy(true)
    try {
      await api.fetchLinks(projectId)
    } catch (e) {
      setError(e)
    } finally {
      setLinksBusy(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.listConnections(projectId)
      .then((rows) => !cancelled && setConnections(rows))
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, reload])

  const retry = () => { setError(null); setReload((n) => n + 1) }

  const sync = async (connection, opts) => {
    setConnections((cs) => cs.map((c) => (c.id === connection.id ? { ...c, status: 'running' } : c)))
    try {
      // Returns immediately with a run id now; it used to block here for the
      // whole scrape and the S3 push, which is why the card could only ever
      // show a before and an after.
      const { runId } = await api.syncConnection(connection.id, opts)
      if (runId) setRunIds((m) => ({ ...m, [connection.id]: runId }))
      else setReload((n) => n + 1)   // github: still synchronous, no run id
    } catch (e) {
      setError(e)
      setConnections((cs) => cs.map((c) => (c.id === connection.id ? { ...c, status: 'error' } : c)))
    }
  }

  const syncFinished = (connectionId) => {
    setRunIds((m) => {
      const { [connectionId]: _gone, ...rest } = m
      return rest
    })
    setReload((n) => n + 1)
  }

  const remove = async (connection) => {
    await api.removeConnection(connection.id)
    setConnections((cs) => cs.filter((c) => c.id !== connection.id))
  }

  const state = forced ?? (loading ? 'loading' : error ? 'error' : 'ready')
  const rows = forced === 'empty' ? [] : connections

  if (state === 'loading') {
    return (
      <div className="mx-auto w-full max-w-5xl">
        <div className={GRID}>
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="rounded-lg border border-border bg-card p-3.5">
              <SkeletonList rows={3} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Couldn’t load this project’s connections."
        detail="Nothing was disconnected — the list just didn’t come back."
        action={
          <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={retry}>
            Try again
          </Button>
        }
      />
    )
  }

  // Opened by "+ Add connector" from either the empty state or the grid. The
  // same picker onboarding uses, so adding a second source is the same act as
  // adding the first.
  const picker = (
    <div className="mb-5 rounded-[10px] border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[13.5px] font-semibold">Add a connector</div>
          <div className="text-[12px] text-muted-foreground">
            It joins {projectName ?? 'this project'}. One sign-in per provider covers everything under it.
          </div>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setPicking(false)}>
          <X size={15} aria-hidden />
        </Button>
      </div>
      <ConnectorPicker
        className="mt-4"
        projects={projectId ? [{ id: projectId, name: projectName }] : []}
        projectId={projectId}
        connectedKinds={rows.map((c) => c.kind)}
        markConnected={false}
        onConnected={({ connectionId, runId } = {}) => {
          setPicking(false)
          if (connectionId && runId) setRunIds((m) => ({ ...m, [connectionId]: runId }))
          setReload((n) => n + 1)
        }}
      />
    </div>
  )

  // Shouldn't normally happen post-onboarding, but a project can be created
  // empty — so the affordance is the whole screen rather than a corner of it.
  if (!rows.length) {
    return picking ? <div className="mx-auto w-full max-w-5xl">{picker}</div> : (
      <EmptyState
        className="py-16"
        icon={Plug}
        title={`Nothing is connected to ${projectName ?? 'this project'} yet.`}
        detail="Connect a source here, or add files from your computer under Files."
        action={
          <Button variant="outline" size="sm" className="mt-1 hover:border-primary" onClick={() => setPicking(true)}>
            <Plus size={13} aria-hidden />Add connector
          </Button>
        }
      />
    )
  }

  return (
    <div className="mx-auto w-full max-w-5xl">
      {picking && picker}

      {/* Links used to be dragged behind every sync, which is what made an
          incremental sync take an hour. It is project-wide work, so it lives
          here rather than on any one connector's card. */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card/40 px-3 py-2">
        <Globe size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-[12.5px]">Links found in your content</span>
        <span className="text-[11.5px] text-muted-foreground">
          Fetched separately — it runs across the whole project and can take a while.
        </span>
        <Button size="xs" variant="outline" className="ml-auto"
                disabled={linksBusy} onClick={fetchLinks}>
          {linksBusy ? 'Fetching…' : 'Fetch links'}
        </Button>
      </div>

      <div className="flex items-center justify-between pb-2.5">
        <span className="text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
          {rows.length} connection{rows.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className={GRID}>
        {rows.map((c) => (
          <ConnectionCard
            key={c.id} connection={c} runId={runIds[c.id]}
            onSync={(_c, opts) => (c.kind === 'upload' ? onGoFiles?.() : sync(c, opts))}
            onRemove={() => remove(c)}
            onFinish={() => syncFinished(c.id)}
          />
        ))}
        <AddConnectorCard onAdd={() => setPicking(true)} />
      </div>
    </div>
  )
}
