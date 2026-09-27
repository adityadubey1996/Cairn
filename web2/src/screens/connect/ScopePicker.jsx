import { useEffect, useMemo, useState } from 'react'
import { Check, RefreshCw, Search, TriangleAlert, X } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'

// One picker for every connector. The server returns the same shape whatever
// the provider is — groups of items, plus the filters that connector declares
// — so Jira projects, Drive folders and Chat spaces are the same screen with
// different words, not three screens.
//
// Nothing here knows what a "project" or a "space" is. `kind` only supplies
// the noun in the copy.

const PLURAL = { project: 'projects', folder: 'folders', space: 'spaces' }
const plural = (kind, n) => (n === 1 ? kind : PLURAL[kind] ?? `${kind}s`)

function Checkbox({ checked, indeterminate = false, onChange, label }) {
  return (
    <input
      type="checkbox" checked={checked} aria-label={label}
      ref={(el) => { if (el) el.indeterminate = indeterminate && !checked }}
      onChange={onChange}
      className="size-4 shrink-0 cursor-pointer accent-[var(--primary)]"
    />
  )
}

function FieldRow({ field, value, onChange }) {
  // A field with no fixed values is a day window: the only numeric filter any
  // connector declares so far, and the one that needs a different control.
  if (!field.values.length) {
    return (
      <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
        {field.label}
        <select
          className="w-full rounded-md border border-border bg-background px-2.5 py-2 text-sm outline-none focus:border-primary"
          value={String(value ?? 0)}
          onChange={(e) => onChange(Number(e.target.value))}
        >
          <option value="0">Any time</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
          <option value="180">Last 6 months</option>
          <option value="365">Last 12 months</option>
        </select>
        <span>Applies to the first sync. After that Cairn follows its own watermark.</span>
      </label>
    )
  }

  const chosen = new Set(value ?? [])
  const toggle = (name) => {
    const next = new Set(chosen)
    if (next.has(name)) next.delete(name); else next.add(name)
    onChange([...next])
  }
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-muted-foreground">{field.label}</span>
      <div className="flex flex-wrap gap-1.5">
        {field.values.map((name) => (
          <label
            key={name}
            className={cn('flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs',
              chosen.has(name)
                ? 'border-primary/50 bg-primary/10 text-foreground'
                : 'border-border text-muted-foreground hover:text-foreground')}
          >
            <Checkbox checked={chosen.has(name)} onChange={() => toggle(name)} label={name} />
            {name}
          </label>
        ))}
      </div>
    </div>
  )
}

