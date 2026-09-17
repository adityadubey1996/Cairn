"""ai-brain — chat over the LLM wikis. Git is the only storage of knowledge;
this service just syncs, indexes and serves it."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, connectors, corpus, models, projects, storage
from .auth import current_user, router as auth_router
from .routers.chat import router as chat_router
from .routers.connections import router as connections_router
from .routers.connectors import router as connectors_router
from .routers.google import router as google_router
from .routers.oauth import router as oauth_router
from .routers.internal import router as internal_router
from .routers.people import router as people_router
from .routers.projects import router as projects_router
from .routers.repos import router as repos_router
from .routers.search import router as search_router
from .routers.settings import router as settings_router
from .routers.sources import router as sources_router
from .routers.pipeline import router as pipeline_router
from .routers.timeline import router as timeline_router
from .routers.wiki import router as wiki_router

log = logging.getLogger("cairn")


async def _sync_loop():
    while True:
        try:
            # Pull first: another instance may have absorbed since last pass,
            # and this is what makes several deployments converge on one wiki
            # rather than each drifting into its own. No-op without S3.
            await asyncio.to_thread(storage.pull)
        except Exception:
            log.exception("s3 pull failed; indexing the local wiki as-is")
        try:
            for r in await asyncio.to_thread(corpus.sync):
                if r["status"] != "unchanged":
                    log.warning("corpus %s: %s", r["status"], r["root"])
        except Exception:
            log.exception("corpus sync failed")
        await asyncio.sleep(900)


async def _connector_loop():
    """Timed browser-connector runs — the 'cyclic job' for WhatsApp/LinkedIn.
    Off unless CONNECTOR_SYNC_INTERVAL_MIN > 0; each connector fails alone
    (a logged-out platform must not block the other)."""
    while True:
        await asyncio.sleep(config.CONNECTOR_SYNC_INTERVAL_MIN * 60)
        for cid in ("whatsapp", "linkedin"):
            try:
                r = await asyncio.to_thread(connectors.run_now, cid)
                log.info("connector %s: %s", cid, r)
            except Exception as e:
                log.warning("connector %s run failed: %s", cid, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.ensure_schema()
    connectors.ensure_rows()
    projects.ensure_default()
    # Pull before the first corpus sync, or the index is built from an empty
    # tree and every search returns nothing until the next 15-minute pass.
    # No-op unless S3_BUCKET is set.
    try:
        if n := await asyncio.to_thread(storage.pull):
            log.info("pulled %d wiki files from s3", n)
    except Exception:
        # A durable copy that cannot be reached must not stop the service —
        # the local volume, or the git-seeded image, is still a valid wiki.
        log.exception("s3 pull failed; continuing with the local wiki")
    task = asyncio.create_task(_sync_loop())  # boot sync + 15-min guarantee
    connector_task = None
    if config.CONNECTOR_SYNC_INTERVAL_MIN > 0:
        connector_task = asyncio.create_task(_connector_loop())
    yield
    task.cancel()
    if connector_task:
        connector_task.cancel()


app = FastAPI(title="ai-brain", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev; prod is same-origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(internal_router)
app.include_router(chat_router)
app.include_router(search_router)
app.include_router(wiki_router)
app.include_router(repos_router)
app.include_router(google_router)
# After google_router so /api/google/status keeps its specific handler;
# this one only claims /api/{provider}/authorize and /callback.
app.include_router(oauth_router)
app.include_router(connectors_router)
app.include_router(sources_router)
app.include_router(pipeline_router)
app.include_router(projects_router)
app.include_router(connections_router)
app.include_router(timeline_router)
app.include_router(people_router)
app.include_router(settings_router)


@app.get("/health")
def health():
    return {"ok": True, "auth_mode": config.AUTH_MODE,
            "google_client_id": config.GOOGLE_CLIENT_ID,
            "dev_ui": config.DEV_UI,
            "wiki_roots": [str(p) for p in corpus.roots()]}


@app.get("/api/me")
def me(email: str = Depends(current_user)):
    return {"email": email}


# V2 is the shipped UI. web/ is still in the tree but no longer served — it was
# reachable only because this pointed at it, and web2/ had every screen it had
# once Repos and Pipeline were ported across.
class _RevalidateIndex(StaticFiles):
    """Cache the hashed assets hard; never cache the page that names them.

    Vite fingerprints every asset, so those are safe to cache for a year. But
    index.html points AT a fingerprint, and it is served with only an ETag —
    which browsers are free to satisfy from memory for the session. The result
    is a page that keeps loading a bundle that no longer exists on disk, with
    no error: the old JS simply runs and the new behaviour appears to be
    missing. That cost real debugging time twice before this header existed.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in ("", ".", "index.html") or path.endswith("/index.html"):
            response.headers["cache-control"] = "no-cache, must-revalidate"
        elif "/assets/" in f"/{path}":
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


_dist = config.ROOT / "web2" / "dist"
if _dist.is_dir():  # prod: FastAPI serves the built UI; dev uses Vite's proxy
    app.mount("/", _RevalidateIndex(directory=_dist, html=True), name="web")
