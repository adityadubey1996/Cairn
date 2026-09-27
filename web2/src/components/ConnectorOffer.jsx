import { ArrowRight, Check, KeyRound, LogIn, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ConnectorIcon } from './ConnectorIcon'

// A connector you could add — not one you have. Its own tile because the two
// are different objects: a connection reports status, last sync and counts,
// and an offer has none of those. Reusing the connection card meant a 500px
// box holding two lines of text, a status dot that always said the same thing,
// and a button stranded on its own row.

const AUTH = {
  oauth: { icon: LogIn, label: 'Sign in', hint: 'one consent, no key to paste' },
  token: { icon: KeyRound, label: 'Token', hint: 'you paste a key you create' },
  browser: { icon: LogIn, label: 'Browser', hint: 'signs in through a real browser' },
  none: { icon: Sparkles, label: 'No setup', hint: 'nothing to configure' },
}

export function ConnectorOffer({ item, added = false, unavailable = false, actionLabel, onPick }) {
  const auth = AUTH[item.auth] ?? AUTH.token
  const AuthIcon = auth.icon

  return (
    <button
      type="button"
      onClick={unavailable ? undefined : onPick}
      disabled={unavailable}
      aria-label={`${item.name} — ${auth.label}`}
      className={cn(
        'group flex w-full flex-col gap-2 rounded-xl border p-3 text-left transition-colors',
        unavailable
          ? 'cursor-not-allowed border-border/60 bg-card/40 opacity-55'
          : 'border-border bg-card hover:border-primary/40 hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2.5">
        <span className={cn(
          'flex size-8 shrink-0 items-center justify-center rounded-lg border border-[#262b36] bg-background transition-colors',
          !unavailable && 'group-hover:border-primary/40',
        )}>
          <ConnectorIcon kind={item.kind} size={16} />
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {item.name}
        </span>
        {added && (
          <span className="flex shrink-0 items-center gap-1 text-xs text-success">
            <Check size={12} aria-hidden />Added
          </span>
        )}
      </div>

      {/* Two lines, clamped: descriptions come from the registry and vary from
          four words to a sentence, and a tile that grows to fit the longest one
          leaves every other tile half empty. */}
      <p className="line-clamp-2 min-h-8 text-xs leading-relaxed text-muted-foreground">
        {unavailable ? `${item.desc} — no feeder behind this yet` : item.desc}
      </p>

      <div className="flex items-center gap-1.5 border-t border-border/70 pt-2">
        <AuthIcon size={12} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-xs font-medium text-foreground">{auth.label}</span>
        <span className="truncate text-xs text-muted-foreground">· {auth.hint}</span>
        {!unavailable && (
          <span className="ml-auto flex shrink-0 items-center gap-1 text-xs font-medium text-primary opacity-0 transition-opacity group-hover:opacity-100">
            {actionLabel ?? 'Add'}<ArrowRight size={12} aria-hidden />
          </span>
        )}
      </div>
    </button>
  )
}
