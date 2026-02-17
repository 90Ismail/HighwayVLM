import base64
import json
import re
import time

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from highwayvlm.settings import (
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
            raise ValueError("Missing VLM API key. Set OPENAI_API_KEY or VLM_API_KEY.")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_prompt(self, camera, captured_at):
        system = (
            "You are an expert traffic incident detection system for freeway monitoring. Your goal is to identify "
            "ALL potential incidents and traffic anomalies to alert human operators. "
            "\n\n"
            "CRITICAL CONTEXT:\n"
            "- Camera resolution: ~480p (low quality, may be blurry or grainy)\n"
            "- Better to report suspicious patterns than miss real incidents\n"
            "- Human operators will review all alerts - false positives are acceptable\n"
            "- When uncertain, report the incident with lower confidence rather than skipping\n"
            "\n"
            
            "DETECTION PRIORITIES (in order):\n"
            "1. Crashes - any visible collision, damaged vehicles, or vehicles at unusual angles\n"
            "2. Stopped vehicles in active lanes - major safety hazard\n"
            "3. Vehicles on shoulders - especially with people visible outside\n"
            "4. Traffic anomalies - unusual gaps, sudden slowdowns, visible brake lights\n"
            "5. Debris or objects on roadway\n"
            "6. Emergency vehicles or unusual vehicle presence\n"
            "\n"
            "TRAFFIC STATE DEFINITIONS:\n"
            "- free: Vehicles moving at normal highway speeds (50+ mph apparent), good spacing, no visible brake lights\n"
            "- moderate: Some slowing visible, vehicles closer together, occasional brake lights, but steady flow\n"
            "- heavy: Dense traffic, frequent brake lights, reduced speeds (20-40 mph apparent), but still moving\n"
            "- stop_and_go: Vehicles stopping and starting, visible stationary traffic, red brake lights prevalent\n"
            "- unknown: Only use when image quality prevents any traffic assessment (fog, night darkness, camera malfunction)\n"
            "\n"
            "INCIDENT DETECTION GUIDANCE:\n"
            "- Look for stationary vehicles (shadows underneath, no motion blur relative to moving traffic)\n"
            "- Check shoulder areas for stopped vehicles or people\n"
            "- Identify unusual vehicle positions (sideways, off-angle, blocking lanes)\n"
            "- Note emergency lights, hazard flashers, or unusual vehicle lighting\n"
            "- Watch for debris, tire marks, or objects in roadway\n"
            "- Even in low resolution, crashes often show as: vehicle clusters, unusual angles, stopped traffic upstream\n"
            "\n"
            "INCIDENT TYPES (use the most specific applicable):\n"
            "- crash: Visible collision, damaged vehicles, or vehicles at impact angles\n"
            "- stopped_vehicle_lane: Vehicle stopped in active travel lane (high severity)\n"
            "- stopped_vehicle_shoulder: Vehicle stopped on shoulder or emergency lane\n"
            "- stalled_vehicle: Vehicle appears disabled (hazards on, hood up, people nearby)\n"
            "- debris: Objects, cargo, or materials on roadway\n"
            "- emergency_response: Police, fire, ambulance, or tow trucks present\n"
            "- pedestrian: Person visible on roadway or shoulder (high severity)\n"
            "- traffic_anomaly: Unexplained slowdown, gap in traffic, or unusual pattern\n"
            "\n"
            "SEVERITY GUIDELINES:\n"
            "- high: Crashes, vehicles in active lanes, pedestrians, lane blockages, clear safety hazards\n"
            "- medium: Shoulder stops with people visible, debris in lanes, emergency response, significant slowdowns\n"
            "- low: Routine shoulder stops, minor debris on shoulder, unclear anomalies\n"
            "\n"
            "DESCRIPTION REQUIREMENTS:\n"
            "Provide detailed spatial context for each incident:\n"
            "- Location: specify lane (left lane, right lane, center lane, shoulder, median)\n"
            "- Direction of travel: match the observed_direction field\n"
            "- Vehicle details: color, type (sedan, truck, SUV), orientation if visible\n"
            "- Context: distance from camera (foreground/midground/background), relation to other vehicles\n"
            "- Indicators: hazard lights, emergency lights, people visible, damage visible\n"
            "\n"
            "Example: 'Dark colored sedan stopped on right shoulder approximately 200 feet from camera, hazard lights visible, appears occupied'\n"
            "\n"
            "RESPONSE FORMAT:\n"
            "Respond with ONLY valid JSON matching this exact schema:\n"
            "{\n"
            "  \"observed_direction\": \"string (EB, WB, NB, SB - direction of traffic flow you observe)\",\n"
            "  \"traffic_state\": \"string (one of: free, moderate, heavy, stop_and_go, unknown)\",\n"
            "  \"incidents\": [\n"
            "    {\n"
            "      \"type\": \"string (use incident types listed above)\",\n"
            "      \"severity\": \"string (low, medium, or high)\",\n"
            "      \"description\": \"string (detailed spatial description with location, vehicle details, context)\"\n"
            "    }\n"
            "  ],\n"
            "  \"notes\": \"string (single-paragraph scene summary; if no incidents, still include weather/visibility, vehicle presence, lane usage, and overall traffic flow)\",\n"
            "  \"overall_confidence\": number (0.0 to 1.0, your confidence in this entire analysis)\n"
            "}\n"
            "\n"
            "CONFIDENCE SCORING:\n"
            "- 0.9-1.0: Excellent visibility, clear incidents/conditions\n"
            "- 0.7-0.9: Good visibility, confident assessment\n"
            "- 0.5-0.7: Moderate visibility or uncertain details, but suspicious patterns detected\n"
            "- 0.3-0.5: Poor visibility or ambiguous scene, reporting out of abundance of caution\n"
            "- 0.0-0.3: Very poor quality, but potential incident indicators visible\n"
            "\n"
            "EXAMPLES:\n"
            "\n"
            "Example 1 - Clear incident:\n"
            "{\n"
            "  \"observed_direction\": \"WB\",\n"
            "  \"traffic_state\": \"free\",\n"
            "  \"incidents\": [\n"
            "    {\n"
            "      \"type\": \"stopped_vehicle_shoulder\",\n"
            "      \"severity\": \"medium\",\n"
            "      \"description\": \"White pickup truck stopped on right shoulder in foreground, hazard lights visible, driver's door appears open with person standing nearby\"\n"
            "    }\n"
            "  ],\n"
            "  \"notes\": \"1 stopped vehicle on shoulder with occupant outside\",\n"
            "  \"overall_confidence\": 0.85\n"
            "}\n"
            "\n"
            "Example 2 - Low resolution but suspicious:\n"
            "{\n"
            "  \"observed_direction\": \"EB\",\n"
            "  \"traffic_state\": \"stop_and_go\",\n"
            "  \"incidents\": [\n"
            "    {\n"
            "      \"type\": \"traffic_anomaly\",\n"
            "      \"severity\": \"medium\",\n"
            "      \"description\": \"Unusual traffic gap in right lane approximately 300 feet from camera, upstream vehicles showing heavy brake lights, possible unseen incident or obstruction\"\n"
            "    }\n"
            "  ],\n"
            "  \"notes\": \"Traffic anomaly detected - possible incident\",\n"
            "  \"overall_confidence\": 0.55\n"
            "}\n"
            "\n"
            "Example 3 - No incidents:\n"
            "{\n"
            "  \"observed_direction\": \"WB\",\n"
            "  \"traffic_state\": \"moderate\",\n"
            "  \"incidents\": [],\n"
            "  \"notes\": \"No active incidents are visible in this frame; westbound traffic is moving steadily at moderate density with consistent spacing across open travel lanes, a typical mix of passenger cars and larger vehicles, and no obvious lane blockages or abrupt braking patterns. Weather and pavement appear stable for the captured image, visibility is sufficient for lane-level monitoring, and the scene reflects normal freeway operations at the time of capture.\",\n"
            "  \"overall_confidence\": 0.90\n"
            "}\n"
            "\n"
            "Remember: Report anything suspicious. Human operators want to know about potential incidents even with uncertainty."
        )
        user_text = (
            "Analyze this freeway camera image for traffic incidents and conditions.\n"
            "\n"
            f"Camera: {camera.get('name')}\n"
            f"Location: {camera.get('corridor')} {camera.get('direction')}bound\n"
            f"Camera ID: {camera.get('camera_id')}\n"
            f"Timestamp: {captured_at}\n"
            "\n"
            "Examine the image carefully for:\n"
            "1. Any stopped or unusual vehicles in lanes or on shoulders\n"
            "2. Traffic flow patterns and density\n"
            "3. Visible incidents, debris, or anomalies\n"
            "4. Emergency response presence\n"
            "\n"
            "Provide your analysis as JSON only."
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

    def _summary_notes(self, incidents, traffic_state=None, observed_direction=None):
        if not incidents:
            direction = (observed_direction or "unknown").upper()
            flow = (traffic_state or "unknown").replace("_", " ")
            return (
                f"No active incidents are visible in this frame; {direction} traffic appears {flow} with vehicles "
                "moving through open lanes and no clear lane-blocking hazards. Vehicle presence appears typical for "
                "the corridor, lane usage looks orderly, and no obvious stopped vehicles or debris are visible in "
                "active travel lanes. Weather and visibility appear adequate for monitoring in this snapshot, with "
                "no clear environmental factor causing abnormal operations."
            )
        parts = []
        for incident in incidents:
            kind = (incident.type or "incident").replace("_", " ").strip()
            label = " ".join(word.capitalize() for word in kind.split())
            if incident.severity:
                label = f"{label} ({incident.severity})"
            parts.append(label)
        return ", ".join(parts)

    def _is_generic_clear_note(self, note):
        if not note:
            return True
        normalized = " ".join(str(note).strip().lower().split())
        generic = {
            "clear traffic",
            "no incidents",
            "no incident",
            "none",
            "no issues",
            "no incidents detected",
            "clear",
            "traffic is clear",
            "normal traffic",
        }
        return normalized in generic

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
                if not result.incidents and self._is_generic_clear_note(result.notes):
                    result.notes = self._summary_notes(
                        result.incidents,
                        traffic_state=result.traffic_state,
                        observed_direction=result.observed_direction,
                    )
                elif not result.notes or not result.notes.strip():
                    result.notes = self._summary_notes(
                        result.incidents,
                        traffic_state=result.traffic_state,
                        observed_direction=result.observed_direction,
                    )
                return result, text
            except (ValidationError, ValueError, RuntimeError) as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            time.sleep(1.0 * attempt)
        raise RuntimeError(f"VLM failed after {self.max_retries} attempts: {last_error}")
