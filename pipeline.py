import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from config_loader import load_cameras
from ingest.fetcher import fetch_snapshot_bytes, save_snapshot
from settings import get_openai_model, get_run_interval_seconds, get_min_vlm_interval_seconds
from storage import init_db, insert_log, sanitize_error_message, upsert_cameras
from vlm.client import VLMClient


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_iso():
    return _utc_now().isoformat()


def _hash_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


@dataclass
class CameraState:
    last_hash: str | None = None
    last_vlm_at: datetime | None = None
    last_result: dict | None = None
    last_skip_at: datetime | None = None


def _should_run_vlm(state, image_hash, force_interval_seconds):
    if state.last_hash != image_hash:
        return True
    if not state.last_vlm_at:
        return True
    elapsed = (_utc_now() - state.last_vlm_at).total_seconds()
    return elapsed >= force_interval_seconds


def run_once(states, client):
    cameras = load_cameras()
    init_db()
    upsert_cameras(cameras)
    min_interval = get_min_vlm_interval_seconds()
    for camera in cameras:
        camera_id = camera.get("camera_id")
        if not camera_id:
            continue
        base_log = {
            "created_at": _utc_iso(),
            "captured_at": None,
            "camera_id": camera_id,
            "camera_name": camera.get("name"),
            "corridor": camera.get("corridor"),
            "direction": camera.get("direction"),
            "observed_direction": None,
            "traffic_state": None,
            "incidents_json": None,
            "notes": None,
            "overall_confidence": None,
            "image_path": None,
            "vlm_model": client.model,
            "raw_response": None,
            "error": None,
            "skipped_reason": None,
        }
        try:
            image_bytes, content_type = fetch_snapshot_bytes(camera)
        except Exception as exc:
            print(f"Snapshot failed for {camera_id}: {exc}")
            base_log["error"] = sanitize_error_message(f"snapshot_failed: {exc}")
            insert_log(base_log)
            continue
        if not image_bytes:
            base_log["skipped_reason"] = "empty_snapshot"
            insert_log(base_log)
            continue
        image_hash = _hash_bytes(image_bytes)
        state = states.setdefault(camera_id, CameraState())
        run_vlm = _should_run_vlm(state, image_hash, min_interval)
        state.last_hash = image_hash
        if not run_vlm:
            now = _utc_now()
            if not state.last_skip_at or (now - state.last_skip_at).total_seconds() >= min_interval:
                base_log["skipped_reason"] = "image_unchanged_within_min_interval"
                insert_log(base_log)
                state.last_skip_at = now
            continue
        captured_at = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        image_path = save_snapshot(camera_id, image_bytes, content_type, captured_at)
        try:
            result, raw_text = client.analyze(camera, image_bytes, captured_at, content_type)
        except Exception as exc:
            print(f"VLM failed for {camera_id}: {exc}")
            base_log["captured_at"] = captured_at
            base_log["image_path"] = str(image_path)
            base_log["error"] = sanitize_error_message(f"vlm_failed: {exc}")
            insert_log(base_log)
            continue
        state.last_vlm_at = _utc_now()
        state.last_result = result.model_dump()
        insert_log(
            {
                "created_at": _utc_iso(),
                "captured_at": captured_at,
                "camera_id": camera_id,
                "camera_name": camera.get("name"),
                "corridor": camera.get("corridor"),
                "direction": camera.get("direction"),
                "observed_direction": result.observed_direction,
                "traffic_state": result.traffic_state,
                "incidents_json": json.dumps([i.model_dump() for i in result.incidents], ensure_ascii=True),
                "notes": result.notes,
                "overall_confidence": result.overall_confidence,
                "image_path": str(image_path),
                "vlm_model": client.model,
                "raw_response": raw_text,
                "error": None,
                "skipped_reason": None,
            }
        )


def run_loop():
    interval = get_run_interval_seconds()
    client = VLMClient(model=get_openai_model())
    states = {}
    while True:
        run_once(states, client)
        time.sleep(interval)
