import { FileText, Globe } from 'lucide-react'
import { cn } from '@/lib/utils'

// Two variants, one shape. The variant comes from the source's own type —
// never guessed from the path string. A link opens the real original URL,
// not the cached copy, so it is obvious at a glance which you are reading.
export function CitationChip({ label, type = 'file', href, onOpen, className }) {
  const external = type === 'link'
  const Icon = external ? Globe : FileText
  return (
    <a
      href={href ?? '#'}
      onClick={onOpen}
      target={external ? '_blank' : undefined}
      rel={external ? 'noreferrer' : undefined}
      title={external ? `${label} — opens the original page` : `${label} — opens the cited version`}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-md border bg-card px-1.5 py-px align-baseline',
        'font-mono text-xs text-primary no-underline hover:border-primary',
        external ? 'border-dashed border-border' : 'border-border',
        className,
      )}
    >
      <Icon size={11} className="shrink-0" aria-hidden />
      <span className="truncate">{label}</span>
    </a>
  )
}
