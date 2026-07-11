"""Scrollable log panel widget."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

# Keep the UI responsive on long-running sessions
_MAX_LINES = 500


class LogPanel(ctk.CTkFrame):
    def __init__(
        self,
        master: ctk.CTkBaseClass,
        max_lines: int = _MAX_LINES,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._max_lines = max_lines
        self._line_count = 0

        label = ctk.CTkLabel(
            self, text="Log", font=ctk.CTkFont(size=11), text_color="#888888"
        )
        label.pack(anchor="w", padx=8, pady=(6, 2))

        self._textbox = ctk.CTkTextbox(self, height=120, state="disabled")
        self._textbox.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self._textbox.configure(state="normal")
        self._textbox.insert("end", line)
        self._line_count += 1
        if self._line_count > self._max_lines:
            # Delete oldest lines (1.0 is first char; delete through N newlines)
            excess = self._line_count - self._max_lines
            self._textbox.delete("1.0", f"{excess + 1}.0")
            self._line_count = self._max_lines
        self._textbox.see("end")
        self._textbox.configure(state="disabled")
