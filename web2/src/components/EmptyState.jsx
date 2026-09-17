import { cn } from '@/lib/utils'

// The treatment is shared; the copy never is. Every screen passes its own
// sentence — "No data" is not an acceptable message anywhere in this product.
export function EmptyState({ icon: Icon, title, detail, action, className }) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2.5 px-6 py-10 text-center', className)}>
      {Icon && (
        <span className="flex size-10 items-center justify-center rounded-[10px] border border-border bg-card text-muted-foreground">
          <Icon size={20} aria-hidden />
        </span>
      )}
      <div className="max-w-[320px] text-[13px] leading-relaxed text-foreground">{title}</div>
      {detail && <div className="max-w-[320px] text-[11.5px] leading-relaxed text-muted-foreground">{detail}</div>}
      {action}
    </div>
  )
}
