"""Streamer table widget showing status, recording state, and action buttons."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk


class StreamerRow(ctk.CTkFrame):
    """A single row in the streamer list."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        slug: str,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.slug = slug
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording

        # Name
        self._name_label = ctk.CTkLabel(self, text=slug, width=160, anchor="w")
        self._name_label.pack(side="left", padx=(8, 4))

        # Status indicator
        self._status_label = ctk.CTkLabel(
            self, text="\u25cb Offline", width=120, anchor="w",
            text_color="gray",
        )
        self._status_label.pack(side="left", padx=4)

        # Recording indicator
        self._rec_label = ctk.CTkLabel(
            self, text="\u2014", width=140, anchor="w",
            text_color="gray",
        )
        self._rec_label.pack(side="left", padx=4)

        # Button container (right-aligned)
        self._btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._btn_frame.pack(side="right", padx=(4, 8))

        # Stop recording button (hidden until recording)
        self._stop_btn = ctk.CTkButton(
            self._btn_frame, text="Stop Recording", width=110, height=28,
            fg_color="#cc3333", hover_color="#991111",
            font=ctk.CTkFont(weight="bold"),
            command=lambda: self._on_stop_recording(self.slug),
        )

        # Remove button
        self._remove_btn = ctk.CTkButton(
            self._btn_frame, text="\u2715", width=32,
            fg_color="red", hover_color="#aa0000",
            command=lambda: self._on_remove(self.slug),
        )
        self._remove_btn.pack(side="right", padx=2)

    def set_live(self, is_live: bool, title: str = "") -> None:
        if is_live:
            display = f"\U0001f7e2 LIVE"
            if title:
                display += f" — {title[:30]}"
            self._status_label.configure(text=display, text_color="#22cc44")
        else:
            self._status_label.configure(text="\u25cb Offline", text_color="gray")

    def set_recording(self, is_recording: bool, elapsed: str = "") -> None:
        if is_recording:
            self._rec_label.configure(
                text=f"\u25cf REC {elapsed}", text_color="#ff4444",
                font=ctk.CTkFont(weight="bold"),
            )
            self.configure(fg_color="#3a1515")
            self._stop_btn.pack(side="right", padx=2, before=self._remove_btn)
        else:
            self._rec_label.configure(
                text="\u2014", text_color="gray",
                font=ctk.CTkFont(),
            )
            self.configure(fg_color="transparent")
            self._stop_btn.pack_forget()


class StreamerList(ctk.CTkScrollableFrame):
    """Scrollable list of streamer rows."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording
        self._rows: dict[str, StreamerRow] = {}

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(header, text="Channel", width=160, anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(8, 4))
        ctk.CTkLabel(header, text="Status", width=120, anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4)
        ctk.CTkLabel(header, text="Recording", width=140, anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4)

    def add_streamer(self, slug: str) -> None:
        if slug in self._rows:
            return
        row = StreamerRow(
            self, slug,
            on_remove=self._on_remove,
            on_stop_recording=self._on_stop_recording,
        )
        row.pack(fill="x", pady=1)
        self._rows[slug] = row

    def remove_streamer(self, slug: str) -> None:
        row = self._rows.pop(slug, None)
        if row:
            row.destroy()

    def get_row(self, slug: str) -> StreamerRow | None:
        return self._rows.get(slug)

    def all_slugs(self) -> list[str]:
        return list(self._rows.keys())
