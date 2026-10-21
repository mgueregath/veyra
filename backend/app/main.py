from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import live, remote, training
from .config import settings
from .routers import (
    admin,
    assisted,
    auth,
    datasets,
    detect,
    evaluations,
    frames,
    models,
    projects,
    videos,
    worker,
    workflow,
)
from .routers import (
    live as live_router,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    training.recover_interrupted()  # lo que quedó «en curso» al reiniciar se marca como interrumpido
    live.recover_orphans()
    remote.recover()  # los trabajos remotos que nadie espera ya se cancelan
    yield
    live.stop_all()  # no dejar procesos de detección sueltos al apagar la API


app = FastAPI(title="Veyra", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=False,
)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(frames.router)
app.include_router(videos.router)
app.include_router(workflow.router)
app.include_router(datasets.router)
app.include_router(evaluations.router)
app.include_router(models.router)
app.include_router(assisted.router)
app.include_router(live_router.router)
app.include_router(detect.router)
app.include_router(admin.router)
app.include_router(worker.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# En producción la API sirve el frontend compilado (frontend/dist); en desarrollo se usa `npm run dev`.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
