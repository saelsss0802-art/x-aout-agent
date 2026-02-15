from __future__ import annotations

import json

import httpx

from apps.worker.summarize import GeminiSummarizer


def test_gemini_summarizer_parses_schema_response(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "summary": "これは要約です。" * 20,
                                        "key_points": ["ポイント1", "ポイント2"],
                                        "confidence": "med",
                                        "safe_to_use": True,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    summarizer = GeminiSummarizer(transport=httpx.MockTransport(handler))
    result = summarizer.summarize("long text")

    assert result["confidence"] == "med"
    assert result["safe_to_use"] is True
    assert len(result["key_points"]) == 2
