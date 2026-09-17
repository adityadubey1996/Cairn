import { useEffect, useState } from 'react'
import { TriangleAlert } from 'lucide-react'
import { Button } from './ui/button'

// Destructive actions name exactly what goes, and take a typed confirmation.
// Never a single click.
export function ConfirmDialog({ open, title, detail, confirmWord, confirmLabel = 'Delete', onConfirm, onCancel }) {
  const [typed, setTyped] = useState('')

  useEffect(() => { if (open) setTyped('') }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onCancel?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null
  const armed = !confirmWord || typed.trim() === confirmWord

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-6"
      role="dialog" aria-modal="true" aria-label={title}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel?.() }}
    >
      <div className="w-full max-w-[420px] rounded-[10px] border border-destructive/60 bg-card p-4">
        <div className="mb-1.5 flex items-center gap-2">
          <TriangleAlert size={15} className="text-destructive" aria-hidden />
          <span className="text-[13.5px] font-semibold text-destructive">{title}</span>
        </div>
        <p className="text-[12px] leading-relaxed text-muted-foreground">{detail}</p>

        {confirmWord && (
          <>
            <label htmlFor="confirm-word" className="mt-3 mb-1.5 block text-[12px] text-muted-foreground">
              Type <b className="text-foreground">{confirmWord}</b> to confirm
            </label>
            <input
              id="confirm-word" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus
              className="w-full rounded-lg border border-destructive/50 bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-destructive"
            />
          </>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>Cancel</Button>
          <Button
            size="sm" disabled={!armed} onClick={() => onConfirm?.()}
            className="border border-destructive/60 bg-transparent text-destructive hover:bg-destructive/10"
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
