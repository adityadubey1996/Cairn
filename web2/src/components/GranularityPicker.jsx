import { cn } from '@/lib/utils'
import { Button } from './ui/button'

const OPTIONS = [
  { id: 'monthly', label: 'Monthly' }, { id: 'weekly', label: 'Weekly' },
  { id: 'quarterly', label: 'Quarterly' }, { id: 'tags', label: 'Tagged releases only' },
  { id: 'every', label: 'Every commit' },
]

function Radio({ checked, title, detail, onSelect, children }) {
  return (
    <label className={cn(
      'flex cursor-pointer gap-2.5 rounded-lg border p-2.5',
      checked ? 'border-primary bg-primary/5' : 'border-border',
    )}>
      <input type="radio" checked={checked} onChange={onSelect} className="sr-only" />
      <span className={cn(
        'mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border-[1.5px]',
        checked ? 'border-primary' : 'border-muted-foreground',
      )}>
        {checked && <span className="size-[7px] rounded-full bg-primary" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm">{title}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
        {checked && children}
      </span>
    </label>
  )
}

// GitHub-specific. The estimate is the same "know the cost before you spend
// it" pattern absorb's dry run already uses — not a new idea for this screen.
export function GranularityPicker({ mode = 'snapshot', granularity = 'monthly', estimate, onChange, onConfirm }) {
  return (
    <div className="flex flex-col gap-2">
      <Radio
        checked={mode === 'snapshot'}
        title="Latest snapshot"
        detail="Fast and cheap — just the current code."
        onSelect={() => onChange?.({ mode: 'snapshot', granularity })}
      />
      <Radio
        checked={mode === 'history'}
        title="Map commit history"
        detail="Walks the codebase's history at an interval you choose — costs more, takes longer."
        onSelect={() => onChange?.({ mode: 'history', granularity })}
      >
        <span className="mt-2.5 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Granularity</span>
          <select
            value={granularity}
            onChange={(e) => onChange?.({ mode: 'history', granularity: e.target.value })}
            className="rounded-md border border-border bg-background px-2 py-1 text-sm outline-none focus:border-primary"
          >
            {OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </span>

        {estimate && (
          <span className="mt-2.5 flex flex-wrap items-center gap-5 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5">
            {[['Checkpoints', estimate.checkpoints], ['Estimated tokens', estimate.tokens], ['Estimated cost', estimate.cost]]
              .map(([label, value]) => (
                <span key={label} className="block">
                  <span className="block text-xs text-muted-foreground">{label}</span>
                  <b className="block text-base tabular-nums text-warning">{value}</b>
                </span>
              ))}
            <Button size="sm" className="ml-auto" onClick={(e) => { e.preventDefault(); onConfirm?.() }}>
              Start import
            </Button>
          </span>
        )}
        <span className="mt-2 block text-xs text-muted-foreground">
          Estimated on your own key's current rate. Nothing is charged until you confirm.
        </span>
      </Radio>
    </div>
  )
}
