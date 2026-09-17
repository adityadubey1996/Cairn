import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Check, Plug, Upload } from 'lucide-react'
import * as api from '@/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { AddFiles } from '@/components/AddFiles'
import { ConnectorPicker } from './connect/ConnectorPicker'

// First run only: shown instead of the shell when no key is saved and nothing
// has ever synced. Three full-page steps, one centred ~480px column.
// onDone('chat' | 'connect') hands control back to the app.

const KEY_CONSOLE = 'https://console.anthropic.com/settings/keys'
const FIRST_SYNC_FILES = 34

function Steps({ step }) {
  return (
    <div className="flex items-center gap-2.5 pb-6">
      <span className="text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">
        Step {step} of 3
      </span>
      <span className="flex flex-1 gap-1">
        {[1, 2, 3].map((n) => (
          <span key={n} className={cn('h-px flex-1', n <= step ? 'bg-primary' : 'bg-border')} />
        ))}
      </span>
    </div>
  )
}

function Headline({ title, children }) {
  return (
    <>
      <h1 className="text-[15px] font-semibold">{title}</h1>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">{children}</p>
    </>
  )
}

function KeyStep({ apiKey, onKey, tested, testing, onTest, onContinue }) {
  return (
    <>
      <Headline title="Bring your own Claude key.">
        This product never has a key of its own. Every answer and every wiki article is
        paid for by the key you paste here, and it stays on this machine.
      </Headline>

      <label htmlFor="claude-key" className="mt-5 mb-1.5 block text-[12px] text-muted-foreground">
        Claude API key
      </label>
      <input
        id="claude-key" type="password" value={apiKey} placeholder="sk-ant-…"
        onChange={(e) => onKey(e.target.value)}
        className="w-full rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
      />
      <a
        href={KEY_CONSOLE} target="_blank" rel="noreferrer"
        className="mt-2 inline-flex items-center gap-1 text-[12px] text-primary hover:underline"
      >
        Get a key from the Anthropic console
        <ArrowUpRight size={12} aria-hidden />
      </a>

      <div className="mt-5 flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={!apiKey.trim() || testing} onClick={onTest}>
          {testing && <Spinner className="size-3.5" />}
          Test key
        </Button>
        <Button size="sm" disabled={!apiKey.trim()} onClick={onContinue}>Continue</Button>
      </div>

      {tested && (
        <p className={cn('mt-2.5 flex items-center gap-1.5 text-[12px]', tested.ok ? 'text-success' : 'text-destructive')}>
          {tested.ok && <Check size={12} aria-hidden />}
          {tested.ok ? tested.detail : `Couldn’t use that key — ${tested.detail}`}
        </p>
      )}
    </>
  )
}

function PathCard({ icon: Icon, title, detail, onClick }) {
  return (
    <button
      type="button" onClick={onClick}
      className="flex flex-col items-start gap-1.5 rounded-[10px] border border-border bg-card p-3.5 text-left transition-colors hover:border-primary"
    >
      <Icon size={16} className="text-muted-foreground" aria-hidden />
      <span className="text-[13.5px] font-semibold">{title}</span>
      <span className="text-[12px] leading-relaxed text-muted-foreground">{detail}</span>
    </button>
  )
}

// Two ways in, not one. A connector signs in once and keeps syncing; files are
// handed over once and never sync again. Neither is a special case of the
// other, so neither is buried inside the other's picker.
function ConnectStep({
  projectName, onProjectName, connected, filesAdded,
  onConnected, onFilesAdded, onContinue,
}) {
  const [path, setPath] = useState(null)   // null · 'connector' · 'files'
  const project = { id: 'new', name: projectName.trim() || 'My first project' }
  const ready = connected.length > 0 || filesAdded > 0

  return (
    <>
      <Headline title="Bring in your first content.">
        Connect an account and it keeps syncing on its own, or hand over files from
        this computer. Either one is enough to carry on.
      </Headline>

      <label htmlFor="project-name" className="mt-5 mb-1.5 block text-[12px] text-muted-foreground">
        Name this project
      </label>
      <input
        id="project-name" value={projectName} onChange={(e) => onProjectName(e.target.value)}
        className="w-full rounded-lg border border-border bg-card px-2.5 py-2 text-[13.5px] outline-none focus:border-primary"
      />

      {path === null ? (
        <div className="mt-5 grid gap-2 sm:grid-cols-2">
          <PathCard
            icon={Plug} title="Connect a source"
            detail="Drive, Chat, GitHub, Jira. One sign-in, then it keeps itself up to date."
            onClick={() => setPath('connector')}
          />
          <PathCard
            icon={Upload} title="Add files"
            detail="Word, PDF, slides, spreadsheets — single files or a whole folder."
            onClick={() => setPath('files')}
          />
        </div>
      ) : (
        <button
          type="button" onClick={() => setPath(null)}
          className="mt-5 flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft size={12} aria-hidden />
          Other ways to bring content in
        </button>
      )}

      {path === 'connector' && (
        <ConnectorPicker
          className="mt-4"
          projects={[project]} projectId={project.id}
          connectedKinds={connected}
          onConnected={({ kind }) => onConnected(connected.includes(kind) ? connected : [...connected, kind])}
          onRemove={(kind) => onConnected(connected.filter((k) => k !== kind))}
        />
      )}

      {path === 'files' && (
        <AddFiles
          className="mt-4"
          projectId={project.id}
          onStaged={({ files }) => onFilesAdded(filesAdded + files)}
        />
      )}

      {ready && (
        <p className="mt-4 flex items-center gap-1.5 text-[12.5px] text-success">
          <Check size={13} aria-hidden />
          {[
            connected.length && `${connected.length} source${connected.length === 1 ? '' : 's'} connected`,
            filesAdded && `${filesAdded} file${filesAdded === 1 ? '' : 's'} added`,
          ].filter(Boolean).join(' · ')}
        </p>
      )}

      <div className="mt-6 flex items-center gap-2">
        <Button size="sm" disabled={!ready} onClick={onContinue}>Continue</Button>
        {!ready && (
          <span className="text-[12px] text-muted-foreground">
            Connect a source or add files to carry on.
          </span>
        )}
      </div>
    </>
  )
}

