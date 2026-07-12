"""Settings panel for poll interval and recording output directory."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from ..config import Settings


class SettingsPanel(ctk.CTkFrame):
    """Inline editors that mutate a shared :class:`~src.config.Settings` object.

    Changes are applied on Enter / focus-out / Browse and reported via
    ``on_change`` so the parent can persist and push updates to the monitor.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        settings: Settings,
        on_change: Callable[[Settings], None],
        **kwargs,
    ) -> None:
        """Create the settings controls bound to ``settings``.

        Args:
            master: Parent widget.
            settings: Live settings object (mutated in place).
            on_change: Called after each successful apply.
        """
        super().__init__(master, **kwargs)
        self._settings = settings
        self._on_change = on_change

        label = ctk.CTkLabel(
            self, text="Settings", font=ctk.CTkFont(size=11), text_color="#888888"
        )
        label.pack(anchor="w", padx=8, pady=(6, 2))

        # Row 1: poll interval
        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(row1, text="Poll interval (sec)").pack(side="left", padx=(0, 4))
        self._interval_var = ctk.StringVar(value=str(settings.poll_interval_seconds))
        self._interval_entry = ctk.CTkEntry(
            row1, textvariable=self._interval_var, width=60, height=28
        )
        self._interval_entry.pack(side="left")
        self._interval_entry.bind("<FocusOut>", lambda _: self._apply())
        self._interval_entry.bind("<Return>", lambda _: self._apply())

        # Row 2: output dir
        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(0, 6))

        ctk.CTkLabel(row2, text="Output directory").pack(side="left", padx=(0, 4))
        self._dir_var = ctk.StringVar(value=str(Path(settings.output_dir).resolve()))
        self._dir_entry = ctk.CTkEntry(row2, textvariable=self._dir_var, height=28)
        self._dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._dir_entry.bind("<FocusOut>", lambda _: self._apply())
        self._dir_entry.bind("<Return>", lambda _: self._apply())

        self._browse_btn = ctk.CTkButton(
            row2, text="Browse", width=70, height=28, command=self._browse,
        )
        self._browse_btn.pack(side="left")

    def _browse(self) -> None:
        """Open a folder picker and apply the chosen output directory."""
        path = filedialog.askdirectory(initialdir=self._settings.output_dir)
        if path:
            self._dir_var.set(path)
            self._apply()

    def _apply(self) -> None:
        """Validate inputs, update ``settings``, and notify ``on_change``."""
        try:
            interval = int(self._interval_var.get())
            if interval < 10:
                interval = 10
        except ValueError:
            interval = self._settings.poll_interval_seconds

        self._settings.poll_interval_seconds = interval
        self._settings.output_dir = (
            self._dir_var.get().strip() or self._settings.output_dir
        )
        self._on_change(self._settings)
