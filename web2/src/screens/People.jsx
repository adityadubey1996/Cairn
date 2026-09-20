import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Search, TriangleAlert, Users } from 'lucide-react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { ago } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useNarrow } from '@/Shell'
import { Button } from '@/components/ui/button'
import { CitationChip } from '@/components/CitationChip'
import { EmptyState } from '@/components/EmptyState'
import { EventRow } from '@/components/EventRow'
import { PersonAvatar } from '@/components/PersonAvatar'
import { SkeletonList } from '@/components/SkeletonList'

// The owner is pinned first whatever the timestamps say; everyone else sorts
// by how recently they were active.
const byRecency = (a, b) =>
  (b.isOwner ? 1 : 0) - (a.isOwner ? 1 : 0) ||
  new Date(b.lastActiveAt ?? 0) - new Date(a.lastActiveAt ?? 0)

function ErrorState({ title, onRetry }) {
  return (
    <EmptyState
      icon={TriangleAlert}
      title={title}
      action={
        <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={onRetry}>
          Try again
        </Button>
      }
    />
  )
}

function Picker({ people, state, q, onQ, selectedId, onSelect, onRetry, className }) {
  return (
    <div className={cn('flex min-h-0 flex-col', className)}>
      <div className="flex items-center gap-2 px-2.5 pt-2.5 pb-1.5">
        <span className="px-1 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">People</span>
        {state === 'ready' && <span className="text-[11px] text-muted-foreground">{people.length}</span>}
      </div>

      <div className="px-2.5 pb-2.5">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 focus-within:border-primary">
          <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
          <input
            value={q} onChange={(e) => onQ(e.target.value)}
            placeholder="Search people…" aria-label="Search people"
            className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2.5">
        {state === 'loading' && <SkeletonList rows={5} avatar />}

        {state === 'error' && <ErrorState title="Couldn’t load people." onRetry={onRetry} />}

        {state === 'ready' && people.length === 0 && (
          <EmptyState
            icon={Users}
            title={q.trim()
              ? `Nobody here matches “${q.trim()}”.`
              : 'People mentioned in your connected sources will show up here once your wiki has some content.'}
          />
        )}

        {state === 'ready' && people.map((p) => (
          <button
            key={p.id} type="button" onClick={() => onSelect(p.id)}
            aria-current={p.id === selectedId ? 'true' : undefined}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left',
              p.id === selectedId ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
            )}
          >
            <PersonAvatar name={p.name} initials={p.initials} />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="min-w-0 truncate text-[13px] text-foreground">{p.name}</span>
                {p.isOwner && (
                  <span className="shrink-0 rounded-full border border-border px-1.5 text-[10.5px] uppercase tracking-[0.03em] text-muted-foreground">
                    You
                  </span>
                )}
              </span>
              <span className="block truncate text-[11.5px] text-muted-foreground">
                {p.eventCount ?? 0} {p.eventCount === 1 ? 'event' : 'events'} · {ago(p.lastActiveAt)}
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function sourceChip(source) {
  if (!source) return null
  const label = source.type === 'link'
    ? source.name
    : `${source.name.split('/').pop()}${source.sha ? `@${source.sha}` : ''}`
  return <CitationChip label={label} type={source.type} href={source.url} />
}

function Feed({ person, rows, state, chips, filter, onFilter, onRetry }) {
  if (!person) {
    return (
      <div className="flex min-h-0 flex-1 flex-col justify-center px-5">
        {state === 'loading'
          ? <SkeletonList rows={4} />
          : <EmptyState icon={Users} title="Pick someone on the left to see what they touched." />}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-border px-5 py-3.5">
        <PersonAvatar name={person.name} initials={person.initials} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[15px] font-semibold">{person.name}</span>
            {person.isOwner && (
              <span className="shrink-0 rounded-full border border-border px-1.5 text-[10.5px] uppercase tracking-[0.03em] text-muted-foreground">
                You
              </span>
            )}
          </div>
          <div className="truncate text-[12px] text-muted-foreground">Last active {ago(person.lastActiveAt)}</div>
        </div>
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border px-5 py-2.5">
          {chips.map((c) => (
            <button
              key={c.id} type="button" onClick={() => onFilter(c.id)}
              aria-pressed={filter === c.id}
              className={cn(
                'rounded-full border px-2.5 py-px text-[11.5px]',
                filter === c.id
                  ? 'border-primary text-foreground'
                  : 'border-border text-muted-foreground hover:border-primary hover:text-foreground',
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-5">
        {state === 'loading' && <div className="pt-4"><SkeletonList rows={4} /></div>}

        {state === 'error' && <ErrorState title="Couldn’t load this person’s events." onRetry={onRetry} />}

        {state === 'ready' && rows.length === 0 && (
          <EmptyState icon={Users} title="Nothing recorded for this person yet." />
        )}

        {state === 'ready' && rows.map(({ event, source }) => (
          <div key={event.id} className="border-b border-border last:border-b-0">
            <EventRow event={event} variant="person" className={cn('border-b-0', source && 'pb-1')} />
            {source && <div className="pb-3 pl-9.5">{sourceChip(source)}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

export function People({ projectId, projectName }) {
  const [people, setPeople] = useState([])
  const [projects, setProjects] = useState([])
  const [sources, setSources] = useState([])
  const [q, setQ] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [events, setEvents] = useState([])
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState(null)
  const [reload, setReload] = useState(0)
  const [pickerOpen, setPickerOpen] = useState(false)

  const narrow = useNarrow()
  const forced = forcedState()

  // Sources come along because a person's event cites one: the row's open link
  // needs the source's name and type, and its project drives the filter chips.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      api.listPeople({ projectId, q }),
      api.listProjects(),
      api.listSources({ projectId }),
    ])
      .then(([rows, projectRows, { rows: sourceRows }]) => {
        if (cancelled) return
        const sorted = [...rows].sort(byRecency)
        setPeople(sorted)
        setProjects(projectRows)
        setSources(sourceRows)
        setSelectedId((id) => id ?? sorted[0]?.id ?? null)
      })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, q, reload])

  // A project switch is a different set of people entirely.
  useEffect(() => { setSelectedId(null); setQ('') }, [projectId])

  useEffect(() => {
    if (!selectedId) { setEvents([]); return }
    let cancelled = false
    setEventsLoading(true)
    setEventsError(null)
    api.personEvents(selectedId, projectId)
      .then((rows) => !cancelled && setEvents(rows))
      .catch((e) => !cancelled && setEventsError(e))
      .finally(() => !cancelled && setEventsLoading(false))
    return () => { cancelled = true }
  }, [selectedId, projectId, reload])

  const sourceById = useMemo(() => new Map(sources.map((s) => [s.id, s])), [sources])

  const visiblePeople = forced === 'empty' ? [] : people
  const selected = visiblePeople.find((p) => p.id === selectedId) ?? null

  const rows = useMemo(() => {
    if (forced === 'empty') return []
    return events.map((event) => ({ event, source: sourceById.get(event.sourceId) ?? null }))
  }, [events, sourceById, forced])

  // Events carry no project of their own, so a row belongs to the project of
  // the source it cites, falling back to the project being viewed.
  const projectOfRow = (row) => row.source?.projectId ?? projectId
  const chips = useMemo(() => {
    if (!rows.length) return []
    const ids = [...new Set(rows.map(projectOfRow))]
    const named = ids.map((id) => ({
      id,
      label: projects.find((p) => p.id === id)?.name ?? projectName ?? 'This project',
    }))
    return [{ id: 'all', label: 'All projects' }, ...named]
  }, [rows, projects, projectName, projectId])

  const filtered = filter === 'all' ? rows : rows.filter((r) => projectOfRow(r) === filter)

  const pickerState = forced === 'loading' ? 'loading'
    : forced === 'error' ? 'error'
      : loading && people.length === 0 ? 'loading'
        : error ? 'error' : 'ready'

  const feedState = forced === 'loading' ? 'loading'
    : forced === 'error' ? 'error'
      : eventsLoading ? 'loading'
        : eventsError ? 'error' : 'ready'

  const retry = () => setReload((n) => n + 1)

  const picker = (
    <Picker
      people={visiblePeople} state={pickerState} q={q} onQ={setQ}
      selectedId={selectedId}
      onSelect={(id) => { setSelectedId(id); setFilter('all'); setPickerOpen(false) }}
      onRetry={retry}
      className={narrow ? 'min-h-0 flex-1' : 'w-[300px] shrink-0 border-r border-border'}
    />
  )

  if (narrow) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Button variant="outline" size="sm" onClick={() => setPickerOpen((v) => !v)} aria-expanded={pickerOpen}>
            <Users size={13} aria-hidden />
            {pickerOpen ? 'Close list' : selected?.name ?? 'People'}
            {!pickerOpen && <ChevronRight size={13} aria-hidden />}
          </Button>
          <span className="truncate text-[12px] text-muted-foreground">{projectName ?? '—'}</span>
        </div>
        {pickerOpen
          ? picker
          : (
            <Feed
              person={selected} rows={filtered} state={feedState}
              chips={chips} filter={filter} onFilter={setFilter} onRetry={retry}
            />
          )}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1">
      {picker}
      <Feed
        person={selected} rows={filtered} state={feedState}
        chips={chips} filter={filter} onFilter={setFilter} onRetry={retry}
      />
    </div>
  )
}
