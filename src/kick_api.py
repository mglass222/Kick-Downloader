"""Kick.com API client — checks channel live status via the v2 API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from curl_cffi import requests as curl_requests

log = logging.getLogger(__name__)

API_URL = "https://kick.com/api/v2/channels/{slug}"

REQUEST_TIMEOUT = 30


class LiveState(Enum):
    """Channel availability as reported by the API (or lack thereof)."""

    LIVE = "live"
    OFFLINE = "offline"
    ERROR = "error"  # request/parse failure — not a confirmed offline


@dataclass
class ChannelStatus:
    slug: str
    state: LiveState
    playback_url: str | None = None
    title: str | None = None
    viewer_count: int | None = None
    started_at: str | None = None
    error: str | None = None

    @property
    def is_live(self) -> bool:
        return self.state is LiveState.LIVE

    @property
    def is_offline(self) -> bool:
        return self.state is LiveState.OFFLINE

    @property
    def is_error(self) -> bool:
        return self.state is LiveState.ERROR


def get_channel_status(slug: str) -> ChannelStatus:
    """Query the Kick v2 API for a channel's live status.

    Uses curl_cffi to impersonate a real browser TLS fingerprint,
    which is required to avoid Kick's 403 bot detection.

    On network/HTTP/parse errors, returns state=ERROR (not OFFLINE)
    so callers do not treat transient failures as stream end.
    """
    url = API_URL.format(slug=slug)
    try:
        resp = curl_requests.get(
            url,
            impersonate="chrome",
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except curl_requests.RequestsError as exc:
        log.warning("Request failed for channel '%s': %s", slug, exc)
        return ChannelStatus(slug=slug, state=LiveState.ERROR, error=str(exc))
    except Exception as exc:
        log.warning("Unexpected error for channel '%s': %s", slug, exc)
        return ChannelStatus(slug=slug, state=LiveState.ERROR, error=str(exc))

    livestream = data.get("livestream")
    if not livestream:
        return ChannelStatus(slug=slug, state=LiveState.OFFLINE)

    return ChannelStatus(
        slug=slug,
        state=LiveState.LIVE,
        playback_url=livestream.get("playback_url"),
        title=livestream.get("session_title"),
        viewer_count=livestream.get("viewer_count"),
        started_at=livestream.get("created_at"),
    )
