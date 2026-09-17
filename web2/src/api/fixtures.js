// Sample data for batches 1-5. Every count here agrees with the rows it
// describes: a project's sourceCount matches its sources, an article's
// citations exist in the source list, a person's eventCount matches events.
// Inconsistent fixtures produce screens that look right and are wrong.

const delay = (ms = 120) => new Promise((r) => setTimeout(r, ms))
const minutes = (n) => new Date(Date.now() - n * 60_000).toISOString()

const PROJECTS = [
  { id: 'p1', name: 'Acme Corp' },
  { id: 'p2', name: 'Personal' },
]

const CONNECTIONS = [
  { id: 'c1', projectId: 'p1', kind: 'github', name: 'acme/ai-brain', detail: 'GitHub · main',
    status: 'ok', lastSyncAt: minutes(12), itemCount: 341, auth: 'token' },
  { id: 'c2', projectId: 'p1', kind: 'gdrive', name: 'Google Drive', detail: 'you@acme.com',
    status: 'running', lastSyncAt: minutes(1), itemCount: 218, auth: 'oauth' },
  { id: 'c3', projectId: 'p1', kind: 'gchat', name: 'Google Chat', detail: 'you@acme.com',
    status: 'ok', lastSyncAt: minutes(60), itemCount: 12, auth: 'oauth' },
  { id: 'c4', projectId: 'p1', kind: 'gmail', name: 'Gmail', detail: 'you@acme.com',
    status: 'error', lastSyncAt: minutes(240), itemCount: 0, auth: 'oauth',
    error: 'Couldn’t connect — the Google token expired.' },
  { id: 'c5', projectId: 'p1', kind: 'jira', name: 'Jira', detail: 'acme.atlassian.net · 3 projects',
    status: 'ok', lastSyncAt: minutes(120), itemCount: 604, auth: 'token' },
  { id: 'c6', projectId: 'p1', kind: 'github', name: 'acme/infra', detail: 'GitHub · main',
    status: 'never_run', lastSyncAt: null, itemCount: 0, auth: 'token',
    note: 'Latest snapshot · history not mapped' },
  { id: 'c7', projectId: 'p2', kind: 'links', name: 'Saved links', detail: 'reading list',
    status: 'ok', lastSyncAt: minutes(2880), itemCount: 46, auth: 'none' },
  { id: 'c8', projectId: 'p1', kind: 'upload', name: 'Files', detail: 'Added by hand',
    status: 'ok', lastSyncAt: minutes(30), itemCount: 9, auth: 'none' },
]

const SOURCES = [
  { id: 's1', text: 'An article is marked stale when the head SHA of a cited source moves past the recorded manifest entry. Staleness never deletes anything.', projectId: 'p1', kind: 'github', type: 'file', name: 'pipeline/absorb.py',
    detail: 'acme/ai-brain', bytes: 18_432, scrapedAt: minutes(120), sha: '3f9c1a2' },
  { id: 's2', text: 'Input and output token pricing per model, used by the cost estimate shown before a run starts.', projectId: 'p1', kind: 'links', type: 'link', name: 'Anthropic — Pricing',
    detail: 'anthropic.com/pricing', url: 'https://www.anthropic.com/pricing',
    bytes: null, scrapedAt: minutes(8640) },
  { id: 's3', text: 'Agreed the stale queue gets re-absorbed weekly rather than on every push. Ravi to own the Jira feeder retries.', projectId: 'p1', kind: 'gdrive', type: 'file', name: 'Q3 planning — pipeline notes.docx',
    detail: 'Google Drive', authors: ['Maya Srinivasan'], folder: 'research/findings',
    bytes: 45_056, scrapedAt: minutes(1440) },
  { id: 's4', text: 'The links feeder assigns a scrape-date TTL, because a scraped page has no SHA to compare against.', projectId: 'p1', kind: 'github', type: 'file', name: 'feeders/links.py',
    detail: 'acme/ai-brain', bytes: 7_168, scrapedAt: minutes(120), sha: '8b41ee0' },
  { id: 's5', text: 'Jira feeder retries are dropping on 429. Needs backoff before the next sync window.', projectId: 'p1', kind: 'jira', type: 'file', name: 'AI-412 — Jira feeder retries',
    detail: 'Jira · AI board', bytes: null, scrapedAt: minutes(120) },
  { id: 's6', text: 'the stale queue should just run weekly, not per push', projectId: 'p1', kind: 'gchat', type: 'file', name: '#pipeline — 14 Sep',
    detail: 'Google Chat', bytes: null, scrapedAt: minutes(180) },
  { id: 's7', text: 'Groups pending sources into topics, capped by the prompt size ceiling.', projectId: 'p1', kind: 'github', type: 'file', name: 'pipeline/topics.py',
    detail: 'acme/ai-brain', bytes: 11_264, scrapedAt: minutes(120), sha: '3f9c1a2' },
  { id: 's8', text: 'retries land on 429 about one run in five', projectId: 'p1', kind: 'gchat', type: 'file', name: '#pipeline — 13 Sep',
    detail: 'Google Chat', bytes: null, scrapedAt: minutes(1600) },
  { id: 's9', text: 'the source row should show which article cited it', projectId: 'p1', kind: 'gchat', type: 'file', name: '#design — 14 Sep',
    detail: 'Google Chat', bytes: null, scrapedAt: minutes(200) },
  { id: 's10', text: 'Deck for the Q4 review, superseded by the January version.', projectId: 'p1', kind: 'gdrive', type: 'file', name: 'Q4 review deck.pdf',
    detail: 'Google Drive', authors: ['Tom Nguyen'], bytes: 2_310_144, scrapedAt: minutes(2600) },
  { id: 's11', text: 'Transcript of the January review, shared from a teammate.', projectId: 'p1', kind: 'gdrive', type: 'file', name: 'Platform review — 2026/01/13 — Notes by Gemini',
    detail: 'Google Drive', authors: ['Maya Srinivasan'], bytes: 12_288, scrapedAt: minutes(900) },
]

