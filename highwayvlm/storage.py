import json
import re
import sqlite3
from datetime import datetime, timezone

from highwayvlm.settings import INCIDENTS_LOG_PATH, get_db_path


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
                frame_hash TEXT,
                last_seen_at TEXT,
                last_processed_at TEXT,
                FOREIGN KEY(camera_id) REFERENCES cameras(camera_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                captured_at TEXT,
                camera_id TEXT,
                camera_name TEXT,
                corridor TEXT,
                direction TEXT,
                observed_direction TEXT,
                traffic_state TEXT,
                incident_type TEXT,
                severity TEXT,
                description TEXT,
                notes TEXT,
                overall_confidence REAL,
                image_path TEXT,
                vlm_model TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hourly_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT,
                camera_name TEXT,
                corridor TEXT,
                direction TEXT,
                hour_bucket TEXT,
                created_at TEXT,
                captured_at TEXT,
                image_path TEXT,
                frame_hash TEXT,
                traffic_state TEXT,
                incident_count INTEGER,
                status TEXT,
                summary TEXT,
                error TEXT,
                skipped_reason TEXT,
                UNIQUE(camera_id, hour_bucket)
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
                "frame_hash": "frame_hash TEXT",
                "last_seen_at": "last_seen_at TEXT",
                "last_processed_at": "last_processed_at TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vlm_logs_camera ON vlm_logs(camera_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vlm_logs_created ON vlm_logs(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_incident_events_camera_created ON incident_events(camera_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hourly_snapshots_camera_hour ON hourly_snapshots(camera_id, hour_bucket)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hourly_snapshots_hour ON hourly_snapshots(hour_bucket)"
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


def sync_cameras(cameras):
    if not cameras:
        return
    upsert_cameras(cameras)
    camera_ids = sorted(
        {camera.get("camera_id") for camera in cameras if camera.get("camera_id")}
    )
    if not camera_ids:
        return
    placeholders = ",".join("?" for _ in camera_ids)
    with _connect() as conn:
        conn.execute(
            f"DELETE FROM cameras WHERE camera_id NOT IN ({placeholders})",
            camera_ids,
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
                skipped_reason,
                frame_hash,
                last_seen_at,
                last_processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                log_entry.get("frame_hash"),
                log_entry.get("last_seen_at"),
                log_entry.get("last_processed_at"),
            ),
        )
    _append_incident_log(log_entry)
    _archive_incident_events(log_entry)
    _archive_hourly_snapshot(log_entry)


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


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_hour_bucket(log_entry):
    captured = _parse_datetime(log_entry.get("captured_at"))
    created = _parse_datetime(log_entry.get("created_at"))
    value = captured or created
    if not value:
        return None
    hour = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return hour.isoformat().replace("+00:00", "Z")


def _build_hourly_summary(log_entry, incidents):
    created_at = log_entry.get("created_at") or "unknown time"
    camera_name = log_entry.get("camera_name") or log_entry.get("camera_id") or "unknown camera"
    notes = (log_entry.get("notes") or "").strip()
    error = sanitize_error_message(log_entry.get("error"))
    skipped_reason = sanitize_error_message(log_entry.get("skipped_reason"))
    traffic_state = (log_entry.get("traffic_state") or "unknown").replace("_", " ")
    if error:
        return (
            f"Hourly heartbeat for {camera_name} at {created_at} recorded an error while polling or analyzing this "
            f"camera: {error}. The system remained active, but this interval should be reviewed for pipeline health."
        )
    if incidents:
        incident_types = []
        for incident in incidents:
            kind = (incident.get("type") or "incident").replace("_", " ")
            incident_types.append(kind)
        label = ", ".join(incident_types)
        if notes:
            return (
                f"Hourly heartbeat captured active incident conditions for {camera_name} with traffic state "
                f"{traffic_state}: {notes}. Incident types observed in this frame include {label}."
            )
        return (
            f"Hourly heartbeat captured active incident conditions for {camera_name} with traffic state "
            f"{traffic_state}. Incident types observed in this frame include {label}."
        )
    if notes:
        return (
            f"Hourly heartbeat confirms camera coverage for {camera_name} with traffic state {traffic_state}. "
            f"Summary: {notes}"
        )
    if skipped_reason:
        return (
            f"Hourly heartbeat captured a frame for {camera_name} but detailed VLM analysis was skipped for this "
            f"interval due to {skipped_reason}; this still confirms the ingest pipeline was active."
        )
    return (
        f"Hourly heartbeat confirms {camera_name} was reachable and a frame was stored for this interval; "
        "the pipeline appears operational for this camera."
    )


