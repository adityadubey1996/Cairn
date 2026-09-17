import { useEffect, useRef, useState } from 'react'
import { FolderUp, Upload } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { bytes } from '@/lib/format'
import { Button } from './ui/button'
import { Spinner } from './ui/spinner'
import { Progress } from './ui/progress'

// Files handed over by hand. This is deliberately NOT a connector: there is no
// account behind it, nothing to sign into and nothing to re-sync, so it never
// appears in the connector grid. It still rides the upload connection
// underneath, which is what gives a batch a real run to report progress from.
//
// onStaged({ connectionId, runId }) fires once the bytes are on the server and
// the extraction run has started. The host follows that run — the bar here
// ends at "handed over", not at "ingested".

export function AddFiles({ projectId, onStaged, className }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // { sent, total, files } while a batch is in flight. Bytes, not files: the
  // request is one multipart POST, so bytes-sent is the only thing that
  // actually moves during it.
  const [at, setAt] = useState(null)
  const [dragging, setDragging] = useState(false)
  const aliveRef = useRef(true)
  const fileInputRef = useRef(null)
  const folderInputRef = useRef(null)

  // Reset on every (re)mount — under StrictMode's dev-only mount->cleanup->
  // remount cycle a setup that only returns a cleanup never flips this back to
  // true, and every guarded update below would no-op for the component's whole
  // real lifetime. See Settings.jsx for how this was found.
  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const send = async (fileList) => {
    const files = Array.from(fileList).map((file) => ({
      path: file.webkitRelativePath || file.name, file,
    }))
    if (!files.length) return
    setBusy(true)
    setError(null)
    setAt({ sent: 0, total: 0, files: files.length })
    try {
      const existing = await api.listConnections(projectId)
      const conn = existing.find((c) => c.kind === 'upload')
        ?? await api.createConnection(projectId, 'upload', 'Files', {})
      await api.stageUpload(conn.id, files, (sent, total) => {
        if (aliveRef.current) setAt({ sent, total, files: files.length })
      })
      const { runId } = await api.syncConnection(conn.id)
      if (!aliveRef.current) return
      onStaged?.({ connectionId: conn.id, runId, files: files.length })
    } catch (e) {
      if (aliveRef.current) setError(e.message)
    } finally {
      if (aliveRef.current) { setBusy(false); setAt(null) }
    }
  }

  const drop = (e) => {
    e.preventDefault()
    setDragging(false)
    if (busy) return
    // ponytail: a dropped folder arrives as a directory entry that only
    // webkitGetAsEntry recursion can walk, and as a zero-byte File that would
    // fail extraction with nothing useful to say. Caught here and pointed at
    // the folder button instead; walk the tree if folder-dropping is asked for.
    const entries = Array.from(e.dataTransfer.items ?? [])
      .map((item) => item.webkitGetAsEntry?.())
    if (entries.some((entry) => entry && !entry.isFile)) {
      setError('Drop files, or use “Choose a folder” for a whole folder.')
      return
    }
    if (e.dataTransfer.files?.length) send(e.dataTransfer.files)
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={drop}
      className={cn(
        'flex flex-col gap-2.5 rounded-[10px] border border-dashed p-4 transition-colors',
        dragging ? 'border-primary bg-primary/5' : 'border-border bg-card/40',
        className,
      )}
    >
      <div className="flex items-start gap-2.5">
        <Upload size={15} className="mt-px shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-[13px] text-muted-foreground">
          Drop files here, or pick them. Word, PowerPoint, PDF, Excel, CSV and plain text are read;
          anything else is listed as not read.
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => fileInputRef.current?.click()}>
          <Upload size={12} aria-hidden /> Choose files
        </Button>
        <Button
          size="sm" variant="outline" disabled={busy}
          onClick={() => folderInputRef.current?.click()}
        >
          <FolderUp size={12} aria-hidden /> Choose a folder
        </Button>
      </div>
      <input
        ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { send(e.target.files); e.target.value = '' }}
      />
      <input
        ref={folderInputRef} type="file" multiple hidden webkitdirectory=""
        onChange={(e) => { send(e.target.files); e.target.value = '' }}
      />

      {busy && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
            {/* No byte total until the first progress event lands, and a 0% bar
                would read as stalled — so the spinner covers exactly that gap,
                and the bar takes over after it. */}
            {at?.total ? null : <Spinner className="size-3.5" />}
            <span>
              Uploading {at?.files ?? 0}
              {' '}{at?.files === 1 ? 'file' : 'files'}
              {at?.total ? ` · ${bytes(at.sent)} of ${bytes(at.total)}` : '…'}
            </span>
          </div>
          {!!at?.total && (
            <Progress label="Upload progress" value={(at.sent / at.total) * 100} />
          )}
        </div>
      )}

      {error && <div className="text-[12px] text-destructive">{error}</div>}
    </div>
  )
}
