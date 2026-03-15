"""Main application window — wires together all GUI panels and the backend."""

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
    def __init__(self) -> None:
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

        self._start_btn = ctk.CTkButton(
            btn_frame, text="Start Monitoring", height=36,
            fg_color="#228822", hover_color="#116611",
            command=self._start_monitoring,
        )
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = ctk.CTkButton(
            btn_frame, text="Stop Monitoring", height=36,
            fg_color="#cc3333", hover_color="#881111",
            command=self._stop_monitoring,
        )
        self._stop_btn.pack(side="left")

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
            height=150,
        )
        self._streamer_list.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        # Populate streamer list from config
        for entry in self.config_data.streamers:
            self._streamer_list.add_streamer(entry.slug)

        # Timer to update recording elapsed times
        self._update_timer()

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-start monitoring on launch
        self._start_monitoring()
        self._log("Monitoring started — checking every 60s for live streamers.")

    # ── Streamer management ──────────────────────────────────

    def _add_streamer(self, slug: str) -> None:
        slug = slug.strip().lower()
        if self.config_data.add_streamer(slug):
            self._streamer_list.add_streamer(slug)
            self._log(f"Added streamer: {slug}")
            # Check live status in background so UI doesn't block
            threading.Thread(
                target=self._check_live_status, args=(slug,), daemon=True
            ).start()
        else:
            self._log(f"Streamer '{slug}' already in list or invalid")

    def _check_live_status(self, slug: str) -> None:
        from ..kick_api import get_channel_status
        status = get_channel_status(slug)
        if status.is_live:
            self.after(0, self._handle_event, slug, "live",
                       f"LIVE — {status.title or ''}")

    def _remove_streamer(self, slug: str) -> None:
        # Stop recording first if active
        if self.monitor.recorder.is_recording(slug):
            self.monitor.stop_recording(slug)
        self.config_data.remove_streamer(slug)
        self._streamer_list.remove_streamer(slug)
        self._log(f"Removed streamer: {slug}")

    def _start_recording(self, slug: str) -> None:
        row = self._streamer_list.get_row(slug)
        if row:
            row.set_recording(True, "00:00")
        self.monitor.update_settings()
        path = self.monitor.recorder.start(slug)
        self._log(f"[{slug}] Manual recording started → {path.name}")

    def _stop_recording(self, slug: str) -> None:
        # Disable button immediately to prevent duplicate clicks
        row = self._streamer_list.get_row(slug)
        if row:
            row.set_recording(False)
        # Run stop in background thread to avoid blocking UI
        threading.Thread(
            target=self.monitor.stop_recording, args=(slug,), daemon=True
        ).start()

    # ── Monitoring controls ──────────────────────────────────

    def _start_monitoring(self) -> None:
        if self.monitor.running:
            self._log("Already monitoring")
            return
        self.monitor.update_settings()
        self.monitor.start()

    def _stop_monitoring(self) -> None:
        if not self.monitor.running:
            self._log("Not currently monitoring")
            return
        self.monitor.stop()
        # Reset all row states
        for slug in self._streamer_list.all_slugs():
            row = self._streamer_list.get_row(slug)
            if row:
                row.set_recording(False)

    # ── Settings ─────────────────────────────────────────────

    def _on_settings_change(self, settings: Settings) -> None:
        self.config_data.save()
        self.monitor.update_settings()
        self._log(f"Settings updated — interval: {settings.poll_interval_seconds}s, dir: {settings.output_dir}")

    # ── Monitor events (called from background thread) ───────

    def _on_monitor_event(self, slug: str, event: str, detail: str) -> None:
        # Schedule UI update on the main thread
        self.after(0, self._handle_event, slug, event, detail)

    def _handle_event(self, slug: str, event: str, detail: str) -> None:
        if slug:
            self._log(f"[{slug}] {detail}")
        else:
            self._log(detail)

        row = self._streamer_list.get_row(slug) if slug else None
        if row is None:
            return

        if event == "live":
            row.set_live(True, detail.replace("LIVE — ", ""))
        elif event in ("offline", "recording_ended", "recording_failed"):
            row.set_live(False)
            row.set_recording(False)
        elif event == "recording_started":
            row.set_recording(True, "00:00")
        elif event == "recording_stopped":
            row.set_recording(False)

    # ── Periodic UI update ───────────────────────────────────

    def _update_timer(self) -> None:
        """Update recording elapsed times every second."""
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
        self._log_panel.log(message)
        log.info(message)

    def _on_close(self) -> None:
        if self.monitor.running:
            self._log("Shutting down — stopping all recordings...")
            self.monitor.stop()
        self.destroy()
