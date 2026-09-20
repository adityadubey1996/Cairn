import { FileText, Globe } from 'lucide-react'
import { cn } from '@/lib/utils'

// The icon describes the source. The destination may be either the live page
// or its captured revision, so the tooltip describes the actual link target.
export function CitationChip({ label, type = 'file', href, onOpen, className }) {
  const external = type === 'link'
  const citedVersion = !external || !!onOpen || href?.startsWith('/api/sources/')
  const Icon = external ? Globe : FileText
  return (
    <a
      href={href ?? '#'}
      onClick={onOpen}
      target={external && !citedVersion ? '_blank' : undefined}
      rel={external && !citedVersion ? 'noreferrer' : undefined}
      title={citedVersion ? `${label} — opens the cited version` : `${label} — opens the original page`}
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