// Which article(s) each source was absorbed into. A source absent from this
// map has not been absorbed — the UI says so plainly rather than showing
// an unexplained empty list.
const SOURCE_ARTICLES = {
  s1: ['absorb-pipeline', 'wiki-staleness'],
  s2: ['absorb-pipeline'],
  s3: ['absorb-pipeline'],
  s5: ['jira-feeder'],
  s6: ['jira-feeder'],
  s7: ['absorb-pipeline'],
}

const ARTICLES = [
  { path: 'absorb-pipeline', title: 'Absorb pipeline', type: 'flow', stale: false,
    grades: { verified: 6, code: 4, doc: 2, conflict: 1, gap: 0 }, sourceIds: ['s1', 's7', 's2', 's3'] },
  { path: 'wiki-staleness', title: 'Wiki staleness', type: 'system', stale: false,
    grades: { verified: 4, code: 2, doc: 1, conflict: 0, gap: 1 }, sourceIds: ['s1'] },
  { path: 'feeders-scraping', title: 'Feeders and scraping', type: 'flow', stale: false,
    grades: { verified: 3, code: 3, doc: 1, conflict: 0, gap: 0 }, sourceIds: ['s4'] },
  { path: 'two-tier-retrieval', title: 'Two-tier retrieval', type: 'flow', stale: true,
    grades: { verified: 2, code: 1, doc: 2, conflict: 1, gap: 2 }, sourceIds: [] },
  { path: 'project-isolation', title: 'Project isolation', type: 'system', stale: false,
    grades: { verified: 5, code: 1, doc: 3, conflict: 0, gap: 0 }, sourceIds: [] },
  { path: 'byok-keys', title: 'BYOK key handling', type: 'system', stale: false,
    grades: { verified: 3, code: 2, doc: 1, conflict: 0, gap: 1 }, sourceIds: [] },
  { path: 'cost-before-spend', title: 'Cost before spend', type: 'decision', stale: true,
    grades: { verified: 1, code: 0, doc: 2, conflict: 0, gap: 1 }, sourceIds: ['s2'] },
  { path: 'why-no-multi-user', title: 'Why no multi-user', type: 'decision', stale: false,
    grades: { verified: 2, code: 0, doc: 2, conflict: 0, gap: 0 }, sourceIds: [] },
  { path: 'jira-feeder', title: 'Jira feeder', type: 'domain', stale: false,
    grades: { verified: 2, code: 2, doc: 0, conflict: 0, gap: 1 }, sourceIds: ['s5', 's6'] },
  { path: 'connector-connections', title: 'Connector connections', type: 'domain', stale: false,
    grades: { verified: 3, code: 1, doc: 1, conflict: 0, gap: 0 }, sourceIds: [] },
  { path: 'sync-scheduling', title: 'Sync scheduling', type: 'flow', stale: false,
    grades: { verified: 2, code: 1, doc: 1, conflict: 0, gap: 0 }, sourceIds: [] },
]

