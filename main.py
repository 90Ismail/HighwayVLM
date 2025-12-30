import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from config_loader import load_cameras
from pipeline import run_loop
from settings import FRAMES_DIR
from storage import get_status_summary, init_db, list_cameras, list_latest_log, list_logs, upsert_cameras

app = FastAPI(title="HighwayVLM API")

_worker_started = False
_ROOT = Path(__file__).resolve().parent
_WEB_DIR = _ROOT / "web"
_STATIC_DIR = _WEB_DIR / "static"

FRAMES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/frames", StaticFiles(directory=FRAMES_DIR), name="frames")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _bootstrap():
    init_db()
    cameras = load_cameras()
    if cameras:
        upsert_cameras(cameras)


def _start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()


@app.on_event("startup")
def startup():
    _bootstrap()
    _start_worker()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return FileResponse(_WEB_DIR / "dashboard.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cameras")
def cameras():
    return list_cameras()


@app.get("/logs/latest")
def logs_latest(camera_id: Optional[str] = None):
    return list_latest_log(camera_id=camera_id) or {}


@app.get("/logs")
def logs(
    camera_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    return list_logs(limit=limit, camera_id=camera_id)


@app.get("/status/summary")
def status_summary():
    return get_status_summary()
