# MnDOT I-94 VLM Traffic Monitoring (Prototype)

## Overview
This project analyzes MnDOT freeway camera imagery with a vision-language model.
It polls still-image snapshots (not live video feeds), stores the frames, and
runs Qwen2.5-VL on new or changed images to summarize traffic conditions.

## How It Works
- Polls each camera's snapshot URL on a per-camera interval.
- Saves snapshots to `data/frames` and computes a hash.
- Runs the VLM only when the snapshot changes.
- Logs results to SQLite and keeps raw VLM responses in `data/raw_vlm_outputs`.

## Model
By default the system uses Qwen2.5-VL via OpenRouter:

- Model: `qwen/qwen2.5-vl-32b-instruct`
- Override with `VLM_MODEL`

## Camera Configuration
Cameras are configured in `config/cameras.yaml`:

- `camera_id`, `name`, `corridor`, `direction`
- `snapshot_url` (direct image or metadata endpoint)
- `poll_interval_sec` (per-camera polling interval)

## External APIs & Credentials
Required but not included:

- Camera snapshot endpoints (or metadata endpoints that resolve to images)
- OpenRouter API key for VLM calls

Credentials are injected via environment variables, including:

- `OPENROUTER_API_KEY` or `VLM_API_KEY`
- `VLM_MODEL`
- `SNAPSHOT_URL_TEMPLATE` (optional)
- `CAMERA_METADATA_URL_TEMPLATE` (optional)
- `IMAGE_URL_REGEX` (optional)

## Runtime Notes
- The system uses snapshot polling, not streaming video.
- Logs and the minimal API/dashboard are served via `main.py`.

## Disclaimer
This repository is a prototype and not a production system.
