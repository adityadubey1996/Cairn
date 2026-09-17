import { useEffect, useState } from 'react'
import { listProjects } from './api'
import { Shell } from './Shell'
import { Kit } from './Kit'
import { Chat } from './screens/Chat'
import { Connect } from './screens/Connect'
import { Wiki } from './screens/Wiki'
import { People } from './screens/People'
import { Repos } from './screens/Repos'
import { Settings } from './screens/Settings'
import { Files } from './screens/Files'
import { Onboarding } from './screens/Onboarding'

const SCREENS = {
  chat: Chat,
  connect: Connect,
  wiki: Wiki,
  people: People,
  repos: Repos,
  files: Files,
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
    listProjects().then((rows) => {
      if (cancelled) return
      setProjects(rows)
      setProjectId((current) => current ?? rows[0]?.id ?? null)
    })
    return () => { cancelled = true }
  }, [])

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
  if (projectId === null) return null

  const projectName = projects.find((p) => p.id === projectId)?.name
  const Screen = SCREENS[view]

  return (
    <Shell
      view={view} onView={setView}
      projects={projects} projectId={projectId} onProject={setProjectId}
    >
      <Screen
        projectId={projectId} projectName={projectName} projects={projects}
        target={view === 'wiki' || view === 'connect' ? target : null}
        onNavigate={(next, to = null) => { setView(next); setTarget(to) }}
        onProjectsChange={setProjects}
      />
    </Shell>
  )
}
