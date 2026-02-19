from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone

import httpx

from core import ExternalPost, ExternalPostMetrics, XUsage
from core.models import PostType


class XApiError(RuntimeError):
    pass


class MissingXUserIdError(XApiError):
    pass


class RealXClient:
    def __init__(
        self,
        *,
        bearer_token: str,
        user_id: str | None = None,
        base_url: str = "https://api.x.com/2",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._bearer_token = bearer_token
        self._user_id = user_id
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client or httpx.Client(timeout=15.0)

    @classmethod
    def from_env(cls) -> "RealXClient":
        token = os.getenv("X_BEARER_TOKEN")
        if not token:
            raise XApiError("X_BEARER_TOKEN is required when USE_REAL_X=1")
        return cls(bearer_token=token, user_id=os.getenv("X_USER_ID"))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer_token}", "Accept": "application/json"}

    def _request_json(self, path: str, params: dict[str, object]) -> dict[str, object]:
        try:
            response = self._http_client.get(
                f"{self._base_url}/{path.lstrip('/')}",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise XApiError(f"X API request failed: {path} status={exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise XApiError(f"X API request failed: {path} network_error={exc.__class__.__name__}") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return payload

    def _post_json(self, path: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = self._http_client.post(
                f"{self._base_url}/{path.lstrip('/')}",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise XApiError(f"X API request failed: {path} status={exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise XApiError(f"X API request failed: {path} network_error={exc.__class__.__name__}") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return payload

    def resolve_user_id(self, handle_or_me: str = "me") -> str:
        del handle_or_me
        if self._user_id:
            return self._user_id

        try:
            response = self._http_client.get(f"{self._base_url}/users/me", headers=self._headers(), params={})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise MissingXUserIdError("Unable to resolve user id from /2/users/me. Please set X_USER_ID.") from exc
            raise XApiError(f"X API request failed: users/me status={exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise XApiError(f"X API request failed: users/me network_error={exc.__class__.__name__}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise MissingXUserIdError("Unable to resolve user id from /2/users/me. Please set X_USER_ID.")
        user = payload.get("data")
        if not isinstance(user, dict) or not isinstance(user.get("id"), str):
            raise MissingXUserIdError("Unable to resolve user id from /2/users/me. Please set X_USER_ID.")
        self._user_id = user["id"]
        return self._user_id

    def list_posts(self, agent_id: int, target_date: date) -> list[ExternalPost]:
        del agent_id
        user_id = self.resolve_user_id("me")
        start_dt = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        params = {
            "max_results": 100,
            "tweet.fields": "created_at,attachments",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url",
            "start_time": start_dt.isoformat().replace("+00:00", "Z"),
            "end_time": end_dt.isoformat().replace("+00:00", "Z"),
        }
        payload = self._request_json(f"users/{user_id}/tweets", params)
        data = payload.get("data", [])
        includes = payload.get("includes", {})
        media_urls = self._media_map(includes)
        posts: list[ExternalPost] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            tweet_id = item.get("id")
            created_at_raw = item.get("created_at")
            text = item.get("text")
            if not isinstance(tweet_id, str) or not isinstance(created_at_raw, str) or not isinstance(text, str):
                continue
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            if created_at < start_dt or created_at >= end_dt:
                continue
            post_media = []
            attachments = item.get("attachments")
            if isinstance(attachments, dict):
                keys = attachments.get("media_keys", [])
                if isinstance(keys, list):
                    post_media = [media_urls[k] for k in keys if isinstance(k, str) and k in media_urls]
            posts.append(
                ExternalPost(
                    external_id=tweet_id,
                    posted_at=created_at,
                    text=text,
                    type=PostType.tweet,
                    media_urls=post_media,
                )
            )
        return posts

    def get_post_metrics(self, external_post: ExternalPost) -> ExternalPostMetrics:
        payload = self._request_json(
            "tweets",
            {
                "ids": external_post.external_id,
                "tweet.fields": "public_metrics,organic_metrics,non_public_metrics",
            },
        )
        data = payload.get("data", [])
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return ExternalPostMetrics(external_id=external_post.external_id)

        tweet = data[0]
        public_metrics = tweet.get("public_metrics") if isinstance(tweet.get("public_metrics"), dict) else {}
        organic_metrics = tweet.get("organic_metrics") if isinstance(tweet.get("organic_metrics"), dict) else {}
        non_public_metrics = tweet.get("non_public_metrics") if isinstance(tweet.get("non_public_metrics"), dict) else {}

        impressions = self._int_metric(organic_metrics.get("impression_count"))
        if impressions == 0:
            impressions = self._int_metric(non_public_metrics.get("impression_count"))
        impressions_unavailable = impressions == 0
        clicks = self._int_metric(organic_metrics.get("url_link_clicks"))
        if clicks == 0:
            clicks = self._int_metric(non_public_metrics.get("url_link_clicks"))

        return ExternalPostMetrics(
            external_id=external_post.external_id,
            impressions=impressions,
            likes=self._int_metric(public_metrics.get("like_count")),
            replies=self._int_metric(public_metrics.get("reply_count")),
            retweets=self._int_metric(public_metrics.get("retweet_count")),
            clicks=clicks,
            impressions_unavailable=impressions_unavailable,
        )

    def create_tweet(
        self,
        *,
        text: str,
        in_reply_to_tweet_id: str | None = None,
        quote_tweet_id: str | None = None,
    ) -> str:
        body: dict[str, object] = {"text": text}
        if in_reply_to_tweet_id:
            body["reply"] = {"in_reply_to_tweet_id": in_reply_to_tweet_id}
        if quote_tweet_id:
            body["quote_tweet_id"] = quote_tweet_id

        payload = self._post_json("tweets", body)
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise XApiError("X API post response missing tweet id")
        return data["id"]

    def post_text(self, text: str) -> str:
        return self.create_tweet(text=text)

    def get_daily_usage(self, usage_date: date) -> XUsage:
        start_time = datetime.combine(usage_date, time.min, tzinfo=timezone.utc)
        end_time = start_time + timedelta(days=1)
        payload = self._request_json(
            "usage/tweets",
            {
                "start_time": start_time.isoformat().replace("+00:00", "Z"),
                "end_time": end_time.isoformat().replace("+00:00", "Z"),
            },
        )
        units = self._extract_usage_units(payload)
        return XUsage(usage_date=usage_date, units=units, raw=payload)

    def _extract_usage_units(self, payload: dict[str, object]) -> int:
        data = payload.get("data")
        if isinstance(data, list):
            return sum(self._int_metric(item.get("usage")) for item in data if isinstance(item, dict))
        if isinstance(data, dict):
            if isinstance(data.get("usage"), (int, float, str)):
                return self._int_metric(data.get("usage"))
            totals = data.get("totals")
            if isinstance(totals, dict):
                return self._int_metric(totals.get("usage"))
        return 0

    @staticmethod
    def _media_map(includes: object) -> dict[str, str]:
        if not isinstance(includes, dict):
            return {}
        media = includes.get("media", [])
        mapping: dict[str, str] = {}
        if not isinstance(media, list):
            return mapping
        for item in media:
            if not isinstance(item, dict):
                continue
            media_key = item.get("media_key")
            url = item.get("url") or item.get("preview_image_url")
            if isinstance(media_key, str) and isinstance(url, str):
                mapping[media_key] = url
        return mapping

    @staticmethod
    def _int_metric(value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
