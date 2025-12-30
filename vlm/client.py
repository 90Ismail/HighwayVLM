import base64
import json
import re
import time

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator

from settings import get_openai_api_key, get_openai_timeout_seconds, get_vlm_max_retries


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
    def __init__(self, model, timeout_seconds=None, max_retries=None):
        self.model = model
        self.timeout_seconds = timeout_seconds or get_openai_timeout_seconds()
        self.max_retries = max_retries or get_vlm_max_retries()
        self._client = OpenAI(api_key=get_openai_api_key(), timeout=self.timeout_seconds)

    def _build_prompt(self, camera, captured_at):
        system = (
            "You are a traffic incident analyst. Focus on crashes, stopped vehicles, or vehicles on the shoulder. Respond with valid JSON only. "
            "Schema: {observed_direction, traffic_state, incidents, notes, overall_confidence}. "
            "traffic_state must be one of free, moderate, heavy, stop_and_go, unknown. "
            "incidents is a list of objects with {type, severity, description}. Use types like crash, stopped_vehicle, or shoulder_stall when applicable. "
            "severity must be low, medium, or high. Use [] if none. "
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

    def _extract_output_text(self, response):
        if hasattr(response, "output_text"):
            return response.output_text
        try:
            return response.output[0].content[0].text
        except Exception:
            return str(response)

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

    def analyze(self, camera, image_bytes, captured_at, content_type=None):
        system, user_text = self._build_prompt(camera, captured_at)
        image_url = self._image_to_data_url(image_bytes, content_type)
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.responses.create(
                    model=self.model,
                    input=[
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": user_text},
                                {"type": "input_image", "image_url": image_url},
                            ],
                        },
                    ],
                    temperature=0.2,
                )
                text = self._extract_output_text(response)
                payload = self._parse_json(text)
                result = VLMResult.model_validate(payload)
                return result, text
            except (ValidationError, ValueError) as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            time.sleep(1.0 * attempt)
        raise RuntimeError(f"VLM failed after {self.max_retries} attempts: {last_error}")
