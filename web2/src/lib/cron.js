// Standard five-field cron, enough to tell the user what their schedule does.
// The server fires the real thing through croniter (server/automation.py), so
// this mirrors croniter's semantics — including the one that surprises people:
// when BOTH day-of-month and day-of-week are restricted they are OR'd, not
// AND'd. `0 3 1 * 1` means the 1st *and* every Monday.
//
// The server is still the authority. An expression this cannot read gets no
// preview rather than an error, so a schedule croniter accepts is never
// blocked by a gap here.

const NAMES = {
  sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6,
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
}

const RANGES = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 6]]

const toNumber = (token) => {
  const named = NAMES[token.toLowerCase()]
  if (named !== undefined) return named
  return /^\d+$/.test(token) ? Number(token) : NaN
}

function matchField(spec, value, min, max) {
  return spec.split(',').some((part) => {
    const slash = part.indexOf('/')
    const step = slash < 0 ? 1 : Number(part.slice(slash + 1))
    const range = slash < 0 ? part : part.slice(0, slash)
    if (!Number.isInteger(step) || step < 1) return false

    let lo, hi
    if (range === '*') { lo = min; hi = max }
    else if (range.indexOf('-') > 0) {
      const [a, b] = range.split('-')
      lo = toNumber(a); hi = toNumber(b)
    } else { lo = hi = toNumber(range) }

    if (!Number.isInteger(lo) || !Number.isInteger(hi)) return false
    if (lo < min || hi > max || lo > hi) return false
    return value >= lo && value <= hi && (value - lo) % step === 0
  })
}

const fieldCanMatch = (spec, [min, max]) => {
  for (let v = min; v <= max; v++) if (matchField(spec, v, min, max)) return true
  return false
}

/** The five fields, or null when this cannot read the expression. */
export function parseCron(expression) {
  const fields = String(expression ?? '').trim().split(/\s+/)
  if (fields.length !== 5) return null
  if (!fields.every((f) => /^[\w*,\-/]+$/.test(f))) return null
  // A field matching nothing in its own range is a typo, not a schedule.
  return fields.every((f, i) => fieldCanMatch(f, RANGES[i])) ? fields : null
}

function fires(fields, at) {
  if (!matchField(fields[0], at.getUTCMinutes(), 0, 59)) return false
  if (!matchField(fields[1], at.getUTCHours(), 0, 23)) return false
  if (!matchField(fields[3], at.getUTCMonth() + 1, 1, 12)) return false

  const [, , dom, , dow] = fields
  const onDay = matchField(dom, at.getUTCDate(), 1, 31)
  const onWeekday = matchField(dow, at.getUTCDay(), 0, 6)
  // croniter's rule: restrict both and either one is enough.
  return dom !== '*' && dow !== '*' ? onDay || onWeekday : onDay && onWeekday
}

/** `from` as a wall clock in `zone`, carried in a Date's UTC fields. */
function wallClock(from, zone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone, hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(from).reduce((acc, p) => {
    if (p.type !== 'literal') acc[p.type] = Number(p.value)
    return acc
  }, {})
  // hour12:false still reports midnight as 24 in some engines.
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day,
                           parts.hour % 24, parts.minute))
}

/**
 * The next `count` firing times as WALL CLOCKS in `zone` — the times the
 * server's schedule actually names — carried in a Date's UTC fields and
 * formatted back by runLabel. Computing in the browser's own zone instead
 * would misreport every schedule saved for somewhere else.
 *
 * null when unreadable, [] when nothing fires inside the horizon. Minute
 * stepping rather than calendar arithmetic: 90 days of minutes runs fast
 * enough for every keystroke, and shortcuts are where a clever version breaks.
 *
 * ponytail: the hour either side of a DST change can be off by one. Naming
 * that is cheaper than a full zone-transition table for a three-line preview.
 */
export function nextRuns(expression, count = 3, from = new Date(), zone = localZone()) {
  const fields = parseCron(expression)
  if (!fields) return null

  let at
  try { at = wallClock(from, zone) } catch { at = wallClock(from, localZone()) }
  at.setUTCMinutes(at.getUTCMinutes() + 1)

  const out = []
  const HORIZON_MINUTES = 60 * 24 * 90
  for (let i = 0; i < HORIZON_MINUTES && out.length < count; i++) {
    if (fires(fields, at)) out.push(new Date(at.getTime()))
    at.setUTCMinutes(at.getUTCMinutes() + 1)
  }
  return out
}

export const localZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone

/** Two zone names are the same zone when they agree on a fixed instant. */
export function sameZone(a, b) {
  if (!a || !b) return true
  if (a === b) return true
  const shows = (zone, ms) => {
    try { return new Date(ms).toLocaleString('en-US', { timeZone: zone }) } catch { return null }
  }
  // Two probes six months apart, so zones that differ only in summer show it.
  const winter = Date.UTC(2026, 0, 15, 12), summer = Date.UTC(2026, 6, 15, 12)
  return shows(a, winter) !== null && shows(a, winter) === shows(b, winter)
      && shows(a, summer) === shows(b, summer)
}

// The Date carries a wall clock in its UTC fields, so it is read back as UTC.
export const runLabel = (date) => date.toLocaleString(undefined, {
  timeZone: 'UTC',
  weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
})
