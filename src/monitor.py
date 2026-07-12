"""Background polling loop that monitors streamers and triggers recordings.

The monitor runs on a daemon thread, reports events through a callback
(marshaled onto the GUI thread by the app), and coordinates with
:class:`~src.recorder.Recorder` for start/stop/reap.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .kick_api import ChannelStatus, LiveState, get_channel_status
from .recorder import Recorder

log = logging.getLogger(__name__)

# Callback type: (slug, event_name, detail_string)
EventCallback = Callable[[str, str, str], None]

# Require this many consecutive confirmed-offline polls before stopping a recording
OFFLINE_CONFIRM_POLLS = 2


class StreamMonitor:
    """Polls Kick API in a background thread and manages recordings.

    Event names passed to ``on_event`` include ``monitor``, ``poll``,
    ``live``, ``status``, ``status_offline``, ``offline``,
    ``recording_started``, ``recording_ended``, ``recording_failed``,
    ``recording_stopped``, ``api_error``, and ``next_check``.
    """

    def __init__(self, config: AppConfig, on_event: EventCallback | None = None) -> None:
        """Create a monitor bound to ``config``.

        Args:
            config: Shared app config (read for enabled slugs and settings).
            on_event: Optional ``(slug, event, detail)`` callback. Empty
                ``slug`` means a global/monitor-level message.
        """
        self.config = config
        self.on_event = on_event or (lambda *_: None)
        self.recorder = Recorder(
            output_dir=config.settings.output_dir,
            filename_template=config.settings.filename_template,
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_status: dict[str, ChannelStatus] = {}
        self._offline_streak: dict[str, int] = {}

    # ── Public API (called from GUI thread) ──────────────────

    @property
    def running(self) -> bool:
        """True while the background poll thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background polling thread if it is not already running."""
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.on_event("", "monitor", "Monitoring started")

    def stop(self) -> None:
        """Stop polling, wait briefly for the thread, and stop all recordings."""
        if not self.running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.recorder.stop_all()
        self._thread = None
        self._offline_streak.clear()
        self.on_event("", "monitor", "Monitoring stopped")

    def stop_recording(self, slug: str) -> None:
        """Manually stop recording for ``slug`` and emit ``recording_stopped``."""
        self.recorder.stop(slug)
        self._offline_streak.pop(slug, None)
        self.on_event(slug, "recording_stopped", "Recording stopped manually")

    def update_settings(self) -> None:
        """Re-apply output dir and filename template from the shared config."""
        self.recorder.output_dir = Path(self.config.settings.output_dir)
        self.recorder.filename_template = self.config.settings.filename_template

    # ── Background thread ────────────────────────────────────

    def _run(self) -> None:
        """Poll loop: check channels, then sleep a randomized interval."""
        log.info("Monitor thread started")
        while not self._stop_event.is_set():
            self._poll_all()
            wait = self._next_wait_seconds()
            for _ in range(wait):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        log.info("Monitor thread exiting")

    def _next_wait_seconds(self) -> int:
        """Return a randomized wait based on configured poll interval.

        Uses roughly 75%–150% of ``poll_interval_seconds``, floored at 10s.
        """
        base = max(10, int(self.config.settings.poll_interval_seconds))
        low = max(10, int(base * 0.75))
        high = max(low, int(base * 1.5))
        return random.randint(low, high)

    def _poll_all(self) -> None:
        """One full poll cycle over all enabled streamers."""
        # Reap first so dead processes aren't mistaken for "not recording"
        self._reap_finished_recordings()

        slugs = self.config.get_enabled_slugs()
        if not slugs:
            return
        self.on_event("", "poll", f"Polling {len(slugs)} channel(s)...")

        for slug in slugs:
            if self._stop_event.is_set():
                break
            self._poll_one(slug)
            time.sleep(1.5)

        # Catch anything that exited during this poll cycle
        self._reap_finished_recordings()

        base = max(10, int(self.config.settings.poll_interval_seconds))
        self.on_event("", "next_check", f"Next check in ~{base}s (±)")

    def _reap_finished_recordings(self) -> None:
        """Finalize exited yt-dlp processes and emit success/failure events."""
        for result in self.recorder.reap_finished():
            self._offline_streak.pop(result.slug, None)
            if result.failed:
                detail = (
                    f"Recording failed (exit code {result.exit_code}, "
                    f"ran {result.elapsed_seconds:.0f}s)"
                )
                if result.output:
                    last_line = result.output.splitlines()[-1]
                    detail += f" — {last_line}"
                log.warning(
                    "yt-dlp failed for '%s': exit=%d elapsed=%.0fs\n%s",
                    result.slug,
                    result.exit_code,
                    result.elapsed_seconds,
                    result.output,
                )
                self.on_event(result.slug, "recording_failed", detail)
            else:
                self.on_event(
                    result.slug, "recording_ended", "Stream ended — recording saved"
                )

    def _poll_one(self, slug: str) -> None:
        """Check one channel and start/stop recording based on live state.

        API errors leave an active recording running. Confirmed offline
        requires :data:`OFFLINE_CONFIRM_POLLS` consecutive hits before stop.
        """
        status = get_channel_status(slug)
        prev = self._last_status.get(slug)
        self._last_status[slug] = status

        is_recording = self.recorder.is_recording(slug)

        if status.state is LiveState.ERROR:
            # Do not treat errors as offline — keep recording if active
            err = status.error or "unknown error"
            self.on_event(slug, "api_error", f"API error (keeping state) — {err}")
            return

        if status.is_live:
            self._offline_streak[slug] = 0
            if not is_recording:
                title = status.title or ""
                self.on_event(slug, "live", f"LIVE — {title}")
                path = self.recorder.start(slug)
                self.on_event(slug, "recording_started", f"Recording → {path.name}")
            else:
                title = status.title or ""
                viewers = status.viewer_count or 0
                self.on_event(
                    slug, "status", f"LIVE — {title} ({viewers:,} viewers)"
                )
            return

        # Confirmed offline
        if is_recording:
            streak = self._offline_streak.get(slug, 0) + 1
            self._offline_streak[slug] = streak
            if streak < OFFLINE_CONFIRM_POLLS:
                self.on_event(
                    slug,
                    "status",
                    f"Offline signal ({streak}/{OFFLINE_CONFIRM_POLLS}) — waiting to confirm",
                )
                return
            self.recorder.stop(slug)
            self._offline_streak.pop(slug, None)
            self.on_event(slug, "offline", "Went offline — recording saved")
        else:
            self._offline_streak.pop(slug, None)
            was_live = prev.is_live if prev else False
            if was_live or prev is None or prev.is_error:
                self.on_event(slug, "status_offline", "Offline")
