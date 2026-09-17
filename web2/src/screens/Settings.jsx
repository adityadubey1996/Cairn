import { useEffect, useRef, useState } from 'react'
import { Check, Pencil, Trash2, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ProviderPresetPicker } from '@/components/ProviderPresetPicker'
import { SkeletonList } from '@/components/SkeletonList'

// One scrollable page, not a tabbed modal. No Appearance section: there is no
// light mode, and a toggle that does nothing is worse than an absent one.
function Section({ title, detail, children, danger = false }) {
  return (
    <section className={cn(
      'rounded-[10px] border p-4',
      danger ? 'border-destructive/40 bg-card' : 'border-border bg-card',
    )}>
      <h2 className={cn(
        'text-[11px] font-medium uppercase tracking-[0.03em]',
        danger ? 'text-destructive' : 'text-muted-foreground',
      )}>
        {title}
      </h2>
      {detail && <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">{detail}</p>}
      <div className="mt-3.5">{children}</div>
    </section>
  )
}

const EMBEDDING_PRESETS = [
  { id: 'ollama', label: 'Ollama (local)', hosted: false },
  { id: 'hosted', label: 'Hosted API', hosted: true },
]

// The answer provider and the embeddings provider are two independent choices,
// so this is its own picker — never a mode of the one above it.
function EmbeddingsPicker({ value, onChange }) {
  const preset = EMBEDDING_PRESETS.find((p) => p.id === (value.preset ?? 'ollama')) ?? EMBEDDING_PRESETS[0]
  return (
    <div>
      <label htmlFor="embeddings" className="mb-1.5 block text-[12px] text-muted-foreground">Embeddings run on</label>
      <select
        id="embeddings" value={preset.id}
        onChange={(e) => onChange({ ...value, preset: e.target.value })}
        className="w-full rounded-lg border border-border bg-card px-2.5 py-2 text-[13.5px] outline-none focus:border-primary"
      >
        {EMBEDDING_PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>

      {!preset.hosted && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[12px]">
          <span className={cn(
            'rounded-[9px] border px-2 py-px',
            value.reachable ? 'border-success/40 text-success' : 'border-destructive/40 text-destructive',
          )}>
            {value.reachable ? 'reachable' : 'not reachable'}
          </span>
          <span className="text-muted-foreground">No key needed — embeddings never leave this machine.</span>
        </div>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <input
          aria-label="Embedding model" placeholder="nomic-embed-text"
          value={value.model ?? ''} onChange={(e) => onChange({ ...value, model: e.target.value })}
          className="rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
        />
        {preset.hosted && (
          <input
            aria-label="Embeddings API key" type="password" placeholder="paste your key"
            value={value.key ?? ''} onChange={(e) => onChange({ ...value, key: e.target.value })}
            className="rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
          />
        )}
      </div>
    </div>
  )
}

function ProjectRow({ project, isCurrent, onRename, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(project.name)

  useEffect(() => { setName(project.name) }, [project.name])

  return (
    <div className="flex items-center gap-2.5 border-b border-border py-2.5 last:border-b-0">
      {editing ? (
        <input
          value={name} onChange={(e) => setName(e.target.value)} autoFocus aria-label="Project name"
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-primary"
        />
      ) : (
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[13.5px]">{project.name}</span>
            {isCurrent && (
              <span className="shrink-0 rounded-full border border-border px-1.5 text-[10.5px] uppercase tracking-[0.03em] text-muted-foreground">
                current
              </span>
            )}
          </div>
          <div className="truncate text-[11.5px] text-muted-foreground">
            {plural(project.connectionCount ?? 0, 'connection')} · {plural(project.sourceCount ?? 0, 'source')}
          </div>
        </div>
      )}

      {editing ? (
        <>
          <Button variant="outline" size="xs" onClick={() => { setEditing(false); setName(project.name) }}>Cancel</Button>
          <Button
            size="xs" disabled={!name.trim() || name.trim() === project.name}
            onClick={() => { setEditing(false); onRename(project.id, name.trim()) }}
          >
            Save
          </Button>
        </>
      ) : (
        <>
          <Button variant="outline" size="xs" onClick={() => setEditing(true)}>
            <Pencil size={11} aria-hidden />Rename
          </Button>
          <Button
            variant="outline" size="xs"
            className="border-destructive/50 text-destructive disabled:opacity-40"
            disabled={isCurrent}
            title={isCurrent ? 'Switch to another project before deleting this one.' : undefined}
            onClick={() => onDelete(project)}
          >
            <Trash2 size={11} aria-hidden />Delete
          </Button>
        </>
      )}
    </div>
  )
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

export function Settings({ projects = [], projectId, onProjectsChange }) {
  const [provider, setProvider] = useState({})
  const [embeddings, setEmbeddings] = useState({})
  const [effective, setEffective] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [saved, setSaved] = useState(null)          // { section, ok }
  const [rows, setRows] = useState(projects)
  const [confirm, setConfirm] = useState(null)
  const [projectNote, setProjectNote] = useState(null)
  const [dangerNote, setDangerNote] = useState(null)
  const aliveRef = useRef(true)

  const currentName = rows.find((p) => p.id === projectId)?.name ?? '—'

  // Reset on every (re)mount, not just declared true at useRef() time — under
  // StrictMode's dev-only mount->cleanup->remount cycle, a setup that only
  // returns a cleanup never flips this back to true, so it stays false for
  // the component's entire real lifetime and every guarded update below
  // silently no-ops. Confirmed live: renaming a project persisted to the
  // backend but the screen never updated until a reload, in dev mode only.
  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])
  useEffect(() => { setRows(projects) }, [projects])

  useEffect(() => {
    let cancelled = false
    api.getSettings()
      .then((s) => {
        if (cancelled) return
        setProvider(s.provider ?? {})
        setEmbeddings(s.embeddings ?? {})
        setEffective(s.effective ?? null)
      })
      .catch((e) => !cancelled && setLoadError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [])

  // getSettings() returns { provider, embeddings }; saveProvider takes the same
  // shape back, so both pickers persist through the one call the api exposes.
  const save = async (section) => {
    setSaved(null)
    try {
      const next = await api.saveProvider({ provider, embeddings })
      if (aliveRef.current) {
        setSaved({ section, ok: true })
        if (next?.effective) setEffective(next.effective)
      }
    } catch {
      if (aliveRef.current) setSaved({ section, ok: false })
    }
  }

  const test = async (cfg) => {
    setTestResult(null)
    try {
      const r = await api.testProvider(cfg)
      if (aliveRef.current) setTestResult(r)
    } catch {
      if (aliveRef.current) setTestResult({ ok: false, detail: 'Couldn’t reach the provider.' })
    }
  }

  const confirmDeleteProject = (project) => setConfirm({
    title: `Delete “${project.name}”?`,
    detail: `This removes the project, its ${project.connectionCount ?? 0} connector connection(s), its ${project.sourceCount ?? 0} scraped source(s) and every wiki article built from them. Other projects are untouched.`,
    confirmLabel: 'Delete project',
    run: async () => {
      setProjectNote(null)
      await api.deleteProject(project.id)
      if (aliveRef.current) {
        const next = rows.filter((p) => p.id !== project.id)
        setRows(next)
        onProjectsChange?.(next)
      }
    },
  })

  const confirmDisconnectAll = () => setConfirm({
    title: 'Disconnect all sources?',
    detail: `Every connector connection in “${currentName}” is removed and nothing new will sync. Sources already scraped and the wiki articles built from them stay where they are.`,
    confirmWord: 'disconnect',
    confirmLabel: 'Disconnect all',
    run: async () => {
      const connections = await api.listConnections(projectId)
      await Promise.all(connections.map((c) => api.removeConnection(c.id)))
      if (aliveRef.current) setDangerNote(`Disconnected ${connections.length} connection(s) in “${currentName}”.`)
    },
  })

  // There is no single reset endpoint, so this is composed from the delete the
  // api does expose — every project, and with it everything scoped to one.
  const confirmReset = () => setConfirm({
    title: 'Reset local data?',
    detail: `This deletes all ${rows.length} project(s) on this machine — every connection, every scraped source and every wiki article. Your provider and embeddings settings are kept.`,
    confirmWord: 'reset',
    confirmLabel: 'Reset everything',
    run: async () => {
      await Promise.all(rows.map((p) => api.deleteProject(p.id)))
      if (aliveRef.current) {
        setRows([])
        onProjectsChange?.([])
        setDangerNote('Local data reset. Create a project to start again.')
      }
    },
  })

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <header className="flex items-baseline gap-3 px-6 pt-4">
        <h1 className="text-[15px] font-semibold">Settings</h1>
        <span className="text-[12.5px] text-muted-foreground">{currentName}</span>
      </header>

      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4 px-6 py-5">
        <Section
          title="LLM provider"
          detail="Every answer and every absorbed article is paid for by the key you provide here. This product never has a key of its own."
        >
          {loading ? <SkeletonList rows={2} icon={false} /> : loadError ? (
            <div className="flex items-center gap-2 text-[13px] text-destructive">
              <TriangleAlert size={14} aria-hidden />Couldn’t load your settings.
            </div>
          ) : (
            <>
              <ProviderPresetPicker value={provider} onChange={setProvider} onTest={test}
                                    testResult={testResult} effective={effective} />
              <div className="mt-3.5 flex items-center gap-2">
                <Button size="sm" onClick={() => save('provider')}>Save provider</Button>
                {saved?.section === 'provider' && (saved.ok
                  ? <span className="flex items-center gap-1.5 text-[12px] text-success"><Check size={12} aria-hidden />Saved</span>
                  : <span className="text-[12px] text-destructive">Couldn’t save — nothing was changed.</span>
                )}
              </div>
            </>
          )}
        </Section>

        <Section
          title="Embeddings"
          detail="Retrieval embeddings are a separate choice from the provider that writes your answers — running them locally costs nothing and keeps your text on this machine."
        >
          {loading ? <SkeletonList rows={1} icon={false} /> : (
            <>
              <EmbeddingsPicker value={embeddings} onChange={setEmbeddings} />
              <div className="mt-3.5 flex items-center gap-2">
                <Button size="sm" onClick={() => save('embeddings')}>Save embeddings</Button>
                {saved?.section === 'embeddings' && (saved.ok
                  ? <span className="flex items-center gap-1.5 text-[12px] text-success"><Check size={12} aria-hidden />Saved</span>
                  : <span className="text-[12px] text-destructive">Couldn’t save — nothing was changed.</span>
                )}
              </div>
            </>
          )}
        </Section>

        <Section title="Projects" detail="Projects are hard-isolated: nothing is ever read across them.">
          {rows.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">No projects yet.</p>
          ) : rows.map((p) => (
            <ProjectRow
              key={p.id} project={p} isCurrent={p.id === projectId}
              onRename={async (id, name) => {
                try {
                  await api.renameProject(id, name)
                  if (aliveRef.current) {
                    const next = rows.map((x) => (x.id === id ? { ...x, name } : x))
                    setRows(next)
                    onProjectsChange?.(next)
                  }
                } catch {
                  if (aliveRef.current) setProjectNote(`Couldn’t rename “${p.name}” — the old name still stands.`)
                }
              }}
              onDelete={confirmDeleteProject}
            />
          ))}
          {projectNote && <p className="pt-2.5 text-[12px] text-destructive">{projectNote}</p>}
        </Section>

        <Section
          title="Danger zone" danger
          detail="Both of these are irreversible and ask you to type the word before they run."
        >
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px]">Disconnect all sources</div>
                <div className="text-[12px] text-muted-foreground">
                  Removes every connection in “{currentName}”. Nothing new syncs after this.
                </div>
              </div>
              <Button
                variant="outline" size="sm"
                className="border-destructive/60 text-destructive hover:bg-destructive/10"
                onClick={confirmDisconnectAll}
              >
                Disconnect all
              </Button>
            </div>

            <div className="h-px bg-destructive/20" />

            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px]">Reset local data</div>
                <div className="text-[12px] text-muted-foreground">
                  Deletes every project, connection, source and article stored on this machine.
                </div>
              </div>
              <Button
                variant="outline" size="sm"
                className="border-destructive/60 text-destructive hover:bg-destructive/10"
                onClick={confirmReset}
              >
                Reset local data
              </Button>
            </div>

            {dangerNote && <p className="text-[12px] text-muted-foreground">{dangerNote}</p>}
          </div>
        </Section>
      </div>

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title}
        detail={confirm?.detail}
        confirmWord={confirm?.confirmWord}
        confirmLabel={confirm?.confirmLabel}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          const run = confirm?.run
          setConfirm(null)
          try {
            await run?.()
          } catch {
            if (aliveRef.current) setDangerNote('That didn’t go through — nothing was changed.')
          }
        }}
      />
    </div>
  )
}
