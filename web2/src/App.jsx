import { useEffect, useState } from 'react'
import { createProject, listProjects } from './api'
import { Button } from './components/ui/button'
import { Shell } from './Shell'
import { Kit } from './Kit'
import { Chat } from './screens/Chat'
import { Connect } from './screens/Connect'
import { Wiki } from './screens/Wiki'
import { People } from './screens/People'
import { Repos } from './screens/Repos'
import { Settings } from './screens/Settings'
import { Files } from './screens/Files'
import { PipelineScreen } from './screens/Pipeline'
import { Onboarding } from './screens/Onboarding'

const SCREENS = {
  chat: Chat,
  connect: Connect,
  wiki: Wiki,
  people: People,
  repos: Repos,
  files: Files,
  pipeline: PipelineScreen,
  settings: Settings,
}

// The Google callback returns to "/" with ?connected= or ?connect_error=, so
// the round trip lands on the tab that started it instead of on Chat.
const startView = () => {
  const q = new URLSearchParams(window.location.search)
  return q.has('connected') || q.has('connect_error') ? 'connect' : 'chat'
}

export default function App() {
  const [view, setView] = useState(startView)
  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState(null)
  const [projectError, setProjectError] = useState(null)
  const [projectLoading, setProjectLoading] = useState(true)
  const [projectReload, setProjectReload] = useState(0)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [createBusy, setCreateBusy] = useState(false)
  const [route, setRoute] = useState(() => window.location.hash)
  // Where a cross-screen jump wants to land, e.g. Sources -> a wiki article.
  // Held here because `view` alone cannot carry a destination, and the
  // "Written up in" chips were plain #wiki/<path> anchors that no route ever
  // matched — they changed the URL and did nothing else.
  const [target, setTarget] = useState(null)

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    let cancelled = false
    setProjectLoading(true)
    listProjects().then((rows) => {
      if (cancelled) return
      setProjects(rows)
      setProjectError(null)
      setProjectId((current) => {
        const preferred = current ?? localStorage.getItem('cairn_project')
        return rows.find((p) => p.id === preferred)?.id ?? rows[0]?.id ?? null
      })
    }).catch((e) => { if (!cancelled) setProjectError(e.message) })
      .finally(() => { if (!cancelled) setProjectLoading(false) })
    return () => { cancelled = true }
  }, [projectReload])

  const selectProject = (id) => {
    setProjectId(id); setCreating(false)
    localStorage.setItem('cairn_project', id)
  }
  const addProject = async (e) => {
    e.preventDefault()
    if (!newName.trim()) return
    setCreateBusy(true); setProjectError(null)
    try {
      const project = await createProject(newName.trim())
      setProjects((rows) => [...rows, project]); selectProject(project.id)
      setNewName(''); setView('connect')
    } catch (error) { setProjectError(error.message) }
    finally { setCreateBusy(false) }
  }

  if (route === '#kit') return <Kit />
  // Onboarding replaces the shell entirely on first run. Until the backend can
  // report "no key and no completed sync", #onboarding is how it is reached.
  if (route === '#onboarding') {
    return <Onboarding onDone={(tab) => { window.location.hash = ''; setView(tab ?? 'chat') }} />
  }

  // Every screen requires a projectId for its very first fetch (V2's hard
  // isolation — there is no "all projects" call to fall back to), so mounting
  // one before listProjects() resolves sends a project-id-less request that
  // 422s and can leave a screen's error state stuck. One tick of blank is
  // cheaper than that.
  const projectForm = <form onSubmit={addProject} className="mx-auto w-full max-w-md px-6 py-12">
    <h1 className="text-xl font-semibold">{projects.length ? 'New project' : 'Create your knowledge base'}</h1>
    <p className="mt-2 text-sm text-muted-foreground">Keep connected files, wiki articles, and conversations together in one project.</p>
    <label className="mt-6 block text-xs text-muted-foreground" htmlFor="new-project-name">Project name</label>
    <input id="new-project-name" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Personal knowledge"
      className="mt-2 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm" />
    {projectError && <p role="alert" className="mt-3 text-sm text-destructive">{projectError}</p>}
    <div className="mt-4 flex gap-2"><Button type="submit" disabled={createBusy || !newName.trim()}>{createBusy ? 'Creating…' : 'Create project'}</Button>
      {!!projects.length && <Button type="button" variant="ghost" onClick={() => { setCreating(false); setProjectError(null) }}>Cancel</Button>}</div>
  </form>
  if (projectId === null) {
    if (projectLoading) return <div role="status" className="flex h-screen items-center justify-center text-sm text-muted-foreground">Opening your knowledge base…</div>
    if (projectError && !creating) return <div className="mx-auto max-w-md px-6 py-16"><h1 className="text-lg font-semibold">Cairn could not reach the server.</h1>
      <p className="mt-3 text-sm text-destructive" role="alert">{projectError}</p>
      <Button className="mt-5" onClick={() => setProjectReload((n) => n + 1)}>Retry connection</Button></div>
    return projectForm
  }

  const projectName = projects.find((p) => p.id === projectId)?.name
  const Screen = SCREENS[view]

  return (
    <Shell
      view={view} onView={setView}
      projects={projects} projectId={projectId} onProject={selectProject}
      onCreateProject={() => { setCreating(true); setProjectError(null) }}
    >
      {creating ? projectForm : <Screen
        key={projectId}
        projectId={projectId} projectName={projectName} projects={projects}
        target={view === 'wiki' || view === 'connect' ? target : null}
        onNavigate={(next, to = null) => { setView(next); setTarget(to) }}
        onProjectsChange={setProjects}
      />}
    </Shell>
  )
}