def _archive_incident_events(log_entry):
    incidents = _parse_incidents(log_entry.get("incidents_json"))
    if not incidents:
        return
    base_notes = (log_entry.get("notes") or "").strip()
    with _connect() as conn:
        for incident in incidents:
            if isinstance(incident, dict):
                incident_type = incident.get("type")
                severity = incident.get("severity")
                description = incident.get("description")
            else:
                incident_type = "incident"
                severity = "low"
                description = str(incident)
            event_notes = base_notes
            if not event_notes:
                kind = (incident_type or "incident").replace("_", " ")
                level = severity or "low"
                details = description or "No detailed summary was provided by the model."
                event_notes = f"{kind} ({level}): {details}"
            conn.execute(
                """
                INSERT INTO incident_events (
                    created_at,
                    captured_at,
                    camera_id,
                    camera_name,
                    corridor,
                    direction,
                    observed_direction,
                    traffic_state,
                    incident_type,
                    severity,
                    description,
                    notes,
                    overall_confidence,
                    image_path,
                    vlm_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    incident_type,
                    severity,
                    description,
                    event_notes,
                    log_entry.get("overall_confidence"),
                    log_entry.get("image_path"),
                    log_entry.get("vlm_model"),
                ),
            )


def _archive_hourly_snapshot(log_entry):
    image_path = log_entry.get("image_path")
    camera_id = log_entry.get("camera_id")
    captured_at = log_entry.get("captured_at")
    if not image_path or not camera_id or not captured_at:
        return
    hour_bucket = _to_hour_bucket(log_entry)
    if not hour_bucket:
        return
    incidents = _parse_incidents(log_entry.get("incidents_json"))
    error = sanitize_error_message(log_entry.get("error"))
    skipped_reason = sanitize_error_message(log_entry.get("skipped_reason"))
    if error:
        status = "error"
    elif incidents:
        status = "incident"
    elif skipped_reason:
        status = "skipped"
    elif log_entry.get("traffic_state"):
        status = "healthy"
    else:
        status = "unknown"
    summary = _build_hourly_summary(log_entry, incidents)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO hourly_snapshots (
                camera_id,
                camera_name,
                corridor,
                direction,
                hour_bucket,
                created_at,
                captured_at,
                image_path,
                frame_hash,
                traffic_state,
                incident_count,
                status,
                summary,
                error,
                skipped_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id, hour_bucket) DO NOTHING
            """,
            (
                camera_id,
                log_entry.get("camera_name"),
                log_entry.get("corridor"),
                log_entry.get("direction"),
                hour_bucket,
                log_entry.get("created_at"),
                log_entry.get("captured_at"),
                image_path,
                log_entry.get("frame_hash"),
                log_entry.get("traffic_state"),
                len(incidents),
                status,
                summary,
                error,
                skipped_reason,
            ),
        )


def list_logs(limit=100, camera_id=None):
    query = (
        "SELECT id, created_at, captured_at, camera_id, camera_name, corridor, "
        "direction, observed_direction, traffic_state, incidents_json, notes, "
        "overall_confidence, image_path, vlm_model, raw_response, error, skipped_reason, "
        "frame_hash, last_seen_at, last_processed_at "
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
                "frame_hash": row[17],
                "last_seen_at": row[18],
                "last_processed_at": row[19],
            }
        )
    return results


def list_latest_log(camera_id=None):
    query = (
        "SELECT id, created_at, captured_at, camera_id, camera_name, corridor, "
        "direction, observed_direction, traffic_state, incidents_json, notes, "
        "overall_confidence, image_path, vlm_model, raw_response, error, skipped_reason, "
        "frame_hash, last_seen_at, last_processed_at "
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
        "frame_hash": row[17],
        "last_seen_at": row[18],
        "last_processed_at": row[19],
    }


