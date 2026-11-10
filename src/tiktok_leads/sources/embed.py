"""TikTok's creator embed page.

`https://www.tiktok.com/embed/@<handle>` carries a creator's follower count,
bio, and about ten videos in one plain HTTP response: no browser session and
no request signature. It kept answering on 2026-11-10 while the signed
`/api/user/detail/` call failed for every profile.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_STATE = re.compile(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', re.DOTALL)


@dataclass(frozen=True)
class EmbedProfile:
    handle: str
    followers: int
    bio: str
    views: list[int]
    captions: list[str]


def parse_embed(page: str) -> EmbedProfile | None:
    """None when TikTok says the creator does not exist. Raises ValueError
    when the page is not a creator page at all (a block, or a changed page)."""
    state = _STATE.search(page)
    if state is None:
        raise ValueError("embed page carries no creator state")
    try:
        routes = json.loads(state.group(1))["source"]["data"]
        creator = next(data for route, data in routes.items() if route.startswith("/embed/@"))
        if creator.get("isError"):
            return None
        user = creator["userInfo"]
        videos = creator.get("videoList") or []
        return EmbedProfile(
            handle=str(user["uniqueId"]),
            followers=int(user["followerCount"]),
            bio=str(user.get("signature") or ""),
            views=[int(video["playCount"]) for video in videos if video.get("playCount") is not None],
            captions=[str(video.get("desc") or "") for video in videos],
        )
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError(f"unexpected embed page state: {error!r}") from error


def fetch_embed(handle: str, *, timeout_seconds: float = 20.0, attempts: int = 4) -> EmbedProfile | None:
    status = 0
    for attempt in range(attempts):
        response = requests.get(
            f"https://www.tiktok.com/embed/@{handle}",
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
        )
        status = response.status_code
        # A missing creator answers 400 with a well-formed error state.
        if status in (200, 400):
            return parse_embed(response.text)
        # 503 "overload-protect triggered" clears on a retry.
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"embed page for @{handle} answered HTTP {status} {attempts} times")
