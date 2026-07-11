"""Manages yt-dlp subprocesses for recording Kick streams."""

from __future__ import annotations

import logging
import subprocess
import threading
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


@dataclass
class FinishedRecording:
    """Result of reaping a yt-dlp process that has exited."""

    slug: str
    exit_code: int
    elapsed_seconds: float
    output: str
    failed: bool


class Recorder:
    """Manages one yt-dlp subprocess per streamer."""

    def __init__(self, output_dir: str, filename_template: str) -> None:
        self.output_dir = Path(output_dir)
        self.filename_template = filename_template
        self._active: dict[str, RecordingInfo] = {}
        self._lock = threading.Lock()

    def is_recording(self, slug: str) -> bool:
        """Return True if a yt-dlp process for slug is still running (read-only)."""
        with self._lock:
            info = self._active.get(slug)
            return info is not None and info.is_alive()

    def get_info(self, slug: str) -> RecordingInfo | None:
        with self._lock:
            return self._active.get(slug)

    def active_slugs(self) -> list[str]:
        with self._lock:
            return list(self._active)

    def reap_finished(self) -> list[FinishedRecording]:
        """Remux and clean up any recordings whose process has exited."""
        finished: list[FinishedRecording] = []
        with self._lock:
            dead = [
                slug
                for slug, info in self._active.items()
                if not info.is_alive()
            ]
            for slug in dead:
                info = self._active[slug]
                result = self._finalize_locked(info, expected_stop=False)
                finished.append(result)
        return finished

    def start(self, slug: str) -> Path:
        """Start recording a channel. Returns the output file path."""
        with self._lock:
            existing = self._active.get(slug)
            if existing is not None and existing.is_alive():
                return existing.output_path
            if existing is not None:
                # Stale dead entry — finalize before starting a new one
                self._finalize_locked(existing, expected_stop=False)

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

    def stop(self, slug: str) -> FinishedRecording | None:
        """Gracefully stop recording a channel."""
        with self._lock:
            info = self._active.get(slug)
            if info is None:
                return None
            if info.is_alive():
                log.info("Stopping recording for '%s'", slug)
                info.process.terminate()
                try:
                    info.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning("yt-dlp did not exit for '%s', killing", slug)
                    info.process.kill()
                    info.process.wait(timeout=5)
            return self._finalize_locked(info, expected_stop=True)

    def stop_all(self) -> None:
        """Stop all active recordings."""
        with self._lock:
            slugs = list(self._active)
        for slug in slugs:
            self.stop(slug)

    def _finalize_locked(
        self, info: RecordingInfo, *, expected_stop: bool
    ) -> FinishedRecording:
        """Close log, remux, pop from _active. Caller must hold self._lock."""
        elapsed = info.elapsed_seconds()
        self._close_log(info)
        exit_code = info.process.returncode
        if exit_code is None:
            exit_code = -1
        output = self._read_log_tail(info)
        self._remux_to_mp4(info)
        self._active.pop(info.slug, None)
        if info.process.poll() is None:
            info.process.kill()
        # Keep yt-dlp log on failure for debugging
        if info.log_path and info.log_path.exists():
            if exit_code == 0 or expected_stop:
                info.log_path.unlink(missing_ok=True)
        failed = (not expected_stop) and (exit_code != 0 or elapsed < 30)
        return FinishedRecording(
            slug=info.slug,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            output=output,
            failed=failed,
        )

    @staticmethod
    def _read_log_tail(info: RecordingInfo) -> str:
        if not info.log_path or not info.log_path.exists():
            return ""
        try:
            text = info.log_path.read_text(errors="replace").strip()
            lines = text.splitlines()
            if len(lines) > 10:
                return "\n".join(lines[-10:])
            return text
        except OSError:
            return ""

    def _remux_to_mp4(self, info: RecordingInfo) -> None:
        """Remux the recorded .ts file to a QuickTime-compatible .mp4."""
        ts_path = info.output_path.with_suffix(".ts")
        mp4_path = info.output_path
        if not ts_path.exists() or ts_path.stat().st_size == 0:
            return
        log.info("Remuxing %s → %s", ts_path, mp4_path)
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(ts_path),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if (
                result.returncode == 0
                and mp4_path.exists()
                and mp4_path.stat().st_size > 0
            ):
                ts_path.unlink()
                log.info("Remux complete: %s", mp4_path)
            else:
                err = (result.stderr or "").strip().splitlines()
                last = err[-1] if err else f"exit {result.returncode}"
                log.error(
                    "Remux failed for %s (%s), keeping .ts",
                    ts_path,
                    last,
                )
                if mp4_path.exists() and mp4_path.stat().st_size == 0:
                    mp4_path.unlink(missing_ok=True)
        except FileNotFoundError:
            log.error(
                "ffmpeg not found — install it to get QuickTime-compatible .mp4 files"
            )
        except subprocess.TimeoutExpired:
            log.error("Remux timed out for %s", ts_path)

    @staticmethod
    def _close_log(info: RecordingInfo) -> None:
        if info.log_file and not info.log_file.closed:
            info.log_file.close()
