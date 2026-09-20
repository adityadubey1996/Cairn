import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Check, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { ConnectorIcon } from '@/components/ConnectorIcon'

// The one connector connection flow (spec §6), used by onboarding's grid and
// by Connect > Health. It is a panel, not a page: the host renders it inline
// under the card that was clicked, so success and failure land in place.
//
// Props:
//   connector      { kind, name, desc, auth }  — a CONNECTOR_CATALOGUE item
//   projects       [{ id, name }]              — what the connection can belong to
//   projectId      currently chosen project    (onboarding passes its not-yet-created one)
//   onProjectId    (id) => void
//   connectedKinds string[] — already-authorized kinds, for the one-click re-consent path
//   onConnected    ({ kind, projectId, mode, granularity }) => void — fires once, when the
//                  flow finishes (GitHub: after the history choice; everything else: on success)
//   onCancel       () => void
//   onAuthorize    optional ({ kind, projectId }) => Promise — the REAL provider navigation.
//                  Absent (fixtures), the hand-off is simulated; ?force=error makes it fail.

const TOKEN_HELP = {
  github: { href: 'https://github.com/settings/tokens', label: 'Create a token on GitHub' },
  jira: { href: 'https://id.atlassian.com/manage-profile/security/api-tokens', label: 'Create an API token on Atlassian' },
}

const groupOf = (kind) => api.CONNECTOR_CATALOGUE.find((g) => g.items.some((i) => i.kind === kind))

