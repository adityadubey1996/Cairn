import { useCallback, useEffect, useState } from 'react'
import { FolderGit2, RefreshCw, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { DOT, TEXT } from '@/lib/status'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState } from '@/components/EmptyState'

function ago(iso) {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const mb = (b) => (b ? `${(b / 1e6).toFixed(1)} MB` : '—')

// The clone→graph→ingest→absorb state machine, mapped onto the four tones the
// Vocabulary Rule in lib/status.js allows. V1 gave each of these nine states
// its own hex colour; here anything mid-flight is amber, done is green, failed
// is red, and not-started is neutral. `failed` is not terminal — the step that
// failed is re-runnable and the clone is kept.
const REPO_STATE = {
  added: ['idle', 'not cloned'],
  cloning: ['warn', 'cloning…'],
  cloned: ['warn', 'cloned'],
  graphed: ['warn', 'graphed'],
  ingested: ['warn', 'ready to absorb'],
  absorbing: ['warn', 'absorbing…'],
  ready: ['ok', 'ready'],
  failed: ['bad', 'failed'],
  evicted: ['idle', 'evicted'],
}

// Free steps only, in the order they must run. Absorb is deliberately absent —
// it spends money and never belongs on the one-button path.
const STEPS = [
  { id: 'clone', label: 'Clone', needsClone: false },
  { id: 'graph', label: 'Graph', needsClone: true },
  { id: 'ingest', label: 'Ingest', needsClone: true },
]

const Pill = ({ children, tone }) => (
  <span className={cn('rounded border border-border px-1.5 py-[1px] text-[11.5px]',
                      tone && TEXT[tone])}>
    {children}
  </span>
)

const Err = ({ children }) => (
  <p className="mt-2 flex items-start gap-1.5 text-[12.5px] text-destructive">
    <TriangleAlert size={14} className="mt-[2px] shrink-0" aria-hidden />
    <span>{children}</span>
  </p>
)

function AddRepo({ onAdded, projectId }) {
  const [url, setUrl] = useState('')
  const [probe, setProbe] = useState(null)
  const [branch, setBranch] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  // Visibility is confirmed before anything is cloned — the whole point of a
  // separate check call: no bytes hit the disk until the repo is known good.
  async function check() {
    setBusy(true); setError(null); setProbe(null)
    try {
      const p = await api.checkRepo(url)
      setProbe(p)
      setBranch(p.default_branch || '')
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  async function add() {
    setBusy(true); setError(null)
    try {
      await api.addRepo(url, branch, token, projectId)
      setUrl(''); setProbe(null); setBranch(''); setToken(''); onAdded()
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 p-3.5">
      <span className="text-[13px] font-medium">Track a repo</span>
      <div className="mt-2 flex gap-2">
        <input
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setProbe(null) }}
          onKeyDown={(e) => e.key === 'Enter' && url && !busy && check()}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2.5 py-1.5 font-mono text-[12.5px] outline-none focus:border-primary"
        />
        <Button size="sm" onClick={check} disabled={!url || busy}>Check</Button>
      </div>

      {/* A private repo needs a token before Check, not after: ls-remote is
          what probes it and that already needs auth. */}
      <label htmlFor="repo-token" className="mt-2.5 mb-1 block text-[12px] text-muted-foreground">
        Access token <span className="opacity-70">— only for a private repo</span>
      </label>
      <input
        id="repo-token" type="password" value={token}
        placeholder="github_pat_… or ghp_…"
        onChange={(e) => setToken(e.target.value)}
        className="w-full rounded-lg border border-border bg-background px-2.5 py-1.5 font-mono text-[12.5px] outline-none focus:border-primary"
      />
      <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
        Read-only contents access is all this needs — no write scopes. It is
        stored for this repo and never sent back to the browser.{' '}
        <a
          href="https://github.com/settings/personal-access-tokens/new"
          target="_blank" rel="noreferrer" className="text-primary hover:underline"
        >
          Create a fine-grained token
        </a>
        {' · '}
        <a
          href="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens/managing-your-personal-access-tokens"
          target="_blank" rel="noreferrer" className="text-primary hover:underline"
        >
          How tokens work
        </a>
      </p>

      {probe && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[12.5px]">
          <Pill tone="ok">{token ? 'reachable with your token' : 'public · reachable'}</Pill>
          <span className="font-mono">{probe.slug}</span>
          {probe.size_kb != null && <span className="text-muted-foreground">{(probe.size_kb / 1024).toFixed(1)} MB</span>}
          {probe.too_big && <Pill tone="bad">over the size cap</Pill>}
          <label className="flex items-center gap-1.5 text-muted-foreground">
            branch
            <select
              value={branch} onChange={(e) => setBranch(e.target.value)}
              className="rounded border border-border bg-background px-1.5 py-1 text-[12.5px] text-foreground outline-none"
            >
              {probe.branches.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <Button size="sm" onClick={add} disabled={busy || probe.too_big}>Track it</Button>
        </div>
      )}
      {error && <Err>{error}</Err>}
    </div>
  )
}

function Commits({ repoId, pinned, onIngestAt }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.repoCommits(repoId, 30)
      .then((r) => { if (!cancelled) setRows(r.commits) })
      .catch((e) => { if (!cancelled) setError(String(e.message || e)) })
    return () => { cancelled = true }
  }, [repoId])

  if (error) return <Err>{error}</Err>
  if (!rows) return <p className="mt-3 text-[12.5px] text-muted-foreground">loading commits…</p>

  return (
    <table className="mt-3 w-full text-[12.5px]">
      <tbody>
        {rows.map((c) => (
          <tr
            key={c.sha}
            className={cn('border-t border-border/60',
                          pinned && c.sha.startsWith(pinned) && 'text-warning')}
          >
            <td className="py-1 pr-2 font-mono">{c.short}</td>
            <td className="py-1 pr-2 text-muted-foreground">{c.date}</td>
            <td className="py-1 pr-2">{c.author}</td>
            <td className="py-1 pr-2">{c.subject}</td>
            <td className="py-1 text-right">
              <Button variant="ghost" size="sm" onClick={() => onIngestAt(c.sha)}>
                Ingest at this commit
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Queue({ repoId, onAbsorbKind }) {
  const [q, setQ] = useState(null)
  const [error, setError] = useState(null)
  const [kind, setKind] = useState('')

  useEffect(() => {
    let cancelled = false
    api.repoQueue(repoId)
      .then((r) => { if (!cancelled) setQ(r) })
      .catch((e) => { if (!cancelled) setError(String(e.message || e)) })
    return () => { cancelled = true }
  }, [repoId])

  if (error) return <Err>{error}</Err>
  if (!q) return <p className="mt-3 text-[12.5px] text-muted-foreground">loading queue…</p>

  const allRows = [...q.changed.map((r) => ({ ...r, why: 'changed' })),
                   ...q.new.map((r) => ({ ...r, why: 'new' }))]
  // The endpoint keeps absorbed units in the manifest for provenance. They
  // are history, not work left to buy, so the open queue must match the count
  // on the repository card.
  const rows = allRows.filter((r) => !r.absorbed)
  const alreadyAbsorbed = allRows.length - rows.length
  const byKind = rows.reduce((counts, row) => ({
    ...counts,
    [row.kind]: (counts[row.kind] || 0) + 1,
  }), {})
  const shown = kind ? rows.filter((r) => r.kind === kind) : rows

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Button variant={kind ? 'ghost' : 'outline'} size="sm" onClick={() => setKind('')}>
          all {rows.length}
        </Button>
        {Object.entries(byKind).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
          <Button
            key={k} size="sm" variant={kind === k ? 'outline' : 'ghost'}
            onClick={() => setKind(kind === k ? '' : k)}
          >{k} {n}</Button>
        ))}
        {kind && (
          <Button size="sm" onClick={() => onAbsorbKind(kind, shown.length)}>
            Absorb {kind}
          </Button>
        )}
        {alreadyAbsorbed > 0 && (
          <span className="ml-auto text-[11.5px] text-muted-foreground">
            {alreadyAbsorbed} already absorbed
          </span>
        )}
      </div>

      <table className="mt-2 w-full text-[12.5px]">
        <tbody>
          {shown.slice(0, 200).map((u) => (
            <tr key={u.id} className="border-t border-border/60">
              <td className="py-1 pr-2 font-mono">{u.kind}</td>
              <td className="py-1 pr-2 font-mono">{u.path}</td>
              <td className="py-1 pr-2 text-muted-foreground">{u.first}</td>
              <td className="py-1">
                {u.why === 'changed' && <Pill tone="warn">changed</Pill>}
                {u.status === 'superseded' && <Pill tone="warn">superseded</Pill>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {shown.length > 200 && (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          showing first 200 of {shown.length}
        </p>
      )}
    </div>
  )
}

function RepoCard({ r, onRun, onDelete, modelReady, onConfirm, onNavigate }) {
  const recentStatus = r.active_run?.status || r.last_run?.status
  const activeLabel = recentStatus === 'queued' ? 'Queued' : recentStatus === 'cancelling' ? 'Stopping' : null
  const running = r.active_run?.phase || r.active_run?.step || r.running_step
  const [tone, label] = running ? ['warn', activeLabel || 'Running']
    : recentStatus === 'stopped' || recentStatus === 'interrupted' ? ['idle', recentStatus]
    : recentStatus === 'error' || recentStatus === 'failed' || recentStatus === 'partial' ? ['bad', recentStatus === 'error' ? 'failed' : recentStatus]
    : REPO_STATE[r.state] || ['idle', r.state]
  const [open, setOpen] = useState('')   // '' | 'commits' | 'queue'
  const queued = (r.queue_new || 0) + (r.queue_changed || 0)
  const cloned = !!r.clone_path && r.state !== 'added'

  return (
    <div className="rounded-lg border border-border bg-card/40 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('size-2 shrink-0 rounded-full', DOT[tone])} aria-hidden />
        <span className="font-mono text-[13px] font-medium">{r.id}</span>
        <Pill tone={tone}>{label}</Pill>
        <Pill>{r.branch}</Pill>
        {r.pinned_sha && <Pill tone="warn">pinned @ {r.pinned_sha.slice(0, 8)}</Pill>}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted-foreground">
        <span><b className="text-foreground">{r.articles}</b> articles</span>
        <span><b className="text-foreground">{queued}</b> left to absorb</span>
        {r.absorbed > 0 && <span><b className="text-foreground">{r.absorbed}</b> absorbed</span>}
        <span className="font-mono">{r.head_sha ? r.head_sha.slice(0, 8) : '—'}</span>
        <span>{mb(Number(r.clone_bytes))}</span>
        <span>used {ago(r.last_used_at)}</span>
      </div>

      {/* During a run the only honest signal is the article count ticking up —
          absorb reports no percentage, so a progress bar here would be a lie. */}
      {running && (
        <p className="mt-2 flex items-center gap-2 text-[12.5px] text-warning">
          <Spinner className="size-3.5" />
          {activeLabel || 'Running'} · <b>{running}</b>
          {running === 'absorb' && <> · <b>{r.articles}</b> articles written so far</>}
        </p>
      )}
      {!running && r.last_run && (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          last: {r.last_run.step} · {r.last_run.status}
          {r.last_run.items_written != null &&
            ` · ${r.last_run.items_written}/${r.last_run.items_seen} units`}
          {r.last_run.finished_at && ` · ${ago(r.last_run.finished_at)}`}
        </p>
      )}
      {(r.last_run?.error || r.last_error) && <Err>{r.last_run?.error || r.last_error}</Err>}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => onRun(r.id, 'sync')} disabled={!!running}>
          Sync — clone, graph, ingest
        </Button>
        {STEPS.map((s) => (
          <Button
            key={s.id} variant="ghost" size="sm"
            onClick={() => onRun(r.id, s.id)}
            disabled={!!running || (s.needsClone && !cloned)}
          >{s.label}</Button>
        ))}

        {/* Absorb is the only step that spends money, so it is visually
            separate and always behind a confirmation. */}
        <Button
          variant="outline" size="sm"
          disabled={!!running || !cloned || !modelReady || queued === 0}
          title={!modelReady ? 'Configure an answer model in Settings' : queued === 0 ? 'Nothing queued' : ''}
          onClick={() => onConfirm({
            title: 'Absorb 5 units',
            detail: r.pinned_sha
              ? `This rewrites current articles using code from ${r.pinned_sha.slice(0, 8)}, which is older than the branch head.`
              : `Absorb up to 5 units for ${r.id} using your configured model.`,
            confirmLabel: 'Absorb',
            run: () => onRun(r.id, 'absorb', { kind: '', limit: 5 }),
          })}
        >Absorb ×5</Button>

        <Button
          variant={open === 'queue' ? 'outline' : 'ghost'} size="sm" disabled={!cloned}
          title="What an absorb would buy, unit by unit"
          onClick={() => setOpen(open === 'queue' ? '' : 'queue')}
        >Queue {queued}</Button>
        <Button
          variant={open === 'commits' ? 'outline' : 'ghost'} size="sm" disabled={!cloned}
          onClick={() => setOpen(open === 'commits' ? '' : 'commits')}
        >Commits</Button>
        <Button
          variant="ghost" size="sm" className="text-destructive hover:text-destructive"
          disabled={!!running}
          onClick={() => onConfirm({
            title: `Stop tracking ${r.id}?`,
            detail: 'The clone is deleted from disk. Every wiki article it produced is kept.',
            confirmWord: r.id.split('/').pop(),
            confirmLabel: 'Stop tracking',
            run: () => onDelete(r.id),
          })}
        >Remove</Button>
        {r.last_run && <Button variant="ghost" size="sm" onClick={() => onNavigate?.('pipeline')}>
          View pipeline
        </Button>}
      </div>

      {r.pinned_sha && (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Parked on an older commit. Ingesting with no commit returns to{' '}
          <span className="font-mono">{r.branch}</span>.{' '}
          <Button variant="ghost" size="sm" disabled={!!running} onClick={() => onRun(r.id, 'ingest')}>
            Back to head
          </Button>
        </p>
      )}

      {open === 'queue' && (
        <Queue
          repoId={r.id}
          onAbsorbKind={(kind, n) => onConfirm({
            title: `Absorb ${kind}`,
            detail: `Absorb up to ${Math.min(n, 30)} ${kind} units for ${r.id} using your configured model.`,
            confirmLabel: 'Absorb',
            run: () => onRun(r.id, 'absorb', { kind, limit: Math.min(n, 30) }),
          })}
        />
      )}

      {open === 'commits' && (
        <Commits
          repoId={r.id} pinned={r.pinned_sha}
          onIngestAt={(sha) => onConfirm({
            title: `Ingest at ${sha.slice(0, 8)}?`,
            detail: 'A preview: it checks out that commit and rebuilds the queue. No articles change until you absorb.',
            confirmLabel: 'Ingest',
            run: () => onRun(r.id, 'ingest', { commit: sha }),
          })}
        />
      )}
    </div>
  )
}

export function Repos({ projectId, onNavigate }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(null)
  const [modelReady, setModelReady] = useState(false)

  useEffect(() => {
    let alive = true
    api.getSettings().then((s) => { if (alive) setModelReady(!!s.effective?.model) }).catch(() => {})
    return () => { alive = false }
  }, [])

  const load = useCallback(async () => {
    try {
      setData(await api.listRepos(projectId))
      setError(null)
    } catch (e) { setError(String(e.message || e)) }
  }, [projectId])

  // Steps run in the background and return 202, so the screen polls. 4s is
  // fast enough to feel live and slow enough that a clone is not hammered.
  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [load])

  async function run(id, step, body) {
    try { await api.runRepoStep(id, step, body); load() }
    catch (e) { setError(String(e.message || e)) }
  }

  async function sweep() {
    try { await api.sweepRepos(projectId); load() }
    catch (e) { setError(String(e.message || e)) }
  }

  async function remove(id) {
    try { await api.removeRepo(id); load() }
    catch (e) { setError(String(e.message || e)) }
  }

  const anyBusy = data?.repos.some((r) => r.active_run || r.running_step)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-baseline gap-3 px-6 pt-4">
        <h1 className="text-[15px] font-semibold">Repos</h1>
        {data && (
          <Button
            variant="ghost" size="sm" onClick={sweep}
            disabled={anyBusy || data.repos.length === 0}
            title="Pull and re-ingest every tracked repo. Free — never absorbs."
          >
            <RefreshCw size={13} aria-hidden /> Sync all
          </Button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-3 pb-8">
        <p className="text-[12.5px] text-muted-foreground">
          Clone a public repository, or connect a private one with a token. Inspect its files and absorb them
          into wiki articles using your configured local or hosted model.
        </p>

        {/* A failed first load must not read as a spinner that never resolves —
            the error is the answer. */}
        {!data ? (
          error ? <Err>{error}</Err>
                : <p className="mt-4 text-[12.5px] text-muted-foreground">loading…</p>
        ) : (
          <div className="mt-3 flex flex-col gap-3">
            {!modelReady && (
              <p className="text-[12.5px] text-warning">
                Choose an answer model to enable absorption. <button className="text-primary hover:underline" onClick={() => onNavigate?.('settings')}>Open Settings</button>
              </p>
            )}
            {error && <Err>{error}</Err>}

            {data.repos.length < data.max_tracked && <AddRepo projectId={projectId} onAdded={load} />}

            {data.repos.map((r) => (
              <RepoCard
                key={r.id} r={r} onRun={run} onDelete={remove}
                modelReady={modelReady} onConfirm={setPending} onNavigate={onNavigate}
              />
            ))}

            {data.repos.length === 0 && (
              <EmptyState
                icon={FolderGit2}
                title="No repos tracked yet"
                detail="Add a public GitHub repo above and it gets cloned, graphed and ingested — all free."
              />
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!pending}
        title={pending?.title}
        detail={pending?.detail}
        confirmWord={pending?.confirmWord}
        confirmLabel={pending?.confirmLabel}
        onCancel={() => setPending(null)}
        onConfirm={() => { pending?.run(); setPending(null) }}
      />
    </div>
  )
}
