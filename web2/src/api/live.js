// Real fetches against the V2 backend. Same function signatures as
// fixtures.js — every screen reads through index.js and never knows which
// implementation is live. See docs/superpowers/plans/2026-09-14-v2-ui-build.md
// Batch 6 for the endpoint map this was built against.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export const token = () => localStorage.getItem('brain_token')
const authHeaders = () => (token() ? { Authorization: `Bearer ${token()}` } : {})

async function api(path, opts = {}) {
  const isFormData = opts.body instanceof FormData
  const r = await fetch(path, {
    ...opts,
    headers: { ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
              ...authHeaders(), ...(opts.headers || {}) },
  })
  if (r.status === 401 && token()) {
    localStorage.removeItem('brain_token')
    window.location.reload()
  }
  if (!r.ok) {
    let detail
    try { detail = (await r.json())?.detail } catch { /* body wasn't JSON */ }
    // FastAPI reports a 422 as a LIST of {loc, msg, type}, not a string.
    // Passing that straight to Error() is what renders as "[object Object]".
    if (Array.isArray(detail)) {
      detail = detail.map((d) => [d?.loc?.slice(-1)[0], d?.msg].filter(Boolean).join(' ')
                                 || JSON.stringify(d)).join('; ')
    } else if (detail && typeof detail === 'object') {
      detail = JSON.stringify(detail)
    }
    throw new Error(detail || `${opts.method || 'GET'} ${path}: ${r.status}`)
  }
  return r.status === 204 ? null : r.json()
}

const qs = (params) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, v)
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// A handful of contract functions (wikiArticle, sourceArticles, personEvents)
// take no projectId — the screens that call them were built against fixtures,
// which have no isolation to violate. Every function that DOES receive a
// projectId remembers it here, so those few fall back to "whichever project
// was last active" instead of needing screen edits to thread one through.
let activeProject = null
const remember = (id) => { if (id) activeProject = id; return id }

// ---------------------------------------------------------------- projects

export async function listProjects() {
  return api('/api/projects')
}
export async function createProject(name) {
  return api('/api/projects', { method: 'POST', body: JSON.stringify({ name }) })
}
export async function renameProject(id, name) {
  return api(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) })
}
export async function deleteProject(id) {
  await api(`/api/projects/${id}`, { method: 'DELETE' })
  return { id }
}

// -------------------------------------------------------------- connections

