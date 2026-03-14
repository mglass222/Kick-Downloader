"""Streamer table widget showing status, recording state, and action buttons."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

# Fixed column widths so header and rows stay aligned.
_COL_CHANNEL = 130
_COL_STATUS = 140
_COL_REC = 110
_COL_BTNS = 120


class StreamerRow(ctk.CTkFrame):
    """A single row in the streamer list."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        slug: str,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        on_start_recording: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.slug = slug
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording
        self._on_start_recording = on_start_recording
        self._is_live = False
        self._is_recording = False

        # Pack RIGHT side first so buttons always have space.
        self._btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._btn_frame.pack(side="right", padx=(0, 4))

        self._remove_btn = ctk.CTkButton(
            self._btn_frame, text="\u2715", width=28, height=28,
            fg_color="#555555", hover_color="#aa0000",
            command=lambda: self._on_remove(self.slug),
        )
        self._remove_btn.pack(side="right", padx=2)

        self._stop_btn = ctk.CTkButton(
            self._btn_frame, text="Stop", width=70, height=28,
            fg_color="#cc3333", hover_color="#991111",
            command=lambda: self._on_stop_recording(self.slug),
        )

        self._start_btn = ctk.CTkButton(
            self._btn_frame, text="Record", width=70, height=28,
            fg_color="#228822", hover_color="#116611",
            command=lambda: self._on_start_recording(self.slug),
        )

        # Pack LEFT side: fixed-width label containers.
        self._name_label = ctk.CTkLabel(
            self, text=slug, width=_COL_CHANNEL, anchor="w",
        )
        self._name_label.pack(side="left", padx=(8, 0))

        self._status_label = ctk.CTkLabel(
            self, text="Offline", width=_COL_STATUS, anchor="w",
            text_color="gray",
        )
        self._status_label.pack(side="left", padx=4)

        self._rec_label = ctk.CTkLabel(
            self, text="", width=_COL_REC, anchor="w",
            text_color="gray",
        )
        self._rec_label.pack(side="left", padx=4)

    def set_live(self, is_live: bool, title: str = "") -> None:
        self._is_live = is_live
        if is_live:
            display = "Live"
            if title:
                display += f"  {title[:10]}"
            self._status_label.configure(text=display, text_color="#53d769")
        else:
            self._status_label.configure(text="Offline", text_color="gray")
        self._update_action_buttons()

    def set_recording(self, is_recording: bool, elapsed: str = "") -> None:
        self._is_recording = is_recording
        if is_recording:
            self._rec_label.configure(
                text=f"\u25cf REC {elapsed}", text_color="#ff4444",
            )
            self.configure(fg_color="#2a1111")
        else:
            self._rec_label.configure(text="", text_color="gray")
            self.configure(fg_color="transparent")
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        self._stop_btn.pack_forget()
        self._start_btn.pack_forget()
        if self._is_recording:
            self._stop_btn.pack(side="right", padx=2)
        elif self._is_live:
            self._start_btn.pack(side="right", padx=2)


def create_header(master: ctk.CTkBaseClass) -> ctk.CTkFrame:
    """Create the column header bar (placed outside the scrollable area)."""
    header = ctk.CTkFrame(master, fg_color="transparent")
    hdr_font = ctk.CTkFont(size=11)

    ctk.CTkLabel(header, text="Channel", width=_COL_CHANNEL, anchor="w",
                 font=hdr_font, text_color="#888888").pack(side="left", padx=(8, 0))
    ctk.CTkLabel(header, text="Status", width=_COL_STATUS, anchor="w",
                 font=hdr_font, text_color="#888888").pack(side="left", padx=4)
    ctk.CTkLabel(header, text="Recording", width=_COL_REC, anchor="w",
                 font=hdr_font, text_color="#888888").pack(side="left", padx=4)
    return header


class StreamerList(ctk.CTkScrollableFrame):
    """Scrollable list of streamer rows."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        on_start_recording: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording
        self._on_start_recording = on_start_recording
        self._rows: dict[str, StreamerRow] = {}

    def add_streamer(self, slug: str) -> None:
        if slug in self._rows:
            return
        row = StreamerRow(
            self, slug,
            on_remove=self._on_remove,
            on_stop_recording=self._on_stop_recording,
            on_start_recording=self._on_start_recording,
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
