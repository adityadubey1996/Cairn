import { useEffect, useState } from 'react'
import { Clock, FileText, Plug, Plus, Search, Users } from 'lucide-react'
import * as api from './api'
import { Button } from './components/ui/button'
import { CitationChip } from './components/CitationChip'
import { ConfirmDialog } from './components/ConfirmDialog'
import { ConnectorCard } from './components/ConnectorCard'
import { EmptyState } from './components/EmptyState'
import { EventRow } from './components/EventRow'
import { GradeBadgePills } from './components/GradeBadgePills'
import { GranularityPicker } from './components/GranularityPicker'
import { ModeToggle } from './components/ModeToggle'
import { PersonAvatar } from './components/PersonAvatar'
import { ProjectSelector } from './components/ProjectSelector'
import { ProviderPresetPicker } from './components/ProviderPresetPicker'
import { SkeletonList } from './components/SkeletonList'
import { SourceRow } from './components/SourceRow'
import { StatusDot } from './components/StatusDot'
import { SubTabNav, TabNav } from './components/TabNav'
import { TrustLine } from './components/TrustLine'

function Cell({ name, used, wide = false, children }) {
  return (
    <section className={`flex flex-col gap-2.5 rounded-[10px] border border-border bg-card p-3.5 ${wide ? 'md:col-span-2' : ''}`}>
      <header>
        <h2 className="text-sm font-semibold">{name}</h2>
        <p className="text-xs leading-snug text-muted-foreground">{used}</p>
      </header>
      <div className="flex flex-1 flex-col justify-center gap-2.5 rounded-lg border border-border bg-background p-3">
        {children}
      </div>
    </section>
  )
}

