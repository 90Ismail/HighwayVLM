# System Architecture

## Layers
1. `web/` (presentation)
   - Static dashboard and archive pages.
   - Reads data from API endpoints.
2. `highwayvlm/api.py` (API layer)
   - Serves web pages and API routes.
   - Starts background worker loop on startup.
3. `highwayvlm/pipeline.py` (orchestration layer)
   - Polls cameras.
   - Deduplicates unchanged frames.
   - Calls VLM client only when due.
4. `highwayvlm/ingest/` and `highwayvlm/vlm/` (integration layer)
   - Ingest: snapshot fetching and local file storage.
   - VLM: model request/response handling.
5. `highwayvlm/storage.py` (persistence layer)
   - SQLite schema and query helpers.
   - Incident/event/hourly archive writes.
6. `config/`, `data/`, `logs/` (config + state)
   - Declarative config + runtime artifacts.

## Runtime Flow
1. API startup initializes DB and camera catalog.
2. Worker loop loads cameras and fetches snapshots.
3. New frames trigger VLM analysis.
4. Parsed outputs are written to `vlm_logs`, `incident_events`, and `hourly_snapshots`.
5. UI reads aggregated endpoints from FastAPI.

## Entry Points
- Primary: `main.py` -> `highwayvlm.api:app`
- Operational scripts:
  - `scripts/snapshot.py`
  - `scripts/run_vlm.py`
- Legacy wrappers retained under `legacy/compat/`.
