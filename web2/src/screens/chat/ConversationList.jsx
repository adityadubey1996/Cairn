import { useState } from 'react'
import { MessageSquare, Plus, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { SkeletonList } from '@/components/SkeletonList'

const DAY = 86_400_000

function groupByRecency(convs) {
  const startOfToday = new Date().setHours(0, 0, 0, 0)
  const weekAgo = startOfToday - 6 * DAY
  const groups = { Today: [], 'This week': [], Older: [] }
  for (const c of convs) {
    const t = new Date(c.createdAt).getTime()
    if (t >= startOfToday) groups.Today.push(c)
    else if (t >= weekAgo) groups['This week'].push(c)
    else groups.Older.push(c)
  }
  return groups
}

// Belongs to Ask. Search is ephemeral and never adds a row here — the note at
// the bottom says so, because an empty-looking list during Search is otherwise
// the obvious wrong conclusion.
export function ConversationList({ conversations, loading, activeId, onSelect, onNew, onDelete, mode }) {
  const [hovered, setHovered] = useState(null)
  const groups = groupByRecency(conversations ?? [])

  return (
    <aside className="hidden w-[280px] shrink-0 flex-col border-r border-border bg-background p-2.5 md:flex">
      <div className="flex items-center justify-between px-1.5 pb-2.5">
        <span className="text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">Conversations</span>
        <Button variant="outline" size="xs" onClick={onNew}>
          <Plus size={12} aria-hidden />New
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading
          ? <SkeletonList rows={5} icon={false} />
          : Object.entries(groups).map(([label, items]) => items.length > 0 && (
            <div key={label}>
              <div className="px-2 pt-2.5 pb-1 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
                {label}<span className="ml-1 opacity-70">{items.length}</span>
              </div>
              {items.map((c) => (
                <div
                  key={c.id}
                  onMouseEnter={() => setHovered(c.id)} onMouseLeave={() => setHovered(null)}
                  onClick={() => onSelect?.(c.id)}
                  className={cn(
                    'flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-[13px]',
                    c.id === activeId && mode === 'ask'
                      ? 'bg-accent text-foreground'
                      : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                  )}
                >
                  <MessageSquare size={13} className="shrink-0 opacity-60" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{c.title}</span>
                  <button
                    type="button"
                    aria-label={`Delete “${c.title}”`}
                    onClick={(e) => { e.stopPropagation(); onDelete?.(c.id) }}
                    className={cn('shrink-0 rounded p-0.5 hover:text-destructive', hovered === c.id ? 'visible' : 'invisible')}
                  >
                    <X size={13} aria-hidden />
                  </button>
                </div>
              ))}
            </div>
          ))}
      </div>

      <p className="border-t border-border px-1.5 pt-2.5 text-[11px] leading-relaxed text-muted-foreground">
        Conversations belong to Ask. Search is ephemeral and never adds one.
      </p>
    </aside>
  )
}
