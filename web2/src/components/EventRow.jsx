import { AtSign, ChevronRight, RefreshCw, Send, Sparkles, TriangleAlert, UserCheck } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago } from '@/lib/format'
import { ConnectorIcon } from './ConnectorIcon'

// One row shape, two icon sets. Connect > Timeline distinguishes sync from
// absorb; People distinguishes what a person did. Absorb is amber because
// amber is already the system's "this spends money" colour, and sync is
// neutral because a completed log entry is not interactive.
const TIMELINE_ICONS = {
  sync: { Icon: RefreshCw, tone: 'text-muted-foreground border-border' },
  absorb: { Icon: Sparkles, tone: 'text-warning border-warning/30' },
  error: { Icon: TriangleAlert, tone: 'text-destructive border-destructive/30' },
}

// A run still in flight is Stale Amber, per the Vocabulary Rule — the same
// tone absorb already uses, not a colour of its own.
const RUNNING_ICON = { Icon: RefreshCw, tone: 'text-warning border-warning/30' }

const ROLE_ICONS = { authored: Sparkles, sent: Send, mentioned: AtSign, assigned: UserCheck }

// Phases carry their own timings, so a run-level duration would only repeat
// them. It is the whole detail available on an old-runner row, which recorded
// no phases at all.
function chipsFor(event) {
  if (event.phases?.length) return event.phases
  return event.seconds == null ? [] : [{ name: 'took', detail: `${event.seconds}s` }]
}

export function EventRow({ event, variant = 'timeline', className, expanded, onToggle, children }) {
  const person = variant === 'person'
  const running = event.status === 'running'
  const { Icon, tone } = running
    ? RUNNING_ICON
    : TIMELINE_ICONS[event.kind] ?? TIMELINE_ICONS.sync
  const RoleIcon = ROLE_ICONS[event.role] ?? Sparkles
  const chips = person ? [] : chipsFor(event)

  return (
    <div className={cn('border-b border-border py-3 last:border-b-0', className)}>
      <div className="flex items-start gap-3">
        <span className={cn(
          'flex size-6.5 shrink-0 items-center justify-center rounded-[7px] border',
          person ? 'border-border bg-card text-muted-foreground' : tone,
        )}>
          {person
            ? <ConnectorIcon kind={event.kind} size={13} />
            : <Icon size={13} className={cn(running && 'animate-spin')} aria-hidden />}
        </span>

        <div className="min-w-0 flex-1">
          {person && (
            <div className="mb-0.5 flex items-center gap-2">
              <span className="rounded-full border border-border px-1.5 text-xs uppercase tracking-[0.03em] text-muted-foreground">
                {event.role}
              </span>
              <RoleIcon size={11} className="text-muted-foreground" aria-hidden />
            </div>
          )}

          {onToggle ? (
            <button
              type="button" onClick={onToggle} aria-expanded={!!expanded}
              className="flex w-full items-start gap-1.5 text-left text-sm hover:text-primary"
            >
              <ChevronRight
                size={12} aria-hidden
                className={cn('mt-1 shrink-0 text-muted-foreground transition-transform',
                  expanded && 'rotate-90')}
              />
              <span>{event.text}</span>
            </button>
          ) : (
            <div className="text-sm">{event.text}</div>
          )}

          {chips.length > 0 && (
            <div className={cn('mt-1.5 flex flex-wrap gap-1.5', onToggle && 'pl-[18px]')}>
              {chips.map((p) => (
                <span
                  key={p.name}
                  className="rounded-full border border-border px-1.5 py-px text-xs text-muted-foreground"
                >
                  <span className="text-foreground/80">{p.name}</span>{' '}{p.detail}
                  {p.seconds != null && ` · ${p.seconds}s`}
                </span>
              ))}
            </div>
          )}

          {event.links?.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-2 text-xs">
              {event.links.map((l) => (
                <a key={l.path} href={`#wiki/${l.path}`} className="text-primary hover:underline">{l.label}</a>
              ))}
            </div>
          )}

          {children}
        </div>

        <span className="shrink-0 text-xs text-muted-foreground">{ago(event.at)}</span>
      </div>
    </div>
  )
}
