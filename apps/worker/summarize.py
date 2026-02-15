from __future__ import annotations

import json
import os
from typing import Any

import httpx


DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiSummarizeError(RuntimeError):
    pass


class GeminiSummarizer:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise GeminiSummarizeError("GEMINI_API_KEY is required")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._transport = transport

    def summarize(self, extracted_text: str) -> dict[str, Any]:
        trimmed = extracted_text[: int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "12000"))]
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        req = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Summarize this web content in Japanese for internal analytics.\n"
                                f"Content:\n{trimmed}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {"type": "STRING"},
                        "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "confidence": {"type": "STRING", "enum": ["low", "med", "high"]},
                        "safe_to_use": {"type": "BOOLEAN"},
                    },
                    "required": ["summary", "key_points", "confidence", "safe_to_use"],
                },
            },
            "systemInstruction": {
                "parts": [
                    {
                        "text": "Return only JSON. summary should be around 200-400 Japanese characters. key_points max 5.",
                    }
                ]
            },
        }
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            resp = client.post(endpoint, params={"key": self.api_key}, json=req)
            resp.raise_for_status()
            data = resp.json()

        return self._parse_and_validate(data)

    def _parse_and_validate(self, body: dict[str, Any]) -> dict[str, Any]:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiSummarizeError("Gemini response did not include candidates")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        parsed: dict[str, Any] | None = None
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                parsed = json.loads(text)
                break
        if not isinstance(parsed, dict):
            raise GeminiSummarizeError("Gemini response did not include JSON text")

        key_points = parsed.get("key_points")
        if not isinstance(key_points, list):
            key_points = []
        out = {
            "summary": str(parsed.get("summary", "")).strip(),
            "key_points": [str(item).strip() for item in key_points if str(item).strip()][:5],
            "confidence": parsed.get("confidence") if parsed.get("confidence") in {"low", "med", "high"} else "low",
            "safe_to_use": bool(parsed.get("safe_to_use", False)),
        }
        if not out["summary"]:
            raise GeminiSummarizeError("summary is required")
        return out
