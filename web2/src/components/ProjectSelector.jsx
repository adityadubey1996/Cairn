import { useEffect, useRef, useState } from 'react'
import { ChevronDown, LayoutGrid, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'

// No "All projects" option, by design: projects are hard-isolated, so data is
// always viewed one project at a time. With a single project this control
// still shows — it is where project names get managed.
export function ProjectSelector({ projects = [], value, onChange, onCreate, collapsed = false }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const current = projects.find((p) => p.id === value) ?? projects[0]

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox" aria-expanded={open}
        title={collapsed ? current?.name : undefined}
        className={cn(
          'flex w-full items-center gap-2.5 rounded-lg border border-border bg-background text-left',
          'hover:border-primary',
          collapsed ? 'justify-center p-2' : 'px-2.5 py-2',
        )}
      >
        <LayoutGrid size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{current?.name ?? 'No project'}</span>
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" aria-hidden />
          </>
        )}
      </button>

      {open && (
        <div role="listbox" className="absolute top-full left-0 z-20 mt-1 w-full min-w-[200px] rounded-lg border border-border bg-popover p-1">
          {projects.map((p) => (
            <button
              key={p.id} role="option" aria-selected={p.id === current?.id} type="button"
              onClick={() => { onChange?.(p.id); setOpen(false) }}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px]',
                p.id === current?.id ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
              )}
            >
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              {p.sourceCount != null && <span className="shrink-0 text-[11px] opacity-70">{p.sourceCount}</span>}
            </button>
          ))}
          <div className="my-1 h-px bg-border" />
          <button
            type="button"
            onClick={() => { setOpen(false); onCreate?.() }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] text-muted-foreground hover:bg-accent/60 hover:text-foreground"
          >
            <Plus size={13} aria-hidden />
            New project
          </button>
        </div>
      )}
    </div>
  )
}
