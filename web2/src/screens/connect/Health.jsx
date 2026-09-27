import { useEffect, useState } from 'react'
import { ArrowRight, Globe, Plug, Plus, TriangleAlert, X } from 'lucide-react'
import * as api from '@/api'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { ConnectorPicker } from './ConnectorPicker'
import { ScopePicker } from './ScopePicker'
import { Button } from '@/components/ui/button'
import { ConnectorCard } from '@/components/ConnectorCard'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'

const GRID = 'grid grid-cols-1 gap-3 lg:grid-cols-2'

// A card that follows its own sync. The hook cannot be called in a loop over
// connections (rules of hooks), so each card owns one — which also means two
// connections can sync at once without sharing a timer.
//
// `note` and `status` are existing ConnectorCard props, so live progress needed
// no change to the card itself.
function ConnectionCard({ connection, runId, onSync, onRemove, onFinish, onOpen,
                         scope, onScope }) {
  const { run, running, pct } = usePipelineRun(runId, { onFinish })

  const progress = running && run
    ? `${run.status === 'queued' ? 'Waiting for worker' : run.status === 'cancelling' ? 'Stopping' : run.phase ?? 'Starting'}`
      + (run.items_seen ? ` · ${run.items_written}/${run.items_seen}` : '')
    : null

  return (
    <ConnectorCard
      connection={{
        ...connection,
        status: running ? run.status : connection.status,
        note: progress ?? connection.note,
      }}
      progress={running && run?.items_seen ? pct : null}
      onSync={onSync}
      onRemove={onRemove}
      onOpen={onOpen}
      scope={scope}
      onScope={onScope}
      openLabel={connection.kind === 'github' ? 'Open repository' : 'Open files'}
    />
  )
}


// One card per CONNECTION, never per connector type — two GitHub repos are
// two rows in connector_connections and two cards here.
export function Health({ projectId, projectName, forced, onGoFiles, onNavigate }) {
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [picking, setPicking] = useState(false)
  // The connection whose scope is being edited, if any.
  const [scoping, setScoping] = useState(null)
  // connection id -> the run it started. Cleared when that run finishes, which
  // is also when the connection list is refetched so status and item counts
  // come from the server rather than being guessed at here.
  const [runIds, setRunIds] = useState({})
  const [linksBusy, setLinksBusy] = useState(false)

  // One cheap read per connection so every card can say what it is allowed to
  // read. Decorative: a failure leaves the line off rather than breaking the
  // screen, and a connector with no scope of its own never shows one.
  const [scopes, setScopes] = useState({})
  useEffect(() => {
    let alive = true
    Promise.all(connections.map((c) =>
      api.connectionScope(c.id).then((s) => [c.id, s]).catch(() => null)))
      .then((pairs) => {
        if (alive) setScopes(Object.fromEntries(pairs.filter(Boolean)))
      })
    return () => { alive = false }
  }, [connections])

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
    Promise.all([
      api.listConnections(projectId),
      api.listRepos(projectId).catch(() => ({ repos: [] })),
    ])
      .then(([rows, repoData]) => {
        if (cancelled) return
        const repos = new Map((repoData.repos || []).map((repo) => [repo.id, repo]))
        setConnections(rows.map((connection) => {
          const repo = connection.kind === 'github' ? repos.get(connection.id) : null
          if (!repo) return connection
          return {
            ...connection,
            pendingCount: repo.queue_new ?? 0,
            absorbedCount: repo.absorbed ?? 0,
            articleCount: repo.articles ?? 0,
            itemCount: (repo.queue_new ?? 0) + (repo.absorbed ?? 0),
          }
        }))
      })
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

  // Two cards, because two is what a project usually has — six tall skeletons
  // resolving into two short cards read as a failed load.
  if (state === 'loading') {
    return (
      <div className={GRID}>
        {Array.from({ length: 2 }, (_, i) => (
          <div key={i} className="rounded-lg border border-border bg-card p-3.5">
            <SkeletonList rows={3} />
          </div>
        ))}
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
    <div className="mb-6">
      <div className="mb-4 flex items-start justify-between gap-3 border-b border-border pb-3">
        <div>
          <div className="text-base font-semibold">Add a connector</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            Add a source to {projectName ?? 'this project'}, then inspect its files and choose an absorption policy.
          </div>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setPicking(false)}>
          <X size={15} aria-hidden />
        </Button>
      </div>
      <ConnectorPicker
        projects={projectId ? [{ id: projectId, name: projectName }] : []}
        projectId={projectId}
        connectedKinds={rows.map((c) => c.kind)}
        markConnected={false}
        onNavigate={onNavigate}
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
    return picking ? <div>{picker}</div> : (
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
    <div>
      {picking && picker}

      <div className="mb-4 rounded-lg border border-border bg-card/40 px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
          <span>Sync</span><ArrowRight size={13} className="text-muted-foreground" aria-hidden />
          <span>Inspect</span><ArrowRight size={13} className="text-muted-foreground" aria-hidden />
          <span>Absorb</span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Sync imports source material. Inspect regular sources in Files and GitHub sources in Repos,
          then choose what becomes durable knowledge in the wiki.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5">
        <span className="text-xs font-medium uppercase tracking-[0.03em] text-muted-foreground">
          {rows.length} connection{rows.length === 1 ? '' : 's'}
        </span>
        <Button variant="outline" size="sm" onClick={() => setPicking(true)}>
          <Plus size={13} aria-hidden /> Add connector
        </Button>
      </div>
      {scoping && (
        <ScopePicker
          connection={scoping}
          onClose={() => setScoping(null)}
          onSaved={() => { setScoping(null); setReload((n) => n + 1) }}
        />
      )}

      <div className={GRID}>
        {rows.map((c) => (
          <ConnectionCard
            key={c.id} connection={c} runId={runIds[c.id] ?? c.runId}
            scope={scopes[c.id]}
            onScope={scopes[c.id]?.scopeable ? () => setScoping(c) : undefined}
            onSync={(_c, opts) => (c.kind === 'upload' ? onGoFiles?.() : sync(c, opts))}
            onRemove={() => remove(c)}
            onFinish={() => syncFinished(c.id)}
            onOpen={() => onNavigate?.(c.kind === 'github' ? 'repos' : 'files')}
          />
        ))}
      </div>

      {/* Link fetching is project-wide and can take much longer than a
          connector sync, so it stays explicit and separate from every card. */}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Globe size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-xs">Fetch links found inside synced content</span>
        <span className="text-xs text-muted-foreground">Runs across the whole project.</span>
        <Button size="xs" variant="ghost" className="ml-auto"
                disabled={linksBusy} onClick={fetchLinks}>
          {linksBusy ? 'Fetching…' : 'Fetch links'}
        </Button>
      </div>
    </div>
  )
}
