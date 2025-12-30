# MnDOT I-94 VLM Traffic Monitoring (Prototype)

## Overview
This repository defines an early-stage prototype for evaluating
Vision-Language Models (VLMs) on MnDOT freeway camera imagery.

The project is intentionally minimal and research-focused.

---

## Project Phases

### Part 1  Scaffolding (Current)
- Repository structure
- External API framing
- Credential-agnostic design
- No execution logic

### Part 2  API Integration
- Camera snapshot ingestion
- OpenAI VLM inference

### Part 3  Logging & Dashboard
- Structured incident logs
- Minimal web dashboard

---

## External APIs & Credentials

The following are required but **not included** in this repository:

- Camera snapshots (camera IDs, snapshot URLs)
- OpenAI API key (for vision-language inference)

All credentials are injected via environment variables.

---

## Execution Environment

This project is designed to run as a workload on **VESSL AI**.
VESSL provides GPU/CPU execution, while OpenAI provides the hosted VLM.

GPU usage is optional in early phases.

---

## Disclaimer
This repository represents an initial scope and pipeline draft,
not a production system.
