import { useEffect, useRef, useState } from 'react'
import { LayoutGrid, Search, Send } from 'lucide-react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ModeToggle } from '@/components/ModeToggle'
import { AskThread } from './chat/AskThread'
import { ConversationList } from './chat/ConversationList'
import { SearchPane } from './chat/SearchPane'

export function Chat({ projectId, projectName }) {
  const [mode, setMode] = useState('search')      // Search is free, so it opens first
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [pending, setPending] = useState(null)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const abortRef = useRef(null)

  const forced = forcedState()
  const starters = api.STARTERS?.[projectId] ?? []

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.listConversations({ projectId })
      .then((rows) => {
        if (cancelled) return
        setConversations(rows)
        setActiveId((id) => id ?? rows[0]?.id ?? null)
      })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId])

  useEffect(() => {
    if (!activeId) { setMessages([]); return }
    let cancelled = false
    api.conversationMessagesFull(activeId).then((rows) => !cancelled && setMessages(rows))
    return () => { cancelled = true }
  }, [activeId])

  // Abort any in-flight answer when the thread changes or the screen unmounts,
  // so a stream can never write into a conversation the user has left.
  useEffect(() => () => abortRef.current?.abort(), [])

  // Sequence-guarded: a slow earlier query must never overwrite a newer one's
  // results, and "Nothing matched" must not flash while a search is in flight.
  const seqRef = useRef(0)
  const runSearch = async (q) => {
    setQuery(q)
    const seq = ++seqRef.current
    if (!q.trim()) { setResults([]); setSearching(false); return }
    setSearching(true)
    try {
      const { rows } = await api.search({ projectId, q })
      if (seq === seqRef.current) setResults(rows)
    } finally {
      if (seq === seqRef.current) setSearching(false)
    }
  }

  const ask = async (text) => {
    if (!text.trim() || pending) return
    setDraft('')
    setMessages((m) => [...m, { id: `u${Date.now()}`, role: 'user', text }])
    const controller = new AbortController()
    abortRef.current = controller
    setPending({ stage: null, text: '' })
    try {
      const answer = await api.sendMessage(activeId ?? 'v1', text, {
        signal: controller.signal,
        onStage: (stage) => setPending((p) => ({ ...p, stage: { ...p?.stage, ...stage } })),
        onToken: (t) => setPending((p) => ({ ...p, text: t })),
      })
      setMessages((m) => [...m, answer])
    } catch (e) {
      if (e.name !== 'AbortError') setError(e)
    } finally {
      setPending(null)
      abortRef.current = null
    }
  }

  const state = forced ?? (loading ? 'loading' : error ? 'error' : 'ready')

  return (
    <div className="flex min-h-0 flex-1">
      <ConversationList
        conversations={forced === 'empty' ? [] : conversations}
        loading={state === 'loading'} activeId={activeId} mode={mode}
        onSelect={setActiveId} onNew={() => { setActiveId(null); setMode('ask') }}
        onDelete={(id) => setConversations((c) => c.filter((x) => x.id !== id))}
      />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-5">
          <div className="mx-auto w-full max-w-3xl">
            {mode === 'ask' ? (
              <AskThread
                state={state === 'empty' ? 'ready' : state}
                messages={forced === 'empty' ? [] : messages}
                pending={pending} starters={starters} projectName={projectName}
                onStarter={ask} onRetry={() => setError(null)}
              />
            ) : (
              <SearchPane
                state={state === 'empty' ? 'ready' : searching ? 'loading' : state}
                query={forced === 'empty' ? 'zzzz' : query}
                rows={forced === 'empty' ? [] : results}
                starters={starters}
                onStarter={(s) => runSearch(s)}
                onAskInstead={() => { setMode('ask'); ask(query) }}
                onRetry={() => setError(null)}
              />
            )}
          </div>
        </div>

        <div className="px-5 pb-5">
          <div className="mx-auto w-full max-w-3xl">
            <div className="flex items-center gap-3 pb-2.5">
              <ModeToggle value={mode} onChange={setMode} />
              <span className="ml-auto flex items-center gap-1.5 rounded-[9px] border border-border px-2 py-px text-[11px] text-muted-foreground">
                <LayoutGrid size={11} aria-hidden />
                scoped to {projectName ?? '—'}
              </span>
            </div>

            {mode === 'search' ? (
              <div className="flex items-center gap-2.5 rounded-[10px] border border-border bg-card px-3.5 py-2.5 focus-within:border-primary">
                <Search size={16} className="shrink-0 text-muted-foreground" aria-hidden />
                <input
                  value={query} onChange={(e) => runSearch(e.target.value)}
                  placeholder="Search everything you’ve connected…"
                  aria-label="Search sources"
                  className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
                />
              </div>
            ) : (
              <div className="flex items-end gap-2.5">
                <Textarea
                  rows={1} value={draft} placeholder="Ask the brain…"
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(draft) }
                  }}
                  className="min-h-13 resize-none py-3.5"
                />
                <Button
                  size="icon" className="size-13 shrink-0" aria-label="Send message"
                  disabled={!draft.trim() || !!pending} onClick={() => ask(draft)}
                >
                  <Send size={18} aria-hidden />
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
