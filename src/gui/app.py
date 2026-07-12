"""Main application window — wires GUI panels to the monitor backend.

All monitor callbacks arrive on a background thread and are marshaled
onto the Tk main loop with :meth:`tkinter.Misc.after`.
"""

from __future__ import annotations

import logging
import threading

import customtkinter as ctk

from ..config import AppConfig, Settings
from ..monitor import StreamMonitor
from .add_streamer import AddStreamerBar
from .log_panel import LogPanel
from .settings_panel import SettingsPanel
from .streamer_list import StreamerList

log = logging.getLogger(__name__)


class App(ctk.CTk):
    """Root window for the Kick Stream Recorder.

    Owns the shared :class:`~src.config.AppConfig` and
    :class:`~src.monitor.StreamMonitor`, builds the layout, and keeps
    streamer rows in sync with monitor events.
    """

    def __init__(self) -> None:
        """Build the UI, load config, and auto-start monitoring."""
        super().__init__()

        self.title("Kick Stream Recorder")
        self.geometry("700x600")
        self.minsize(520, 420)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Load config
        self.config_data = AppConfig.load()

        # Backend monitor
        self.monitor = StreamMonitor(
            config=self.config_data,
            on_event=self._on_monitor_event,
        )

        # ── Build UI ─────────────────────────────────────────
        # Pack fixed-height elements first (top and bottom),
        # then expandable panels fill the remaining space.

        # Top: Add streamer bar
        self._add_bar = AddStreamerBar(self, on_add=self._add_streamer)
        self._add_bar.pack(fill="x", padx=10, pady=(10, 4))

        # Top: Streamer list header
        from .streamer_list import create_header
        self._list_header = create_header(self)
        self._list_header.pack(fill="x", padx=10, pady=(4, 0))

        # Bottom: Control buttons (packed early so they never disappear)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        self._monitor_btn = ctk.CTkButton(
            btn_frame, text="Stop Monitoring", height=36, width=160,
            fg_color="#cc3333", hover_color="#881111",
            command=self._toggle_monitoring,
        )
        self._monitor_btn.pack(side="left", padx=(0, 8))

        self._status_label = ctk.CTkLabel(
            btn_frame, text="Monitoring: on", text_color="#53d769",
        )
        self._status_label.pack(side="left")

        # Bottom: Settings panel (above buttons)
        self._settings_panel = SettingsPanel(
            self,
            settings=self.config_data.settings,
            on_change=self._on_settings_change,
        )
        self._settings_panel.pack(side="bottom", fill="x", padx=10, pady=4)

        # Bottom: Log panel (above settings, expands)
        self._log_panel = LogPanel(self)
        self._log_panel.pack(side="bottom", fill="both", expand=True, padx=10, pady=4)

        # Middle: Streamer list (expands to fill remaining space)
        self._streamer_list = StreamerList(
            self,
            on_remove=self._remove_streamer,
            on_stop_recording=self._stop_recording,
            on_start_recording=self._start_recording,
            on_toggle_enabled=self._toggle_streamer_enabled,
            height=150,
        )
        self._streamer_list.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        # Populate streamer list from config
        for entry in self.config_data.streamers:
            self._streamer_list.add_streamer(entry.slug, enabled=entry.enabled)

        # Timer to update recording elapsed times
        self._update_timer()

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-start monitoring on launch
        self._start_monitoring()
        interval = self.config_data.settings.poll_interval_seconds
        self._log(
            f"Monitoring started — checking about every {interval}s for live streamers."
        )

    # ── Streamer management ──────────────────────────────────

    def _add_streamer(self, slug: str) -> None:
        """Validate and add a channel, then check live status in the background."""
        from ..config import is_valid_slug

        slug = slug.strip().lower()
        if not is_valid_slug(slug):
            self._log(
                f"Invalid channel name '{slug}' — use letters, numbers, _ or -"
            )
            return
        if self.config_data.add_streamer(slug):
            self._streamer_list.add_streamer(slug, enabled=True)
            self._log(f"Added streamer: {slug}")
            threading.Thread(
                target=self._check_live_status, args=(slug,), daemon=True
            ).start()
        else:
            self._log(f"Streamer '{slug}' already in list")

    def _check_live_status(self, slug: str) -> None:
        """Background live check used right after adding a streamer."""
        from ..kick_api import get_channel_status
        status = get_channel_status(slug)
        if status.is_live:
            self.after(0, self._handle_event, slug, "live",
                       f"LIVE — {status.title or ''}")
        elif status.is_error:
            self.after(
                0,
                self._handle_event,
                slug,
                "api_error",
                f"API error — {status.error or 'unknown'}",
            )

    def _remove_streamer(self, slug: str) -> None:
        """Stop any recording, remove from config, and drop the UI row."""
        if self.monitor.recorder.is_recording(slug):
            self._stop_recording(slug)
        self.config_data.remove_streamer(slug)
        self._streamer_list.remove_streamer(slug)
        self._log(f"Removed streamer: {slug}")

    def _toggle_streamer_enabled(self, slug: str, enabled: bool) -> None:
        """Persist enable/disable; stopping recording when disabled."""
        self.config_data.set_enabled(slug, enabled)
        if not enabled and self.monitor.recorder.is_recording(slug):
            self._stop_recording(slug)
        state = "enabled" if enabled else "disabled"
        self._log(f"[{slug}] Monitoring {state}")

    def _start_recording(self, slug: str) -> None:
        """Start a manual recording for a live (or soon-to-be-live) channel."""
        row = self._streamer_list.get_row(slug)
        if row:
            row.set_recording(True, "00:00")
        self.monitor.update_settings()
        path = self.monitor.recorder.start(slug)
        self._log(f"[{slug}] Manual recording started → {path.name}")

    def _stop_recording(self, slug: str) -> None:
        """Stop recording on a background thread so the UI stays responsive."""
        row = self._streamer_list.get_row(slug)
        if row:
            row.set_recording(False)
        threading.Thread(
            target=self.monitor.stop_recording, args=(slug,), daemon=True
        ).start()

    # ── Monitoring controls ──────────────────────────────────

    def _toggle_monitoring(self) -> None:
        """Toggle the background poll loop on or off."""
        if self.monitor.running:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self) -> None:
        """Start the monitor if needed and refresh the status control."""
        if self.monitor.running:
            self._refresh_monitor_ui()
            return
        self.monitor.update_settings()
        self.monitor.start()
        self._refresh_monitor_ui()

    def _stop_monitoring(self) -> None:
        """Stop the monitor on a background thread so the UI stays responsive."""
        if not self.monitor.running:
            return
        self._monitor_btn.configure(state="disabled")

        def _stop_worker():
            self.monitor.stop()
            self.after(0, self._on_monitor_stopped)

        threading.Thread(target=_stop_worker, daemon=True).start()

    def _on_monitor_stopped(self) -> None:
        """Clear REC indicators and re-enable the toggle after stop completes."""
        for slug in self._streamer_list.all_slugs():
            row = self._streamer_list.get_row(slug)
            if row:
                row.set_recording(False)
        self._monitor_btn.configure(state="normal")
        self._refresh_monitor_ui()

    def _refresh_monitor_ui(self) -> None:
        """Sync the toggle button and status label with ``monitor.running``."""
        if self.monitor.running:
            self._monitor_btn.configure(
                text="Stop Monitoring",
                fg_color="#cc3333",
                hover_color="#881111",
            )
            self._status_label.configure(
                text="Monitoring: on", text_color="#53d769",
            )
        else:
            self._monitor_btn.configure(
                text="Start Monitoring",
                fg_color="#228822",
                hover_color="#116611",
            )
            self._status_label.configure(
                text="Monitoring: off", text_color="gray",
            )

    # ── Settings ─────────────────────────────────────────────

    def _on_settings_change(self, settings: Settings) -> None:
        """Persist settings and push output-dir/template to the recorder."""
        self.config_data.save()
        self.monitor.update_settings()
        self._log(
            f"Settings updated — interval: {settings.poll_interval_seconds}s, "
            f"dir: {settings.output_dir}"
        )

    # ── Monitor events (called from background thread) ───────

    def _on_monitor_event(self, slug: str, event: str, detail: str) -> None:
        """Thread-safe entry: schedule UI handling on the main loop."""
        self.after(0, self._handle_event, slug, event, detail)

    def _handle_event(self, slug: str, event: str, detail: str) -> None:
        """Apply a monitor event to the log and streamer row (main thread)."""
        if slug:
            self._log(f"[{slug}] {detail}")
        else:
            self._log(detail)

        if event in ("monitor",):
            self._refresh_monitor_ui()

        row = self._streamer_list.get_row(slug) if slug else None
        if row is None:
            return

        if event == "live":
            row.set_live(True, detail.replace("LIVE — ", ""))
        elif event == "api_error":
            # Keep existing live/recording UI — error is not confirmed offline
            pass
        elif event == "status_offline":
            row.set_live(False)
        elif event in ("offline", "recording_ended", "recording_failed"):
            row.set_live(False)
            row.set_recording(False)
        elif event == "recording_started":
            row.set_recording(True, "00:00")
        elif event == "recording_stopped":
            row.set_recording(False)

    # ── Periodic UI update ───────────────────────────────────

    def _update_timer(self) -> None:
        """Refresh recording elapsed times once per second."""
        for slug in self._streamer_list.all_slugs():
            info = self.monitor.recorder.get_info(slug)
            row = self._streamer_list.get_row(slug)
            if row and info and info.is_alive():
                elapsed = int(info.elapsed_seconds())
                h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
                if h > 0:
                    row.update_elapsed(f"{h}:{m:02d}:{s:02d}")
                else:
                    row.update_elapsed(f"{m:02d}:{s:02d}")
        self.after(1000, self._update_timer)

    # ── Helpers ──────────────────────────────────────────────

    def _log(self, message: str) -> None:
        """Append to the on-screen log and the Python logger."""
        self._log_panel.log(message)
        log.info(message)

    def _on_close(self) -> None:
        """Gracefully stop recordings before destroying the window."""
        if self.monitor.running:
            self._log("Shutting down — stopping all recordings...")
            self.monitor.stop()
        self.destroy()
