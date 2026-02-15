from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx


class WebFetchError(RuntimeError):
    pass


@dataclass
class WebFetchResult:
    url: str
    status: str
    http_status: int | None
    content_type: str | None
    content_length: int | None
    extracted_text: str | None
    failure_reason: str | None = None


class WebFetchClient:
    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_redirects: int | None = None,
        max_bytes: int | None = None,
        max_chars: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout = timeout or float(os.getenv("WEB_FETCH_TIMEOUT", "10"))
        self.max_redirects = max_redirects or int(os.getenv("WEB_FETCH_MAX_REDIRECTS", "5"))
        self.max_bytes = max_bytes or int(os.getenv("WEB_FETCH_MAX_BYTES", str(1024 * 1024)))
        self.max_chars = max_chars or int(os.getenv("WEB_FETCH_MAX_CHARS", "20000"))
        self._transport = transport

    def fetch(self, url: str) -> WebFetchResult:
        headers = {"accept": "text/html,text/plain"}
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                max_redirects=self.max_redirects,
                transport=self._transport,
            ) as client:
                response = client.get(url, headers=headers)
                raw = response.content[: self.max_bytes + 1]
        except httpx.HTTPError as exc:
            return WebFetchResult(
                url=url,
                status="failed",
                http_status=None,
                content_type=None,
                content_length=None,
                extracted_text=None,
                failure_reason=str(exc),
            )

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower() or None
        if content_type and content_type not in {"text/html", "text/plain"}:
            return WebFetchResult(
                url=str(response.url),
                status="failed",
                http_status=response.status_code,
                content_type=content_type,
                content_length=len(response.content),
                extracted_text=None,
                failure_reason="unsupported_content_type",
            )

        if len(raw) > self.max_bytes:
            return WebFetchResult(
                url=str(response.url),
                status="failed",
                http_status=response.status_code,
                content_type=content_type,
                content_length=len(response.content),
                extracted_text=None,
                failure_reason="max_bytes_exceeded",
            )

        text = raw.decode(response.encoding or "utf-8", errors="ignore")
        extracted = self._extract_text(text, content_type=content_type)
        return WebFetchResult(
            url=str(response.url),
            status="succeeded",
            http_status=response.status_code,
            content_type=content_type,
            content_length=len(raw),
            extracted_text=extracted,
        )

    def _extract_text(self, text: str, *, content_type: str | None) -> str:
        normalized = text
        if content_type == "text/html" or (content_type is None and "<html" in text.lower()):
            normalized = re.sub(r"<script[^>]*>.*?</script>", " ", normalized, flags=re.IGNORECASE | re.DOTALL)
            normalized = re.sub(r"<style[^>]*>.*?</style>", " ", normalized, flags=re.IGNORECASE | re.DOTALL)
            normalized = re.sub(r"<[^>]+>", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized[: self.max_chars]
