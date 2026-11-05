"""Direct Groq multimodal image analysis, independent of MCP tool routing."""

from __future__ import annotations

import base64
import json
import os
import re

try:
    from groq import Groq
except ImportError:  # pragma: no cover - dependency diagnostic path
    Groq = None


class GroqVisionService:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
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

    def describe(self, jpeg_bytes: bytes | None) -> str:
        if not self._client or not jpeg_bytes:
            if not jpeg_bytes:
                self.error = "No camera image was supplied to Groq vision"
            return ""
        try:
            b64_img = base64.b64encode(jpeg_bytes).decode("utf-8")
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Describe only what is clearly visible in this current camera image. "
                                    "Do not guess the room type, unseen objects, identities, emotions, or events. "
                                    "If the image is dark, blurred, or unclear, say that the camera view is unclear. "
                                    "Reply with one short factual sentence suitable for speech."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                            },
                        ],
                    }
                ],
                temperature=0.0,
                max_completion_tokens=60,
                reasoning_effort="none",
            )
            self.error = ""
            return (completion.choices[0].message.content or "").strip()
        except Exception as exc:
            self.error = f"Groq vision failed: {str(exc)[:180]}"
            print(f"[GroqVisionService] {self.error}")
            return ""

    def analyze_discovery(self, jpeg_bytes: bytes | None, previous: str = "") -> dict:
        """Return a conservative structured observation for curiosity missions."""
        if not self._client or not jpeg_bytes:
            if not jpeg_bytes:
                self.error = "No camera image was supplied to Groq vision"
            return {}
        try:
            b64_img = base64.b64encode(jpeg_bytes).decode("utf-8")
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
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                    ],
                }],
                temperature=0.0,
                max_completion_tokens=120,
                reasoning_effort="none",
            )
            raw = (completion.choices[0].message.content or "").strip()
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