const WIKILINKS = [
  ['absorb-pipeline', 'wiki-staleness'], ['absorb-pipeline', 'feeders-scraping'],
  ['absorb-pipeline', 'cost-before-spend'], ['absorb-pipeline', 'two-tier-retrieval'],
  ['feeders-scraping', 'jira-feeder'], ['feeders-scraping', 'connector-connections'],
  ['project-isolation', 'why-no-multi-user'], ['project-isolation', 'connector-connections'],
  ['byok-keys', 'cost-before-spend'], ['sync-scheduling', 'feeders-scraping'],
  ['jira-feeder', 'connector-connections'], ['wiki-staleness', 'two-tier-retrieval'],
]

const PEOPLE = [
  { id: 'me', projectId: 'p1', name: 'You', initials: 'YO', isOwner: true, lastActiveAt: minutes(12) },
  { id: 'rk', projectId: 'p1', name: 'Ravi Kulkarni', initials: 'RK', lastActiveAt: minutes(120) },
  { id: 'ms', projectId: 'p1', name: 'Maya Srinivasan', initials: 'MS', lastActiveAt: minutes(300) },
  { id: 'tn', projectId: 'p1', name: 'Tom Nguyen', initials: 'TN', lastActiveAt: minutes(1440) },
]

const PERSON_EVENTS = {
  rk: [
    { id: 'e1', role: 'assigned', kind: 'jira', text: 'AI-412 — Jira feeder retries assigned to Ravi', at: minutes(120), sourceId: 's5' },
    { id: 'e2', role: 'sent', kind: 'gchat', text: '“the stale queue should run weekly, not per push” in #pipeline', at: minutes(180), sourceId: 's6' },
    { id: 'e3', role: 'authored', kind: 'github', text: 'Commit 3f9c1a2 — tighten the topics size ceiling', at: minutes(300), sourceId: 's7' },
    { id: 'e4', role: 'mentioned', kind: 'gdrive', text: 'Mentioned in Q3 planning — pipeline notes.docx', at: minutes(1440), sourceId: 's3' },
  ],
  ms: [
    { id: 'e5', role: 'authored', kind: 'github', text: 'Commit 8b41ee0 — links feeder TTL', at: minutes(4320), sourceId: 's4' },
  ],
  me: [], tn: [],
}

const TIMELINE = [
  { id: 't1', projectId: 'p1', kind: 'sync', status: 'running', text: 'Syncing acme/ai-brain — scrape', at: minutes(0),
    phases: [{ name: 'scrape', detail: '128 written of 341 seen', seconds: 42.3 }] },
  { id: 't2', projectId: 'p1', kind: 'sync', status: 'ok', text: 'Synced 341 files from acme/ai-brain', at: minutes(12), seconds: 61.4,
    phases: [
      { name: 'scrape', detail: '341 written of 341 seen', seconds: 48.1 },
      { name: 'push', detail: '341 files to S3', seconds: 9.7 },
      { name: 'ingest', detail: '341 units queued', seconds: 3.6 },
    ] },
  { id: 't3', projectId: 'p1', kind: 'absorb', status: 'ok', text: 'Absorbed 3 articles', at: minutes(38), seconds: 402.8,
    phases: [
      { name: 'absorb', detail: '3 of 12 articles · 184,204 tokens', seconds: 391.2 },
      { name: 'push', detail: '3 files to S3', seconds: 1.4 },
    ],
    links: [{ label: 'Absorb pipeline', path: 'absorb-pipeline' }, { label: 'Wiki staleness', path: 'wiki-staleness' }] },
  { id: 't4', projectId: 'p1', kind: 'sync', status: 'ok', text: 'Synced 0 messages from Google Chat', at: minutes(60), seconds: 146.1,
    phases: [
      { name: 'scrape', detail: '0 written of 585 seen', seconds: 142.1 },
      { name: 'ingest', detail: '432 units queued', seconds: 3.6 },
    ] },
  { id: 't5', projectId: 'p1', kind: 'error', status: 'interrupted', text: 'Sync interrupted — Google Drive stopped reporting', at: minutes(90),
    phases: [{ name: 'scrape', detail: '134 written of 330 seen', seconds: 88.4 }] },
  { id: 't6', projectId: 'p1', kind: 'error', status: 'error', text: 'Sync failed — Gmail: token expired', at: minutes(240), seconds: 1.2 },
  { id: 't7', projectId: 'p1', kind: 'sync', status: 'ok', text: 'Synced 218 files from Google Drive', at: minutes(1440), seconds: 74.9,
    phases: [
      { name: 'scrape', detail: '218 written of 218 seen', seconds: 66.2 },
      { name: 'push', detail: '218 files to S3', seconds: 8.7 },
    ] },
  { id: 't8', projectId: 'p2', kind: 'sync', status: 'ok', text: 'Synced 46 saved links', at: minutes(2880), seconds: 12.0 },
]

