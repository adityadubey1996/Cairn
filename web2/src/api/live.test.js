import test from 'node:test'
import assert from 'node:assert/strict'
import * as api from './live.js'

test('delayed calls from another project cannot change article or provenance scope', async () => {
  const requests = []
  const originalFetch = globalThis.fetch
  const originalStorage = globalThis.localStorage
  globalThis.localStorage = { getItem: () => null }
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options })
    return { ok: true, status: 200, json: async () => ({ sources: [] }) }
  }
  try {
    // Simulate an old screen's completion callback after the user switched.
    await api.listConnections('previous-project')
    await api.wikiArticle({ path: 'local/systems/decision.md', projectId: 'selected-project' })
    await api.deleteArticle({ path: 'local/systems/decision.md', projectId: 'selected-project' })
    await api.sourceArticles('source-with/slash', 'selected-project')
    await api.personEvents('person-1', 'selected-project')
    assert.equal(requests.length, 5)
    for (const { url } of requests.slice(1)) {
      assert.equal(new URL(url, 'http://localhost').searchParams.get('project_id'), 'selected-project')
    }
    assert.equal(requests[2].options.method, 'DELETE')
    assert.match(requests[3].url, /source-with%2Fslash/)
  } finally {
    globalThis.fetch = originalFetch
    globalThis.localStorage = originalStorage
  }
})
