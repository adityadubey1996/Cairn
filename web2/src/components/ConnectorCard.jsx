import { Filter, FolderOpen, RefreshCw, Upload } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { BORDER, TEXT, statusOf } from '@/lib/status'
import { Button } from './ui/button'
import { ConnectorIcon } from './ConnectorIcon'
import { OverflowMenu } from './OverflowMenu'
import { StatusDot } from './StatusDot'
import { Progress } from './ui/progress'

// One card per CONNECTION, not per connector type: two GitHub repos are two
// cards. `auth` picks the structural variant — an oauth card redirects, a
// token card expands in place.
// A scope in one line. No scope saved means everything reachable, which is
// what these connectors did before scope existed — so it says that plainly
// rather than leaving the row blank.
const SCOPE_NOUN = { project: 'projects', folder: 'folders', space: 'spaces' }

function scopeLabel(scope) {
  const items = scope?.scope?.items ?? []
  if (!items.length) return 'Reading everything it can see'
  const noun = SCOPE_NOUN[scope?.kind] ?? 'items'
  return `Reading ${items.length} ${items.length === 1 ? (scope?.kind ?? 'item') : noun}`
}


// What a connector will ask for, said before it is clicked rather than
// discovered halfway through a consent screen.
const AUTH_LABEL = {
  oauth: ['Sign in', 'One consent, no key to paste'],
  token: ['Token', 'You paste a key you create'],
  none: ['No setup', 'Nothing to configure'],
  browser: ['Browser session', 'Signs in through a real browser'],
}

export function ConnectorCard({ connection, progress = null, onSync, onRemove, onOpen,
                                openLabel, className, actionLabel, offer = false,
                                scope, onScope }) {
  const { kind, name, detail, status, lastSyncAt, itemCount, error, note, auth } = connection
  const { tone, label } = statusOf(status)
  const connected = status !== 'not_configured'
  // Hand-fed files are not a connector: there is no account behind them, so
  // nothing here offers to sign in again or re-sync. The row still earns its
  // place — it is where an in-flight batch reports what it is doing.
  const files = kind === 'upload'
  const repository = kind === 'github'
  const title = files ? 'Files' : name
  // The stored detail for an upload connection is just its kind — there is no
  // account name to put here the way there is for Drive or Jira.
  const subtitle = files ? 'Added from this computer' : detail
  const ingested = repository
    ? (connection.pendingCount ?? 0) + (connection.absorbedCount ?? 0)
    : itemCount ?? 0
  const destination = repository ? 'Repos' : 'Files'

  return (
    <div className={cn('flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5', className)}>
      <div className="flex items-center gap-2.5">
        <span className="flex size-7.5 shrink-0 items-center justify-center rounded-lg border border-[#262b36] bg-background">
          <ConnectorIcon kind={kind} size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{title}</div>
          <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
        </div>
        <StatusDot status={status} />
      </div>

      {/* A catalogue offer has no status worth reporting — "not configured,
          last sync never" is true of everything you have not added yet. What
          it needs from you is the useful thing to say instead. */}
      {offer ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 pb-2.5">
          <span className="rounded-[9px] border border-primary/40 px-2 py-px text-xs text-primary">
            {(AUTH_LABEL[auth] ?? AUTH_LABEL.token)[0]}
          </span>
          <span className="text-xs text-muted-foreground">
            {(AUTH_LABEL[auth] ?? AUTH_LABEL.token)[1]}
          </span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 pb-2.5">
          <span className={cn('rounded-[9px] border px-2 py-px text-xs', BORDER[tone], TEXT[tone])}>
            {label}
          </span>
          <span className="text-xs text-muted-foreground">
            Last sync {ago(lastSyncAt)}
          </span>
          {connected && <span className="ml-auto text-xs text-muted-foreground">Stored in {destination}</span>}
        </div>
      )}

      {connected && repository && (
        <div className="grid grid-cols-3 divide-x divide-border/60 rounded-md border border-border/60 bg-background/35 py-2">
          <Metric value={ingested} label="ingested" />
          <Metric value={connection.absorbedCount ?? 0} label="absorbed" />
          <Metric value={connection.articleCount ?? 0} label="articles" />
        </div>
      )}

      {connected && !repository && (
        <div className="flex items-baseline gap-1.5 text-xs text-muted-foreground">
          <strong className="text-base font-semibold tabular-nums text-foreground">{itemCount ?? 0}</strong>
          <span>{itemCount === 1 ? 'item available' : 'items available'} in Files</span>
        </div>
      )}

      {/* What the next sync will actually do. Without this an incremental sync
          that writes nothing looks like a sync that failed. */}
      {!files && connected && !repository && (
        <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span>
            {connection.syncedAt
              ? `Next sync: changes since ${ago(connection.syncedAt)}`
              : 'Next sync imports everything available'}
          </span>
          {connection.syncedAt && (
            <button
              type="button"
              onClick={() => onSync?.(connection, { full: true })}
              className="text-primary hover:underline"
            >
              Full resync
            </button>
          )}
        </div>
      )}

      {repository && connected && (
        <div className="text-xs leading-relaxed text-muted-foreground">
          {connection.pendingCount
            ? `${connection.pendingCount} ${connection.pendingCount === 1 ? 'unit is' : 'units are'} ready to absorb in Repos.`
            : 'The ingestion queue is clear. Sync to look for repository changes.'}
        </div>
      )}

      {error && <div className="text-xs text-destructive">{error}</div>}
      {status === 'partial' && !error && (
        <div className="text-xs text-warning">
          The sync completed with a bounded sample or skipped items. Open Pipeline for details.
        </div>
      )}
      {/* What this connection is allowed to read. Only for connectors that can
          be scoped at all — everything else reads what it can reach, and a row
          saying so on every card would be noise. */}
      {onScope && (
        <button
          type="button" onClick={onScope}
          className="flex w-full items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs transition-colors hover:border-primary/50"
        >
          <Filter size={12} className="shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{scopeLabel(scope)}</span>
          <span className="shrink-0 text-primary">Change</span>
        </button>
      )}
      {note && <div className="text-xs text-muted-foreground">{note}</div>}
      {/* Only while a run reports a total. A scrape that has not counted its
          items yet has `note` alone, which already says which phase it is in. */}
      {progress != null && <Progress value={progress} label={`${title} sync progress`} />}

      <div className="relative mt-auto flex items-center gap-2 pt-0.5">
        {connected && onOpen && (
          <Button variant="outline" size="sm" onClick={() => onOpen(connection)}>
            <FolderOpen size={12} aria-hidden />
            {openLabel || `Open ${destination.toLowerCase()}`}
          </Button>
        )}
        {/* No onSync = nothing to connect to yet (an unbuilt catalogue entry). */}
        {(!files || !onOpen) && (
          <Button
            variant={connected && onOpen ? 'ghost' : 'outline'} size="sm"
            disabled={!onSync} onClick={() => onSync?.(connection)}
          >
            {files ? <Upload size={12} aria-hidden /> : <RefreshCw size={12} aria-hidden />}
            {actionLabel || (files ? 'Add files' : connected ? 'Sync now' : auth === 'token' ? 'Add token' : 'Connect')}
          </Button>
        )}
        {onRemove && (
          <OverflowMenu
            className="ml-auto"
            label={`Options for ${title}`}
            items={[{
              label: files ? 'Remove and forget' : 'Disconnect and remove',
              danger: true,
              onClick: () => onRemove?.(connection),
            }]}
          />
        )}
      </div>
    </div>
  )
}

function Metric({ value, label }) {
  return (
    <div className="px-2.5">
      <div className="text-base font-semibold tabular-nums text-foreground">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}
