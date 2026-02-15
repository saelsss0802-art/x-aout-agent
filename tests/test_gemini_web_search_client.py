from __future__ import annotations

import json

import httpx

from apps.worker.gemini_web_search_client import GeminiWebSearchClient


def test_gemini_web_search_client_normalizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "key=test-key" in str(request.url)
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "results": [
                                            {
                                                "title": "A",
                                                "snippet": "x" * 500,
                                                "url": "https://example.com/a",
                                            },
                                            {
                                                "title": "B",
                                                "snippet": "short",
                                                "url": "https://example.com/b",
                                            },
                                        ],
                                        "citations": [{"url": "https://source.example/c", "title": "Source C"}],
                                        "notes": {"grounded": True},
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    client = GeminiWebSearchClient(api_key="test-key", transport=httpx.MockTransport(handler))

    results = client.search("latest ai news", k=1)

    assert len(results) == 1
    assert results[0]["title"] == "A"
    assert len(results[0]["snippet"]) == 300
    assert client.last_payload["notes"] == {"grounded": True}
    assert client.last_payload["citations"][0]["url"] == "https://source.example/c"
