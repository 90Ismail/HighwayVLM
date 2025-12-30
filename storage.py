import json
import re
import sqlite3
from datetime import datetime, timezone

from settings import INCIDENTS_LOG_PATH, get_db_path


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


_REDACTION_RULES = [
    (re.compile(r"sk-[A-Za-z0-9]{10,}"), "sk-REDACTED"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{10,}"), r"\1REDACTED"),
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)"), r"\1REDACTED"),
    (re.compile(r"(?i)(token\s*[:=]\s*)(\S+)"), r"\1REDACTED"),
]


def sanitize_error_message(value):
    if not value or not isinstance(value, str):
        return value
    sanitized = value
    for pattern, replacement in _REDACTION_RULES:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _connect():
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _ensure_columns(conn, table, columns):
    existing = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, ddl in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cameras (
                camera_id TEXT PRIMARY KEY,
                name TEXT,
                snapshot_url TEXT,
                source_url TEXT,
                corridor TEXT,
                direction TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vlm_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                captured_at TEXT,
                camera_id TEXT,
                camera_name TEXT,
                corridor TEXT,
                direction TEXT,
                observed_direction TEXT,
                traffic_state TEXT,
                incidents_json TEXT,
                notes TEXT,
                overall_confidence REAL,
                image_path TEXT,
                vlm_model TEXT,
                raw_response TEXT,
                error TEXT,
                skipped_reason TEXT,
                FOREIGN KEY(camera_id) REFERENCES cameras(camera_id)
            )
            """
        )
        _ensure_columns(
            conn,
            "cameras",
            {
                "source_url": "source_url TEXT",
            },
        )
        _ensure_columns(
            conn,
            "vlm_logs",
            {
                "created_at": "created_at TEXT",
                "captured_at": "captured_at TEXT",
                "camera_id": "camera_id TEXT",
                "camera_name": "camera_name TEXT",
                "corridor": "corridor TEXT",
                "direction": "direction TEXT",
                "observed_direction": "observed_direction TEXT",
                "traffic_state": "traffic_state TEXT",
                "incidents_json": "incidents_json TEXT",
                "notes": "notes TEXT",
                "overall_confidence": "overall_confidence REAL",
                "image_path": "image_path TEXT",
                "vlm_model": "vlm_model TEXT",
                "raw_response": "raw_response TEXT",
                "error": "error TEXT",
                "skipped_reason": "skipped_reason TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vlm_logs_camera ON vlm_logs(camera_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vlm_logs_created ON vlm_logs(created_at)"
        )


def upsert_cameras(cameras):
    if not cameras:
        return
    with _connect() as conn:
        for camera in cameras:
            camera_id = camera.get("camera_id")
            if not camera_id:
                continue
            conn.execute(
                """
                INSERT INTO cameras (
                    camera_id,
                    name,
                    snapshot_url,
                    source_url,
                    corridor,
                    direction,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    name = excluded.name,
                    snapshot_url = excluded.snapshot_url,
                    source_url = excluded.source_url,
                    corridor = excluded.corridor,
                    direction = excluded.direction,
                    updated_at = excluded.updated_at
                """,
                (
                    camera_id,
                    camera.get("name"),
                    camera.get("snapshot_url"),
                    camera.get("source_url"),
                    camera.get("corridor"),
                    camera.get("direction"),
                    _utc_now(),
                ),
            )


def list_cameras():
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT camera_id, name, snapshot_url, source_url, corridor, direction, updated_at
            FROM cameras
            ORDER BY camera_id
            """
        ).fetchall()
    return [
        {
            "camera_id": row[0],
            "name": row[1],
            "snapshot_url": row[2],
            "source_url": row[3],
            "corridor": row[4],
            "direction": row[5],
            "updated_at": row[6],
        }
        for row in rows
    ]


def insert_log(log_entry):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO vlm_logs (
                created_at,
                captured_at,
                camera_id,
                camera_name,
                corridor,
                direction,
                observed_direction,
                traffic_state,
                incidents_json,
                notes,
                overall_confidence,
                image_path,
                vlm_model,
                raw_response,
                error,
                skipped_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_entry.get("created_at"),
                log_entry.get("captured_at"),
                log_entry.get("camera_id"),
                log_entry.get("camera_name"),
                log_entry.get("corridor"),
                log_entry.get("direction"),
                log_entry.get("observed_direction"),
                log_entry.get("traffic_state"),
                log_entry.get("incidents_json"),
                log_entry.get("notes"),
                log_entry.get("overall_confidence"),
                log_entry.get("image_path"),
                log_entry.get("vlm_model"),
                log_entry.get("raw_response"),
                log_entry.get("error"),
                log_entry.get("skipped_reason"),
            ),
        )
    _append_incident_log(log_entry)


