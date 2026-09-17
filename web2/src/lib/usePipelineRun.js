import { useEffect, useRef, useState } from 'react'
import * as api from '@/api'

const POLL_MS = 2000
const MAX_LINES = 2000

/**
 * Follow one pipeline run: its phases, counters and log tail.
 *
 * Pass null to follow nothing. Passing a new id resets everything, so a card
 * that syncs twice does not show the previous run's log.
 *
 * A recursive setTimeout rather than setInterval: a finished run never changes
 * again, so the loop simply stops rescheduling itself instead of needing a
 * flag to suppress an interval that keeps firing. Errors reschedule — a single
 * failed poll during a redeploy should not abandon a run that is still going.
 */
export function usePipelineRun(runId, { onFinish, pollMs = POLL_MS } = {}) {
  const [run, setRun] = useState(null)
  const [lines, setLines] = useState([])
  const [error, setError] = useState(null)
  const seq = useRef(0)

  // Held in a ref so a caller passing an inline arrow does not restart the
  // poll loop on every render.
  const onFinishRef = useRef(onFinish)
  useEffect(() => { onFinishRef.current = onFinish }, [onFinish])

  useEffect(() => {
    if (!runId) {
      setRun(null); setLines([]); setError(null); seq.current = 0
      return
    }
    seq.current = 0
    setLines([])
    setError(null)
    // Seeded optimistically: the POST that produced this id already told us a
    // run is in flight, so the UI can say so before the first poll lands.
    setRun({ id: runId, status: 'running', phase: null, phases: {},
             items_seen: 0, items_written: 0 })

    let alive = true
    let timer = null

    const loop = async () => {
      if (!alive) return
      let keepGoing = true
      try {
        const row = await api.pipelineRun(runId)
        if (!alive) return
        setRun(row)
        const l = await api.pipelineRunLog(runId, seq.current)
        if (!alive) return
        if (l.lines.length) {
          seq.current = l.last
          setLines((prev) => [...prev, ...l.lines].slice(-MAX_LINES))
        }
        if (row.status !== 'running') {
          keepGoing = false
          onFinishRef.current?.(row)
        }
      } catch (e) {
        if (!alive) return
        setError(String(e.message || e))
      }
      if (alive && keepGoing) timer = setTimeout(loop, pollMs)
    }

    loop()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [runId, pollMs])

  const running = run?.status === 'running'
  const pct = run?.items_seen
    ? Math.round((run.items_written / run.items_seen) * 100)
    : 0

  return { run, lines, error, running, pct }
}
