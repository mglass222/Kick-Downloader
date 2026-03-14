"""Scrollable log panel widget."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk


class LogPanel(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass, **kwargs) -> None:
        super().__init__(master, **kwargs)

        label = ctk.CTkLabel(self, text="Log", font=ctk.CTkFont(size=11), text_color="#888888")
        label.pack(anchor="w", padx=8, pady=(6, 2))

        self._textbox = ctk.CTkTextbox(self, height=120, state="disabled")
        self._textbox.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self._textbox.configure(state="normal")
        self._textbox.insert("end", line)
        self._textbox.see("end")
        self._textbox.configure(state="disabled")