def get_status_summary(cameras=None):
    cameras = cameras or list_cameras()
    with _connect() as conn:
        latest_rows = conn.execute(
            """
            SELECT l.camera_id, l.created_at, l.captured_at, l.traffic_state,
                   l.incidents_json, l.overall_confidence, l.error, l.skipped_reason,
                   l.observed_direction, l.notes, l.image_path, l.frame_hash,
                   l.last_seen_at, l.last_processed_at
            FROM vlm_logs l
            INNER JOIN (
                SELECT camera_id, MAX(created_at) AS max_created
                FROM vlm_logs
                GROUP BY camera_id
            ) latest
            ON l.camera_id = latest.camera_id AND l.created_at = latest.max_created
            """
        ).fetchall()
        analysis_rows = conn.execute(
            """
            SELECT l.camera_id, l.created_at, l.captured_at, l.traffic_state,
                   l.incidents_json, l.overall_confidence, l.error, l.skipped_reason,
                   l.observed_direction, l.notes, l.image_path, l.frame_hash,
                   l.last_seen_at, l.last_processed_at
            FROM vlm_logs l
            INNER JOIN (
                SELECT camera_id, MAX(created_at) AS max_created
                FROM vlm_logs
                WHERE traffic_state IS NOT NULL
                GROUP BY camera_id
            ) latest
            ON l.camera_id = latest.camera_id AND l.created_at = latest.max_created
            """
        ).fetchall()

    def _row_to_log(row):
        incidents = []
        if row[4]:
            try:
                incidents = json.loads(row[4])
            except json.JSONDecodeError:
                incidents = []
        error = sanitize_error_message(row[6])
        skipped_reason = sanitize_error_message(row[7])
        return {
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
            "frame_hash": row[11],
            "last_seen_at": row[12],
            "last_processed_at": row[13],
        }

    latest_by_camera = {row[0]: _row_to_log(row) for row in latest_rows}
    analysis_by_camera = {row[0]: _row_to_log(row) for row in analysis_rows}
    summary = []
    for camera in cameras:
        camera_id = camera.get("camera_id")
        latest = latest_by_camera.get(camera_id)
        analysis = analysis_by_camera.get(camera_id)
        summary.append(
            {
                "camera_id": camera_id,
                "name": camera.get("name"),
                "corridor": camera.get("corridor"),
                "direction": camera.get("direction"),
                "latest_log": latest,
                "analysis_log": analysis,
            }
        )
    return summary


def list_incident_events(limit=200, camera_id=None):
    query = (
        "SELECT id, created_at, captured_at, camera_id, camera_name, corridor, direction, "
        "observed_direction, traffic_state, incident_type, severity, description, notes, "
        "overall_confidence, image_path, vlm_model "
        "FROM incident_events"
    )
    params = []
    if camera_id:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
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
            "incident_type": row[9],
            "severity": row[10],
            "description": row[11],
            "notes": row[12],
            "overall_confidence": row[13],
            "image_path": row[14],
            "vlm_model": row[15],
        }
        for row in rows
    ]


def list_hourly_snapshots(limit=336, camera_id=None):
    query = (
        "SELECT id, camera_id, camera_name, corridor, direction, hour_bucket, created_at, captured_at, "
        "image_path, frame_hash, traffic_state, incident_count, status, summary, error, skipped_reason "
        "FROM hourly_snapshots"
    )
    params = []
    if camera_id:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY hour_bucket DESC, id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        results.append(
            {
                "id": row[0],
                "camera_id": row[1],
                "camera_name": row[2],
                "corridor": row[3],
                "direction": row[4],
                "hour_bucket": row[5],
                "created_at": row[6],
                "captured_at": row[7],
                "image_path": row[8],
                "frame_hash": row[9],
                "traffic_state": row[10],
                "incident_count": row[11],
                "status": row[12],
                "summary": row[13],
                "error": sanitize_error_message(row[14]),
                "skipped_reason": sanitize_error_message(row[15]),
            }
        )
    return results


def get_archive_overview(camera_id=None):
    incidents_where = " WHERE camera_id = ?" if camera_id else ""
    hourly_where = " WHERE camera_id = ?" if camera_id else ""
    params = [camera_id] if camera_id else []
    with _connect() as conn:
        incident_total = conn.execute(
            f"SELECT COUNT(*) FROM incident_events{incidents_where}",
            params,
        ).fetchone()[0]
        hourly_total = conn.execute(
            f"SELECT COUNT(*) FROM hourly_snapshots{hourly_where}",
            params,
        ).fetchone()[0]
        latest_incident_at = conn.execute(
            f"SELECT MAX(created_at) FROM incident_events{incidents_where}",
            params,
        ).fetchone()[0]
        latest_hourly_bucket = conn.execute(
            f"SELECT MAX(hour_bucket) FROM hourly_snapshots{hourly_where}",
            params,
        ).fetchone()[0]
    return {
        "camera_id": camera_id,
        "incident_total": incident_total or 0,
        "hourly_total": hourly_total or 0,
        "latest_incident_at": latest_incident_at,
        "latest_hour_bucket": latest_hourly_bucket,
    }
