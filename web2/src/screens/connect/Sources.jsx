import { useCallback, useEffect, useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, BookPlus, ChevronDown, ChevronRight, ExternalLink, FileSearch, Folder, Search, Sparkles, TriangleAlert, Upload, X } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ContentResults } from '@/components/ContentResults'
import { ConnectorIcon } from '@/components/ConnectorIcon'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'
import { SourceRow } from '@/components/SourceRow'

// Absorb runs for hours, so seconds is the wrong unit to show a human.
const minutes = (seconds) => {
  if (!seconds) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h ? `${h}h ${m}m` : `${Math.max(1, m)}m`
}

const CATALOGUE_NAMES = new Map(
  (api.CONNECTOR_CATALOGUE ?? []).flatMap((g) => g.items.map((i) => [i.kind, i.name])),
)
const kindLabel = (kind) =>
  (kind === 'upload' ? 'Files' : CATALOGUE_NAMES.get(kind))
  ?? kind.charAt(0).toUpperCase() + kind.slice(1)

// What one group is, per connector — mirroring server/sources.py's GROUP_BY.
// Chat writes one file per space per day, so the space is the unit. Drive
// groups by folder path, with everything Drive shows no parent for collected
// under one group — which is most of a transcript-heavy corpus, since those
// are shared out of other people's Drives.
const GROUP_UNIT = {
  gchat: { one: 'space', many: 'spaces', column: 'Space', items: 'Days' },
  gdrive: { one: 'folder', many: 'folders', column: 'Folder', items: 'Files' },
}

// Connectors whose groups nest, browsed a level at a time the way the source
// itself looks. `group` doubles as the current path — it is already an exact
// folder match on the server, so opening a folder and filtering to its files
// are the same request.
const TREE_KINDS = { gdrive: { root: 'All folders' } }

function GroupRow({ group, kind, folder = false, onOpen }) {
  return (
    <button
      type="button" onClick={onOpen}
      className="flex w-full items-center gap-2 border-b border-border py-2.5 text-left hover:bg-muted/40"
    >
      {folder
        ? <Folder size={13} className="shrink-0 text-muted-foreground" aria-hidden />
        : <ConnectorIcon kind={kind} size={13} className="shrink-0 text-muted-foreground" />}
      <span className="min-w-0 flex-1 truncate text-[13.5px]">{group.label}</span>
      <span className="shrink-0 text-[11.5px] text-muted-foreground">
        {group.count.toLocaleString()}
      </span>
      <ChevronRight size={14} className="shrink-0 text-muted-foreground" aria-hidden />
    </button>
  )
}

