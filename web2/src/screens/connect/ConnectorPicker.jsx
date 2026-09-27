import { useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import * as api from '@/api'
import { ConnectorOffer } from '@/components/ConnectorOffer'
import { ConnectFlow } from './ConnectFlow'

// The three kinds one Google consent covers (feeders/google/auth.py SCOPES).
// Every other OAuth connector owns its own consent and its own provider segment.
const GOOGLE_OAUTH_KINDS = ['gdrive', 'gchat', 'gmail']

// A connector is identified by its registry id, never by its kind: WhatsApp and
// LinkedIn are two connectors that both produce connections of kind "browser".
const idOf = (item) => item.id ?? item.kind

// The one connector picker. Onboarding step 2 and Connect > Health both render
// this, so "add a source" is the same act in both places — grouped by provider,
// because one Google sign-in covers Drive, Chat and Gmail and the grouping is
// what makes that legible.
export function ConnectorPicker({
  projects = [], projectId, onProjectId,
  connectedKinds = [], onConnected, onRemove, onNavigate, className,
  // Onboarding marks a card as connected the moment it authorizes. Health must
  // not: there every card is an offer to add ANOTHER connection (two GitHub
  // repos are two connections), so a card already in the project stays
  // addable. connectedKinds still flows to ConnectFlow either way, which is
  // what drives the one-click re-consent copy for provider siblings.
  markConnected = true,
}) {
  const [openId, setOpenId] = useState(null)
  const [query, setQuery] = useState('')

  // The provider hand-off ConnectFlow needs. Without it ConnectFlow throws
  // "not wired to the backend yet" in live mode — the flow was built before
  // the endpoints behind it existed.
  const authorize = async ({ id, kind, projectId: pid, token, site, email, urls, maxItems,
                            config: declaredConfig }) => {
    const listed = (api.CONNECTOR_CATALOGUE ?? []).flatMap((g) => g.items)
      .find((i) => idOf(i) === id)
    // Same reason as the merge below: the registry decides how it signs in.
    const item = { ...listed, ...(builtById.get(id) ?? {}) }

    // A repo is not a connection: it has its own row under /api/repos and its
    // own clone/graph/ingest lifecycle, which is why connections.create
    // rejects the kind outright.
    if (kind === 'github') {
      const probe = await api.checkRepo(site)
      const repo = await api.addRepo(site, probe.default_branch, token, pid)
      const run = await api.syncConnection(repo.id)
      return { connectionId: repo.id, runId: run.runId ?? run.run_id }
    }

    if (item?.auth === 'oauth') {
      // Which consent to send this kind to. Google's covers only the kinds its
      // scopes actually request (feeders/google/auth.py SCOPES) — sending
      // Outlook, OneDrive or Teams down it would grant Drive access and then
      // claim the wrong connector was connected. Every other kind uses the
      // provider its own connector declares, which is what lets a connector
      // ship a sign-in without an edit here.
      // Google's is named for the token's owner because one consent covers
      // three connectors. Everywhere else the consent belongs to a single
      // connector, so its id IS the provider segment and nothing has to be
      // listed here when a new one ships.
      const provider = GOOGLE_OAUTH_KINDS.includes(kind) ? 'google' : kind
      if (!builtById.has(id)) {
        throw new Error(`${item.name} has no connector behind it yet — the catalogue lists it ahead of the backend.`)
      }
      // The browser leaves for the provider. The callback writes the connection
      // rows and redirects back, so there is nothing here to resolve.
      window.location.assign(
        `/api/${encodeURIComponent(provider)}/authorize?project_id=${encodeURIComponent(pid)}`
        + `&kind=${encodeURIComponent(kind)}`
        + (maxItems ? `&max_items=${encodeURIComponent(maxItems)}` : ''))
      return
    }

    // A connector that declares its own fields sends them straight through; the
    // rest keep their hand-rolled shapes until they declare theirs.
    const config = declaredConfig && Object.keys(declaredConfig).length ? declaredConfig
      : kind === 'links' ? { urls } : kind === 'jira' ? { site, email, token } : token ? { token } : {}
    const connection = await api.createConnection(pid, kind, item?.name ?? kind, config)
    const run = await api.syncConnection(connection.id)
    return { connectionId: connection.id, runId: run.runId ?? run.run_id }
  }

  // A catalogue entry with no feeder behind it says so on the card instead of
  // opening a flow that throws once the user has already picked a project and
  // clicked through consent.
  const cardFor = (item) => {
    const added = markConnected && connectedKinds.includes(item.kind)
    return (
      <ConnectorOffer
        key={idOf(item)} item={item} added={added}
        unavailable={item.available === false}
        actionLabel={item.kind === 'github' ? 'Track repo' : undefined}
        onPick={() => (item.kind === 'github' && onNavigate
          ? onNavigate('repos')
          : setOpenId(idOf(item)))}
      />
    )
  }

  const flowFor = (item) => (
    <div key={`flow-${idOf(item)}`} className="col-span-full">
      <ConnectFlow
        connector={item}
        projects={projects} projectId={projectId} onProjectId={onProjectId}
        connectedKinds={connectedKinds}
        onAuthorize={authorize}
        onCancel={() => setOpenId(null)}
        onConnected={(result) => { setOpenId(null); onConnected?.(result) }}
      />
    </div>
  )

  // The form belongs directly under the card that opened it. Appending it to
  // the end of the group put "Connect Google Drive" below Gmail, so the thing
  // you clicked and the form you got were two cards apart.
  const cardsAndFlow = (group) => group.items.flatMap((item) => (
    idOf(item) === openId ? [cardFor(item), flowFor(item)] : [cardFor(item)]
  ))

  // The server's registry decides what is actually built; the static catalogue
  // only supplies grouping and order. A connector discovered from its own
  // folder therefore appears here with no edit to this file.
  const [built, setBuilt] = useState(null)
  useEffect(() => {
    let cancelled = false
    api.connectorCatalogue?.()
      .then((rows) => !cancelled && setBuilt(rows))
      .catch(() => !cancelled && setBuilt([]))
    return () => { cancelled = true }
  }, [])

  const builtById = new Map((built ?? []).map((c) => [c.id, c]))
  const listed = new Set((api.CONNECTOR_CATALOGUE ?? []).flatMap((g) => g.items).map(idOf))
  const groups = [
    ...(api.CONNECTOR_CATALOGUE ?? []).map((g) => ({
      ...g,
      // How a connector signs in is the server's answer, not the static
      // list's: the roadmap entry was written before the connector existed and
      // guesses. Taking `auth` from the registry is what lets a connector
      // switch from a token form to a sign-in without an edit here.
      items: g.items.map((i) => {
        if (built === null) return i
        const row = builtById.get(idOf(i))
        // The registry wins on everything it knows: a connector that changes
        // how it signs in, or rewords what it pulls, needs no edit here.
        return { ...i, available: !!row || !!i.selfManaged, auth: row?.auth ?? i.auth, desc: row?.description ?? i.desc }
      }),
    })),
    // Anything the server has that the static catalogue never heard of.
    ...((built ?? []).some((c) => !listed.has(c.kind))
      ? [{
        group: 'More',
        items: (built ?? []).filter((c) => !listed.has(c.id))
          .map((c) => ({ id: c.id, kind: c.kind, name: c.name, desc: c.description, auth: c.auth })),
      }]
      : []),
  ]

  // Thirteen connectors is past the point where reading every group is faster
  // than typing. Matching the description too means "issues" finds Jira.
  const needle = query.trim().toLowerCase()
  const shown = useMemo(() => groups
    .map((g) => ({
      ...g,
      items: needle
        ? g.items.filter((i) => `${i.name} ${i.desc ?? ''}`.toLowerCase().includes(needle))
        : g.items,
    }))
    // A group whose flow is open stays, so filtering cannot yank the form away.
    .filter((g) => g.items.length || g.items.some((i) => idOf(i) === openId)),
    [groups, needle, openId])

  const matchCount = shown.reduce((n, g) => n + g.items.length, 0)
  const total = groups.reduce((n, g) => n + g.items.length, 0)

  return (
    <div className={className}>
      <label className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 focus-within:border-primary">
        <Search size={15} className="shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search ${total} connectors…`} aria-label="Search connectors"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </label>

      {needle && matchCount === 0 && (
        <p className="mt-4 text-sm text-muted-foreground" role="status">
          Nothing matches “{query.trim()}”. Cairn connects {total} sources — clear the search to see them all.
        </p>
      )}

      {shown.filter((g) => !g.advanced).map((group) => (
        <div key={group.group} className="mt-5 first:mt-0">
          <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-xs font-medium uppercase tracking-[0.03em] text-muted-foreground">
              {group.group}
            </span>
            {group.note && <span className="text-xs text-muted-foreground">{group.note}</span>}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {cardsAndFlow(group)}
          </div>
        </div>
      ))}

      {/* WhatsApp is opt-in only in the product design, so it reads as secondary
          rather than sitting in the main eight. */}
      {shown.filter((g) => g.advanced).map((group) => (
        <div key={group.group} className="mt-6 border-t border-border pt-4 opacity-80">
          <div className="mb-2 text-xs font-medium uppercase tracking-[0.03em] text-muted-foreground">
            Advanced
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {cardsAndFlow(group)}
          </div>
        </div>
      ))}
    </div>
  )
}