const CONVERSATIONS = [
  { id: 'v1', projectId: 'p1', title: 'Staleness rules in absorb', createdAt: minutes(30) },
  { id: 'v2', projectId: 'p1', title: 'Who owns the Jira feeder?', createdAt: minutes(400) },
  { id: 'v3', projectId: 'p2', title: 'Reading list themes', createdAt: minutes(5000) },
]

const OLLAMA_STATUS = {
  base: 'http://localhost:11434',
  reachable: true,
  models: ['llama3.1:8b', 'qwen3:8b'],
  suggested: 'qwen3:8b',
  recommended: [],
}

const SETTINGS = {
  provider: { preset: 'claude', keyMasked: 'sk-ant-···7f2a', verifiedAt: minutes(120) },
  embeddings: { preset: 'ollama', model: 'nomic-embed-text', reachable: true },
  // Which provider a question would actually use — a key in .env resolves
  // without anything being saved here, so the screen has to be able to say so.
  effective: { preset: 'claude', model: 'claude-opus-5', source: 'settings' },
  ollama: OLLAMA_STATUS,
}

// A real result quotes the text around the match, not the query back at you.
function snippetAround(text, needle, radius = 70) {
  const at = text.toLowerCase().indexOf(needle)
  if (at === -1) return text.slice(0, radius * 2)
  const from = Math.max(0, at - radius)
  const to = Math.min(text.length, at + needle.length + radius)
  return `${from > 0 ? '…' : ''}${text.slice(from, to)}${to < text.length ? '…' : ''}`
}

const byProject = (rows, projectId) => rows.filter((r) => !projectId || r.projectId === projectId)

export async function listProjects() {
  await delay()
  return PROJECTS.map((p) => ({
    ...p,
    connectionCount: byProject(CONNECTIONS, p.id).length,
    sourceCount: byProject(SOURCES, p.id).length,
  }))
}
export async function createProject(name) { await delay(); return { id: `p${PROJECTS.length + 1}`, name } }
export async function renameProject(id, name) { await delay(); return { id, name } }
export async function deleteProject(id) { await delay(); return { id } }

export async function listConnections(projectId) { await delay(); return byProject(CONNECTIONS, projectId) }
export async function createConnection(projectId, kind, name, config) {
  await delay()
  return { id: `${kind}-fixture`, projectId, kind, name, detail: config?.detail ?? kind,
           status: 'never_run', lastSyncAt: null, itemCount: 0, auth: 'oauth', error: null }
}
export async function stageUpload(connectionId, files, onProgress) {
  // Walks the bar in ten steps so the fixture UI exercises the same
  // determinate path the live XHR drives, rather than jumping 0 -> 100.
  const total = files.reduce((n, f) => n + (f.file?.size ?? 0), 0) || files.length
  for (let i = 1; onProgress && i <= 10; i++) {
    await delay(60)
    onProgress(Math.round((total * i) / 10), total)
  }
  await delay()
  return { staged: files.length }
}

// A queue that lives only for this page load — fixtures have no server to
// remember it. `queued` on a source row is the same flag the live API sets.
const WIKI_QUEUE = new Set()

export async function queueSources({ ids = [], queued = true } = {}) {
  await delay()
  for (const id of ids) queued ? WIKI_QUEUE.add(id) : WIKI_QUEUE.delete(id)
  for (const s of SOURCES) s.queued = WIKI_QUEUE.has(s.id)
  return { changed: ids.length, queued: WIKI_QUEUE.size }
}

export async function sourcesByUrl() {
  await delay()
  return {}
}

export async function wikiQueue() {
  await delay()
  const queued = WIKI_QUEUE.size
  return { queued, tokens: queued * 15000, seconds: queued * 45,
           measured: false, ids: [...WIKI_QUEUE], runId: null }
}