function FilterChip({ active, onClick, children }) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={active}
      className={cn(
        'flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] transition-colors',
        active
          ? 'border-border bg-card text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

// Expanding a row answers two different questions — "what does this file say"
// and "what did the wiki make of it" — so the panel is two tabs rather than one
// stack. The file is the default: it is the thing that was scraped, and the
// wiki side is often empty.
function ProvenancePanel({ source, articles, projectId, onOpenArticle }) {
  const [tab, setTab] = useState('file')
  const count = articles?.length ?? 0
  return (
    <div className="border-t border-border bg-card/40 px-3 py-3">
      <div className="mb-2 flex gap-1" role="tablist" aria-label="What to show for this source">
        {[['file', 'Markdown file'], ['wiki', `Wiki articles${count ? ` (${count})` : ''}`]].map(([id, label]) => (
          <button
            key={id} type="button" role="tab" aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={cn(
              'rounded-md px-2 py-0.5 text-[11.5px] transition-colors',
              tab === id ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'file' && (
        <SourceViewer source={source} embedded projectId={projectId} />
      )}

      {tab === 'wiki' && (
      <div className="mt-1.5">
        {articles === null ? (
          <SkeletonList rows={2} icon={false} />
        ) : articles.length === 0 ? (
          <p className="text-[12.5px] text-muted-foreground">
            {source.queued
              ? 'Queued for the wiki — not written up yet.'
              : 'Not in the wiki yet.'}
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {articles.map((a) => (
              <button
                key={a.path} type="button"
                onClick={() => onOpenArticle?.(a.path)}
                className="rounded-full border border-border bg-card px-2.5 py-1 text-[11.5px] text-primary hover:border-primary"
              >
                {a.title}
              </button>
            ))}
          </div>
        )}
      </div>
      )}

      {tab === 'wiki' && source.text && (
        <>
          <div className="mt-3 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            Cached copy
          </div>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">{source.text}</p>
        </>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted-foreground">
        <span>{source.detail}</span>
        {source.sha && <span>read at {source.sha}</span>}
        {source.url && (
          <a
            href={source.url} target="_blank" rel="noreferrer"
            className="flex items-center gap-1 text-primary hover:underline"
          >
            <ExternalLink size={11} aria-hidden />original URL
          </a>
        )}
      </div>
    </div>
  )
}

// A source file's line structure is part of what you are inspecting: one chat
// message per line, one transcript cue per line. Markdown collapses single
// newlines into a paragraph, so they become hard breaks here. Blank lines still
// separate paragraphs, so prose documents are unaffected.
const keepLineBreaks = (text) => text.replace(/\n(?!\n)/g, '  \n')

// The stored markdown, rendered in place. Only one is open at a time, so this
// component IS the cache — closing it is what frees the text.
// A browser renders a PDF or an image inline and nothing else. A .docx in an
// iframe is a download prompt in one browser and a blank rectangle in another,
// so those get an honest sentence and a button instead.
const RENDERS_INLINE = /\.(pdf|png|jpe?g|gif|webp|svg)$/i

function OriginalPane({ path }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setUrl(null)
    setError(null)
    api.viewSource({ path })
      .then((got) => { if (!cancelled) setUrl(got.url) })
      .catch((e) => { if (!cancelled) setError(e) })
    return () => { cancelled = true }
  }, [path])

  if (error) {
    return (
      <p className="px-3 py-4 text-xs text-destructive">
        {error.message ?? 'Could not read the original.'}
      </p>
    )
  }
  if (!url) return <p className="px-3 py-4 text-xs text-muted-foreground">Loading…</p>
  if (!RENDERS_INLINE.test(path)) {
    return (
      <div className="flex flex-col items-start gap-2 px-3 py-4">
        <p className="text-[12.5px] text-muted-foreground">
          {path.split('/').pop()} can’t be shown in the browser. The text pulled
          out of it is on the Extracted tab.
        </p>
        <Button variant="outline" size="xs" onClick={() => window.open(url, '_blank', 'noreferrer')}>
          <ExternalLink size={11} aria-hidden /> Download the original
        </Button>
      </div>
    )
  }
  return (
    <iframe
      src={url} title={`Original of ${path.split('/').pop()}`}
      className="h-[420px] w-full rounded-b-lg border-0 bg-white"
    />
  )
}

// Mirrors feeders/links/sync.py:URL_RE. The two must agree on what counts as a
// URL, or a pill appears for something the fetcher will never go and get.
const URL_RE = /https?:\/\/[^\s)\]>"]+/g

const urlsIn = (text) => [...new Set(
  (text.match(URL_RE) ?? []).map((u) => u.replace(/[.,;:]+$/, '')),
)]

const hostOf = (url) => { try { return new URL(url).host } catch { return url } }

// One pill per link mentioned in a transcript. Clicking opens OUR fetched copy
// — the frozen text the wiki cites, which still exists after the page 404s —
// and the ↗ goes to the live page. Hover shows the real destination either way,
// so a pill can never misrepresent where it leads.
function LinkPills({ text, projectId }) {
  const urls = useMemo(() => urlsIn(text), [text])
  const [known, setKnown] = useState({})
  const [open, setOpen] = useState(null)

  useEffect(() => {
    if (!urls.length) { setKnown({}); return }
    let cancelled = false
    api.sourcesByUrl({ projectId, urls })
      .then((got) => !cancelled && setKnown(got))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, urls])

  if (!urls.length) return null

  return (
    <div className="border-t border-border px-3 py-2.5">
      <div className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
        Links in this {urls.length === 1 ? 'message' : 'transcript'} ({urls.length})
      </div>
      <div className="flex flex-wrap gap-1.5">
        {urls.map((url) => {
          const src = known[url]
          const failed = src?.status === 'failed'
          return (
            <span
              key={url} title={url}
              className={cn(
                'inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px]',
                failed ? 'border-destructive/50 text-destructive'
                  : src ? 'border-border bg-card text-primary'
                    : 'border-dashed border-border text-muted-foreground',
              )}
            >
              <button
                type="button"
                onClick={() => src && !failed && setOpen(src)}
                disabled={!src || failed}
                className="max-w-[240px] truncate disabled:cursor-default"
              >
                {src && !failed ? src.name : hostOf(url)}
              </button>
              {/* Always offered, fetched or not: the live page is the one thing
                  we can always point at. */}
              <a
                href={url} target="_blank" rel="noreferrer"
                aria-label={`Open ${url} in a new tab`}
                className="shrink-0 opacity-70 hover:opacity-100"
              >
                <ExternalLink size={10} aria-hidden />
              </a>
            </span>
          )
        })}
      </div>
      {/* Not fetched yet is a real state, not a gap — say which. */}
      {urls.some((u) => !known[u]) && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Dashed pills have not been fetched yet — run the Saved links connector.
        </p>
      )}
      {open && (
        <div className="mt-2">
          <SourceViewer source={open} embedded />
        </div>
      )}
    </div>
  )
}


export function SourceViewer({ source, onClose, embedded = false, projectId }) {
  const [state, setState] = useState('loading')
  const [doc, setDoc] = useState(null)
  // Extracted first even where an original exists: the markdown is what the
  // wiki will actually be written from, so it is the copy worth checking.
  const [tab, setTab] = useState('extracted')

  useEffect(() => { setTab('extracted') }, [source.path])

  useEffect(() => {
    if (!source.path) { setDoc(null); setState('nopath'); return }
    let cancelled = false
    setState('loading')
    api.sourceContent({ path: source.path })
      .then((got) => { if (!cancelled) { setDoc(got); setState('ready') } })
      .catch((e) => { if (!cancelled) { setDoc(e); setState('error') } })
    return () => { cancelled = true }
  }, [source.path])

  const openRaw = async () => {
    const path = tab === 'original' ? source.originalPath : source.path
    if (!path) return
    try {
      const { url } = await api.viewSource({ path })
      window.open(url, '_blank', 'noreferrer')
    } catch (e) {
      // An unhandled rejection here is invisible except in the console, and
      // the button just appears dead.
      setDoc(e); setState('error'); setTab('extracted')
    }
  }

  return (
    <div className={cn('rounded-lg border border-border bg-card', !embedded && 'mb-2')}>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-[12.5px]">{source.name}</span>
        {/* Only where extraction actually lost something. Chat days, links and
            plain-text uploads have one file, and a tab strip over a single
            document is a control that does nothing. */}
        {source.originalPath && (
          <div className="flex shrink-0 gap-1" role="tablist" aria-label="Which copy to show">
            {[['extracted', 'Extracted'], ['original', 'Original']].map(([id, label]) => (
              <button
                key={id} type="button" role="tab" aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={cn(
                  'rounded-md px-2 py-0.5 text-[11.5px] transition-colors',
                  tab === id
                    ? 'bg-muted text-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <Button variant="ghost" size="xs" disabled={!source.path && !source.originalPath}
                onClick={openRaw}>
          <ExternalLink size={12} aria-hidden /> Raw
        </Button>
        {!embedded && (
          <Button variant="ghost" size="xs" onClick={onClose} aria-label="Close preview">
            <X size={13} aria-hidden />
          </Button>
        )}
      </div>

      {tab === 'original' && <OriginalPane path={source.originalPath} />}

      {tab === 'extracted' && state === 'loading' && (
        <p className="px-3 py-4 text-xs text-muted-foreground">Loading…</p>
      )}
      {tab === 'extracted' && state === 'nopath' && (
        <p className="px-3 py-4 text-xs text-muted-foreground">
          Nothing was stored for this one — the fetch failed, so there is no copy to read.
        </p>
      )}
      {tab === 'extracted' && state === 'error' && (
        <p className="px-3 py-4 text-xs text-destructive">
          {doc?.message ?? 'Could not read this file.'}
        </p>
      )}
      {tab === 'extracted' && state === 'ready' && (
        <div className="max-h-[420px] overflow-auto px-3 py-2">
          <div className="md-content">
            <Markdown remarkPlugins={[remarkGfm]}>{keepLineBreaks(doc.text)}</Markdown>
          </div>
          {doc.truncated && (
            <p className="mt-2 border-t border-border pt-2 text-[11px] text-muted-foreground">
              Truncated at 512 KB — use Raw for the whole file.
            </p>
          )}
        </div>
      )}

      {/* Off the FETCHED text, not source.text — the row carries only a
          snippet, so parsing that would find almost no links. */}
      {tab === 'extracted' && state === 'ready' && projectId && (
        <LinkPills text={doc.text} projectId={projectId} />
      )}
    </div>
  )
}

export function Sources({ projectId, forced, onNavigate }) {
  const [input, setInput] = useState('')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState(null)
  const [rows, setRows] = useState([])
  // The API caps its result set but counts the matches separately, so the row
  // array is not the answer to "how many are there".
  const [matchTotal, setMatchTotal] = useState(0)
  const [kinds, setKinds] = useState([])
  const [status, setStatus] = useState(null)   // null = both, 'failed' = only failures
  const [failedCount, setFailedCount] = useState(0)
  const [connections, setConnections] = useState([])
  const [connectionId, setConnectionId] = useState(null)
  const [group, setGroup] = useState(null)
  const [groups, setGroups] = useState([])
  const [subfolders, setSubfolders] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [expandedId, setExpandedId] = useState(null)
  const [viewing, setViewing] = useState(null)
  const [articles, setArticles] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  // Ids ticked in the list. Held here rather than as a flag on each row so
  // clearing it is one assignment and cannot half-apply.
  const [selected, setSelected] = useState(() => new Set())
  const [queue, setQueue] = useState(null)
  const [writeUpRunId, setWriteUpRunId] = useState(null)
  const [queueBusy, setQueueBusy] = useState(false)
  const [queueError, setQueueError] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [mode, setMode] = useState('name')   // name · contents
  const [failureReasons, setFailureReasons] = useState([])

  useEffect(() => {
    const timer = setTimeout(() => setQ(input.trim()), 180)
    return () => clearTimeout(timer)
  }, [input])

  // Connections are read for their names: brain_sources stores connection_id,
  // and resolving it here beats a join on every page of rows.
  useEffect(() => {
    let cancelled = false
    api.listConnections(projectId)
      .then((rows) => !cancelled && setConnections(rows))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, reload])

  // Only while the failed view is open: it is a group-by over every failed row
  // and nobody looking at the normal list needs it.
  useEffect(() => {
    if (status !== 'failed') { setFailureReasons([]); return }
    let cancelled = false
    api.sourceFailures({ projectId, kind })
      .then((got) => !cancelled && setFailureReasons(got.reasons ?? []))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, status, kind, reload])

  // Read unfiltered so the chip can say how many items the last scrape could
  // not bring in, whether or not that filter is currently active.
  useEffect(() => {
    let cancelled = false
    api.listSources({ projectId, status: 'failed' })
      .then(({ total }) => !cancelled && setFailedCount(total ?? 0))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, reload])

  // Counted server-side, not derived from a page of rows: the row list is
  // ordered by scrape time, so one page is whichever connector synced last —
  // which is how this chip row came to show Drive alone and hide 585 Chat days.
  useEffect(() => {
    let cancelled = false
    api.listSourceGroups({ projectId })
      .then((found) => !cancelled && setKinds(found.map((g) => g.key)))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, reload])

  // One tree level: the folders inside wherever we are. Root is group === null.
  useEffect(() => {
    if (!TREE_KINDS[kind]) { setSubfolders([]); return }
    let cancelled = false
    api.listSubfolders({ projectId, kind, folder: group ?? '', q, status, connectionId })
      .then((found) => !cancelled && setSubfolders(found))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, kind, group, q, status, connectionId, reload])

  useEffect(() => {
    if (!GROUP_UNIT[kind] || TREE_KINDS[kind]) { setGroups([]); return }
    let cancelled = false
    api.listSourceGroups({ projectId, kind, q, status, connectionId })
      .then((found) => !cancelled && setGroups(found))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId, kind, q, status, connectionId, reload])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.listSources({ projectId, q, kind, status, connectionId, group })
      .then(({ rows: found, total, cursor: next }) => {
        if (cancelled) return
        setRows(found)
        setMatchTotal(total ?? found.length)
        setCursor(next ?? null)
      })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, q, kind, status, connectionId, group, reload])

  // Changing the filter changes what "select all" would have meant, so a
  // selection carried across it could send ids the user can no longer see.
  useEffect(() => { setCursor(null); setExpandedId(null); setViewing(null); setSelected(new Set()) },
            [projectId, q, kind, status, connectionId, group])

  // The wiki queue is project-wide, not per filter — it is what the Write up
  // button would spend on, wherever in the list those rows happen to sit.
  const refreshQueue = useCallback(() => {
    api.wikiQueue(projectId)
      .then((got) => {
        setQueue(got)
        // Rejoins a write-up already in flight, so a reload mid-run shows the
        // run instead of a button that would 409.
        if (got.runId) setWriteUpRunId(got.runId)
      })
      .catch(() => {})
  }, [projectId])

  useEffect(refreshQueue, [refreshQueue, reload])

  const { running: writingUp, pct: writeUpPct, run: writeUpRun } =
    usePipelineRun(writeUpRunId, {
      // A run that fails before its first unit leaves the queue exactly as it
      // was, so without this the button would simply appear to do nothing.
      // The reason has to survive clearing the id the poll was following.
      onFinish: (row) => {
        setWriteUpRunId(null)
        if (row.status !== 'ok') setQueueError(row.error || `the write-up ${row.status}`)
        refreshQueue()
        setReload((n) => n + 1)
      },
    })

  const toggleSelected = (id) => setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const setQueued = async (queued) => {
    const ids = [...selected]
    setQueueBusy(true)
    setQueueError(null)
    try {
      await api.queueSources({ projectId, ids, queued })
      // Patched in place rather than refetching: a refetch would reset paging
      // to the first page and throw away however far the user had loaded.
      const touched = new Set(ids)
      setRows((prev) => prev.map((r) => (touched.has(r.id) ? { ...r, queued } : r)))
      setSelected(new Set())
      refreshQueue()
    } catch (e) {
      setQueueError(e.message)
    } finally {
      setQueueBusy(false)
    }
  }

  const startWriteUp = async () => {
    setConfirming(false)
    setQueueBusy(true)
    setQueueError(null)
    try {
      const { run_id } = await api.startWikiWriteUp(projectId)
      setWriteUpRunId(run_id)
    } catch (e) {
      setQueueError(e.message)
    } finally {
      setQueueBusy(false)
    }
  }

  // Leaving a connector leaves its groups behind with it.
  useEffect(() => { setGroup(null) }, [projectId, kind])

  // Single-expand: only the open row's provenance is held, so nothing
  // accumulates as the user works down a long list.
  useEffect(() => {
    if (!expandedId) return
    let cancelled = false
    setArticles(null)
    api.sourceArticles(expandedId).then((found) => !cancelled && setArticles(found))
    return () => { cancelled = true }
  }, [expandedId])

  // Appends rather than replacing, and carries the cursor forward. Keyset
  // paging means rows landing at the top while a scrape runs cannot shift the
  // boundary and duplicate or skip a row, which OFFSET would.
  const loadMore = async () => {
    if (!cursor || loadingMore) return
    setLoadingMore(true)
    try {
      const { rows: more, cursor: next } = await api.listSources({ projectId, q, kind, status, connectionId, group, cursor })
      setRows((prev) => [...prev, ...more])
      setCursor(next ?? null)
    } catch (e) {
      setError(e)
    } finally {
      setLoadingMore(false)
    }
  }

  // Opens the stored markdown in a panel on this screen. It used to
  // window.open a presigned S3 URL, which answered "can I reach the file" but
  // dumped raw markdown in a new tab and left the app to do it.
  const openSource = (source) =>
    setViewing((at) => (at?.id === source.id ? null : source))

  const retry = () => { setError(null); setReload((n) => n + 1) }
  const clearFilters = () =>
    { setInput(''); setQ(''); setKind(null); setStatus(null); setConnectionId(null); setGroup(null) }

  const connByAtId = Object.fromEntries(connections.map((c) => [c.id, c.name]))
  const state = forced ?? (loading ? 'loading' : error ? 'error' : 'ready')
  const filtered = forced === 'empty' ? [] : rows
  const filtering = !!q || !!kind || !!status || !!connectionId || !!group
  // Groups replace the flat list only at the top level of a grouped connector:
  // once you are inside one, the rows are the point.
  // Contents mode replaces the list wholesale rather than adding to it: a name
  // filter and a content scan answer different questions, and the same file
  // showing up in both halves would read as two hits.
  const searchingContents = mode === 'contents' && !!q
  const listState = searchingContents ? 'hidden' : state
  const unit = GROUP_UNIT[kind]
  const tree = TREE_KINDS[kind]
  const showGroups = !!unit && !tree && !group && !!groups.length
  // A tree root has no loose files: every file is inside a folder, or inside
  // the pseudo-folder for the ones Drive will not tell us the location of.
  const showFiles = !showGroups && (!tree || !!group)
  const nothingToShow = !showGroups && !subfolders.length && (!showFiles || !filtered.length)
  const pageRows = filtered
  const selectableRows = pageRows.filter((s) => s.status !== 'failed')
  const allSelected = selectableRows.length > 0
    && selectableRows.every((s) => selected.has(s.id))
  const someSelected = selectableRows.some((s) => selected.has(s.id))

  return (
    <div className="mx-auto w-full max-w-5xl">
      <div className="flex flex-col gap-2.5 pb-3 md:flex-row md:items-center">
        <div className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2 focus-within:border-primary md:w-[280px]">
          <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
          <input
            value={input} onChange={(e) => setInput(e.target.value)}
            placeholder={mode === 'name' ? 'Filter by name…' : 'Search inside files…'}
            aria-label={mode === 'name' ? 'Filter sources by name' : 'Search inside file contents'}
            className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
          />
          {/* Inside the box, because it changes what the box DOES — a chip out
              in the filter row would read as one more filter. */}
          <div className="flex shrink-0 gap-0.5" role="group" aria-label="What to search">
            {[['name', 'Name'], ['contents', 'Inside']].map(([id, label]) => (
              <button
                key={id} type="button" aria-pressed={mode === id}
                onClick={() => setMode(id)}
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-[11px] transition-colors',
                  mode === id ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-1 overflow-x-auto" role="group" aria-label="Filter by connector">
          <FilterChip active={!kind} onClick={() => setKind(null)}>All</FilterChip>
          {kinds.map((k) => (
            <FilterChip key={k} active={kind === k} onClick={() => setKind(k)}>
              <ConnectorIcon kind={k} size={12} />
              {kindLabel(k)}
            </FilterChip>
          ))}
            {/* A connection chip only earns its place where a kind has more than
              one: with a single Drive connection it would be a second chip
              reading "Google Drive" next to the first. */}
          {connections
            // Files have no account to tell apart, so the kind chip above is
            // already the whole story — a second chip per upload connection
            // would just repeat it.
            .filter((c) => c.kind !== 'upload')
            .filter((c) => connections.filter((o) => o.kind === c.kind).length > 1)
            .map((c) => (
              <FilterChip
                key={c.id} active={connectionId === c.id}
                onClick={() => setConnectionId(connectionId === c.id ? null : c.id)}
              >
                <ConnectorIcon kind={c.kind} size={12} />
                {c.name}
              </FilterChip>
            ))}
        {/* Only offered when there is something to see: a chip permanently
              reading "0 not synced" trains you to ignore it. */}
          {failedCount > 0 && (
            <FilterChip
              active={status === 'failed'}
              onClick={() => setStatus(status === 'failed' ? null : 'failed')}
            >
              <TriangleAlert size={12} className="text-destructive" />
              {failedCount.toLocaleString()} not synced
            </FilterChip>
          )}
        </div>
      </div>

      {/* Sticky because the list is long and the count it carries is the
          answer to "what am I about to do" — scrolling past it loses that. */}
      {selected.size > 0 && (
        <div className="sticky top-0 z-10 mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-card px-3 py-2">
          <span className="text-[12.5px]">{selected.size.toLocaleString()} selected</span>
          <Button size="xs" disabled={queueBusy} onClick={() => setQueued(true)}>
            <BookPlus size={12} aria-hidden /> Add to wiki
          </Button>
          <Button variant="outline" size="xs" disabled={queueBusy} onClick={() => setQueued(false)}>
            Remove from wiki
          </Button>
          <Button variant="ghost" size="xs" className="ml-auto text-muted-foreground"
                  onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {(writingUp || !!queue?.queued) && (
        <div className="mb-2 rounded-lg border border-border bg-card/40 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12.5px]">
              {writingUp
                ? `Writing up · ${writeUpRun?.items_written ?? 0}/${writeUpRun?.items_seen ?? queue?.queued ?? 0}`
                : `${queue.queued.toLocaleString()} queued for the wiki`}
            </span>
            <span className="text-[11.5px] text-muted-foreground">
              ~{queue?.tokens?.toLocaleString() ?? '—'} tokens · ~{minutes(queue?.seconds)}
              {queue && !queue.measured && ' (estimate, no run measured yet)'}
            </span>
            {!writingUp && (
              <Button size="xs" className="ml-auto" disabled={queueBusy}
                      onClick={() => setConfirming(true)}>
                <Sparkles size={12} aria-hidden /> Write up
              </Button>
            )}
          </div>
          {writingUp && <Progress className="mt-2" value={writeUpPct} label="Write-up progress" />}
        </div>
      )}

      {/* A couple of thousand failures is one undifferentiated wall until it is
          split by cause: a 404 is dead and nothing recovers it, a 429 drains on
          its own, and "no extractable text" is a page that needs a browser. */}
      {status === 'failed' && !!failureReasons.length && (
        <div className="mb-2 rounded-lg border border-border bg-card/40 px-3 py-2">
          <div className="pb-1 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            Why they failed
          </div>
          <div className="flex flex-wrap gap-1.5">
            {failureReasons.slice(0, 10).map((r) => (
              <span key={r.reason}
                    className="rounded-full border border-border px-2.5 py-0.5 text-[11.5px]">
                <span className="text-foreground">{r.count.toLocaleString()}</span>{' '}
                <span className="text-muted-foreground">{r.reason}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {queueError && (
        <p className="mb-2 flex items-start gap-1.5 text-[12.5px] text-destructive">
          <TriangleAlert size={14} className="mt-[2px] shrink-0" aria-hidden />
          <span>{queueError}</span>
        </p>
      )}

      <ConfirmDialog
        open={confirming}
        title={`Write up ${queue?.queued ?? 0} source${queue?.queued === 1 ? '' : 's'}?`}
        detail={`This is the only step that costs money: roughly ${queue?.tokens?.toLocaleString() ?? '—'} `
               + `tokens and about ${minutes(queue?.seconds)} of model time. It runs in the `
               + `background and you can stop it from Connect > Pipeline.`}
        confirmLabel="Write up"
        onConfirm={startWriteUp}
        onCancel={() => setConfirming(false)}
      />

      {group && (tree || unit) && (
        <nav aria-label="Folder path" className="mb-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted-foreground">
          <button
            type="button" onClick={() => setGroup(null)}
            className="flex items-center gap-1.5 hover:text-foreground"
          >
            <ArrowLeft size={12} aria-hidden />
            {tree ? tree.root : `All ${unit.many}`}
          </button>
          {(tree ? group.split('/') : [group]).map((segment, i, all) => {
            const here = i === all.length - 1
            return (
              <span key={`${segment}-${i}`} className="flex items-center gap-1.5">
                <span className="text-border">/</span>
                {here ? (
                  <span className="text-foreground">{segment}</span>
                ) : (
                  <button
                    type="button" onClick={() => setGroup(all.slice(0, i + 1).join('/'))}
                    className="hover:text-foreground"
                  >
                    {segment}
                  </button>
                )}
              </span>
            )
          })}
        </nav>
      )}

      {listState === 'loading' && <SkeletonList rows={8} />}

      {listState === 'error' && (
        <EmptyState
          icon={TriangleAlert}
          title="Couldn’t load this project’s sources."
          detail="Nothing was lost — the list just didn’t come back."
          action={
            <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={retry}>
              Try again
            </Button>
          }
        />
      )}

      {listState === 'ready' && nothingToShow && (
        filtering ? (
          <EmptyState
            icon={FileSearch}
            title="No source matches those filters."
            detail="Try a shorter term, or widen the connector filter."
            action={<Button variant="outline" size="sm" onClick={clearFilters}>Clear filters</Button>}
          />
        ) : (
          <EmptyState
            icon={FileSearch}
            title="Nothing here yet."
            detail="Connect a source on Health, or add files from your computer."
            action={
              <Button variant="outline" size="sm" onClick={() => onNavigate?.('files')}>
                <Upload size={13} aria-hidden /> Add files
              </Button>
            }
          />
        )
      )}

      {searchingContents && (
        <ContentResults
          projectId={projectId} q={q} kind={kind}
          onOpen={(row) => openSource(row)}
        />
      )}

      {state === 'ready' && viewing && (
        <SourceViewer source={viewing} onClose={() => setViewing(null)} />
      )}

      {listState === 'ready' && !!subfolders.length && (
        <>
          <div className="flex items-center gap-2 border-b border-border pb-1.5 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            <span>Folder</span>
            <span className="ml-auto">Files inside</span>
          </div>
          {subfolders.map((f) => (
            <GroupRow key={f.path} group={f} kind={kind} folder onOpen={() => setGroup(f.path)} />
          ))}
        </>
      )}

      {listState === 'ready' && showGroups && (
        <>
          <div className="flex items-center gap-2 border-b border-border pb-1.5 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            <span>{unit.column}</span>
            <span className="ml-auto">{unit.items}</span>
          </div>

          {groups.map((g) => (
            <GroupRow key={g.key} group={g} kind={kind} onOpen={() => setGroup(g.key)} />
          ))}

          <div className="pt-3 text-[11.5px] text-muted-foreground">
            {groups.length.toLocaleString()}{' '}
            {groups.length === 1 ? unit.one : unit.many}
            {' · '}{matchTotal.toLocaleString()} items
          </div>
        </>
      )}

      {listState === 'ready' && showFiles && filtered.length > 0 && (
        <>
          <div className="flex items-center gap-2 border-b border-border pb-1.5 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            {/* "Loaded", not "matching": paging means the rows in hand are the
                only ids this page can name. Load more, then select all again. */}
            <input
              type="checkbox" className="ml-1 size-3.5 accent-[var(--primary)]"
              aria-label={`Select all ${selectableRows.length} loaded sources`}
              checked={allSelected}
              ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected }}
              onChange={() => setSelected(allSelected
                ? new Set()
                : new Set(selectableRows.map((s) => s.id)))}
            />
            <span>Source</span>
            <span className="ml-auto">Scraped</span>
          </div>

          {pageRows.map((s) => {
            const open = expandedId === s.id
            const Chevron = open ? ChevronDown : ChevronRight
            return (
              <div key={s.id} className="border-b border-border">
                <div className="flex items-center gap-1">
                  {/* A failed row has no file behind it, so there is nothing
                      to write up — the server refuses those anyway. */}
                  <input
                    type="checkbox" className="ml-1 size-3.5 shrink-0 accent-[var(--primary)]"
                    aria-label={`Select ${s.name}`}
                    disabled={s.status === 'failed'}
                    checked={selected.has(s.id)}
                    onChange={() => toggleSelected(s.id)}
                  />
                  <button
                    type="button"
                    onClick={() => setExpandedId(open ? null : s.id)}
                    aria-expanded={open}
                    aria-label={`Show which articles were built from ${s.name}`}
                    className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
                  >
                    <Chevron size={14} aria-hidden />
                  </button>
                  <SourceRow
                    source={{
                      ...s,
                      // brain_sources holds the id; the name lives on the
                      // connection. Rows predating attribution have none.
                      detail: connByAtId[s.connectionId] ?? s.detail,
                    }} className="min-w-0 flex-1"
                    onOpenCached={() => openSource(s)}
                    onOpenOriginal={(src) => window.open(src.url, '_blank', 'noreferrer')}
                  />
                </div>
                {open && (
                  <ProvenancePanel
                    source={s} articles={articles} projectId={projectId}
                    onOpenArticle={(path) => onNavigate?.('wiki', path)}
                  />
                )}
              </div>
            )
          })}

          <div className="flex items-center gap-2 pt-3">
            <span className="text-[11.5px] text-muted-foreground">
              {filtered.length.toLocaleString()} of {matchTotal.toLocaleString()}
              {cursor ? ' loaded' : ''}
            </span>
            {cursor && (
              <Button
                variant="outline" size="xs" className="ml-auto"
                disabled={loadingMore} onClick={loadMore}
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