export async function listConnections(projectId) {
  return api(`/api/connections${qs({ project_id: remember(projectId) })}`)
}
export async function createConnection(projectId, kind, name, config) {
  return api('/api/connections', {
    method: 'POST',
    body: JSON.stringify({ projectId: remember(projectId), kind, name, config }),
  })
}
export async function stageUpload(connectionId, files, onProgress) {
  const form = new FormData()
  for (const { path, file } of files) form.append('files', file, path)
  const url = `/api/connections/${encodeURIComponent(connectionId)}/upload`
  if (!onProgress) return api(url, { method: 'POST', body: form })

  // XHR rather than the api() helper: fetch() cannot report REQUEST progress
  // at all — there is no upload-side event and the streaming request body that
  // would substitute is not available everywhere. XHR's upload.onprogress is
  // the only way to know how many bytes have actually left, which is the whole
  // point of a determinate bar over a folder upload.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    const t = token()
    if (t) xhr.setRequestHeader('Authorization', `Bearer ${t}`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText || '{}')) }
        catch { resolve({}) }
        return
      }
      let detail
      try { detail = JSON.parse(xhr.responseText)?.detail } catch { /* not JSON */ }
      reject(new Error(detail || `POST ${url}: ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('the upload connection failed'))
    xhr.onabort = () => reject(new Error('the upload was cancelled'))
    xhr.send(form)
  })
}
export async function ingestUnits({ projectId, state = null } = {}) {
  return api(`/api/pipeline/units${qs({ project_id: remember(projectId), state })}`)
}
export async function fetchLinks(projectId) {
  return api(`/api/pipeline/links/fetch${qs({ project_id: remember(projectId) })}`,
             { method: 'POST' })
}
export async function retryUnits(projectId) {
  return api(`/api/pipeline/units/retry${qs({ project_id: remember(projectId) })}`,
             { method: 'POST' })
}

export async function syncConnection(id, { full = false } = {}) {
  // `full` re-lists everything; the default fetches only what changed since
  // this connection's watermark.
  return api(`/api/connections/${id}/sync${qs({ full: full || null })}`,
             { method: 'POST' })
}
export async function removeConnection(id) {
  await api(`/api/connections/${encodeURIComponent(id)}`, { method: 'DELETE' })
  return { id }
}

// ------------------------------------------------------------------ sources

function toSource(r) {
  return {
    id: r.id, projectId: r.project_id ?? r.projectId, connectionId: r.connection_id,
    kind: r.kind, type: r.type, name: r.name, path: r.path, url: r.url,
    detail: r.detail, folder: r.folder ?? null,
    bytes: r.bytes, sha: r.sha, authors: r.authors,
    scrapedAt: r.scraped_at ?? r.scrapedAt, text: r.text,
    status: r.status ?? 'ok', error: r.error ?? null,
    // Null for everything whose stored file IS the original — only an
    // extracted PDF/docx has a second, byte-identical copy to offer.
    originalPath: r.original_path ?? r.originalPath ?? null,
    queued: !!(r.wiki_queued_at ?? r.wikiQueuedAt),
  }
}

export async function listSources({ projectId, q = '', kind = null, status = null,
                                    connectionId = null, group = null, cursor = '' } = {}) {
  // `cursor` comes straight back from the previous page; `total` is the real
  // match count, not the page size, so a caller can say "8 of 3,871".
  // `status` is 'ok' | 'failed'; omitted means both. `group` narrows to one
  // Chat space or Drive folder and only means anything alongside its `kind`.
  const res = await api(
    `/api/sources${qs({ project_id: remember(projectId), q, kind, status,
                       connection_id: connectionId, group, cursor })}`)
  return { rows: res.rows.map(toSource), total: res.total, cursor: res.cursor ?? null }
}
export async function listSubfolders({ projectId, kind, folder = '', q = '',
                                       status = null, connectionId = null } = {}) {
  // One tree level. `count` is everything beneath a child, so a folder row can
  // say what opening it is worth.
  const rows = await api(
    `/api/sources/folders${qs({ project_id: remember(projectId), kind, folder,
                               q, status, connection_id: connectionId })}`)
  // `label` is what a group row renders, whichever level it came from.
  return rows.map((f) => ({ ...f, label: f.name }))
}

export async function listSourceGroups({ projectId, kind = null, q = '', status = null,
                                         connectionId = null } = {}) {
  // Counted server-side: the row list is paginated, so grouping a page would
  // undercount every group bigger than it.
  const rows = await api(
    `/api/sources/groups${qs({ project_id: remember(projectId), kind, q, status,
                              connection_id: connectionId })}`)
  return rows.map((g) => ({ key: g.key, label: g.key, count: g.count, last: g.last }))
}
export async function sourceContent({ path, etag = '' } = {}) {
  // The markdown itself, read through the server so the page never has to
  // fetch a presigned S3 URL cross-origin.
  return api(`/api/sources/content${qs({ path, etag })}`)
}
export async function viewSource({ path, etag = '' } = {}) {
  // Minted per click and never stored, so an old conversation keeps working:
  // the link is generated at read time, not at answer time.
  return api(`/api/sources/view${qs({ path, etag })}`)
}

export async function sourceArticles(sourceId) {
  return api(`/api/sources/${encodeURIComponent(sourceId)}/articles${qs({ project_id: activeProject })}`)
}

export async function sourcesByUrl({ projectId, urls } = {}) {
  // POST because a transcript can mention dozens of URLs. Returns url -> row
  // for the ones we hold, including failed ones.
  const rows = await api('/api/sources/by-url', {
    method: 'POST',
    body: JSON.stringify({ project_id: remember(projectId), urls }),
  })
  return Object.fromEntries(Object.entries(rows).map(([u, r]) => [u, toSource(r)]))
}

// ------------------------------------------------------------- wiki queue

export async function queueSources({ projectId, ids, queued = true } = {}) {
  // Free and reversible: this only marks rows. Nothing is read by a model
  // until startWikiWriteUp().
  return api('/api/sources/queue', {
    method: 'POST',
    body: JSON.stringify({ project_id: remember(projectId), ids, queued }),
  })
}

export async function wikiQueue(projectId) {
  // `runId` is non-null when a write-up is already in flight, so a reload
  // rejoins it instead of showing a Write up button that would 409.
  return api(`/api/pipeline/wiki-queue${qs({ project_id: remember(projectId) })}`)
}

export async function startWikiWriteUp(projectId) {
  return api('/api/pipeline/wiki-queue/run', {
    method: 'POST',
    body: JSON.stringify({ project_id: remember(projectId) }),
  })
}

// ----------------------------------------------------------------- timeline

export async function listTimeline({ projectId } = {}) {
  return api(`/api/timeline${qs({ project_id: remember(projectId) })}`)
}

// ------------------------------------------------------------------- people

export async function listPeople({ projectId, q = '' } = {}) {
  const rows = await api(`/api/people${qs({ project_id: remember(projectId) })}`)
  const needle = q.trim().toLowerCase()
  return needle ? rows.filter((p) => p.name.toLowerCase().includes(needle)) : rows
}
export async function personEvents(personId) {
  return api(`/api/people/${encodeURIComponent(personId)}/events${qs({ project_id: activeProject })}`)
}

// -------------------------------------------------------------- citations

// A chat answer cites bare `path@sha` (framing.py's prompt asks for exactly
// that); an article body cites `[grade: path@sha]` — see server/wikilib.py's
// CITE vs ARTICLE_CITE. Wiki articles arrive already linkified by the server
// (server/routers/wiki.py, which reuses the same citation-resolution code
// chat uses), so only chat needs this client-side pass — and only chat needs
// it live, token by token, since the answer streams before citation hrefs
// are known.
const CITE_RE = /`?([\w./-]+\.\w+)@([0-9a-f]{7,40})`?/g

function linkifyChat(text) {
  return text.replace(CITE_RE, (_m, path, sha) => {
    const label = `${path.split('/').pop()}@${sha.slice(0, 7)}`
    return `[${label}](cite:${path}@${sha})`
  })
}
function chatCitations(raw) {
  return (raw || []).map((c) => ({
    id: `${c.path}@${c.sha}`,
    label: `${c.path.split('/').pop()}@${c.sha.slice(0, 7)}`,
    type: c.path.startsWith('sources/links/') ? 'link' : 'file',
    url: c.href || '#',
  }))
}
const formatHeads = (heads) =>
  Object.entries(heads || {}).map(([repo, sha]) => `${repo}@${sha}`).join(' · ')

// ------------------------------------------------------------- conversations

export async function listConversations({ projectId } = {}) {
  return api(`/api/conversations${qs({ project_id: remember(projectId) })}`)
}
export async function conversationMessages(cid) {
  return api(`/api/conversations/${cid}/messages`)
}
export async function conversationMessagesFull(cid) {
  const rows = await api(`/api/conversations/${cid}/messages`)
  return rows.map((m) => {
    if (m.role !== 'assistant') return { id: m.id, role: m.role, text: m.content }
    const meta = m.citations || {}
    const trust = meta.trust ? {
      grades: meta.trust.grades || {}, articleCount: meta.trust.articles?.length ?? 0,
      heads: formatHeads(meta.trust.heads), citations: chatCitations(meta.citations),
    } : undefined
    return { id: m.id, role: 'assistant', text: linkifyChat(m.content), process: meta.process, trust }
  })
}
export async function deleteConversation(id) {
  await api(`/api/conversations/${id}`, { method: 'DELETE' })
  return { id }
}

export async function sendMessage(cid, text, { onStage, onToken, signal } = {}) {
  let realCid = cid
  if (!UUID_RE.test(cid)) {
    const conv = await api('/api/conversations', {
      method: 'POST', body: JSON.stringify({ projectId: activeProject }),
    })
    realCid = conv.id
  }

  const res = await fetch(`/api/conversations/${realCid}/messages`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ content: text }),
  })
  if (!res.ok || !res.body) throw new Error(`sendMessage: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = '', out = '', final = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      const msg = JSON.parse(line.slice(6))
      if (msg.error) throw new Error(msg.error)
      if (msg.delta !== undefined) {
        out += msg.delta
        onToken?.(linkifyChat(out))
      } else if (msg.done) {
        final = msg
      } else if (msg.stage === 'searching' || msg.stage === 'generating') {
        onStage?.({ stage: msg.stage })
      } else if (msg.stage === 'condensed') {
        onStage?.({ query: msg.query })
      } else if (msg.stage === 'recall') {
        onStage?.({ hits: (msg.hits || []).length })
      } else if (msg.stage === 'context') {
        onStage?.({ articles: msg.articles })
      }
    }
  }

  const trust = final?.trust ? {
    grades: final.trust.grades || {}, articleCount: final.trust.articles?.length ?? 0,
    heads: formatHeads(final.trust.heads), citations: chatCitations(final.citations),
  } : undefined
  return {
    id: `m${Date.now()}`, role: 'assistant', text: linkifyChat(out),
    process: final?.process, trust, conversationId: realCid,
  }
}

