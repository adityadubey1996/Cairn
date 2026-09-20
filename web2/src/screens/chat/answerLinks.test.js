import test from 'node:test'
import assert from 'node:assert/strict'
import { externalAnswerHref } from './answerLinks.js'

const origin = 'http://localhost:8300'

test('generated article paths never become broken application navigation', () => {
  for (const href of [
    'requests/wiki/domain/requests.md', '/requests/wiki/domain/requests.md',
    './wiki/domain/requests.md', '../domain/requests.md', '#Requests',
    '/api/wiki/article?repo=requests&path=domain/requests.md',
    `${origin}/requests/wiki/domain/requests.md`,
    `${origin}/api/wiki/article?repo=requests&path=domain/requests.md`,
  ]) assert.equal(externalAnswerHref(href, origin), null, href)
})

test('source citation schemes and unsafe URLs require a recognized renderer', () => {
  for (const href of ['cite:sources/upload/decision.md@abcdef01', 'javascript:alert(1)', 'data:text/html,hello', '', undefined]) {
    assert.equal(externalAnswerHref(href, origin), null)
  }
})

test('ordinary external links remain navigable', () => {
  for (const href of ['https://github.com/psf/requests', 'https://requests.readthedocs.io/', 'mailto:help@example.test']) {
    assert.equal(externalAnswerHref(href, origin), href)
  }
})
