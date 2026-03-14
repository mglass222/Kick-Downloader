"""Add streamer input bar widget."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk


class AddStreamerBar(ctk.CTkFrame):
    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_add: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._on_add = on_add

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=6)

        self._entry = ctk.CTkEntry(
            row, placeholder_text="Channel name, e.g. xqc", height=32,
        )
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._entry.bind("<Return>", lambda _: self._submit())

        self._btn = ctk.CTkButton(
            row, text="+ Add", width=70, height=32,
            command=self._submit,
        )
        self._btn.pack(side="left")

    def _submit(self) -> None:
        slug = self._entry.get().strip()
        if slug:
            self._on_add(slug)
            self._entry.delete(0, "end")