function Field({ id, label, hint, type = 'text', value, onChange, placeholder }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[12px] text-muted-foreground">{label}</label>
      <input
        id={id} type={type} value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'w-full rounded-lg border border-border bg-card px-2.5 py-2 text-[13.5px] outline-none focus:border-primary',
          type === 'password' && 'font-mono text-[13px]',
        )}
      />
      {hint && <p className="mt-1.5 text-[11.5px] text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function ConnectFlow({
  connector, projects = [], projectId, onProjectId,
  connectedKinds = [], onConnected, onCancel, onAuthorize, className,
}) {
  const [status, setStatus] = useState('form')   // form · connecting · error · history · done
  const [failure, setFailure] = useState(null)
  const [token, setToken] = useState('')
  const [site, setSite] = useState('')
  const [email, setEmail] = useState('')
  const [urls, setUrls] = useState('')
  const [maxItems, setMaxItems] = useState('20')
  const aliveRef = useRef(true)
  const timerRef = useRef(null)

  const { kind, name, desc, auth } = connector
  const group = groupOf(kind)
  const provider = group && !group.advanced && group.items.length > 1 ? group.group : name
  const reconsent = auth === 'oauth' && group?.items.some((i) => i.kind !== kind && connectedKinds.includes(i.kind))

  // Reset on every (re)mount — under StrictMode's dev-only mount->cleanup->
  // remount cycle, a setup that only returns a cleanup never flips this back
  // to true, so every guarded update below (including the one that leaves
  // "Connecting…") would silently no-op for the component's whole real
  // lifetime. See Settings.jsx for how this was found.
  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false; clearTimeout(timerRef.current) }
  }, [])

  // A different connector means a different flow — never inherit the last one's
  // half-typed token or its error.
  useEffect(() => {
    setStatus('form'); setFailure(null); setToken(''); setSite(''); setEmail(''); setUrls(''); setMaxItems('20')
  }, [kind])

  const wait = (ms) => new Promise((resolve) => { timerRef.current = setTimeout(resolve, ms) })

  const ready = kind === 'github'
    ? site.trim().length > 0
    : kind === 'jira'
      ? Boolean(site.trim() && email.trim() && token.trim())
      : kind === 'links' ? urls.trim().length > 0 : true

  const connect = async () => {
    setStatus('connecting')
    setFailure(null)
    try {
      let connected = null
      if (onAuthorize) {
        const parsedUrls = urls.split(/\r?\n/).map((u) => u.trim()).filter(Boolean)
        if (kind === 'links' && parsedUrls.some((url) => {
          try { return !['http:', 'https:'].includes(new URL(url).protocol) } catch { return true }
        })) throw new Error('Enter complete http:// or https:// URLs, one per line.')
        connected = await onAuthorize({ kind, projectId, token, site, email, urls: parsedUrls, maxItems })
      } else {
        if (api.isLive) throw new Error('The provider hand-off is not wired to the backend yet.')
        await wait(reconsent ? 350 : 900)
        if (forcedState() === 'error') throw new Error('The provider rejected the request.')
      }
      if (!aliveRef.current) return
      setStatus('done')
      onConnected?.({ kind, projectId, ...connected })
    } catch (e) {
      if (!aliveRef.current) return
      setFailure(e.message)
      setStatus('error')
    }
  }

  const projectLine = projects.find((p) => p.id === projectId)?.name ?? '—'

  return (
    <div className={cn('rounded-[10px] border border-border bg-card p-3.5', className)}>
      <div className="flex items-center gap-2.5 pb-3">
        <span className="flex size-7.5 shrink-0 items-center justify-center rounded-lg border border-[#262b36] bg-background">
          <ConnectorIcon kind={kind} size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-semibold">Connect {name}</div>
          {desc && <div className="truncate text-[11.5px] text-muted-foreground">{desc}</div>}
        </div>
        {onCancel && status !== 'connecting' && (
          <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={onCancel}>Cancel</Button>
        )}
      </div>

      {/* Every flow asks which project the connection belongs to (spec §6). */}
      {status === 'form' || status === 'error' ? (
        <div className="pb-3">
          <label htmlFor="connect-project" className="mb-1.5 block text-[12px] text-muted-foreground">
            Which project does this connection belong to?
          </label>
          <select
            id="connect-project" value={projectId ?? ''} onChange={(e) => onProjectId?.(e.target.value)}
            className="w-full rounded-lg border border-border bg-card px-2.5 py-2 text-[13.5px] outline-none focus:border-primary"
          >
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      ) : (
        <p className="pb-3 text-[12px] text-muted-foreground">Belongs to {projectLine}.</p>
      )}

      {status === 'connecting' && (
        <div className="flex items-center gap-2 py-1 text-[13px] text-muted-foreground">
          <Spinner className="size-3.5" />
          Connecting…
        </div>
      )}

      {status === 'done' && (
        <div className="flex items-center gap-2 py-1 text-[13px] text-success">
          <Check size={14} aria-hidden />
          Connected — the first sync is starting.
        </div>
      )}

      {(status === 'form' || status === 'error') && (
        <>
          {auth === 'oauth' && (
            <div className="flex flex-col gap-2.5">
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                {reconsent
                  ? `You’re already signed in to ${provider}. This is a one-click re-consent — ${name} just gets added to the access you already granted.`
                  : `You’ll finish this on ${provider}’s own sign-in page, in this browser. We never see your password, and nothing is read until you approve it there.`}
              </p>
              <label className="flex max-w-xs flex-col gap-1.5 text-xs text-muted-foreground">Maximum items per sync
                <input type="number" min="1" max="10000" value={maxItems} placeholder="No limit"
                  onChange={(e) => setMaxItems(e.target.value)} className="rounded-md border border-border bg-background px-2.5 py-2 text-[13px] text-foreground" />
                <span>Start with 20 items to inspect extraction before a larger import. Clear for no limit.</span>
              </label>
              <div>
                <Button size="sm" onClick={connect}>
                  Continue to {provider}
                  <ArrowUpRight size={13} aria-hidden />
                </Button>
              </div>
            </div>
          )}

          {kind === 'links' && <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">Website URLs, one per line
              <textarea rows={5} value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="https://example.com/article"
                className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 font-mono text-[13px] text-foreground outline-none focus:border-primary" />
            </label>
            <p className="text-xs text-muted-foreground">Cairn fetches each page and stores its extracted text. Review the files before absorption, or enable automation in Pipeline.</p>
            <div><Button size="sm" disabled={!ready} onClick={connect}>Connect and fetch pages</Button></div>
          </div>}

          {auth === 'browser' && (
            <div className="flex flex-col gap-2.5">
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                WhatsApp pairs a browser session on this machine — you scan a code from your
                phone, the way WhatsApp Web does. Nothing is pulled until the pairing holds.
              </p>
              <div><Button size="sm" onClick={connect}>Start pairing</Button></div>
            </div>
          )}

          {kind === 'github' && (
            <div className="flex flex-col gap-3">
              <Field id="github-repo" label="Repository URL" value={site} onChange={setSite} placeholder="https://github.com/owner/repo" />
              <Field
                id="github-token" label="Personal access token (optional for public repos)" type="password"
                value={token} onChange={setToken} placeholder="ghp_…"
                hint="Read-only repo access is all this needs — no write scopes."
              />
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" disabled={!ready} onClick={connect}>Connect</Button>
                <a
                  href={TOKEN_HELP.github.href} target="_blank" rel="noreferrer"
                  className="text-[12px] text-primary hover:underline"
                >
                  {TOKEN_HELP.github.label}
                </a>
              </div>
            </div>
          )}

          {kind === 'jira' && (
            <div className="flex flex-col gap-3">
              <Field id="jira-site" label="Jira site URL" value={site} onChange={setSite} placeholder="yourteam.atlassian.net" />
              <Field id="jira-email" label="Account email" type="email" value={email} onChange={setEmail} placeholder="you@yourteam.com" />
              <Field id="jira-token" label="API token" type="password" value={token} onChange={setToken} placeholder="paste your token" />
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" disabled={!ready} onClick={connect}>Connect</Button>
                <a
                  href={TOKEN_HELP.jira.href} target="_blank" rel="noreferrer"
                  className="text-[12px] text-primary hover:underline"
                >
                  {TOKEN_HELP.jira.label}
                </a>
              </div>
            </div>
          )}
        </>
      )}

      {status === 'error' && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-destructive/40 p-2.5">
          <TriangleAlert size={14} className="mt-0.5 shrink-0 text-destructive" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] text-destructive">Couldn’t connect — try again.</div>
            {failure && <div className="text-[11.5px] text-muted-foreground">{failure}</div>}
          </div>
          <Button
            variant="outline" size="xs" className="border-destructive/60 text-destructive"
            disabled={!ready} onClick={connect}
          >
            Retry
          </Button>
        </div>
      )}
    </div>
  )
}