export function ScopePicker({ connection, onClose, onSaved }) {
  const [options, setOptions] = useState(null)
  const [picked, setPicked] = useState(null)   // Set of item ids, null until loaded
  const [fields, setFields] = useState({})
  const [query, setQuery] = useState('')
  const [error, setError] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let alive = true
    setOptions(null); setLoadError(null)
    Promise.all([api.connectionScopeOptions(connection.id), api.connectionScope(connection.id)])
      .then(([choices, current]) => {
        if (!alive) return
        setOptions(choices)
        const scope = current.scope ?? {}
        setPicked(new Set(scope.items ?? []))
        // An unsaved scope starts at whatever the connector calls sensible,
        // so the form opens on the same answer a first sync would use.
        setFields(Object.fromEntries(choices.fields.map((f) => [
          f.name, scope[f.name] ?? (f.values.length ? f.default : 0),
        ])))
      })
      .catch((e) => { if (alive) setLoadError(e.message) })
    return () => { alive = false }
  }, [connection.id, reload])

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!options) return []
    return options.groups
      .map((g) => ({
        ...g,
        items: needle
          ? g.items.filter((i) => `${i.name} ${i.detail ?? ''}`.toLowerCase().includes(needle))
          : g.items,
      }))
      .filter((g) => g.items.length)
  }, [options, query])

  if (loadError) {
    return (
      <Panel connection={connection} onClose={onClose}>
        <EmptyState
          icon={TriangleAlert}
          title={`Couldn’t read what ${connection.name} can see.`}
          detail={loadError}
          action={<Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>
            <RefreshCw size={13} aria-hidden />Try again
          </Button>}
        />
      </Panel>
    )
  }

  if (!options || !picked) {
    return (
      <Panel connection={connection} onClose={onClose}>
        <p className="px-1 pb-3 text-sm text-muted-foreground" role="status">
          Asking {connection.name} what it can see…
        </p>
        <SkeletonList rows={6} />
      </Panel>
    )
  }

  const { kind } = options
  const everything = options.groups.flatMap((g) => g.items)
  const noun = plural(kind, picked.size)

  const toggle = (id) => {
    const next = new Set(picked)
    if (next.has(id)) next.delete(id); else next.add(id)
    setPicked(next); setSaved(false)
  }
  const toggleGroup = (group) => {
    const ids = group.items.map((i) => i.id)
    const all = ids.every((id) => picked.has(id))
    const next = new Set(picked)
    ids.forEach((id) => (all ? next.delete(id) : next.add(id)))
    setPicked(next); setSaved(false)
  }

  const save = async () => {
    setBusy(true); setError(null)
    try {
      await api.saveConnectionScope(connection.id, { items: [...picked], ...fields })
      setSaved(true)
      onSaved?.({ count: picked.size, kind })
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  // The count only means anything where the provider reports one.
  const counted = everything.filter((i) => picked.has(i.id) && typeof i.count === 'number')
  const total = counted.reduce((n, i) => n + i.count, 0)

  return (
    <Panel connection={connection} onClose={onClose} note={options.note}>
      <div className="flex flex-col gap-3">
        {!!options.fields.length && (
          <div className="grid gap-3 rounded-lg border border-border bg-background p-3 sm:grid-cols-2">
            {options.fields.map((f) => (
              <FieldRow
                key={f.name} field={f} value={fields[f.name]}
                onChange={(v) => { setFields((all) => ({ ...all, [f.name]: v })); setSaved(false) }}
              />
            ))}
          </div>
        )}

        <label className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 focus-within:border-primary">
          <Search size={15} className="shrink-0 text-muted-foreground" aria-hidden />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder={`Filter ${everything.length} ${plural(kind, everything.length)}…`}
            aria-label={`Filter ${plural(kind, 2)}`}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </label>

        <div className="max-h-[46vh] overflow-y-auto rounded-lg border border-border">
          {!groups.length && (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground" role="status">
              Nothing matched “{query.trim()}”.
            </p>
          )}
          {groups.map((group) => {
            const ids = group.items.map((i) => i.id)
            const chosen = ids.filter((id) => picked.has(id)).length
            return (
              <div key={group.id}>
                <div className="flex items-center gap-2.5 border-b border-border bg-card px-3 py-1.5">
                  <Checkbox
                    checked={chosen === ids.length} indeterminate={chosen > 0}
                    onChange={() => toggleGroup(group)}
                    label={`Select every item in ${group.name}`}
                  />
                  <span className="text-xs font-medium uppercase tracking-[0.03em]">{group.name}</span>
                  {group.note && <span className="text-xs text-muted-foreground">{group.note}</span>}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {chosen} of {ids.length}
                  </span>
                </div>
                {group.items.map((item) => (
                  <label
                    key={item.id}
                    className={cn('flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-sm',
                      picked.has(item.id) ? 'bg-accent/50' : 'hover:bg-accent/25')}
                  >
                    <Checkbox checked={picked.has(item.id)} onChange={() => toggle(item.id)} label={item.name} />
                    {item.detail && <span className="w-20 shrink-0 truncate font-mono text-xs text-primary">{item.detail}</span>}
                    <span className="min-w-0 flex-1 truncate">{item.name}</span>
                    {typeof item.count === 'number' && (
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                        {item.count.toLocaleString()}
                      </span>
                    )}
                  </label>
                ))}
              </div>
            )
          })}
        </div>

        {error && <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden />{error}
        </p>}

        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">
              {picked.size
                ? `${picked.size} ${noun} selected`
                : `Everything — all ${everything.length} ${plural(kind, everything.length)}`}
            </div>
            <div className="text-xs text-muted-foreground">
              {picked.size === 0
                ? `Tick nothing and this connection keeps reading every ${kind} it can see.`
                : counted.length
                  ? `about ${total.toLocaleString()} items on the first sync`
                  : `Only these ${noun} are read. Widening the scope backfills on the next sync.`}
            </div>
          </div>
          {saved && <span className="flex items-center gap-1.5 text-xs text-success">
            <Check size={13} aria-hidden />Scope saved.
          </span>}
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save scope'}</Button>
        </div>
      </div>
    </Panel>
  )
}

function Panel({ connection, note, onClose, children }) {
  return (
    <section className="mb-5 rounded-[10px] border border-border bg-card p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">What Cairn reads from {connection.name}</h2>
          {note && <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-muted-foreground">{note}</p>}
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X size={15} aria-hidden />
        </Button>
      </div>
      {children}
    </section>
  )
}
