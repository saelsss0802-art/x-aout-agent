from __future__ import annotations

import json
import os
from typing import Any

import httpx


DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_SNIPPET_LIMIT = 300
MAX_TOP_K = 5


class GeminiWebSearchError(RuntimeError):
    pass


class GeminiWebSearchClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise GeminiWebSearchError("GEMINI_API_KEY is required")

        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._transport = transport
        self.last_payload: dict[str, Any] = {"results": [], "citations": [], "notes": {"grounded": False}}

    def search(self, query: str, k: int) -> list[dict[str, str]]:
        payload = self.search_payload(query, k)
        return payload["results"]

    def search_payload(self, query: str, k: int) -> dict[str, Any]:
        top_k = max(1, min(k, MAX_TOP_K))
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        request_body = {
            "contents": [{"role": "user", "parts": [{"text": f"Find web results for: {query}"}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "results": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "title": {"type": "STRING"},
                                    "snippet": {"type": "STRING"},
                                    "url": {"type": "STRING"},
                                },
                                "required": ["title", "snippet", "url"],
                            },
                        },
                        "citations": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "url": {"type": "STRING"},
                                    "title": {"type": "STRING"},
                                },
                                "required": ["url", "title"],
                            },
                        },
                        "notes": {
                            "type": "OBJECT",
                            "properties": {
                                "grounded": {"type": "BOOLEAN"},
                            },
                            "required": ["grounded"],
                        },
                    },
                    "required": ["results", "notes"],
                },
            },
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Use Google Search grounding. Return only valid JSON matching schema. "
                            f"Limit results to {top_k} items."
                        )
                    }
                ]
            },
        }

        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            response = client.post(endpoint, params={"key": self.api_key}, json=request_body)
            response.raise_for_status()
            raw = response.json()

        parsed = self._parse_response(raw)
        normalized = self._normalize_payload(parsed, top_k=top_k)
        self.last_payload = normalized
        return normalized

    def _parse_response(self, body: dict[str, Any]) -> dict[str, Any]:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiWebSearchError("Gemini response did not include candidates")

        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise GeminiWebSearchError("Gemini JSON response was invalid") from exc
                if isinstance(data, dict):
                    return data

        raise GeminiWebSearchError("Gemini response did not include JSON text")

    def _normalize_payload(self, payload: dict[str, Any], *, top_k: int) -> dict[str, Any]:
        raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
        raw_citations = payload.get("citations") if isinstance(payload.get("citations"), list) else []
        raw_notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}

        results: list[dict[str, str]] = []
        for item in raw_results[:top_k]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()[:DEFAULT_SNIPPET_LIMIT]
            if not url:
                continue
            results.append({"title": title, "snippet": snippet, "url": url})

        citations: list[dict[str, str]] = []
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            citations.append({"url": url, "title": str(item.get("title", "")).strip()})

        notes = {"grounded": bool(raw_notes.get("grounded", False))}

        return {
            "results": results,
            "citations": citations,
            "notes": notes,
        }

