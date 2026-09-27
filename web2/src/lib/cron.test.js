import test from 'node:test'
import assert from 'node:assert/strict'
import { nextRuns, parseCron, runLabel, sameZone } from './cron.js'

// Runs carry a wall clock in their UTC fields, so they are read back as UTC.
const at = (...args) => new Date(Date.UTC(...args))
const shape = (runs) => runs.map((d) =>
  `${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getUTCDay()]} ${d.getUTCDate()}`
  + ` ${d.getUTCHours()}:${String(d.getUTCMinutes()).padStart(2, '0')}`)
const UTC = 'UTC'

test('rejects what is not a five-field schedule', () => {
  for (const bad of ['', '0 9 * *', '0 9 * * 1-5 7', '@daily', 'every monday']) {
    assert.equal(parseCron(bad), null, bad)
  }
})

test('rejects fields that can never match', () => {
  assert.equal(parseCron('60 * * * *'), null, 'minute 60')
  assert.equal(parseCron('0 24 * * *'), null, 'hour 24')
  assert.equal(parseCron('0 9 * * 7'), null, 'weekday 7')
  assert.equal(parseCron('0 9-5 * * *'), null, 'reversed range')
  assert.equal(parseCron('0 */0 * * *'), null, 'zero step')
})

test('weekday mornings fire at 09:00, Monday to Friday', () => {
  // A Friday noon, so the first run skips the weekend.
  assert.deepEqual(shape(nextRuns('0 9 * * 1-5', 3, at(2026, 8, 25, 12, 0), UTC)),
    ['Mon 28 9:00', 'Tue 29 9:00', 'Wed 30 9:00'])
})

test('named weekdays mean the same as their numbers', () => {
  assert.deepEqual(nextRuns('0 9 * * MON-FRI', 3, at(2026, 8, 25, 12, 0), UTC),
                   nextRuns('0 9 * * 1-5', 3, at(2026, 8, 25, 12, 0), UTC))
})

test('a step schedule fires on its step, from the start of the range', () => {
  assert.deepEqual(nextRuns('*/15 * * * *', 3, at(2026, 8, 25, 12, 2), UTC).map((d) => d.getUTCMinutes()),
    [15, 30, 45])
})

// Pinned to croniter, which is what server/automation.py actually fires with:
//   croniter('0 3 1 * 1', 2026-09-25 12:00) -> Mon 28 Sep, Thu 01 Oct, Mon 05 Oct
// Restricting BOTH day-of-month and day-of-week ORs them. Getting this wrong
// would preview times the server never runs.
test('day-of-month and day-of-week are OR-ed when both are restricted', () => {
  assert.deepEqual(shape(nextRuns('0 3 1 * 1', 3, at(2026, 8, 25, 12, 0), UTC)),
    ['Mon 28 3:00', 'Thu 1 3:00', 'Mon 5 3:00'])
})

test('one restricted day field still behaves as a plain AND', () => {
  assert.deepEqual(shape(nextRuns('0 3 1 * *', 2, at(2026, 8, 25, 12, 0), UTC)),
    ['Thu 1 3:00', 'Sun 1 3:00'])
})

test('nothing inside the horizon returns no runs rather than a wrong one', () => {
  assert.deepEqual(nextRuns('0 9 31 2 *', 1, at(2026, 8, 25), UTC), [])
})

test('a run label names the day, so 09:00 Monday cannot be misread', () => {
  assert.match(runLabel(at(2026, 8, 28, 9, 0)), /Mon/)
})

// The server fires in the POLICY's zone. Previewing in the browser's would
// show the wrong hour to anyone scheduling for somewhere else.
test('the same expression reads differently in different zones', () => {
  const start = at(2026, 8, 25, 12, 0)
  const kolkata = nextRuns('0 9 * * *', 1, start, 'Asia/Kolkata')
  const newYork = nextRuns('0 9 * * *', 1, start, 'America/New_York')
  // 12:00 UTC is already past 09:00 in Kolkata but not yet in New York.
  assert.equal(kolkata[0].getUTCDate(), 26)
  assert.equal(newYork[0].getUTCDate(), 25)
  assert.equal(kolkata[0].getUTCHours(), 9)
  assert.equal(newYork[0].getUTCHours(), 9)
})

test('zone aliases are not reported as different zones', () => {
  assert.equal(sameZone('Asia/Calcutta', 'Asia/Kolkata'), true)
  assert.equal(sameZone('UTC', 'UTC'), true)
  assert.equal(sameZone('Europe/London', 'America/New_York'), false)
})