function SyncStep({ count, done, onDone }) {
  return (
    <>
      <Headline title={done ? 'First sync finished.' : 'Pulling in your content…'}>
        {done
          ? 'Search works now and costs nothing. Ask costs tokens on your own key, so it waits for you to ask.'
          : 'This keeps running in the background — you don’t have to sit here for it.'}
      </Headline>

      <div className="mt-5 flex items-center gap-2.5 rounded-[10px] border border-border bg-card p-3.5">
        {done
          ? <Check size={15} className="text-success" aria-hidden />
          : <Spinner className="size-4 text-muted-foreground" />}
        <span className="text-[13.5px] tabular-nums">{count} files so far</span>
      </div>

      <div className="mt-5 flex flex-col items-start gap-2.5">
        <Button size="sm" disabled={!done} onClick={() => onDone('chat')}>Go to Chat</Button>
        <button
          type="button" onClick={() => onDone('connect')}
          className="text-[12px] text-primary hover:underline"
        >
          I’ll wait — take me to Connect instead
        </button>
      </div>
    </>
  )
}

export function Onboarding({ onDone }) {
  const [step, setStep] = useState(1)
  const [apiKey, setApiKey] = useState('')
  const [tested, setTested] = useState(null)
  const [testing, setTesting] = useState(false)
  const [projectName, setProjectName] = useState('My first project')
  const [connected, setConnected] = useState([])
  const [filesAdded, setFilesAdded] = useState(0)
  const [count, setCount] = useState(0)
  const aliveRef = useRef(true)
  const done = count >= FIRST_SYNC_FILES

  // Reset on every (re)mount — under StrictMode's dev-only mount->cleanup->
  // remount cycle, a setup that only returns a cleanup never flips this back
  // to true, so every guarded update below would silently no-op for the
  // component's whole real lifetime. See Settings.jsx for how this was found.
  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  // The live count is the only motion on step 3. Real progress arrives with the
  // backend in Batch 6; until then it climbs to the first sync's file count and
  // stops there — the timer is not left running behind the finished state.
  useEffect(() => {
    if (step !== 3 || done) return
    const id = setTimeout(
      () => setCount((n) => Math.min(FIRST_SYNC_FILES, n + 1 + Math.floor(Math.random() * 3))),
      260,
    )
    return () => clearTimeout(id)
  }, [step, count, done])

  const test = async () => {
    setTesting(true)
    setTested(null)
    try {
      const r = await api.testProvider({ preset: 'claude', key: apiKey })
      if (aliveRef.current) setTested(r)
    } catch (e) {
      if (aliveRef.current) setTested({ ok: false, detail: e.message })
    } finally {
      if (aliveRef.current) setTesting(false)
    }
  }

  return (
    <div className="h-screen overflow-y-auto bg-background">
      <div className="mx-auto w-full max-w-[480px] px-5 py-14">
        <Steps step={step} />

        {step === 1 && (
          <KeyStep
            apiKey={apiKey} onKey={setApiKey} tested={tested} testing={testing}
            onTest={test} onContinue={() => setStep(2)}
          />
        )}

        {step === 2 && (
          <ConnectStep
            projectName={projectName} onProjectName={setProjectName}
            connected={connected} onConnected={setConnected}
            filesAdded={filesAdded} onFilesAdded={setFilesAdded}
            onContinue={() => setStep(3)}
          />
        )}

        {step === 3 && <SyncStep count={count} done={done} onDone={onDone} />}
      </div>
    </div>
  )
}