// Dev-only route (#kit): every component from §10 of the spec, in its states.
// Batches 2-5 compose screens from these and add no second version of any.
export function Kit() {
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('search')
  const [project, setProject] = useState('p1')
  const [tab, setTab] = useState('connect')
  const [sub, setSub] = useState('health')
  const [granularity, setGranularity] = useState({ mode: 'history', granularity: 'monthly' })
  const [provider, setProvider] = useState({ preset: 'claude', keyMasked: 'sk-ant-···7f2a' })
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.listProjects(), api.listConnections('p1'), api.listSources({ projectId: 'p1' }),
      api.listTimeline({ projectId: 'p1' }), api.listPeople({ projectId: 'p1' }), api.personEvents('rk', 'p1'),
    ]).then(([projects, connections, sources, timeline, people, events]) => {
      if (!cancelled) setData({ projects, connections, sources: sources.rows, timeline, people, events })
    })
    return () => { cancelled = true }
  }, [])

  if (!data) {
    return (
      <div className="mx-auto max-w-[520px] p-8">
        <SkeletonList rows={6} />
      </div>
    )
  }

  return (
    <div className="h-screen overflow-y-auto p-7">
      <h1 className="text-lg font-semibold">Component kit</h1>
      <p className="mb-5 max-w-[640px] text-sm leading-relaxed text-muted-foreground">
        Every component the V2 screens are composed from. Built once here so the screens in later
        batches stay consistent — status is only ever green, amber, red or neutral, and Dusk Blue
        is spent on interactive signal alone.
      </p>

      <div className="grid gap-3.5 md:grid-cols-2 xl:grid-cols-3">
        <Cell name="StatusDot" used="ConnectorCard · Timeline rows">
          {['ok', 'running', 'error', 'never_run'].map((s) => (
            <div key={s} className="flex items-center gap-2.5 text-sm text-muted-foreground">
              <StatusDot status={s} />
              <span className="text-foreground">{s}</span>
            </div>
          ))}
        </Cell>

        <Cell name="ConnectorCard" used="Connect › Health · onboarding. One card per connection, not per connector type." wide>
          <div className="grid gap-2.5 sm:grid-cols-2">
            {data.connections.slice(0, 4).map((c) => (
              <ConnectorCard key={c.id} connection={c} />
            ))}
          </div>
        </Cell>

        <Cell name="CitationChip" used="Chat answers · Wiki article body. Variant comes from the source type.">
          <CitationChip label="absorb.py@3f9c1a2" type="file" />
          <span className="text-xs text-muted-foreground">file — opens the cited version</span>
          <CitationChip label="pricing (anthropic.com)" type="link" href="https://www.anthropic.com/pricing" />
          <span className="text-xs text-muted-foreground">link — dashed edge, opens the real URL</span>
        </Cell>

        <Cell name="SourceRow" used="Connect › Sources · the Wiki reader's sources panel" wide>
          {data.sources.slice(0, 3).map((s) => <SourceRow key={s.id} source={s} />)}
        </Cell>

        <Cell name="TrustLine" used="Beneath every Ask answer. Read-only.">
          <TrustLine
            grades={{ verified: 3, code: 2, doc: 1, conflict: 0, gap: 1 }}
            articleCount={4} projectName="Acme Corp" heads="ai-brain@3f9c1a2"
          />
        </Cell>

        <Cell name="GradeBadgePills" used="Wiki article header — same vocabulary, relocated.">
          <GradeBadgePills grades={{ verified: 6, code: 4, doc: 2, conflict: 1, gap: 0 }} />
        </Cell>

        <Cell name="ModeToggle" used="Chat composer — used exactly once in the product.">
          <ModeToggle value={mode} onChange={setMode} />
          <span className="text-xs text-muted-foreground">active: {mode}</span>
        </Cell>

        <Cell name="ProjectSelector" used="Global shell. No 'All projects' option — projects are hard-isolated.">
          <ProjectSelector projects={data.projects} value={project} onChange={setProject} />
        </Cell>

        <Cell name="TabNav / SubTabNav" used="Sidebar (primary) and Connect (secondary). The second never carries the first's weight.">
          <TabNav
            tabs={[{ id: 'connect', label: 'Connect', icon: Plug }, { id: 'people', label: 'People', icon: Users }]}
            value={tab} onChange={setTab}
          />
          <SubTabNav
            tabs={['Health', 'Sources', 'Timeline', 'Cost'].map((l) => ({ id: l.toLowerCase(), label: l }))}
            value={sub} onChange={setSub}
          />
        </Cell>

        <Cell name="PersonAvatar" used="People picker and feed. Initials only.">
          <div className="flex items-center gap-2">
            {data.people.map((p) => <PersonAvatar key={p.id} name={p.name} initials={p.initials} />)}
            <PersonAvatar name="Ravi Kulkarni" size="lg" />
          </div>
        </Cell>

        <Cell name="EventRow" used="Connect › Timeline (sync vs absorb) · People's feed (role badges)" wide>
          {data.timeline.slice(0, 3).map((e) => <EventRow key={e.id} event={e} />)}
          {data.events.slice(0, 2).map((e) => <EventRow key={e.id} event={e} variant="person" />)}
        </Cell>

        <Cell name="GranularityPicker" used="GitHub's connection flow only." wide>
          <GranularityPicker
            mode={granularity.mode} granularity={granularity.granularity} onChange={setGranularity}
            estimate={{ checkpoints: 38, tokens: '4.1M', cost: '$12.40' }}
          />
        </Cell>

        <Cell name="ProviderPresetPicker" used="Settings. Each preset shows only the fields it needs." wide>
          <ProviderPresetPicker value={provider} onChange={setProvider} />
        </Cell>

        <Cell name="EmptyState" used="Every list-bearing screen. Treatment shared, copy never.">
          <EmptyState
            icon={Plug} title="Nothing synced yet."
            detail="Connect a source or press Sync now on Connect › Health to see files here."
            action={<Button variant="outline" size="sm"><Plus size={12} aria-hidden />Add connector</Button>}
          />
        </Cell>

        <Cell name="SkeletonList" used="The same screens as EmptyState — real row height, never a spinner.">
          <SkeletonList rows={3} />
          <SkeletonList rows={2} avatar />
        </Cell>

        <Cell name="ConfirmDialog" used="Settings › Danger zone. Names what goes, takes a typed confirmation.">
          <Button
            variant="outline" size="sm" onClick={() => setConfirming(true)}
            className="self-start border-destructive/60 text-destructive"
          >
            Delete project…
          </Button>
          <ConfirmDialog
            open={confirming} confirmWord="Side project" confirmLabel="Delete project"
            title="Delete “Side project”?"
            detail="This removes 1 connection, 12 sources, 3 wiki articles and 4 conversations. It cannot be undone."
            onCancel={() => setConfirming(false)} onConfirm={() => setConfirming(false)}
          />
        </Cell>

        <Cell name="Empty-state copy" used="The exact wording each screen uses, from §8 of the spec." wide>
          <EmptyState icon={Clock} title="Activity will show up here once you've synced or absorbed something." />
          <EmptyState icon={FileText} title="No wiki articles yet. Once you absorb some sources, they'll show up here." />
          <EmptyState icon={Search} title="Search everything you've connected — no LLM cost." />
        </Cell>
      </div>
    </div>
  )
}
