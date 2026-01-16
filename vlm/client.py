import base64
import json
import re
import time

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from settings import (
    get_openrouter_app_title,
    get_openrouter_http_referer,
    get_vlm_api_key,
    get_vlm_base_url,
    get_vlm_max_retries,
    get_vlm_max_tokens,
    get_vlm_timeout_seconds,
)


class Incident(BaseModel):
    type: str
    severity: str
    description: str

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value):
        allowed = {"low", "medium", "high"}
        if value not in allowed:
            raise ValueError("severity must be low, medium, or high")
        return value


class VLMResult(BaseModel):
    observed_direction: str
    traffic_state: str
    incidents: list[Incident] = Field(default_factory=list)
    notes: str | None = None
    overall_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("traffic_state")
    @classmethod
    def _validate_traffic_state(cls, value):
        allowed = {"free", "moderate", "heavy", "stop_and_go", "unknown"}
        if value not in allowed:
            raise ValueError("traffic_state must be free, moderate, heavy, stop_and_go, or unknown")
        return value


class VLMClient:
    def __init__(self, model, timeout_seconds=None, max_retries=None, max_tokens=None, base_url=None, api_key=None):
        self.model = model
        self.timeout_seconds = timeout_seconds or get_vlm_timeout_seconds()
        self.max_retries = max_retries or get_vlm_max_retries()
        self.max_tokens = max_tokens or get_vlm_max_tokens()
        self.base_url = (base_url or get_vlm_base_url()).rstrip("/")
        self.api_key = api_key or get_vlm_api_key()
        if not self.api_key:
            raise ValueError("Missing VLM API key. Set OPENROUTER_API_KEY or VLM_API_KEY.")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        referer = get_openrouter_http_referer()
        title = get_openrouter_app_title()
        if referer:
            self.headers["HTTP-Referer"] = referer
        if title:
            self.headers["X-Title"] = title

    def _build_prompt(self, camera, captured_at):
        system = (
            "You are a traffic incident analyst. Be conservative: only report incidents that are clearly visible. "
            "If visibility is poor, the scene is not a roadway, or you are unsure, return incidents: [] and traffic_state: unknown. "
            "Focus on crashes, stopped vehicles, or vehicles on the shoulder. Respond with valid JSON only. "
            "Schema: {observed_direction, traffic_state, incidents, notes, overall_confidence}. "
            "traffic_state must be one of free, moderate, heavy, stop_and_go, unknown. "
            "incidents is a list of objects with {type, severity, description}. Use types like crash, stopped_vehicle, or shoulder_stall when applicable. "
            "severity must be low, medium, or high. Use [] if none. "
            "notes should be short: if no incidents, write 'clear traffic'; otherwise list only incident types. "
            "overall_confidence must be between 0 and 1."   
        )
        user_text = (
            "Analyze this freeway camera image and summarize traffic conditions. "
            f"Camera ID: {camera.get('camera_id')}. "
            f"Name: {camera.get('name')}. "
            f"Corridor: {camera.get('corridor')}. Direction: {camera.get('direction')}. "
            f"Captured at: {captured_at}."
        )
        return system, user_text

    def _image_to_data_url(self, image_bytes, content_type):
        content_type = (content_type or "image/jpeg").split(";")[0]
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def _extract_output_text(self, payload):
        if isinstance(payload, dict) and payload.get("choices"):
            message = payload["choices"][0].get("message", {})
            content = message.get("content")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text" and item.get("text"):
                            parts.append(item["text"])
                        elif item.get("text"):
                            parts.append(item["text"])
                    elif isinstance(item, str):
                        parts.append(item)
                if parts:
                    return "".join(parts).strip()
            if isinstance(content, str):
                return content
        if isinstance(payload, dict) and payload.get("output_text"):
            return payload["output_text"]
        raise ValueError("No response text found in VLM response")

    def _parse_json(self, text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        for match in re.finditer(r"\{.*?\}", text, re.DOTALL):
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
        raise ValueError("No valid JSON found in VLM response")


    def _normalize_parsed(self, camera, parsed):
        if isinstance(parsed, list):
            parsed = {"incidents": parsed}
        if isinstance(parsed, dict):
            if {"type", "severity", "description"}.issubset(parsed) and "incidents" not in parsed:
                parsed = {"incidents": [parsed]}
            incidents = parsed.get("incidents")
            if incidents is None:
                parsed["incidents"] = []
            elif not isinstance(incidents, list):
                parsed["incidents"] = [incidents]
            normalized_incidents = []
            for incident in parsed["incidents"]:
                if isinstance(incident, dict):
                    item = dict(incident)
                else:
                    item = {"description": str(incident)}
                item.setdefault("type", "incident")
                item.setdefault("description", "unspecified")
                severity = item.get("severity")
                severity_value = str(severity).strip().lower() if severity is not None else ""
                severity_map = {
                    "low": "low",
                    "minor": "low",
                    "medium": "medium",
                    "moderate": "medium",
                    "high": "high",
                    "severe": "high",
                    "critical": "high",
                }
                item["severity"] = severity_map.get(severity_value, "low")
                normalized_incidents.append(item)
            parsed["incidents"] = normalized_incidents
            traffic_state = parsed.get("traffic_state")
            if isinstance(traffic_state, str):
                parsed["traffic_state"] = traffic_state.strip().lower().replace(" ", "_")
            confidence = parsed.get("overall_confidence")
            if confidence is not None:
                try:
                    parsed["overall_confidence"] = float(confidence)
                except (TypeError, ValueError):
                    parsed["overall_confidence"] = 0.2
            parsed.setdefault("observed_direction", camera.get("direction") or "unknown")
            parsed.setdefault("traffic_state", "unknown")
            parsed.setdefault("overall_confidence", 0.2)
            parsed.setdefault("notes", None)
        return parsed

    def _summary_notes(self, incidents):
        if not incidents:
            return "Clear traffic"
        parts = []
        for incident in incidents:
            kind = (incident.type or "incident").replace("_", " ").strip()
            label = " ".join(word.capitalize() for word in kind.split())
            if incident.severity:
                label = f"{label} ({incident.severity})"
            parts.append(label)
        return ", ".join(parts)

    def analyze(self, camera, image_bytes, captured_at, content_type=None):
        system, user_text = self._build_prompt(camera, captured_at)
        image_url = self._image_to_data_url(image_bytes, content_type)
        last_error = None
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                },
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
        }
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(url, headers=self.headers, json=payload, timeout=self.timeout_seconds)
                if response.status_code >= 400:
                    try:
                        error_payload = response.json()
                    except ValueError:
                        error_payload = response.text
                    raise RuntimeError(f"HTTP {response.status_code}: {error_payload}")
                data = response.json()
                text = self._extract_output_text(data)
                parsed = self._parse_json(text)
                parsed = self._normalize_parsed(camera, parsed)
                result = VLMResult.model_validate(parsed)
                if result.traffic_state == "unknown":
                    result.traffic_state = "moderate" if result.incidents else "free"
                if not result.notes or not result.notes.strip():
                    result.notes = self._summary_notes(result.incidents)
                return result, text
            except (ValidationError, ValueError, RuntimeError) as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            time.sleep(1.0 * attempt)
        raise RuntimeError(f"VLM failed after {self.max_retries} attempts: {last_error}")
