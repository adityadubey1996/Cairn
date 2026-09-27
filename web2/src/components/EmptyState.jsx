import { cn } from '@/lib/utils'

// The treatment is shared; the copy never is. Every screen passes its own
// sentence — "No data" is not an acceptable message anywhere in this product.
export function EmptyState({ icon: Icon, title, detail, action, className, titleClassName, detailClassName }) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2.5 px-6 py-10 text-center', className)}>
      {Icon && (
        <span className="flex size-10 items-center justify-center rounded-[10px] border border-border bg-card text-muted-foreground">
          <Icon size={20} aria-hidden />
        </span>
      )}
      <div className={cn('max-w-[320px] text-sm leading-relaxed text-foreground', titleClassName)}>{title}</div>
      {detail && <div className={cn('max-w-[320px] text-xs leading-relaxed text-muted-foreground', detailClassName)}>{detail}</div>}
      {action}
    </div>
  )
}
