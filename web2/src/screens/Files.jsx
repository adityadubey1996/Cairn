import { useState } from 'react'
import { ArrowRight, Check, Upload } from 'lucide-react'
import { AddFiles } from '@/components/AddFiles'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { usePipelineRun } from '@/lib/usePipelineRun'
import { Sources } from './connect/Sources'

const PHASE_LABEL = {
  scrape: 'Extracting files', links: 'Fetching linked sources',
  ingest: 'Indexing extracted content', absorb: 'Absorbing into the wiki', push: 'Saving files',
}

export function Files({ projectId, projectName, onNavigate }) {
  const [adding, setAdding] = useState(false)
  const [runId, setRunId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const { running, pct, run } = usePipelineRun(runId, {
    onFinish: (row) => {
      setRunId(null)
      setNotice(row.status === 'ok'
        ? { text: 'Files extracted. Their absorption state appears in the library below.' }
        : { text: row.error || `File processing ${row.status}. Check Pipeline for details.`, error: true })
      setRefresh((n) => n + 1)
    },
  })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Files"
        subtitle={<>Connected files, from extraction to wiki. Repository files are in{' '}
          <button type="button" className="text-primary hover:underline" onClick={() => onNavigate?.('repos')}>Repos</button>.</>}
        actions={<>
          <Button variant="outline" size="sm" onClick={() => onNavigate?.('pipeline')}>
            Automation <ArrowRight size={13} aria-hidden />
          </Button>
          <Button size="sm" onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
            <Upload size={13} aria-hidden /> {adding ? 'Close upload' : 'Add files'}
          </Button>
        </>}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto w-full max-w-(--page-width)">
          {adding && <AddFiles className="mb-5" projectId={projectId} onStaged={({ runId: id }) => {
            setRunId(id); setNotice(null); setRefresh((n) => n + 1)
          }} />}
          {running && <div className="mb-4 rounded-lg border border-primary/30 bg-card p-3" role="status">
            <p className="text-sm">{PHASE_LABEL[run?.phase] ?? 'Waiting for the file worker'}
              {run?.items_seen ? ` · ${run.items_written}/${run.items_seen}` : ''}</p>
            {!!run?.items_seen && <Progress className="mt-2" value={pct} label="File processing progress" />}
          </div>}
          {notice && <p role={notice.error ? 'alert' : 'status'} className={`mb-4 flex items-center gap-2 text-sm ${notice.error ? 'text-destructive' : 'text-success'}`}>
            {!notice.error && <Check size={14} aria-hidden />}{notice.text}
          </p>}
          <Sources projectId={projectId} onNavigate={onNavigate} refreshKey={refresh} onAddFiles={() => setAdding(true)} />
        </div>
      </div>
    </div>
  )
}
