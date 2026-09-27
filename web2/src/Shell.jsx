import { useEffect, useState } from 'react'
import { BookOpen, FolderGit2, Files, MessageSquare, Plug, Settings, Sparkles, Workflow, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ProjectSelector } from './components/ProjectSelector'
import { TabNav } from './components/TabNav'

export const TABS = [
  { id: 'connect', label: 'Connect', icon: Plug },
  { id: 'files', label: 'Files', icon: Files },
  { id: 'pipeline', label: 'Pipeline', icon: Workflow },
  { id: 'wiki', label: 'Wiki', icon: BookOpen },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'people', label: 'People', icon: Users },
  { id: 'repos', label: 'Repos', icon: FolderGit2 },
]

// V1 has no breakpoints at all; V2 is responsive by requirement. At tablet
// width and below the 256px sidebar becomes a 64px icon rail — labels drop,
// the project selector becomes its icon, the gear stays pinned.
export function useNarrow(query = '(max-width: 767px)') {
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = (e) => setNarrow(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return narrow
}

export function Shell({ view, onView, projects, projectId, onProject, onCreateProject, children }) {
  const collapsed = useNarrow()

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className={cn(
        'flex shrink-0 flex-col gap-2.5 border-r border-border bg-card p-2.5',
        collapsed ? 'w-16 items-center' : 'w-64',
      )}>
        <div className={cn('flex items-center gap-2.5 px-1 pt-1.5 pb-1', collapsed && 'justify-center px-0')}>
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md border border-[#262b36] bg-background">
            <Sparkles size={13} aria-hidden />
          </span>
          {!collapsed && <span className="text-sm font-semibold">Cairn</span>}
        </div>

        <div className={cn(collapsed && 'w-full')}>
          <ProjectSelector
            projects={projects} value={projectId} collapsed={collapsed}
            onChange={onProject} onCreate={onCreateProject}
          />
        </div>

        <div className={cn(collapsed && 'w-full')}>
          <TabNav tabs={TABS} value={view} onChange={onView} collapsed={collapsed} />
        </div>

        <div className={cn('mt-auto w-full border-t border-border pt-2.5')}>
          <TabNav
            tabs={[{ id: 'settings', label: 'Settings', icon: Settings }]}
            value={view} onChange={onView} collapsed={collapsed}
          />
        </div>
      </aside>

      {/* Scrolls independently of the sidebar. No global header bar: each tab
          defines its own, per the spec. */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
    </div>
  )
}
