import { useState } from 'react'
import * as api from '@/api'
import { ConnectorCard } from '@/components/ConnectorCard'
import { ConnectFlow } from './ConnectFlow'

// What the Google consent screen actually asks for. Everything else in the
// catalogue's Google group is aspirational until a feeder exists.
const GOOGLE_OAUTH_KINDS = ['gdrive', 'gchat', 'gmail']

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
  const [openKind, setOpenKind] = useState(null)

  // The provider hand-off ConnectFlow needs. Without it ConnectFlow throws
  // "not wired to the backend yet" in live mode — the flow was built before
  // the endpoints behind it existed.
  const authorize = async ({ kind, projectId: pid, token, site, email, urls, maxItems }) => {
    const item = (api.CONNECTOR_CATALOGUE ?? []).flatMap((g) => g.items)
      .find((i) => i.kind === kind)

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
      // Only Drive and Chat are covered by the scopes the consent actually
      // requests (feeders/google/auth.py SCOPES). Sending Gmail, Outlook,
      // OneDrive or Teams down this path would grant Drive access and then
      // claim the wrong connector was connected.
      if (!GOOGLE_OAUTH_KINDS.includes(kind)) {
        throw new Error(`${item.name} has no connector behind it yet — the catalogue lists it ahead of the backend.`)
      }
      // The browser leaves for Google. The callback writes the connection rows
      // and redirects back, so there is nothing here to resolve.
      window.location.assign(
        `/api/google/authorize?project_id=${encodeURIComponent(pid)}`
        + `&kind=${encodeURIComponent(kind)}`
        + (maxItems ? `&max_items=${encodeURIComponent(maxItems)}` : ''))
      return
    }

    const config = kind === 'links' ? { urls } : kind === 'jira' ? { site, email, token } : token ? { token } : {}
    const connection = await api.createConnection(pid, kind, item?.name ?? kind, config)
    const run = await api.syncConnection(connection.id)
    return { connectionId: connection.id, runId: run.runId ?? run.run_id }
  }

  // A catalogue entry with no feeder behind it says so on the card instead of
  // opening a flow that throws once the user has already picked a project and
  // clicked through consent.
  const cardFor = (item) => (
    <ConnectorCard
      key={item.kind}
      actionLabel={item.kind === 'github' ? 'Add repository' : undefined}
      className={item.available === false ? 'opacity-60' : undefined}
      connection={{
        kind: item.kind, name: item.name, auth: item.auth,
        detail: item.available === false ? `${item.desc} — not built yet` : item.desc,
        note: item.available === false ? 'No feeder behind this yet' : undefined,
        status: markConnected && connectedKinds.includes(item.kind) ? 'running' : 'not_configured',
        lastSyncAt: null, itemCount: 0,
      }}
      onSync={item.available === false ? undefined : () => item.kind === 'github' && onNavigate ? onNavigate('repos') : setOpenKind(item.kind)}
      onRemove={onRemove && markConnected && connectedKinds.includes(item.kind) ? () => onRemove(item.kind) : undefined}
    />
  )

  const flowFor = (group) => {
    const item = group.items.find((i) => i.kind === openKind)
    if (!item) return null
    return (
      <div className="sm:col-span-2">
        <ConnectFlow
          connector={item}
          projects={projects} projectId={projectId} onProjectId={onProjectId}
          connectedKinds={connectedKinds}
          onAuthorize={authorize}
          onCancel={() => setOpenKind(null)}
          onConnected={(result) => { setOpenKind(null); onConnected?.(result) }}
        />
      </div>
    )
  }

  const groups = api.CONNECTOR_CATALOGUE ?? []

  return (
    <div className={className}>
      {groups.filter((g) => !g.advanced).map((group) => (
        <div key={group.group} className="mt-5 first:mt-0">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            {group.group}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {group.items.map(cardFor)}
            {flowFor(group)}
          </div>
        </div>
      ))}

      {/* WhatsApp is opt-in only in the product design, so it reads as secondary
          rather than sitting in the main eight. */}
      {groups.filter((g) => g.advanced).map((group) => (
        <div key={group.group} className="mt-6 border-t border-border pt-4 opacity-80">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
            Advanced
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {group.items.map(cardFor)}
            {flowFor(group)}
          </div>
        </div>
      ))}
    </div>
  )
}