export async function startWikiWriteUp() {
  await delay(400)
  return { run_id: 'fixture-writeup', queued: WIKI_QUEUE.size }
}
export async function ingestUnits() {
  await delay()
  return { summary: { done: 34, running: 2, pending: 11, failed: 3 },
           rows: [
             { unitId: 's1', name: 'Q3 planning — pipeline notes.docx', state: 'done',
               attempts: 1, article: 'wiki/pipeline.md', kind: 'gdrive' },
             { unitId: 's2', name: 'Dev-Group — 2026-08-22', state: 'running',
               attempts: 1, kind: 'gchat' },
             { unitId: 's3', name: 'jira-feeder retries', state: 'failed',
               attempts: 2, error: 'the model returned no citable text', kind: 'links' },
           ] }
}
export async function sourceFailures() {
  await delay()
  return { reasons: [
    { reason: 'no extractable text', count: 591 },
    { reason: 'HTTP Error 403: Forbidden', count: 471 },
    { reason: 'HTTP Error 429: Too Many Requests', count: 343 },
    { reason: 'HTTP Error 404: Not Found', count: 288 },
  ] }
}
export async function pipelineRuns() {
  await delay()
  return { runs: [
    { id: 'r1', connector_id: 'wiki', status: 'ok', started_at: minutes(5),
      finished_at: minutes(2), items_written: 12, items_seen: 12,
      units: { done: 12 } },
    { id: 'r2', connector_id: 'wiki', status: 'error', started_at: minutes(40),
      finished_at: minutes(39), items_written: 3, items_seen: 12,
      error: 'the run was stopped', units: { done: 3, pending: 9 } },
    { id: 'r3', connector_id: 'gdrive', status: 'ok', started_at: minutes(90),
      finished_at: minutes(80), items_written: 41, items_seen: 332, units: {} },
  ] }
}
export async function fetchLinks() { await delay(); return { runId: 'run-links' } }
export async function retryUnits() { await delay(); return { requeued: 3 } }
export async function syncConnection(id, opts) { await delay(400); return { id, status: 'running', full: !!opts?.full } }
export async function removeConnection(id) { await delay(); return { id } }

// Mirrors server/sources.py's GROUP_BY: a Chat file is named "<space> — <day>",
// and a Drive file carries the folder path its feeder resolved, if Drive would
// show one at all.
const groupKeyOf = {
  '': (s) => s.kind,
  gchat: (s) => s.name.split(' — ')[0],
  gdrive: (s) => s.folder ?? NO_FOLDER,
}

function matchingSources({ projectId, q, kind, status, connectionId, group }) {
  const needle = q.trim().toLowerCase()
  const keyOf = groupKeyOf[kind]
  return byProject(SOURCES, projectId)
    .filter((s) => (!kind || s.kind === kind) && (!needle || s.name.toLowerCase().includes(needle)))
    .map((s) => ({ status: 'ok', error: null, ...s }))
    .filter((s) => !status || s.status === status)
    .filter((s) => !connectionId || s.connectionId === connectionId)
    .filter((s) => !group || !keyOf || keyOf(s) === group)
}

export async function listSources({ projectId, q = '', kind = null, status = null,
                                    connectionId = null, group = null, cursor = '' } = {}) {
  // Fixture sets are small enough to fit one page, so cursor is accepted for
  // signature parity with live.js and never emitted — the caller stops asking.
  await delay()
  const rows = matchingSources({ projectId, q, kind, status, connectionId, group })
  return { rows, total: rows.length, cursor: null }
}

const NO_FOLDER = 'No folder (shared)'

export async function listSubfolders({ projectId, kind, folder = '', q = '',
                                       status = null, connectionId = null } = {}) {
  await delay()
  if (!groupKeyOf[kind] || !kind) return []
  const all = matchingSources({ projectId, q, kind, status, connectionId, group: null })
  const depth = folder ? folder.split('/').length : 0
  const children = new Map()
  let unparented = 0
  for (const s of all) {
    if (!s.folder) { unparented += 1; continue }
    if (folder && !s.folder.startsWith(`${folder}/`)) continue
    const name = s.folder.split('/')[depth]
    if (!name) continue
    const path = folder ? `${folder}/${name}` : name
    const at = children.get(name)
    children.set(name, { name, label: name, path, count: (at?.count ?? 0) + 1,
                         last: !at || s.scrapedAt > at.last ? s.scrapedAt : at.last })
  }
  const out = [...children.values()].sort((a, b) => a.name.localeCompare(b.name))
  if (!folder && unparented) {
    out.push({ name: NO_FOLDER, label: NO_FOLDER, path: NO_FOLDER, count: unparented, last: null })
  }
  return out
}

