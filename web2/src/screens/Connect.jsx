import { useEffect, useState } from 'react'
import * as api from '@/api'
import { forcedState } from '@/lib/devState'
import { PageHeader } from '@/components/PageHeader'
import { SubTabNav } from '@/components/TabNav'
import { Health } from './connect/Health'
import { Sources } from './connect/Sources'
import { Timeline } from './connect/Timeline'
import { Cost } from './connect/Cost'
import { Pipeline } from './connect/Pipeline'

const SUB_TABS = [
  { id: 'health', label: 'Health' },
  { id: 'sources', label: 'Sources' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'cost', label: 'Cost' },
  // Always present. Watching a scrape's phases and log is not a developer
  // feature — only the paid absorb control inside it still checks DEV_UI.
  { id: 'pipeline', label: 'Pipeline' },
]

// Health is the default view: it is the only one that answers "is anything
// actually working right now", which is why someone opens this tab.
export function Connect({ projectId, projectName, onNavigate, target }) {
  const [sub, setSub] = useState('health')
  const [devUi, setDevUi] = useState(false)
  const forced = forcedState()

  // Files links here with 'sources'. Validated rather than trusted: `target`
  // is shared with Wiki, where it is an article path.
  useEffect(() => {
    if (SUB_TABS.some((t) => t.id === target)) setSub(target)
  }, [target])

  useEffect(() => {
    let cancelled = false
    api.health()
      .then((h) => { if (!cancelled) setDevUi(!!h.dev_ui) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Connections"
        subtitle={`Manage what enters ${projectName ?? 'this project'} and where it goes next.`}
      >
        {/* Deliberately lighter than the sidebar's TabNav, and scrolling rather
            than wrapping so the five views stay on one line at tablet width. */}
        <SubTabNav className="mt-3" tabs={SUB_TABS} value={sub} onChange={setSub} />
      </PageHeader>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-8">
        <div className="mx-auto w-full max-w-(--page-width)">
        {sub === 'health' && (
          <Health
            projectId={projectId} projectName={projectName}
            forced={forced} onGoFiles={() => onNavigate?.('files')}
            onNavigate={onNavigate}
          />
        )}
        {sub === 'sources' && <Sources projectId={projectId} forced={forced} onNavigate={onNavigate} />}
        {sub === 'timeline' && <Timeline projectId={projectId} projectName={projectName} forced={forced} />}
        {sub === 'cost' && <Cost onGoHealth={() => setSub('health')} />}
        {sub === 'pipeline' && <Pipeline projectId={projectId} onNavigate={onNavigate} />}
        </div>
      </div>
    </div>
  )
}
