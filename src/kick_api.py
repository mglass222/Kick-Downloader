"""Kick.com API client for channel live-status checks.

Uses the public v2 channels endpoint with ``curl_cffi`` Chrome TLS
impersonation so Kick's bot detection does not return HTTP 403.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from curl_cffi import requests as curl_requests

log = logging.getLogger(__name__)

API_URL = "https://kick.com/api/v2/channels/{slug}"

REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 2.0


class LiveState(Enum):
    """Channel availability as reported by the API (or lack thereof).

    ``ERROR`` means the request failed — it is *not* a confirmed offline
    state and must not be used to stop an active recording.
    """

    LIVE = "live"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class ChannelStatus:
    """Live-status snapshot for a single Kick channel.

    Attributes:
        slug: Channel name that was queried.
        state: Live / offline / error classification.
        playback_url: HLS URL when live, else None.
        title: Stream session title when live.
        viewer_count: Current viewers when live.
        started_at: Stream start timestamp from the API when live.
        error: Human-readable error detail when ``state`` is ERROR.
    """

    slug: str
    state: LiveState
    playback_url: str | None = None
    title: str | None = None
    viewer_count: int | None = None
    started_at: str | None = None
    error: str | None = None

    @property
    def is_live(self) -> bool:
        """True when the channel is confirmed live."""
        return self.state is LiveState.LIVE

    @property
    def is_offline(self) -> bool:
        """True when the API confirmed the channel is offline."""
        return self.state is LiveState.OFFLINE

    @property
    def is_error(self) -> bool:
        """True when the status could not be determined."""
        return self.state is LiveState.ERROR


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff delay after a failed attempt (0-based index)."""
    return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))


def _is_non_retryable_http(exc: BaseException) -> bool:
    """Return True for hard client errors that should not be retried (e.g. 404)."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        # curl_cffi / HTTPError may embed the code in the message
        text = str(exc)
        if "404" in text:
            return True
        if "401" in text or "403" in text:
            # 403 can be bot detection — still worth retrying once? Plan says
            # no retry on hard 404 only; 403 may be transient fingerprint blip.
            return False
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        return status == 404 or status in (400, 401, 410, 422)
    return False


def get_channel_status(slug: str) -> ChannelStatus:
    """Query the Kick v2 API for a channel's live status.

    Uses curl_cffi to impersonate a real browser TLS fingerprint,
    which is required to avoid Kick's 403 bot detection.

    Transient network/HTTP failures are retried with exponential backoff.
    After all attempts fail, returns ``state=ERROR`` (not OFFLINE) so
    callers do not treat transient failures as stream end.

    Args:
        slug: Kick channel name (already normalized by the caller).

    Returns:
        A :class:`ChannelStatus` describing live, offline, or error.
    """
    url = API_URL.format(slug=slug)
    last_error: str | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = curl_requests.get(
                url,
                impersonate="chrome",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                return ChannelStatus(
                    slug=slug,
                    state=LiveState.ERROR,
                    error=f"HTTP 404 for channel '{slug}'",
                )
            if 500 <= resp.status_code < 600:
                raise curl_requests.RequestsError(
                    f"HTTP {resp.status_code}",
                )
            resp.raise_for_status()
            data = resp.json()
        except curl_requests.RequestsError as exc:
            last_error = str(exc)
            log.warning(
                "Request failed for channel '%s' (attempt %d/%d): %s",
                slug,
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
            )
            if _is_non_retryable_http(exc) or attempt >= MAX_ATTEMPTS - 1:
                return ChannelStatus(slug=slug, state=LiveState.ERROR, error=last_error)
            time.sleep(_backoff_seconds(attempt))
            continue
        except Exception as exc:
            last_error = str(exc)
            log.warning(
                "Unexpected error for channel '%s' (attempt %d/%d): %s",
                slug,
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt >= MAX_ATTEMPTS - 1:
                return ChannelStatus(slug=slug, state=LiveState.ERROR, error=last_error)
            time.sleep(_backoff_seconds(attempt))
            continue

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

    return ChannelStatus(
        slug=slug,
        state=LiveState.ERROR,
        error=last_error or "unknown error",
    )