export async function listSourceGroups({ projectId, kind = null, q = '', status = null,
                                         connectionId = null } = {}) {
  await delay()
  const keyOf = groupKeyOf[kind ?? '']
  if (!keyOf) return []
  const counts = new Map()
  for (const s of matchingSources({ projectId, q, kind, status, connectionId, group: null })) {
    const key = keyOf(s)
    const at = counts.get(key)
    counts.set(key, { key, label: key, count: (at?.count ?? 0) + 1,
                      last: !at || s.scrapedAt > at.last ? s.scrapedAt : at.last })
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.key.localeCompare(b.key))
}

export async function sourceContent({ path } = {}) {
  await delay()
  const source = SOURCES.find((s) => s.path === path)
  return {
    path,
    text: `# ${source?.name ?? path}\n\n${source?.text ?? 'No fixture text for this source.'}\n`,
    truncated: false,
  }
}

export async function viewSource({ path } = {}) {
  await delay()
  return { url: `data:text/plain,${encodeURIComponent(`fixture: ${path}`)}` }
}

export async function sourceArticles(sourceId) {
  await delay()
  const paths = SOURCE_ARTICLES[sourceId] || []
  return ARTICLES.filter((a) => paths.includes(a.path)).map(({ path, title, type }) => ({ path, title, type }))
}

export async function listTimeline({ projectId } = {}) { await delay(); return byProject(TIMELINE, projectId) }

export async function listPeople({ projectId, q = '' } = {}) {
  await delay()
  const needle = q.trim().toLowerCase()
  return byProject(PEOPLE, projectId)
    .filter((p) => !needle || p.name.toLowerCase().includes(needle))
    .map((p) => ({ ...p, eventCount: (PERSON_EVENTS[p.id] || []).length }))
}
export async function personEvents(personId) { await delay(); return PERSON_EVENTS[personId] || [] }

export async function listConversations({ projectId } = {}) { await delay(); return byProject(CONVERSATIONS, projectId) }
export async function conversationMessages() { await delay(); return [] }
export async function deleteConversation(id) { await delay(); return { id } }

export async function search({ projectId, q = '', kind = null } = {}) {
  await delay(200)
  const needle = q.trim().toLowerCase()
  if (!needle) return { rows: [], query: q }
  const rows = byProject(SOURCES, projectId)
    .filter((s) => !kind || s.kind === kind)
    .filter((s) => [s.name, s.detail, s.text].some((f) => (f || '').toLowerCase().includes(needle)))
    .map((s) => ({ ...s, snippet: snippetAround(s.text || s.name, needle) }))
  return { rows, query: q }
}

export async function deleteArticle({ path }) {
  await delay()
  return { deleted: path, unit: 's1', requeued: true }
}
export async function wikiGraph({ projectId } = {}) {
  await delay()
  if (projectId === 'p2') return { nodes: [], edges: [] }
  return {
    nodes: ARTICLES.map(({ path, title, type, stale, grades }) => ({ id: path, path, title, type, stale, grades })),
    edges: WIKILINKS.map(([source, target]) => ({ source, target })),
  }
}

export async function wikiArticle({ path }) {
  await delay()
  const a = ARTICLES.find((x) => x.path === path)
  if (!a) return null
  return {
    ...a,
    sources: a.sourceIds.map((id) => SOURCES.find((s) => s.id === id)).filter(Boolean),
    related: WIKILINKS.filter(([s, t]) => s === path || t === path)
      .map(([s, t]) => (s === path ? t : s))
      .map((p) => ARTICLES.find((x) => x.path === p))
      .filter(Boolean)
      .map(({ path: p, title }) => ({ path: p, title })),
    body: BODIES[a.path] ?? `No body absorbed yet for **${a.title}**.`,
    citations: a.sourceIds.map((id) => {
      const src = SOURCES.find((x) => x.id === id)
      return src && { id, label: src.type === 'link' ? `${src.name.toLowerCase()}` : src.name.split('/').pop() + '@' + (src.sha ?? 'head'), type: src.type, url: src.url }
    }).filter(Boolean),
  }
}


