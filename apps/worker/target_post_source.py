from __future__ import annotations

import os
from datetime import datetime, timezone

from core import TargetPost

from .real_x_client import RealXClient, XApiError


class FakeTargetPostSource:
    def list_target_posts(self, agent_id: int, handles: list[str], limit: int) -> list[TargetPost]:
        del agent_id
        safe_limit = max(0, limit)
        posts: list[TargetPost] = []
        for idx, handle in enumerate(handles):
            normalized = handle.lstrip("@").strip().lower()
            if not normalized:
                continue
            for post_idx in range(2):
                external_id = f"{normalized}-{post_idx + 1:03d}"
                posts.append(
                    TargetPost(
                        external_id=external_id,
                        url=f"https://x.com/{normalized}/status/{external_id}",
                        author_handle=normalized,
                        text=f"Recent post {post_idx + 1} from {normalized}",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                if len(posts) >= safe_limit:
                    return posts
        return posts


class RealXTargetPostSource:
    def __init__(self, client: RealXClient) -> None:
        self._client = client

    def list_target_posts(self, agent_id: int, handles: list[str], limit: int) -> list[TargetPost]:
        del agent_id
        safe_limit = max(0, limit)
        if safe_limit == 0:
            return []

        per_handle = max(1, min(10, int(os.getenv("TARGET_POSTS_PER_HANDLE", "5"))))
        posts: list[TargetPost] = []

        for handle in handles:
            normalized = handle.lstrip("@").strip().lower()
            if not normalized:
                continue
            try:
                payload = self._client._request_json(f"users/by/username/{normalized}", {})
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                    continue
                user_id = data["id"]
                tweets_payload = self._client._request_json(
                    f"users/{user_id}/tweets",
                    {"max_results": per_handle, "tweet.fields": "created_at"},
                )
            except XApiError:
                continue

            items = tweets_payload.get("data") if isinstance(tweets_payload, dict) else []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                tweet_id = item.get("id")
                text = item.get("text")
                created_raw = item.get("created_at")
                if not isinstance(tweet_id, str) or not isinstance(text, str) or not isinstance(created_raw, str):
                    continue
                try:
                    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                except ValueError:
                    created_at = datetime.now(timezone.utc)
                posts.append(
                    TargetPost(
                        external_id=tweet_id,
                        url=f"https://x.com/{normalized}/status/{tweet_id}",
                        author_handle=normalized,
                        text=text,
                        created_at=created_at,
                    )
                )
                if len(posts) >= safe_limit:
                    return posts
        return posts
