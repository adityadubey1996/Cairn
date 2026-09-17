import { useEffect, useState } from 'react'
import { Check, Eye, EyeOff } from 'lucide-react'
import * as api from '@/api'
import { ago } from '@/lib/format'
import { Button } from './ui/button'

// Each preset shows only the fields it actually needs: Ollama has no key at
// all, Custom adds a base URL and a model name. Showing a disabled key field
// for a local provider would be a lie about how it works.
//
// `keyEnv` is the .env variable the server falls back to for this provider
// (server/llm.py). It is surfaced because a key already in .env means nothing
// has to be pasted here at all, and an empty-looking form is otherwise
// indistinguishable from an unconfigured one.
const PRESETS = [
  { id: 'claude', label: 'Claude', needsKey: true, note: 'default', keyEnv: 'ANTHROPIC_API_KEY' },
  { id: 'ollama', label: 'Ollama (local)', needsKey: false },
  { id: 'groq', label: 'Groq', needsKey: true, keyEnv: 'GROQ_API_KEY' },
  { id: 'deepseek', label: 'DeepSeek', needsKey: true, keyEnv: 'DEEPSEEK_API_KEY' },
  { id: 'openrouter', label: 'OpenRouter', needsKey: true, keyEnv: 'OPENROUTER_API_KEY' },
  { id: 'gemini', label: 'Gemini', needsKey: true, keyEnv: 'GEMINI_API_KEY' },
  { id: 'custom', label: 'Custom', needsKey: true, custom: true, keyEnv: 'LLM_API_KEY' },
]