// Article bodies use the same cite: link convention the chat answers use, so
// CitationChip renders them identically in both places.
const BODIES = {
  'absorb-pipeline': `Absorb turns raw scraped sources into graded wiki articles. It runs per project, never across projects, and every claim it writes carries the citation of the source it came from.

## What one run does

- Reads the pending set built by [build_pending@3f9c1a2](cite:s1) — sources whose SHA moved, plus links past their TTL.
- Groups them into topics, capped by the prompt size ceiling in [topics.py@3f9c1a2](cite:s7).
- Writes each article with a manifest of the exact source versions it read, which is what [[Wiki staleness]] later compares against.

## Cost

A dry run prints the estimated checkpoint count and token spend before anything is charged to your key. Model pricing comes from [pricing (anthropic.com)](cite:s2).`,

  'wiki-staleness': `An article goes stale when the head SHA of any source it cites moves past the SHA recorded at absorb time. The check is per-citation, not per-repo — see [absorb.py@3f9c1a2](cite:s1).

Staleness never deletes anything. The article stays readable and answerable, flagged stale until the next absorb re-verifies it.`,

  'jira-feeder': `Pulls issues, comments and assignees from a Jira site. Retries are currently dropping on 429 — see [AI-412](cite:s5).

Related discussion in [#pipeline](cite:s6).`,
}

// The eight connectors offered at onboarding, grouped the way the OAuth
// consent actually works: one Google login covers Drive, Chat and Gmail.
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

export async function getSettings() { await delay(); return SETTINGS }
export async function saveProvider(cfg) { await delay(); return cfg }
export async function testProvider() { await delay(500); return { ok: true, detail: 'Key works' } }
export async function getOllamaStatus() { await delay(); return OLLAMA_STATUS }

// --- Chat (Batch 2) -------------------------------------------------------

// Citations carry their own type, so CitationChip never has to guess the
// variant from the path string.
const ANSWER = `An article goes stale when the **head SHA of any source it cites** moves past the SHA recorded at absorb time. The check is per-citation, not per-repo — one changed file marks the article, the rest of the wiki stays verified.

- The comparison happens in [absorb.py@3f9c1a2](cite:s1), against the manifest written by the run that built the article.
- Sources scraped from the web have no SHA, so they fall back to a scrape-date TTL — see [pricing (anthropic.com)](cite:s2), which expires 30 days after it was pulled.
- Staleness never deletes anything. The article stays readable and answerable, flagged stale until the next absorb.

Re-absorbing only the stale articles is the cheap path.`

const MESSAGES = {
  v1: [
    { id: 'm1', role: 'user', text: 'How does absorb decide an article is stale?' },
    {
      id: 'm2', role: 'assistant', text: ANSWER,
      process: { query: 'how absorb decides an article is stale', hits: 9,
        articles: [{ path: 'absorb-pipeline', title: 'Absorb pipeline' }, { path: 'wiki-staleness', title: 'Wiki staleness' }] },
      trust: {
        grades: { verified: 3, code: 2, doc: 1, conflict: 0, gap: 1 },
        articleCount: 4, heads: 'ai-brain@3f9c1a2',
        citations: [
          { id: 's1', label: 'absorb.py@3f9c1a2', type: 'file' },
          { id: 's2', label: 'pricing (anthropic.com)', type: 'link', url: 'https://www.anthropic.com/pricing' },
        ],
      },
    },
  ],
  v2: [{ id: 'm3', role: 'user', text: 'Who owns the Jira feeder?' }],
  v3: [],
}

export const STARTERS = {
  p1: ['What changed in acme/ai-brain this week?', 'Who owns the Jira feeder?', 'Which articles are stale?'],
  p2: ['What have I saved about retrieval?', 'Summarise my reading list'],
}

export async function conversationMessagesFull(cid) { await delay(); return MESSAGES[cid] ?? [] }

// Simulates the real thing closely enough that the screen is written once:
// stages first, then the answer token by token, and a trust line at the end.
export async function sendMessage(cid, text, { onStage, onToken, signal } = {}) {
  const step = async (stage, ms) => {
    await delay(ms)
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    onStage?.(stage)
  }
  await step({ stage: 'searching', query: text }, 320)
  await step({ stage: 'reading', hits: 9, articles: MESSAGES.v1[1].process.articles }, 420)
  await step({ stage: 'generating' }, 300)

  const words = ANSWER.split(' ')
  let out = ''
  for (const w of words) {
    await delay(12)
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    out += (out ? ' ' : '') + w
    onToken?.(out)
  }
  return { id: `m${Date.now()}`, role: 'assistant', text: out, ...MESSAGES.v1[1].trust ? { trust: MESSAGES.v1[1].trust } : {} }
}

