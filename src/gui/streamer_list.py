"""Streamer table widget: status, recording state, and action buttons."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

# Fixed column widths so header and rows stay aligned.
_COL_ENABLE = 36
_COL_CHANNEL = 120
_COL_STATUS = 140
_COL_REC = 110


class StreamerRow(ctk.CTkFrame):
    """One streamer row: enable checkbox, status, REC indicator, actions."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        slug: str,
        enabled: bool,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        on_start_recording: Callable[[str], None],
        on_toggle_enabled: Callable[[str, bool], None],
        **kwargs,
    ) -> None:
        """Build the row widgets and wire callbacks.

        Args:
            master: Parent list frame.
            slug: Kick channel name shown in the row.
            enabled: Initial monitoring-enabled checkbox state.
            on_remove: Called when the remove button is clicked.
            on_stop_recording: Called when Stop is clicked.
            on_start_recording: Called when Record is clicked.
            on_toggle_enabled: Called with ``(slug, enabled)`` on checkbox toggle.
        """
        super().__init__(master, **kwargs)
        self.slug = slug
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording
        self._on_start_recording = on_start_recording
        self._on_toggle_enabled = on_toggle_enabled
        self._is_live = False
        self._is_recording = False
        self._enabled = enabled

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

        self._enabled_var = ctk.BooleanVar(value=enabled)
        self._enable_cb = ctk.CTkCheckBox(
            self,
            text="",
            width=_COL_ENABLE,
            variable=self._enabled_var,
            command=self._on_enable_clicked,
        )
        self._enable_cb.pack(side="left", padx=(8, 0))

        self._name_label = ctk.CTkLabel(
            self, text=slug, width=_COL_CHANNEL, anchor="w",
        )
        self._name_label.pack(side="left", padx=(4, 0))

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
        self._apply_enabled_style()

    def _on_enable_clicked(self) -> None:
        """Handle checkbox toggle and notify the parent."""
        self._enabled = bool(self._enabled_var.get())
        self._apply_enabled_style()
        self._update_action_buttons()
        self._on_toggle_enabled(self.slug, self._enabled)

    def set_enabled(self, enabled: bool) -> None:
        """Programmatically set the enable checkbox and dimmed name style."""
        self._enabled = enabled
        self._enabled_var.set(enabled)
        self._apply_enabled_style()
        self._update_action_buttons()

    def _apply_enabled_style(self) -> None:
        """Dim the channel name when monitoring is disabled."""
        color = "#cccccc" if self._enabled else "#666666"
        self._name_label.configure(text_color=color)

    def set_live(self, is_live: bool, title: str = "") -> None:
        """Update the live/offline status label and action buttons."""
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
        """Show or clear the REC indicator and highlight the row."""
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

    def update_elapsed(self, elapsed: str) -> None:
        """Update only the recording timer text (no layout changes)."""
        self._rec_label.configure(text=f"\u25cf REC {elapsed}")

    def _update_action_buttons(self) -> None:
        """Show Stop while recording, Record when live and enabled."""
        self._stop_btn.pack_forget()
        self._start_btn.pack_forget()
        if self._is_recording:
            self._stop_btn.pack(side="right", padx=2)
        elif self._is_live and self._enabled:
            self._start_btn.pack(side="right", padx=2)


def create_header(master: ctk.CTkBaseClass) -> ctk.CTkFrame:
    """Create the column header bar (placed outside the scrollable area)."""
    header = ctk.CTkFrame(master, fg_color="transparent")
    hdr_font = ctk.CTkFont(size=11)

    ctk.CTkLabel(
        header, text="", width=_COL_ENABLE, anchor="w", font=hdr_font,
    ).pack(side="left", padx=(8, 0))
    ctk.CTkLabel(
        header, text="Channel", width=_COL_CHANNEL, anchor="w",
        font=hdr_font, text_color="#888888",
    ).pack(side="left", padx=(4, 0))
    ctk.CTkLabel(
        header, text="Status", width=_COL_STATUS, anchor="w",
        font=hdr_font, text_color="#888888",
    ).pack(side="left", padx=4)
    ctk.CTkLabel(
        header, text="Recording", width=_COL_REC, anchor="w",
        font=hdr_font, text_color="#888888",
    ).pack(side="left", padx=4)
    return header


class StreamerList(ctk.CTkScrollableFrame):
    """Scrollable collection of :class:`StreamerRow` widgets."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_remove: Callable[[str], None],
        on_stop_recording: Callable[[str], None],
        on_start_recording: Callable[[str], None],
        on_toggle_enabled: Callable[[str, bool], None],
        **kwargs,
    ) -> None:
        """Create an empty list that forwards row callbacks to the parent."""
        super().__init__(master, **kwargs)
        self._on_remove = on_remove
        self._on_stop_recording = on_stop_recording
        self._on_start_recording = on_start_recording
        self._on_toggle_enabled = on_toggle_enabled
        self._rows: dict[str, StreamerRow] = {}

    def add_streamer(self, slug: str, enabled: bool = True) -> None:
        """Add a row for ``slug`` if it is not already present."""
        if slug in self._rows:
            return
        row = StreamerRow(
            self, slug,
            enabled=enabled,
            on_remove=self._on_remove,
            on_stop_recording=self._on_stop_recording,
            on_start_recording=self._on_start_recording,
            on_toggle_enabled=self._on_toggle_enabled,
        )
        row.pack(fill="x", pady=1)
        self._rows[slug] = row

    def remove_streamer(self, slug: str) -> None:
        """Destroy and forget the row for ``slug`` if present."""
        row = self._rows.pop(slug, None)
        if row:
            row.destroy()

    def get_row(self, slug: str) -> StreamerRow | None:
        """Return the row widget for ``slug``, or None."""
        return self._rows.get(slug)

    def all_slugs(self) -> list[str]:
        """Return the slugs currently shown in the list."""
        return list(self._rows.keys())
