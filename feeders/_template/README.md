# Connector template

Copy this folder to `feeders/<your-connector>/` and edit it. **Nothing outside
your folder needs to change** — the registry discovers `connector.py`, the app
mounts `router.py` if present, and the Connect screen renders whatever fields
your SPEC declares. That is what lets connectors be built in parallel.

| File | Purpose | Required |
|---|---|---|
| `connector.py` | `SPEC = Connector(...)` — id, auth, the fields the user must supply | yes |
| `sync.py` | `run()` fetches and writes; `probe()` proves access without writing | yes |
| `router.py` | your own endpoints, when a consent step or picker needs them | no |
| `SETUP.md` | what a user creates at the provider, in their words | yes |
| `test_sync.py` | offline tests: no network, no database | yes |

## What your `run()` must honour

- Write source files and `raw/inbox/` entries in the existing shape, and record
  rows via `server.sources.record()`. Citations depend on it.
- Return `SyncResult(seen, written, failures)` from `feeders/result.py`. A
  partial failure must not advance the watermark.
- Read per-connection settings with `connections.settings_for(connection_id)` —
  secrets included, never from `.env`.
- Validate scope with `feeders/options.py` before the first remote call.
- Back off on HTTP 429 using `Retry-After`.
- Never write, send, post or delete at the provider. Read-only.
