import { useEffect, useRef, useState } from 'react'
import { Cpu, Search, Send, Settings2, Square, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ModeToggle } from '@/components/ModeToggle'
import { AskThread } from './chat/AskThread'
import { ConversationList } from './chat/ConversationList'
import { SearchPane } from './chat/SearchPane'

export function Chat({ projectId, projectName, onNavigate }) {
  const [mode, setMode] = useState('ask')
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [pending, setPending] = useState(null)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [loading, setLoading] = useState(true)
  const [threadLoading, setThreadLoading] = useState(false)
  const [error, setError] = useState(null)
  const [provider, setProvider] = useState(null)
  const [stopped, setStopped] = useState(false)
  const [reload, setReload] = useState(0)
  const abortRef = useRef(null)
  const skipLoadRef = useRef(null)
  const scrollRef = useRef(null)
  const pinnedRef = useRef(true)
  const seqRef = useRef(0)
  const forced = forcedState()
  const starters = ['Summarize what is in my knowledge base', 'What decisions have we made recently?', 'Which open questions need follow-up?']

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.listConversations({ projectId })
      .then((rows) => { if (!cancelled) setConversations(rows) })
      .catch((e) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    api.getSettings().then((s) => { if (!cancelled) setProvider(s.effective ?? s.provider) }).catch(() => {})
    return () => { cancelled = true }
  }, [projectId, reload])

  useEffect(() => {
    if (!activeId) { setMessages([]); return }
    if (skipLoadRef.current === activeId) { skipLoadRef.current = null; return }
    let cancelled = false
    setThreadLoading(true)
    api.conversationMessagesFull(activeId)
      .then((rows) => { if (!cancelled) setMessages(rows) })
      .catch((e) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setThreadLoading(false) })
    return () => { cancelled = true }
  }, [activeId])

  useEffect(() => () => abortRef.current?.abort(), [])
  useEffect(() => {
    if (pinnedRef.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, pending, threadLoading])

  const stop = () => {
    abortRef.current?.abort()
    if (pending?.text) setMessages((m) => [...m, { id: `stopped-${Date.now()}`, role: 'assistant', text: pending.text }])
    setPending(null); setStopped(true)
  }
  const selectConversation = (id) => {
    if (id && id === activeId) { setMode('ask'); return }
    abortRef.current?.abort(); abortRef.current = null
    setPending(null); setError(null); setStopped(false); setMessages([])
    setActiveId(id); setMode('ask'); pinnedRef.current = true
  }
  const runSearch = async (q) => {
    setQuery(q); setError(null)
    const seq = ++seqRef.current
    if (!q.trim()) { setResults([]); setSearching(false); return }
    setSearching(true)
    try {
      const { rows } = await api.search({ projectId, q })
      if (seq === seqRef.current) setResults(rows)
    } catch (e) {
      if (seq === seqRef.current) setError(e.message)
    } finally {
      if (seq === seqRef.current) setSearching(false)
    }
  }
  const ask = async (value) => {
    const text = value.trim()
    if (!text || pending || threadLoading) return
    setDraft(''); setError(null); setStopped(false); pinnedRef.current = true
    setMessages((m) => [...m, { id: `u${Date.now()}`, role: 'user', text }])
    const controller = new AbortController()
    abortRef.current = controller
    setPending({ stage: null, text: '' })
    try {
      const answer = await api.sendMessage(activeId ?? 'new', text, {
        projectId, signal: controller.signal,
        onConversation: (id) => {
          if (controller.signal.aborted || id === activeId) return
          skipLoadRef.current = id; setActiveId(id)
          setConversations((rows) => rows.some((r) => r.id === id) ? rows : [{ id, title: text.slice(0, 70), createdAt: new Date().toISOString() }, ...rows])
        },
        onStage: (stage) => { if (!controller.signal.aborted) setPending((p) => ({ ...p, stage: { ...p?.stage, ...stage } })) },
        onToken: (t) => { if (!controller.signal.aborted) setPending((p) => ({ ...p, text: t })) },
      })
      if (controller.signal.aborted) return
      setMessages((m) => [...m, answer])
      api.listConversations({ projectId }).then(setConversations).catch(() => {})
    } catch (e) {
      if (e.name !== 'AbortError') { setError(e.message); setDraft(text) }
    } finally {
      if (abortRef.current === controller) { setPending(null); abortRef.current = null }
    }
  }
  const removeConversation = async (id) => {
    try {
      await api.deleteConversation(id)
      setConversations((rows) => rows.filter((r) => r.id !== id))
      if (activeId === id) selectConversation(null)
    } catch (e) { setError(e.message) }
  }
  const model = provider?.model
  const local = provider?.preset === 'ollama'
  const state = forced ?? (loading || threadLoading ? 'loading' : 'ready')

  return <div className="flex min-h-0 flex-1">
    <ConversationList conversations={forced === 'empty' ? [] : conversations} loading={loading}
      activeId={activeId} mode={mode} onSelect={selectConversation} onNew={() => selectConversation(null)} onDelete={removeConversation} />
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1"><h1 className="text-[17px] font-semibold">Chat with {projectName}</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">Answers grounded in your absorbed knowledge.</p>
        </div>
        <Button variant="ghost" size="xs" onClick={() => onNavigate?.('settings')} title="Configure your answer model">
          <Cpu size={13} aria-hidden />{model ? `${local ? 'Ollama · ' : ''}${model}` : 'Set up a model'}<Settings2 size={12} aria-hidden />
        </Button>
        <Button variant="outline" size="xs" className="md:hidden" onClick={() => selectConversation(null)}>New chat</Button>
      </header>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-5"
        onScroll={(e) => { const el = e.currentTarget; pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100 }}>
        <div className="mx-auto w-full max-w-3xl">
          {mode === 'ask' ? <AskThread state={state === 'empty' ? 'ready' : state} messages={forced === 'empty' ? [] : messages}
            pending={pending} starters={starters} projectName={projectName} onStarter={ask} onRetry={() => { setError(null); setReload((n) => n + 1) }}
            onGoFiles={() => onNavigate?.('files')} />
            : <SearchPane state={searching ? 'loading' : 'ready'} query={query} rows={results} starters={[]}
              onStarter={runSearch} onAskInstead={() => { setMode('ask'); ask(query) }} onRetry={() => runSearch(query)} />}
          {error && <div role="alert" className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
            <p className="flex items-start gap-2 text-[13px] text-destructive"><TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden />{error}</p>
            <div className="mt-2 flex gap-2"><Button variant="outline" size="xs" onClick={() => onNavigate?.('settings')}>Check model settings</Button>
              <Button variant="ghost" size="xs" onClick={() => setError(null)}>Dismiss</Button></div>
          </div>}
          {stopped && <p role="status" className="mb-4 text-xs text-muted-foreground">Answer stopped. You can continue with another question.</p>}
        </div>
      </div>
      <div className="border-t border-border px-5 pt-3 pb-4">
        <div className="mx-auto w-full max-w-3xl">
          <div className="mb-3 flex items-center gap-3"><ModeToggle value={mode} onChange={setMode} />
            <span className="ml-auto text-[11px] text-muted-foreground">{mode === 'ask' ? local ? 'Generated locally with Ollama' : 'Using your configured model' : 'Search extracted source text'}</span>
          </div>
          {mode === 'search' ? <div className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3.5 py-3 focus-within:border-primary">
            <Search size={16} className="text-muted-foreground" aria-hidden />
            <input value={query} onChange={(e) => runSearch(e.target.value)} placeholder="Search your connected files…" aria-label="Search sources"
              className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground" />
          </div> : <div className="flex items-end gap-2.5">
            <Textarea aria-label="Question about your knowledge base" rows={2} value={draft} placeholder="Ask a question about your knowledge…"
              onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); ask(draft) }
              }} className="max-h-48 min-h-16 resize-y py-3" />
            {pending ? <Button variant="outline" size="icon" className="size-12" aria-label="Stop answer" onClick={stop}><Square size={16} aria-hidden /></Button>
              : <Button size="icon" className="size-12" aria-label="Send message" disabled={!draft.trim() || threadLoading || loading} onClick={() => ask(draft)}><Send size={18} aria-hidden /></Button>}
          </div>}
          {mode === 'ask' && <p className="mt-2 text-[11px] text-muted-foreground">Enter to send · Shift + Enter for a new line. Check source citations for important details.</p>}
        </div>
      </div>
    </div>
  </div>
}