def _parse_incidents(incidents_payload):
    if not incidents_payload:
        return []
    if isinstance(incidents_payload, list):
        return incidents_payload
    try:
        parsed = json.loads(incidents_payload)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _append_incident_log(log_entry):
    incidents = _parse_incidents(log_entry.get("incidents_json"))
    if not incidents:
        return
    payload = {
        "created_at": log_entry.get("created_at"),
        "captured_at": log_entry.get("captured_at"),
        "camera_id": log_entry.get("camera_id"),
        "camera_name": log_entry.get("camera_name"),
        "corridor": log_entry.get("corridor"),
        "direction": log_entry.get("direction"),
        "observed_direction": log_entry.get("observed_direction"),
        "traffic_state": log_entry.get("traffic_state"),
        "incidents": incidents,
        "notes": log_entry.get("notes"),
        "overall_confidence": log_entry.get("overall_confidence"),
        "image_path": log_entry.get("image_path"),
        "vlm_model": log_entry.get("vlm_model"),
    }
    INCIDENTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INCIDENTS_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def list_logs(limit=100, camera_id=None):
    query = (
        "SELECT id, created_at, captured_at, camera_id, camera_name, corridor, "
        "direction, observed_direction, traffic_state, incidents_json, notes, "
        "overall_confidence, image_path, vlm_model, raw_response, error, skipped_reason "
        "FROM vlm_logs"
    )
    params = []
    if camera_id:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        incidents = []
        if row[9]:
            try:
                incidents = json.loads(row[9])
            except json.JSONDecodeError:
                incidents = []
        error = sanitize_error_message(row[15])
        skipped_reason = sanitize_error_message(row[16])
        results.append(
            {
                "id": row[0],
                "created_at": row[1],
                "captured_at": row[2],
                "camera_id": row[3],
                "camera_name": row[4],
                "corridor": row[5],
                "direction": row[6],
                "observed_direction": row[7],
                "traffic_state": row[8],
                "incidents": incidents,
                "notes": row[10],
                "overall_confidence": row[11],
                "image_path": row[12],
                "vlm_model": row[13],
                "raw_response": row[14],
                "error": error,
                "skipped_reason": skipped_reason,
            }
        )
    return results


def list_latest_log(camera_id=None):
    query = (
        "SELECT id, created_at, captured_at, camera_id, camera_name, corridor, "
        "direction, observed_direction, traffic_state, incidents_json, notes, "
        "overall_confidence, image_path, vlm_model, raw_response, error, skipped_reason "
        "FROM vlm_logs"
    )
    params = []
    if camera_id:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY created_at DESC LIMIT 1"
    with _connect() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        return None
    incidents = []
    if row[9]:
        try:
            incidents = json.loads(row[9])
        except json.JSONDecodeError:
            incidents = []
    error = sanitize_error_message(row[15])
    skipped_reason = sanitize_error_message(row[16])
    return {
        "id": row[0],
        "created_at": row[1],
        "captured_at": row[2],
        "camera_id": row[3],
        "camera_name": row[4],
        "corridor": row[5],
        "direction": row[6],
        "observed_direction": row[7],
        "traffic_state": row[8],
        "incidents": incidents,
        "notes": row[10],
        "overall_confidence": row[11],
        "image_path": row[12],
        "vlm_model": row[13],
        "raw_response": row[14],
        "error": error,
        "skipped_reason": skipped_reason,
    }


def get_status_summary():
    cameras = list_cameras()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT l.camera_id, l.created_at, l.captured_at, l.traffic_state,
                   l.incidents_json, l.overall_confidence, l.error, l.skipped_reason,
                   l.observed_direction, l.notes, l.image_path
            FROM vlm_logs l
            INNER JOIN (
                SELECT camera_id, MAX(created_at) AS max_created
                FROM vlm_logs
                GROUP BY camera_id
            ) latest
            ON l.camera_id = latest.camera_id AND l.created_at = latest.max_created
            """
        ).fetchall()
    latest_by_camera = {}
    for row in rows:
        incidents = []
        if row[4]:
            try:
                incidents = json.loads(row[4])
            except json.JSONDecodeError:
                incidents = []
        error = sanitize_error_message(row[6])
        skipped_reason = sanitize_error_message(row[7])
        latest_by_camera[row[0]] = {
            "created_at": row[1],
            "captured_at": row[2],
            "traffic_state": row[3],
            "incidents": incidents,
            "overall_confidence": row[5],
            "error": error,
            "skipped_reason": skipped_reason,
            "observed_direction": row[8],
            "notes": row[9],
            "image_path": row[10],
        }
    summary = []
    for camera in cameras:
        camera_id = camera.get("camera_id")
        latest = latest_by_camera.get(camera_id)
        summary.append(
            {
                "camera_id": camera_id,
                "name": camera.get("name"),
                "corridor": camera.get("corridor"),
                "direction": camera.get("direction"),
                "latest_log": latest,
            }
        )
    return summary