// A few generic starters work for any project — real per-project suggestions
// would need to know what's actually connected, which is a further step.
const GENERIC_STARTERS = ['What have I connected?', 'What changed recently?', 'What’s stale?']
export const STARTERS = new Proxy({}, { get: () => GENERIC_STARTERS })

// ------------------------------------------------------------------- search

export async function search({ projectId, q = '', kind = null } = {}) {
  if (!q.trim()) return { rows: [], query: q }
  // `kind` is what makes this "search inside my files" rather than "search
  // everything" — same scan, one connector.
  const { rows } = await api(`/api/search/find${qs({ project_id: remember(projectId), q, kind })}`)
  return { rows, query: q }
}

// --------------------------------------------------------------------- wiki

export async function wikiGraph({ projectId } = {}) {
  const g = await api(`/api/wiki/graph${qs({ project_id: remember(projectId) })}`)
  // Reader.jsx round-trips a single `path` string; the composite `repo/rel`
  // id is what stays unique across roots, so path IS id here (see
  // server/routers/wiki.py's article() docstring).
  return { nodes: g.nodes.map((n) => ({ ...n, path: n.id })), edges: g.edges }
}
export async function wikiArticle({ path }) {
  const slash = path.indexOf('/')
  const repo = path.slice(0, slash)
  const rel = path.slice(slash + 1)
  const a = await api(`/api/wiki/article${qs({ repo, path: rel, project_id: activeProject })}`)
  return { ...a, sources: (a.sources || []).map(toSource) }
}
export async function deleteArticle({ path }) {
  // Same repo/rel split as wikiArticle — the first segment is the wiki root's
  // name, not part of the article path.
  const slash = path.indexOf('/')
  return api(`/api/wiki/article${qs({ repo: path.slice(0, slash),
                                     path: path.slice(slash + 1),
                                     project_id: activeProject })}`,
             { method: 'DELETE' })
}

