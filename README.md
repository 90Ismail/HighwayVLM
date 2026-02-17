# HighwayVLM

Traffic camera snapshot polling + VLM analysis + dashboard/API.

## Quick Start
1. Install deps:
   `pip install -r requirements.txt`
2. Set env vars in `.env`:
   `OPENAI_API_KEY` (or `VLM_API_KEY`) and optional overrides.
3. Run API + worker:
   `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
4. Open:
   `http://localhost:8000`
5. Optional one-off runners:
   - `python scripts/snapshot.py`
   - `python scripts/run_vlm.py`

## Project Layout
- `highwayvlm/`: primary application source code (single source of truth).
- `web/`: HTML/CSS/JS for dashboard and archive pages.
- `config/`: camera configuration (`cameras.yaml`).
- `data/`: frames, raw model outputs, SQLite DB.
- `logs/`: append-only operational logs.
- `scripts/`: operational entry points for one-off ingest/VLM jobs.
- `legacy/`: compatibility shims from the previous layout.
- `docs/`: architecture and structure docs.

## Core Runtime Flow
1. `highwayvlm/api.py` starts FastAPI and background worker.
2. `highwayvlm/pipeline.py` polls cameras and deduplicates unchanged frames.
3. `highwayvlm/vlm/client.py` analyzes frames with the configured model.
4. `highwayvlm/storage.py` writes logs/events/hourly snapshots to SQLite.
5. `web/` pages read API endpoints to render dashboard + archives.

## Documentation
- `docs/ARCHITECTURE.md`: component boundaries and data flow.
- `docs/STRUCTURE.md`: folder-by-folder guide.
