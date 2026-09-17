// DESIGN.md's Vocabulary Rule: a status is Verified Green, Stale Amber or
// Conflict Red, plus neutral Pencil for "never run". A new status reuses one
// of these — it never gets a colour of its own.
export const STATUS = {
  ok: { tone: 'ok', label: 'synced' },
  running: { tone: 'warn', label: 'syncing…' },
  error: { tone: 'bad', label: 'error' },
  never_run: { tone: 'idle', label: 'never run' },
  not_configured: { tone: 'idle', label: 'not configured' },
}

// Written out in full so Tailwind's scanner sees every class it must emit.
export const DOT = {
  ok: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-destructive',
  idle: 'bg-muted-foreground',
}
export const TEXT = {
  ok: 'text-success',
  warn: 'text-warning',
  bad: 'text-destructive',
  idle: 'text-muted-foreground',
}
export const BORDER = {
  ok: 'border-success/40',
  warn: 'border-warning/40',
  bad: 'border-destructive/40',
  idle: 'border-border',
}

export const statusOf = (s) => STATUS[s] ?? { tone: 'idle', label: s ?? 'unknown' }