// Which local model to answer with is a real choice (an 8B replies in a second,
// a 70B is far better and much slower), so it is picked from what is actually
// installed rather than typed from memory. When nothing is installed the panel
// turns into the one command that fixes it.
function OllamaPanel({ value, onChange }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.getOllamaStatus()
      .then((s) => !cancelled && setStatus(s))
      .catch(() => !cancelled && setError(true))
    return () => { cancelled = true }
  }, [])

  // Adopt the server's suggestion rather than sending an empty model: the
  // server would pick the same one, and showing a blank select implies no
  // choice was made.
  useEffect(() => {
    if (status?.suggested && !value.model) onChange?.({ ...value, model: status.suggested })
  }, [status?.suggested])  // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return <p className="mt-2.5 text-[12px] text-destructive">Couldn’t read the Ollama status.</p>
  }
  if (!status) {
    return <p className="mt-2.5 text-[12px] text-muted-foreground">Checking Ollama…</p>
  }

  if (!status.reachable) {
    return (
      <div className="mt-2.5 text-[12px]">
        <span className="rounded-[9px] border border-destructive/40 px-2 py-px text-destructive">
          not reachable
        </span>
        <p className="mt-2 text-muted-foreground">
          Nothing is answering at <code className="font-mono">{status.base}</code>. Start it with{' '}
          <code className="font-mono text-foreground">ollama serve</code>, or point{' '}
          <code className="font-mono">OLLAMA_BASE</code> at wherever it runs.
        </p>
      </div>
    )
  }

  if (!status.models.length) {
    return (
      <div className="mt-2.5 text-[12px]">
        <span className="rounded-[9px] border border-warning/40 px-2 py-px text-warning">
          no chat model
        </span>
        <p className="mt-2 text-muted-foreground">
          Ollama is running but has nothing that can hold a conversation. Pull one:
        </p>
        <ul className="mt-2 flex flex-col gap-1.5">
          {status.recommended.map((r) => (
            <li key={r.model} className="flex flex-wrap items-baseline gap-2">
              <code className="rounded bg-background px-1.5 py-0.5 font-mono text-[11.5px] text-foreground">
                {r.command}
              </code>
              <span className="text-muted-foreground">{r.why}</span>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <>
      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[12px]">
        <span className="rounded-[9px] border border-success/40 px-2 py-px text-success">reachable</span>
        <span className="text-muted-foreground">No key needed — Ollama runs on this machine.</span>
      </div>
      <label htmlFor="ollama-model" className="mt-3 mb-1.5 block text-[12px] text-muted-foreground">
        Model
      </label>
      <select
        id="ollama-model"
        value={value.model ?? status.suggested ?? ''}
        onChange={(e) => onChange?.({ ...value, model: e.target.value })}
        className="w-full rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
      >
        {status.models.map((m) => (
          <option key={m} value={m}>
            {m}{m === status.suggested ? ' — suggested' : ''}
          </option>
        ))}
      </select>
    </>
  )
}

export function ProviderPresetPicker({ value = {}, onChange, onTest, testResult, effective, className }) {
  const [revealed, setRevealed] = useState(false)
  const preset = PRESETS.find((p) => p.id === (value.preset ?? 'claude')) ?? PRESETS[0]

  return (
    <div className={className}>
      <label htmlFor="provider" className="mb-1.5 block text-[12px] text-muted-foreground">Provider</label>
      <select
        id="provider"
        value={preset.id}
        onChange={(e) => onChange?.({ ...value, preset: e.target.value })}
        className="w-full rounded-lg border border-border bg-card px-2.5 py-2 text-[13.5px] outline-none focus:border-primary"
      >
        {PRESETS.map((p) => (
          <option key={p.id} value={p.id}>{p.label}{p.note ? ` — ${p.note}` : ''}</option>
        ))}
      </select>

      {!preset.needsKey && <OllamaPanel value={value} onChange={onChange} />}

      {preset.needsKey && (
        <>
          <label htmlFor="provider-key" className="mt-3 mb-1.5 block text-[12px] text-muted-foreground">API key</label>
          <div className="flex gap-2">
            <input
              id="provider-key"
              type={revealed ? 'text' : 'password'}
              value={value.key ?? ''}
              placeholder={value.keyMasked ?? 'paste your key'}
              onChange={(e) => onChange?.({ ...value, key: e.target.value })}
              className="min-w-0 flex-1 rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
            />
            <Button variant="outline" size="sm" onClick={() => setRevealed((v) => !v)}>
              {revealed ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
              {revealed ? 'Hide' : 'Reveal'}
            </Button>
            <Button variant="outline" size="sm" onClick={() => onTest?.(value)}>Test key</Button>
          </div>
          {preset.keyEnv && (
            <p className="mt-1.5 text-[11.5px] text-muted-foreground">
              Or leave this empty and put <code className="font-mono">{preset.keyEnv}</code> in your{' '}
              <code className="font-mono">.env</code> — the server picks it up either way.
            </p>
          )}
        </>
      )}

      {preset.custom && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <input
            aria-label="Base URL" placeholder="https://api.example.com/v1"
            value={value.baseUrl ?? ''} onChange={(e) => onChange?.({ ...value, baseUrl: e.target.value })}
            className="rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
          />
          <input
            aria-label="Model name" placeholder="model-name"
            value={value.model ?? ''} onChange={(e) => onChange?.({ ...value, model: e.target.value })}
            className="rounded-lg border border-border bg-card px-2.5 py-2 font-mono text-[13px] outline-none focus:border-primary"
          />
        </div>
      )}

      <div className="mt-2.5 text-[12px]">
        {testResult?.ok
          ? <span className="flex items-center gap-1.5 text-success"><Check size={12} aria-hidden />{testResult.detail}</span>
          : testResult
            ? <span className="text-destructive">{testResult.detail}</span>
            : <span className="text-muted-foreground">Last verified {ago(value.verifiedAt)}</span>}
      </div>

      {/* What a question would use right now. Differs from the form whenever the
          key came from .env, or nothing was ever saved. */}
      {effective?.preset && (
        <p className="mt-1.5 text-[11.5px] text-muted-foreground">
          Answering with <span className="text-foreground">{effective.preset}</span>
          {effective.model ? <> · <code className="font-mono">{effective.model}</code></> : null}
          {effective.source === 'env' ? ' (from .env)' : null}
        </p>
      )}
      {effective && !effective.preset && (
        <p className="mt-1.5 text-[11.5px] text-warning">{effective.detail}</p>
      )}
    </div>
  )
}
