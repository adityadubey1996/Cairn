import { useEffect, useState } from 'react'
import { ArrowRight, Check, Search, TriangleAlert } from 'lucide-react'
import { AddFiles } from '@/components/AddFiles'
import { ContentResults } from '@/components/ContentResults'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { usePipelineRun } from '@/lib/usePipelineRun'
// The viewer lives with the Sources list it was built for; this screen reuses
// it rather than growing a second one that would drift from it.
import { SourceViewer } from './connect/Sources'

// Files handed over by hand get their own top-level screen, beside Repos: they
// are not a connector, there is no account behind them and nothing re-syncs.
// This screen is only the ACT of adding them — the files themselves live in
// Connect > Sources with everything else, under the Files chip.
// A connector sync is five phases (scrape · links · ingest · absorb · push,
// see scripts/pipeline_run.py), and only the first one is about your files.
// Labelling the whole run "Reading files" reads as a stall at best and a lie at
// worst once it is off fetching the project's URL backlog.
const PHASE_LABEL = {
  scrape: 'Reading your files',
  links: 'Fetching links found across this project',
  ingest: 'Indexing',
  absorb: 'Writing up',
  push: 'Saving',
}

export function Files({ projectId, projectName, onNavigate }) {
  const [runId, setRunId] = useState(null)
  const [last, setLast] = useState(null)      // the batch that just finished
  const [sent, setSent] = useState(0)        // files handed over, for the summary
  const [error, setError] = useState(null)
  const [input, setInput] = useState('')
  const [q, setQ] = useState('')
  const [viewing, setViewing] = useState(null)

  useEffect(() => {
    const timer = setTimeout(() => setQ(input.trim()), 180)
    return () => clearTimeout(timer)
  }, [input])

  // ponytail: the run is followed only while this screen is mounted. Navigating
  // away mid-batch does not stop it — it finishes server-side and the rows turn
  // up in Sources; you just lose the live bar. Lift the run id to App if
  // watching it from another screen is ever wanted.
  const { running, pct, run } = usePipelineRun(runId, {
    onFinish: (row) => {
      setRunId(null)
      // Counted from what was handed over, not from row.items_* — those carry
      // the LAST phase's numbers, which is push, not the files.
      if (row.status === 'ok') setLast({ files: sent })
      else setError(row.error || `reading the files ${row.status}`)
    },
  })

  const toSources = () => onNavigate?.('connect', 'sources')

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-baseline gap-3 px-6 pt-4">
        <h1 className="text-[15px] font-semibold">Files</h1>
        <span className="text-[12.5px] text-muted-foreground">{projectName ?? '—'}</span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-8">
        <div className="mx-auto w-full max-w-2xl">
          <p className="pb-3 text-[13px] leading-relaxed text-muted-foreground">
            Anything on this computer that no connector reaches. Handed over once —
            unlike a connector, there is no account behind it and nothing to re-sync.
            What lands here shows up in Connect&nbsp;&gt;&nbsp;Sources with the rest.
          </p>

          <AddFiles
            projectId={projectId}
            onStaged={({ runId: id, files }) => {
              setLast(null); setError(null); setSent(files); setRunId(id)
            }}
          />

          {running && (
            <div className="mt-3 rounded-lg border border-border bg-card/40 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[12.5px]">
                  {PHASE_LABEL[run?.phase] ?? 'Starting'}
                  {run?.items_seen ? ` · ${run.items_written}/${run.items_seen}` : ''}
                </span>
                {run?.phase === 'links' && (
                  <span className="text-[11.5px] text-muted-foreground">
                    your files are already in — this part is the rest of the project
                  </span>
                )}
              </div>
              {!!run?.items_seen && (
                <Progress className="mt-2" value={pct} label="File ingest progress" />
              )}
            </div>
          )}

          {last && (
            <div className="mt-3 flex flex-wrap items-center gap-2.5 rounded-lg border border-border bg-card/40 px-3 py-2.5">
              <Check size={14} className="shrink-0 text-success" aria-hidden />
              {/* A file with no extractor is recorded as failed rather than
                  dropped, so a file that could not be read is visible in Sources
                  under "not synced" rather than silently missing. */}
              <span className="text-[12.5px]">
                {last.files} {last.files === 1 ? 'file' : 'files'} added
              </span>
              <Button variant="outline" size="xs" className="ml-auto" onClick={toSources}>
                See them in Sources
                <ArrowRight size={12} aria-hidden />
              </Button>
            </div>
          )}

          {error && (
            <p className="mt-3 flex items-start gap-1.5 text-[12.5px] text-destructive">
              <TriangleAlert size={14} className="mt-[2px] shrink-0" aria-hidden />
              <span>{error}</span>
            </p>
          )}

          {!running && !last && (
            <button
              type="button" onClick={toSources}
              className="mt-3 flex items-center gap-1.5 text-[12px] text-primary hover:underline"
            >
              See the files already added
              <ArrowRight size={12} aria-hidden />
            </button>
          )}

          {/* Inside the files, not across their names: the scan opens each one
              and returns the text around the match. Scoped to uploads, so a
              Drive document with the same word cannot crowd yours out. */}
          <div className="mt-6 flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2 focus-within:border-primary">
            <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
            <input
              value={input} onChange={(e) => setInput(e.target.value)}
              placeholder="Search inside your files…"
              aria-label="Search inside your files"
              className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
            />
          </div>

          {viewing && (
            <div className="mt-2">
              <SourceViewer
                source={viewing} projectId={projectId}
                onClose={() => setViewing(null)}
              />
            </div>
          )}

          <ContentResults
            className="mt-2" projectId={projectId} q={q} kind="upload"
            onOpen={(row) => setViewing((at) => (at?.id === row.id ? null : row))}
          />
        </div>
      </div>
    </div>
  )
}