// ----------------------------------------------------------------- pipeline
// dev_ui is true here so the Pipeline sub-tab is reachable while working on it
// against fixtures. In live mode the real DEV_UI env var decides, and it is off
// unless explicitly set.

export async function health() {
  await delay()
  return { ok: true, auth_mode: 'dev', dev_ui: true, wiki_roots: ['/wiki'] }
}

const PIPELINE_ESTIMATE = {
  gdrive: { queued: 64, tokens: 960_000, seconds: 2880, measured: false },
  gchat: { queued: 423, tokens: 6_345_000, seconds: 19_035, measured: false },
}

export async function pipelineEstimate(connector) {
  await delay()
  return PIPELINE_ESTIMATE[connector] ?? { queued: 0, tokens: 0, seconds: 0, measured: false }
}

export async function startPipelineRun(connector) {
  await delay()
  return { run_id: `fixture-run-${connector}` }
}

export async function pipelineRun(runId) {
  await delay()
  return {
    id: runId, status: 'ok', phase: null, pid: 4242,
    items_seen: 64, items_written: 64, error: null,
    phases: {
      scrape: { seen: 64, written: 12, seconds: 41.2 },
      ingest: { queued: 12, seconds: 6.8 },
      push: { files: 12, seconds: 3.1 },
    },
  }
}

export async function pipelineRunLog(runId, after = 0) {
  await delay()
  if (after) return { lines: [], last: after }
  const lines = [
    'pipeline gdrive starting (pid 4242)',
    '  [1/64] Q3 siting memo',
    '  [2/64] Rack density notes',
    'scrape: 12 written of 64 seen',
    'ingest: 12 gdrive units queued',
    'push: 12 files to s3',
    'done',
  ].map((line, i) => ({ seq: i + 1, at: new Date().toISOString(), line }))
  return { lines, last: lines.length }
}

export async function stopPipelineRun() {
  await delay()
  return { stopping: true }
}

// -------------------------------------------------------------------- repos

const REPOS = {
  max_tracked: 5,
  groq_configured: false,
  repos: [
    {
      id: 'acme/platform', branch: 'main', state: 'ingested',
      articles: 428, absorbed: 411, queue_new: 12, queue_changed: 5,
      head_sha: '9f1c2ab4c0de', clone_bytes: 41_200_000, clone_path: '/var/clones/x',
      last_used_at: new Date(Date.now() - 3_600_000).toISOString(),
      pinned_sha: null, running_step: null, last_error: null,
      last_run: { step: 'ingest', status: 'ok', items_seen: 17, items_written: 17,
                  finished_at: new Date(Date.now() - 3_500_000).toISOString() },
    },
    {
      id: 'acme/infra', branch: 'main', state: 'ready',
      articles: 575, absorbed: 575, queue_new: 0, queue_changed: 0,
      head_sha: '3b59fc3aa112', clone_bytes: 18_900_000, clone_path: '/var/clones/y',
      last_used_at: new Date(Date.now() - 86_400_000).toISOString(),
      pinned_sha: null, running_step: null, last_error: null, last_run: null,
    },
  ],
}

export async function listRepos() { await delay(); return REPOS }
export async function checkRepo(url) {
  await delay()
  const slug = url.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '')
  return { slug, default_branch: 'main', branches: ['main', 'develop'], size_kb: 18_400, too_big: false }
}
export async function addRepo(url, branch, token) { await delay(); return { ok: true, private: !!token } }
export async function removeRepo() { await delay(); return null }
export async function runRepoStep() { await delay(); return { started: true } }
export async function sweepRepos() { await delay(); return { started: true } }
export async function repoCommits() {
  await delay()
  return { commits: [
    { sha: '9f1c2ab4c0de5f', short: '9f1c2ab', date: '2026-09-14', author: 'Maya Srinivasan', subject: 'Wire the sensor registry' },
    { sha: '3b59fc3aa11299', short: '3b59fc3', date: '2026-09-12', author: 'Tom Nguyen', subject: 'Tighten the onboarding copy' },
  ] }
}
export async function repoQueue() {
  await delay()
  return {
    by_kind: { doc: 9, code_package: 5, dataset: 3 },
    new: [{ id: 'u1', kind: 'doc', path: 'docs/00_architecture.md', first: '2026-06-09', status: 'active' }],
    changed: [{ id: 'u2', kind: 'code_package', path: 'services/api/application', first: '2026-05-02', status: 'active' }],
  }
}
