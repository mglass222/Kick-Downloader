"""Manages yt-dlp subprocesses for recording Kick streams."""

from __future__ import annotations

import logging
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
    log_file: object = field(default=None, repr=False)
    log_path: Path | None = None

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
            self._close_log(info)
            self._remux_to_mp4(info)
            self._cleanup(slug)
            return False
        return True

    def get_exit_info(self, info: RecordingInfo) -> tuple[int, str]:
        """Return (exit_code, last_output) for a finished process."""
        code = info.process.returncode or 0
        output = ""
        self._close_log(info)
        if info.log_path and info.log_path.exists():
            try:
                text = info.log_path.read_text(errors="replace").strip()
                lines = text.splitlines()
                if len(lines) > 10:
                    output = "\n".join(lines[-10:])
                else:
                    output = text
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
        if filename.endswith(".mp4"):
            filename = filename[:-4]
        ts_path = channel_dir / (filename + ".ts")
        output_path = channel_dir / (filename + ".mp4")

        cmd = [
            "yt-dlp",
            f"https://kick.com/{slug}",
            "-o", str(ts_path),
            "--no-part",
            "--no-live-from-start",
            "--wait-for-video", "30",
        ]

        log_path = channel_dir / (filename + ".ytdlp.log")
        log.info("Starting recording for '%s' → %s", slug, output_path)
        log_file = open(log_path, "w")  # noqa: SIM115
        process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        self._active[slug] = RecordingInfo(
            slug=slug,
            output_path=output_path,
            process=process,
            log_file=log_file,
            log_path=log_path,
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
        self._close_log(info)
        self._remux_to_mp4(info)
        self._cleanup(slug)

    def stop_all(self) -> None:
        """Stop all active recordings."""
        for slug in list(self._active):
            self.stop(slug)

    def _remux_to_mp4(self, info: RecordingInfo) -> None:
        """Remux the recorded .ts file to a QuickTime-compatible .mp4."""
        ts_path = info.output_path.with_suffix(".ts")
        mp4_path = info.output_path
        if not ts_path.exists() or ts_path.stat().st_size == 0:
            return
        log.info("Remuxing %s → %s", ts_path, mp4_path)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", str(ts_path),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(mp4_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=300,
            )
            if mp4_path.exists() and mp4_path.stat().st_size > 0:
                ts_path.unlink()
                log.info("Remux complete: %s", mp4_path)
            else:
                log.error("Remux produced empty file, keeping .ts: %s", ts_path)
        except FileNotFoundError:
            log.error("ffmpeg not found — install it to get QuickTime-compatible .mp4 files")
        except subprocess.TimeoutExpired:
            log.error("Remux timed out for %s", ts_path)

    @staticmethod
    def _close_log(info: RecordingInfo) -> None:
        if info.log_file and not info.log_file.closed:
            info.log_file.close()

    def _cleanup(self, slug: str) -> None:
        info = self._active.pop(slug, None)
        if info is None:
            return
        self._close_log(info)
        if info.process.poll() is None:
            info.process.kill()
        # Remove log file on successful recordings; keep on failure for debugging
        if info.log_path and info.log_path.exists():
            exit_code = info.process.returncode
            if exit_code == 0:
                info.log_path.unlink(missing_ok=True)
