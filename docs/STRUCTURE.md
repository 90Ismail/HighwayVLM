# Structure Guide

## Top-Level Folders
- `highwayvlm/`
  - Main source package.
  - `api.py`: FastAPI app and startup bootstrapping.
  - `pipeline.py`: polling and orchestration loop.
  - `settings.py`: env vars and filesystem paths.
  - `config_loader.py`: camera config parsing.
  - `storage.py`: SQLite schema, writes, and reads.
  - `ingest/`: snapshot fetch logic.
  - `vlm/`: VLM client and model runner logic.
- `scripts/`
  - Operational entry points.
  - `snapshot.py`: run camera snapshot capture once or in a loop.
  - `run_vlm.py`: run VLM processing once or in a loop.
- `web/`
  - Dashboard/archive HTML and static JS/CSS assets.
- `config/`
  - Configuration data, currently `cameras.yaml`.
- `data/`
  - Runtime artifacts: frame files, raw model outputs, SQLite DB.
- `logs/`
  - Operational JSONL logs (`incidents.jsonl`).
- `legacy/`
  - Compatibility shims from older imports/layouts.
  - `legacy/compat/` contains thin wrappers that forward into `highwayvlm/`.
- `docs/`
  - Architecture and operational docs.

## Runtime Entry Points
- API server: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
- Snapshot runner: `python scripts/snapshot.py`
- VLM runner: `python scripts/run_vlm.py`

## Data Flow
1. Camera definitions are loaded from `config/cameras.yaml`.
2. Snapshots are fetched and written to `data/frames`.
3. New or changed frames are analyzed by the VLM.
4. Logs and archive tables are stored in SQLite and JSONL outputs.
5. API endpoints expose status/logs/incidents/hourly views to the `web/` UI.
