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
  const absorption = failed ? 'failed' : (source.absorptionState ?? (source.queued ? 'queued' : 'extracted'))
  const labels = { extracted: 'Extracted', queued: 'Queued', absorbing: 'Absorbing', running: 'Absorbing',
    absorbed: 'Absorbed', done: 'Absorbed', failed: 'Failed', excluded: 'Excluded', partial: 'Partial', interrupted: 'Interrupted' }
  return (
    <div className={cn('flex items-center gap-3 py-2', className)}>
      <ConnectorIcon kind={isLink ? 'links' : source.kind} size={15} className="text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate text-[13px]">{source.name}</span>
          {isLink && !failed && (
            <span className="shrink-0 rounded-[9px] border border-border px-2 py-px text-[11px] text-muted-foreground">
              web page
            </span>
          )}
          <span className={cn('inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-px text-[11px]',
            absorption === 'failed' ? 'bg-destructive/10 text-destructive'
              : ['absorbed', 'done'].includes(absorption) ? 'bg-success/10 text-success'
                : ['queued', 'absorbing', 'running'].includes(absorption) ? 'bg-primary/10 text-primary'
                  : 'bg-muted text-muted-foreground')}>
            {absorption === 'failed' && <TriangleAlert size={10} aria-hidden />}
            {absorption === 'queued' && <BookPlus size={10} aria-hidden />}
            {absorption === 'failed' ? failed ? 'Extraction failed' : 'Absorption failed' : labels[absorption] ?? absorption}
          </span>
          {source.absorptionPolicy !== 'inherit' && source.absorptionPolicy && (
            <span className="text-[11px] text-muted-foreground">{source.absorptionPolicy === 'exclude' ? 'Excluded from wiki' : 'Manual absorption'}</span>
          )}
        </div>
        <div className={cn('truncate text-[11.5px]',
                           failed ? 'text-destructive/80' : 'text-muted-foreground')}>
          {failed ? source.error : source.absorptionError || source.detail}
        </div>
      </div>
      {!isLink && !failed && (
        <span className="hidden shrink-0 text-[11.5px] text-muted-foreground sm:inline">{bytes(source.bytes)}</span>
      )}
      <span className="hidden shrink-0 text-[11.5px] text-muted-foreground md:inline">{ago(source.scrapedAt)}</span>
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
