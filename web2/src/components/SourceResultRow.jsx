import { ExternalLink } from 'lucide-react'
import { ago } from '@/lib/format'
import { Button } from './ui/button'
import { ConnectorIcon } from './ConnectorIcon'

// One hit from a content scan (sources.find): name, a snippet around the
// needle, and when it was scraped. Shared by Chat's Search pane, Connect >
// Sources in contents mode, and the Files screen — three places showing the
// same rows, which is why this is not three copies.
export function SourceResultRow({ row, onOpen }) {
  const isLink = row.type === 'link'
  return (
    <div className="flex gap-3 border-b border-border py-3">
      <span className="flex size-7.5 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground">
        <ConnectorIcon kind={isLink ? 'links' : row.kind} size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13.5px] font-medium">{row.name}</span>
          {isLink && (
            <span className="shrink-0 rounded-[9px] border border-border px-2 py-px text-[11px] text-muted-foreground">
              web page
            </span>
          )}
          <span className="ml-auto shrink-0 text-[11.5px] text-muted-foreground">scraped {ago(row.scrapedAt)}</span>
        </div>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{row.snippet}</p>
      </div>
      <Button variant="outline" size="xs" className="self-center" onClick={() => onOpen?.(row)}>
        <ExternalLink size={11} aria-hidden />
        {isLink ? 'Original' : 'Open'}
      </Button>
    </div>
  )
}
