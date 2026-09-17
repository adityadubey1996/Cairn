import { useState } from 'react'
import { EllipsisVertical, RefreshCw, Upload } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { BORDER, TEXT, statusOf } from '@/lib/status'
import { Button } from './ui/button'
import { ConnectorIcon } from './ConnectorIcon'
import { StatusDot } from './StatusDot'
import { Progress } from './ui/progress'

// One card per CONNECTION, not per connector type: two GitHub repos are two
// cards. `auth` picks the structural variant — an oauth card redirects, a
// token card expands in place.
export function ConnectorCard({ connection, progress = null, onSync, onRemove, className }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { kind, name, detail, status, lastSyncAt, itemCount, error, note, auth } = connection
  const { tone, label } = statusOf(status)
  const connected = status !== 'not_configured'
  // Hand-fed files are not a connector: there is no account behind them, so
  // nothing here offers to sign in again or re-sync. The row still earns its
  // place — it is where an in-flight batch reports what it is doing.
  const files = kind === 'upload'
  const title = files ? 'Files' : name
  // The stored detail for an upload connection is just its kind — there is no
  // account name to put here the way there is for Drive or Jira.
  const subtitle = files ? 'Added from this computer' : detail

  return (
    <div className={cn('flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5', className)}>
      <div className="flex items-center gap-2.5">
        <span className="flex size-7.5 shrink-0 items-center justify-center rounded-lg border border-[#262b36] bg-background">
          <ConnectorIcon kind={kind} size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-semibold">{title}</div>
          <div className="truncate text-[11.5px] text-muted-foreground">{subtitle}</div>
        </div>
        <StatusDot status={status} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('rounded-[9px] border px-2 py-px text-[11px]', BORDER[tone], TEXT[tone])}>
          {label}
        </span>
        <span className="text-[11.5px] text-muted-foreground">
          {ago(lastSyncAt)}{itemCount ? ` · ${itemCount} items` : ''}
        </span>
      </div>

      {/* What the next sync will actually do. Without this an incremental sync
          that writes nothing looks like a sync that failed. */}
      {!files && connected && (
        <div className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-muted-foreground">
          <span>
            {connection.syncedAt
              ? `Next sync: changes since ${ago(connection.syncedAt)}`
              : 'Next sync: everything — nothing scraped yet'}
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

      {error && <div className="text-[11.5px] text-destructive">{error}</div>}
      {note && <div className="text-[11.5px] text-muted-foreground">{note}</div>}
      {/* Only while a run reports a total. A scrape that has not counted its
          items yet has `note` alone, which already says which phase it is in. */}
      {progress != null && <Progress value={progress} label={`${title} sync progress`} />}

      <div className="relative mt-auto flex items-center gap-2 pt-0.5">
        <Button variant="outline" size="sm" onClick={() => onSync?.(connection)}>
          {files ? <Upload size={12} aria-hidden /> : <RefreshCw size={12} aria-hidden />}
          {files ? 'Add files' : connected ? 'Sync now' : auth === 'token' ? 'Add token' : 'Connect'}
        </Button>
        <Button
          variant="ghost" size="icon-sm" className="ml-auto text-muted-foreground"
          aria-label={`Options for ${title}`} aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
        >
          <EllipsisVertical size={14} aria-hidden />
        </Button>
        {menuOpen && (
          <div className="absolute right-0 bottom-9 z-10 w-40 rounded-lg border border-border bg-popover p-1">
            <button
              type="button"
              onClick={() => { setMenuOpen(false); onRemove?.(connection) }}
              className="w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-destructive hover:bg-accent"
            >
              {files ? 'Remove and forget' : 'Disconnect and remove'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
