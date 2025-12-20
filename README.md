# highway-vlm  
**UROP Spring 2026 — Advisor: Prof. Seongjin Choi**  

Student Reseacher: Ismail Yusuf
---
## Overview

Initial pipeline draft for exploring the use of **Vision-Language Models (VLMs)** on public Minnesota highway camera feeds as part of a Spring 2026 UROP project.

This repository documents the **early system architecture, data flow, and setup** for running VLM-based scene understanding on a small set of freeway cameras.

---

## Initial Scope

- Use **3–10 MnDOT cameras** along the **I-94 corridor**
- Sample frames from live camera feeds at fixed intervals
- Apply VLM inference to generate natural-language descriptions of traffic scenes
- Identify:
  - Vehicles stopped on the shoulder  
  - Collisions or abnormal stoppages
- Store structured incident logs
- Display live video and logs in a basic web dashboard
- Run inference using **VESSL AI** GPU resources

This repository represents an **initial scope and pipeline draft**, not a finalized system.

---

## Data Source

- Public Minnesota traffic cameras  
  https://511mn.org/list/cameras
- Camera selection limited to the I-94 corridor
- Camera metadata stored locally, including:
  - Camera ID  
  - Traffic direction  
  - Stream URL  

---

## System Design (Initial)

1. Camera streams are ingested and sampled  
2. Frames are passed to a Vision-Language Model  
3. VLM output is returned as natural-language text  
4. Text outputs are parsed into structured logs  
5. Live video and logs are served to a dashboard  

All outputs are timestamped and camera-referenced.

---

## Tech Stack (Initial)

### Backend
- Python
- OpenCV (frame capture only)
- FastAPI
- Vision-Language Models

---

### Infrastructure
- VESSL AI
- Docker

---

### Frontend
- React
- TypeScript
- Vite

---

### Data
- JSON-based incident logs

---

## Project Structure

```text
highway-vlm/
│
├── backend/
│   ├── ingest/
│   │   └── camera_stream.py
│   │
│   ├── vlm/
│   │   ├── prompt.py
│   │   └── inference.py
│   │
│   ├── api/
│   │   └── server.py
│   │
│   └── logs/
│       └── incidents.json
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   └── App.tsx
│   └── vite.config.ts
│
├── vessl/
│   └── experiment.yaml
│
├── configs/
│   └── cameras.json
│
├── requirements.txt
├── Dockerfile
└── README.md
