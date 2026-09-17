import { BookPlus, ExternalLink, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago, bytes } from '@/lib/format'
import { Button } from './ui/button'
import { ConnectorIcon } from './ConnectorIcon'

// The row form of the same file-vs-link distinction CitationChip makes inline.
// A link offers both the cached copy and the original URL; a file has one.
export function SourceRow({ source, onOpenCached, onOpenOriginal, right, className }) {
  const isLink = source.type === 'link'
  // A failed row is an item the connector saw and could not bring in. There is
  // no file behind it, so it shows the reason instead of an Open button —
  // offering one would 404 against storage.
  const failed = source.status === 'failed'
  return (
    <div className={cn('flex items-center gap-3 py-2', className)}>
      <ConnectorIcon kind={isLink ? 'links' : source.kind} size={15} className="text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px]">{source.name}</span>
          {isLink && !failed && (
            <span className="shrink-0 rounded-[9px] border border-border px-2 py-px text-[11px] text-muted-foreground">
              web page
            </span>
          )}
          {failed && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-[9px] border border-destructive/50 px-2 py-px text-[11px] text-destructive">
              <TriangleAlert size={10} aria-hidden />
              not synced
            </span>
          )}
          {/* Marked for a wiki write-up but not written up yet. Gone once the
              run publishes an article from it. */}
          {source.queued && !failed && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-[9px] border border-primary/50 px-2 py-px text-[11px] text-primary">
              <BookPlus size={10} aria-hidden />
              queued
            </span>
          )}
        </div>
        <div className={cn('truncate text-[11.5px]',
                           failed ? 'text-destructive/80' : 'text-muted-foreground')}>
          {failed ? source.error : source.detail}
        </div>
      </div>
      {!isLink && !failed && (
        <span className="shrink-0 text-[11.5px] text-muted-foreground">{bytes(source.bytes)}</span>
      )}
      <span className="shrink-0 text-[11.5px] text-muted-foreground">{ago(source.scrapedAt)}</span>
      {failed ? null : right ?? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="outline" size="xs" onClick={() => onOpenCached?.(source)}>
            {isLink ? 'Cached' : 'Open'}
          </Button>
          {isLink && (
            <Button variant="outline" size="xs" onClick={() => onOpenOriginal?.(source)}>
              <ExternalLink size={11} aria-hidden />
              Original
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
