// Generated relative paths are article references, not application routes.
// Only citation records are allowed to resolve internal destinations.
export function externalAnswerHref(href, appOrigin) {
  if (typeof href !== 'string' || !href.trim()) return null
  try {
    const url = new URL(href)
    if (url.protocol === 'mailto:' || url.protocol === 'tel:') return href
    if (!['http:', 'https:'].includes(url.protocol)) return null
    if (url.origin === appOrigin) return null
    return href
  } catch {
    return null
  }
}
