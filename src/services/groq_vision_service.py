"""Direct Groq multimodal image analysis, independent of MCP tool routing."""

from __future__ import annotations

import base64
import json
import logging
import os
import re

try:
    from groq import Groq
except ImportError:  # pragma: no cover - dependency diagnostic path
    Groq = None

logger = logging.getLogger(__name__)


class GroqVisionService:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("GROQ_VISION_MODEL") or "qwen/qwen3.8-27b"
        self.error = ""
        self._client = None
        if self.api_key and Groq is not None:
            try:
                self._client = Groq(api_key=self.api_key, timeout=15.0, max_retries=1)
            except Exception as exc:
                self.error = f"Groq vision initialization failed: {str(exc)[:180]}"
        elif Groq is None:
            self.error = "groq SDK is not installed"
        else:
            self.error = "GROQ_API_KEY is not configured"

    @property
    def ready(self) -> bool:
        return self._client is not None

    def _complete_image(self, jpeg_bytes: bytes, prompt: str, max_tokens: int, **options) -> str:
        image = base64.b64encode(jpeg_bytes).decode("utf-8")
        completion = self._client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
                ],
            }],
            temperature=0.0,
            max_completion_tokens=max_tokens,
            reasoning_effort="none",
            **options,
        )
        return (completion.choices[0].message.content or "").strip()

    def describe(self, jpeg_bytes: bytes | None) -> str:
        if not self._client or not jpeg_bytes:
            if not jpeg_bytes:
                self.error = "No camera image was supplied to Groq vision"
            return ""
        try:
            description = self._complete_image(
                jpeg_bytes,
                "Describe only what is clearly visible in this current camera image. "
                "Do not guess the room type, unseen objects, identities, emotions, or events. "
                "If the image is dark, blurred, or unclear, say that the camera view is unclear. "
                "Reply with one short factual sentence suitable for speech.",
                60,
            )
            self.error = ""
            return description
        except Exception as exc:
            self.error = f"Groq vision failed: {str(exc)[:180]}"
            print(f"[GroqVisionService] {self.error}")
            return ""

    def detect_objects(self, jpeg_bytes: bytes) -> list[str] | None:
        """Return distinct labels for visible objects, or None if the request failed."""
        try:
            raw = self._complete_image(
                jpeg_bytes,
                'Return a JSON object of the form {"objects":["red mug","notebook"]}. '
                'List up to six short labels for clearly visible physical objects. '
                'Prioritize the object held up to the camera or closest to the center. '
                'Ignore people, faces, and text in the image. Do not guess. '
                'Return {"objects":[]} if no objects are clear enough to name.',
                160,
                response_format={"type": "json_object"},
            )
            data = json.loads(raw)
            objects = data.get("objects") if isinstance(data, dict) else None
            if not isinstance(objects, list) or not all(isinstance(label, str) for label in objects):
                raise ValueError("Expected an objects array of labels")
            labels = {}
            for label in objects:
                clean = " ".join(label.split())[:60].strip()
                if clean:
                    labels.setdefault(clean.casefold(), clean)
            return list(labels.values())[:6]
        except Exception as exc:
            logger.warning("Groq object detection failed (%s)", type(exc).__name__)
            return None

    def analyze_discovery(self, jpeg_bytes: bytes | None, previous: str = "") -> dict:
        """Return a conservative structured observation for curiosity missions."""
        if not self._client or not jpeg_bytes:
            if not jpeg_bytes:
                self.error = "No camera image was supplied to Groq vision"
            return {}
        try:
            prior = previous[:180] if previous else "No previous observation."
            prompt = (
                "Inspect only this current camera frame for a mobile robot exploration log. "
                "Do not infer identities, emotions, hidden objects, room type, distance, or events. "
                f"The previous observation was: {prior!r}. "
                "Return JSON only with: observation (one short factual sentence), "
                "category (person, object, route, or scene), interesting (boolean: true only for "
                "a clear person, obstacle, useful route feature, or meaningful change), and "
                "confidence (0 to 1 based on image clarity)."
            )
            raw = self._complete_image(jpeg_bytes, prompt, 120)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)
            observation = " ".join(str(data.get("observation", "")).split())[:240]
            category = str(data.get("category", "scene")).lower()
            if category not in ("person", "object", "route", "scene"):
                category = "scene"
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
            self.error = ""
            return {
                "observation": observation,
                "category": category,
                "interesting": bool(data.get("interesting", False)) and bool(observation),
                "confidence": confidence,
            }
        except Exception as exc:
            self.error = f"Groq discovery analysis failed: {str(exc)[:180]}"
            print(f"[GroqVisionService] {self.error}")
            return {}