// ----------------------------------------------------------------- settings

export async function getSettings() {
  return api('/api/settings/provider')
}
export async function saveProvider(cfg) {
  return api('/api/settings/provider', { method: 'PUT', body: JSON.stringify(cfg) })
}
export async function testProvider(cfg) {
  return api('/api/settings/provider/test', { method: 'POST', body: JSON.stringify(cfg) })
}
export async function getOllamaStatus() {
  return api('/api/settings/ollama')
}

// The connectors offered at onboarding — static product data, not
// backend-sourced, so this matches fixtures.js verbatim.
//
// `available: false` means the card is in the catalogue but has no feeder in
// server/connectors.py REGISTRY yet. Shown greyed rather than hidden, because
// hiding them loses the roadmap and offering them as live leads the user
// through a consent flow that dead-ends. GET /api/connectors/preflight is the
// authoritative per-machine answer for the ones that ARE built.
export const CONNECTOR_CATALOGUE = [
  { group: 'Google', items: [
    { kind: 'gdrive', name: 'Google Drive', desc: 'Docs, sheets and PDFs in your drive', auth: 'oauth' },
    { kind: 'gchat', name: 'Google Chat', desc: 'Messages in spaces and DMs', auth: 'oauth' },
    { kind: 'gmail', name: 'Gmail', desc: 'Threads you sent or were named in', auth: 'oauth', available: false },
  ] },
  { group: 'Microsoft', items: [
    { kind: 'outlook', name: 'Outlook', desc: 'Mail and calendar invitations', auth: 'oauth', available: false },
    { kind: 'onedrive', name: 'OneDrive', desc: 'Files and shared folders', auth: 'oauth', available: false },
    { kind: 'teams', name: 'Microsoft Teams', desc: 'Channel and chat messages', auth: 'oauth', available: false },
  ] },
  { group: 'Code and tickets', items: [
    { kind: 'github', name: 'GitHub', desc: 'A repo, at a snapshot or across its history', auth: 'token' },
    { kind: 'jira', name: 'Jira', desc: 'Issues, comments and assignees', auth: 'token', available: false },
  ] },
  { group: 'Advanced', advanced: true, items: [
    { kind: 'whatsapp', name: 'WhatsApp', desc: 'Opt-in only — pairs a browser session', auth: 'browser' },
  ] },
]

