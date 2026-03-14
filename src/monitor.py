"""Background polling loop that monitors streamers and triggers recordings."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .kick_api import ChannelStatus, get_channel_status
from .recorder import Recorder

log = logging.getLogger(__name__)

# Callback type: (slug, event_name, detail_string)
EventCallback = Callable[[str, str, str], None]


class StreamMonitor:
    """Polls Kick API in a background thread and manages recordings."""

    def __init__(self, config: AppConfig, on_event: EventCallback | None = None) -> None:
        self.config = config
        self.on_event = on_event or (lambda *_: None)
        self.recorder = Recorder(
            output_dir=config.settings.output_dir,
            filename_template=config.settings.filename_template,
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_status: dict[str, ChannelStatus] = {}

    # ── Public API (called from GUI thread) ──────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.on_event("", "monitor", "Monitoring started")

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.recorder.stop_all()
        self._thread = None
        self.on_event("", "monitor", "Monitoring stopped")

    def stop_recording(self, slug: str) -> None:
        self.recorder.stop(slug)
        self.on_event(slug, "recording_stopped", "Recording stopped manually")

    def update_settings(self) -> None:
        """Re-apply settings (output dir, template) from config."""
        self.recorder.output_dir = Path(self.config.settings.output_dir)
        self.recorder.filename_template = self.config.settings.filename_template

    # ── Background thread ────────────────────────────────────

    def _run(self) -> None:
        log.info("Monitor thread started")
        while not self._stop_event.is_set():
            self._poll_all()
            # Sleep in small increments so we can stop quickly
            for _ in range(self.config.settings.poll_interval_seconds):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        log.info("Monitor thread exiting")

    def _poll_all(self) -> None:
        slugs = self.config.get_enabled_slugs()
        if not slugs:
            return
        self.on_event("", "poll", f"Polling {len(slugs)} channel(s)...")

        for slug in slugs:
            if self._stop_event.is_set():
                break
            self._poll_one(slug)
            # Small stagger between requests
            time.sleep(1.5)

        # Check for recordings that ended on their own
        for slug in list(self.recorder._active):
            info = self.recorder._active.get(slug)
            if info is None:
                continue
            if info.is_alive():
                continue
            # Process exited — get details before cleanup
            elapsed = info.elapsed_seconds()
            exit_code, output = self.recorder.get_exit_info(info)
            self.recorder._cleanup(slug)

            if exit_code != 0 or elapsed < 30:
                # Likely a failure rather than a normal stream end
                detail = f"Recording failed (exit code {exit_code}, ran {elapsed:.0f}s)"
                if output:
                    # Extract last meaningful line for the event
                    last_line = output.splitlines()[-1]
                    detail += f" — {last_line}"
                log.warning("yt-dlp failed for '%s': exit=%d elapsed=%.0fs\n%s", slug, exit_code, elapsed, output)
                self.on_event(slug, "recording_failed", detail)
            else:
                self.on_event(slug, "recording_ended", "Stream ended — recording saved")

        # Report next check time
        interval = self.config.settings.poll_interval_seconds
        minutes, seconds = divmod(interval, 60)
        if minutes and seconds:
            next_str = f"{minutes}m {seconds}s"
        elif minutes:
            next_str = f"{minutes}m"
        else:
            next_str = f"{seconds}s"
        self.on_event("", "next_check", f"Next check in {next_str}")

    def _poll_one(self, slug: str) -> None:
        status = get_channel_status(slug)
        prev = self._last_status.get(slug)
        self._last_status[slug] = status

        was_live = prev.is_live if prev else False
        is_recording = self.recorder.is_recording(slug)

        if status.is_live and not is_recording:
            # Stream went live — start recording
            title = status.title or ""
            self.on_event(slug, "live", f"LIVE — {title}")
            path = self.recorder.start(slug)
            self.on_event(slug, "recording_started", f"Recording → {path.name}")

        elif status.is_live and is_recording:
            # Still live and recording
            title = status.title or ""
            viewers = status.viewer_count or 0
            self.on_event(slug, "status", f"LIVE — {title} ({viewers:,} viewers)")

        elif not status.is_live and is_recording:
            # Stream ended — stop recording
            self.recorder.stop(slug)
            self.on_event(slug, "offline", "Went offline — recording saved")

        elif not status.is_live:
            self.on_event(slug, "status_offline", "Offline")
