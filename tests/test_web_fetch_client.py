from __future__ import annotations

import httpx

from apps.worker.web_fetch_client import WebFetchClient


def test_web_fetch_client_extracts_text_from_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body><h1>Title</h1><p>Hello <b>world</b>.</p></body></html>",
        )

    client = WebFetchClient(transport=httpx.MockTransport(handler))
    result = client.fetch("https://example.com/article")

    assert result.status == "succeeded"
    assert result.http_status == 200
    assert result.extracted_text is not None
    assert "Title" in result.extracted_text
    assert "Hello world" in result.extracted_text
