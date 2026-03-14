"""Manages yt-dlp subprocesses for recording Kick streams."""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class RecordingInfo:
    slug: str
    output_path: Path
    process: subprocess.Popen
    started_at: float = field(default_factory=time.time)

    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def is_alive(self) -> bool:
        return self.process.poll() is None


class Recorder:
    """Manages one yt-dlp subprocess per streamer."""

    def __init__(self, output_dir: str, filename_template: str) -> None:
        self.output_dir = Path(output_dir)
        self.filename_template = filename_template
        self._active: dict[str, RecordingInfo] = {}

    def is_recording(self, slug: str) -> bool:
        info = self._active.get(slug)
        if info is None:
            return False
        if not info.is_alive():
            # Process exited on its own (stream ended or failed)
            self._cleanup(slug)
            return False
        return True

    def get_exit_info(self, info: RecordingInfo) -> tuple[int, str]:
        """Return (exit_code, last_output) for a finished process."""
        code = info.process.returncode or 0
        output = ""
        if info.process.stdout:
            try:
                remaining = info.process.stdout.read()
                if remaining:
                    output = remaining.decode("utf-8", errors="replace").strip()
                    # Keep only last few lines for relevance
                    lines = output.splitlines()
                    if len(lines) > 10:
                        output = "\n".join(lines[-10:])
            except Exception:
                pass
        return code, output

    def get_info(self, slug: str) -> RecordingInfo | None:
        return self._active.get(slug)

    def start(self, slug: str) -> Path:
        """Start recording a channel. Returns the output file path."""
        if self.is_recording(slug):
            return self._active[slug].output_path

        channel_dir = self.output_dir / slug
        channel_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        filename = self.filename_template.format(
            channel=slug,
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H-%M-%S"),
        )
        if not filename.endswith(".mp4"):
            filename += ".mp4"
        output_path = channel_dir / filename

        cmd = [
            "yt-dlp",
            f"https://kick.com/{slug}",
            "-o", str(output_path),
            "--no-part",
            "--no-live-from-start",
            "--wait-for-video", "30",
        ]

        log.info("Starting recording for '%s' → %s", slug, output_path)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self._active[slug] = RecordingInfo(
            slug=slug,
            output_path=output_path,
            process=process,
        )
        return output_path

    def stop(self, slug: str) -> None:
        """Gracefully stop recording a channel."""
        info = self._active.get(slug)
        if info is None:
            return
        if info.is_alive():
            log.info("Stopping recording for '%s'", slug)
            info.process.terminate()  # SIGTERM — more reliable than SIGINT for piped processes
            try:
                info.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("yt-dlp did not exit for '%s', killing", slug)
                info.process.kill()
                info.process.wait(timeout=5)
        self._cleanup(slug)

    def stop_all(self) -> None:
        """Stop all active recordings."""
        for slug in list(self._active):
            self.stop(slug)

    def _cleanup(self, slug: str) -> None:
        info = self._active.pop(slug, None)
        if info and info.process.poll() is None:
            info.process.kill()
