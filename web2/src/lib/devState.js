// Dev-only: ?force=loading|empty|error renders that state on any list screen,
// so all three stay reachable without hand-faking data.
export const forcedState = () =>
  new URLSearchParams(window.location.search).get('force')
