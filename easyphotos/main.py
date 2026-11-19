"""EasyPhotos web server."""
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db
from .routes import admin, assets, auth, people, public

log = logging.getLogger("easyphotos")

_worker_proc = None
_stop = threading.Event()


def _start_worker():
    global _worker_proc
    cmd = [sys.executable, "-m", "easyphotos.worker"]
    # Lowest CPU priority, low disk priority: other apps always go first,
    # but the worker never stops completely.
    if shutil.which("ionice"):
        cmd = ["ionice", "-c", "2", "-n", "7"] + cmd
    if shutil.which("nice"):
        cmd = ["nice", "-n", "19"] + cmd
    _worker_proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    log.info("START Background worker started (low priority, pid %s)", _worker_proc.pid)


def _supervise():
    while not _stop.wait(10):
        if _worker_proc is not None and _worker_proc.poll() is not None:
            log.warning("Background worker stopped (code %s); restarting", _worker_proc.returncode)
            time.sleep(5)
            _start_worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init()
    from . import settings as _s
    log.info("START EasyPhotos %s on port %s", __version__, config.PORT)
    log.info("START Database %s (%s)", config.DB_PATH, "encrypted" if config.DB_KEY and db._sqlcipher else "NOT encrypted - set EP_DB_KEY on a new install")
    log.info("START Thumbnails folder: %s", _s.thumbs_dir())
    libs = db.q("SELECT name, path, enabled FROM libraries")
    for lib in libs:
        log.info("START Library %s -> %s%s", lib["name"], lib["path"], "" if lib["enabled"] else " (turned off)")
    if not libs:
        log.info("START No libraries yet. Add one in the web page: Library > Add library")
    if os.environ.get("EP_NO_WORKER") != "1":
        _start_worker()
        threading.Thread(target=_supervise, daemon=True).start()
    yield
    _stop.set()
    if _worker_proc is not None:
        _worker_proc.terminate()
        try:
            _worker_proc.wait(10)
        except Exception:
            _worker_proc.kill()


app = FastAPI(title="EasyPhotos", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    p = request.url.path
    if p.startswith("/static/js/") or p.startswith("/static/css/"):
        # Always check for a newer version after an update (cheap: unchanged files return 304).
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(people.router)
app.include_router(admin.router)
app.include_router(public.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/version")
def version():
    return {"version": __version__}


app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(config.WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(config.WEB_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def run():
    config.ensure_dirs()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(config.LOG_DIR / "web.log", maxBytes=2_000_000, backupCount=2))
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, proxy_headers=True, forwarded_allow_ips="*", log_level="warning")


if __name__ == "__main__":
    run()
