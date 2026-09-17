import { useEffect, useState } from 'react'
import { FileSearch, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { EmptyState } from './EmptyState'
import { SkeletonList } from './SkeletonList'
import { SourceResultRow } from './SourceResultRow'

// Searches INSIDE files, not across their names: a live scan that opens each
// source and returns the text around the match. Deliberately has no search box
// of its own — both callers already own one, and two boxes for one query is
// the bug this would otherwise introduce.
//
// `kind` scopes the scan to one connector. That is what makes it "search my
// files" on the Files screen, and "search inside Drive" when that chip is
// active on Sources.
export function ContentResults({ projectId, q, kind = null, onOpen, className }) {
  const [state, setState] = useState('idle')
  const [rows, setRows] = useState([])

  useEffect(() => {
    if (!q?.trim()) { setRows([]); setState('idle'); return }
    let cancelled = false
    setState('loading')
    api.search({ projectId, q, kind })
      .then((got) => { if (!cancelled) { setRows(got.rows); setState('ready') } })
      .catch(() => { if (!cancelled) setState('error') })
    return () => { cancelled = true }
  }, [projectId, q, kind])

  if (state === 'idle') return null
  if (state === 'loading') return <div className={className}><SkeletonList rows={5} /></div>

  if (state === 'error') {
    return (
      <EmptyState
        className={className} icon={TriangleAlert}
        title="Couldn’t search inside the files."
        detail="Nothing was lost — the scan just didn’t come back."
      />
    )
  }

  if (!rows.length) {
    return (
      <EmptyState
        className={className} icon={FileSearch}
        title={`Nothing inside these files mentions “${q}”.`}
        detail="This looks at what the files say, not what they are called."
      />
    )
  }

  return (
    <div className={className}>
      <div className="pb-1 text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
        {rows.length}{rows.length === 30 ? '+' : ''} inside file contents
      </div>
      {rows.map((row) => <SourceResultRow key={row.id} row={row} onOpen={onOpen} />)}
    </div>
  )
}
