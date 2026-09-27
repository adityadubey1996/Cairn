import { useEffect, useRef, useState } from 'react'
import { EllipsisVertical } from 'lucide-react'
import { cn } from '@/lib/utils'

// The secondary actions a card has but should not spend a button on. Items are
// { label, onClick, disabled, danger }. Closes on Escape and on a click
// outside, because a menu that only closes on its own trigger strands people.
export function OverflowMenu({ label, items, className, align = 'right', drop = 'up' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    const onClick = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onClick)
    }
  }, [open])

  const usable = items.filter(Boolean)
  if (!usable.length) return null

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        type="button"
        aria-label={label} aria-expanded={open} aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
          open && 'bg-accent text-foreground',
        )}
      >
        <EllipsisVertical size={14} aria-hidden />
      </button>
      {open && (
        <div
          role="menu"
          className={cn(
            'absolute z-20 min-w-44 rounded-lg border border-border bg-popover p-1 shadow-lg',
            align === 'right' ? 'right-0' : 'left-0',
            drop === 'up' ? 'bottom-9' : 'top-9',
          )}
        >
          {usable.map((item) => (
            <button
              key={item.label}
              type="button" role="menuitem" disabled={item.disabled}
              title={item.title}
              onClick={() => { setOpen(false); item.onClick?.() }}
              className={cn(
                'w-full rounded-md px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent',
                item.danger ? 'text-destructive' : 'text-foreground',
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
