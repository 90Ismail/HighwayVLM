"""
API server for HighwayVLM.
"""

import os
import threading

from flask import Flask, jsonify, request

from config_loader import load_cameras
from pipeline import run_loop
from storage import init_db, list_cameras, list_logs, upsert_cameras

app = Flask(__name__)

_worker_started = False


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


def _maybe_start_worker():
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    _start_worker()


@app.get("/")
def index():
    _maybe_start_worker()
    return jsonify({"message": "HighwayVLM API"})


@app.get("/api/health")
def health():
    _maybe_start_worker()
    return jsonify({"status": "ok"})


@app.get("/api/cameras")
def cameras():
    _maybe_start_worker()
    _bootstrap()
    return jsonify(list_cameras())


@app.get("/api/logs")
def logs():
    _maybe_start_worker()
    _bootstrap()
    limit_raw = request.args.get("limit", "100")
    try:
        limit = min(int(limit_raw), 500)
    except ValueError:
        limit = 100
    camera_id = request.args.get("camera_id")
    return jsonify(list_logs(limit=limit, camera_id=camera_id))


if __name__ == "__main__":
    _start_worker()
    app.run(host="0.0.0.0", port=8000, debug=True)