// ----------------------------------------------------------------- pipeline
// Not project-scoped: a run is per connector and writes the whole corpus, so
// none of these endpoints take a project_id.

export async function health() {
  return api('/health')
}

export async function pipelineEstimate(connector) {
  return api(`/api/pipeline/estimate${qs({ connector })}`)
}

export async function startPipelineRun(connector, { skipAbsorb = false } = {}) {
  return api('/api/pipeline/runs', {
    method: 'POST',
    body: JSON.stringify({ connector, skip_absorb: skipAbsorb }),
  })
}

export async function sourceFailures({ projectId, kind = null } = {}) {
  return api(`/api/sources/failures${qs({ project_id: remember(projectId), kind })}`)
}
export async function pipelineRuns({ projectId, connector = null, limit = 25 } = {}) {
  return api(`/api/pipeline/runs${qs({ project_id: remember(projectId), connector, limit })}`)
}
export async function pipelineRun(runId) {
  return api(`/api/pipeline/runs/${encodeURIComponent(runId)}`)
}

export async function pipelineRunLog(runId, after = 0) {
  return api(`/api/pipeline/runs/${encodeURIComponent(runId)}/log${qs({ after })}`)
}

export async function stopPipelineRun(runId) {
  return api(`/api/pipeline/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' })
}

// -------------------------------------------------------------------- repos
// Not project-scoped: /api/repos has no project_id, a tracked repo is global.
// `id` is "owner/name" and the slash is part of the path, so it is never
// encodeURIComponent'd here.

export async function listRepos() {
  return api('/api/repos')
}

export async function checkRepo(url) {
  return api('/api/repos/check', { method: 'POST', body: JSON.stringify({ url }) })
}

export async function addRepo(url, branch, token = '') {
  // The token is only ever sent, never read back: /api/repos returns the repo
  // row, which deliberately has no token field.
  return api('/api/repos', {
    method: 'POST',
    body: JSON.stringify({ url, branch: branch || undefined,
                           token: token || undefined }),
  })
}

export async function removeRepo(id) {
  return api(`/api/repos/${id}?keep_wiki=true`, { method: 'DELETE' })
}

export async function runRepoStep(id, step, body) {
  return api(`/api/repos/${id}/${step}`, { method: 'POST', body: JSON.stringify(body || {}) })
}

export async function sweepRepos() {
  return api('/api/repos/sweep', { method: 'POST' })
}

export async function repoCommits(id, limit = 30) {
  return api(`/api/repos/${id}/commits${qs({ limit })}`)
}

export async function repoQueue(id) {
  return api(`/api/repos/${id}/queue`)
}
