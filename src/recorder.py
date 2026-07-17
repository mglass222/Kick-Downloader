"""yt-dlp subprocess management for recording Kick live streams.

Each active channel gets one subprocess writing a ``.ts`` file. On stop
or natural exit the file is remuxed to QuickTime-friendly ``.mp4`` via
ffmpeg. All mutations of the active-recording map are guarded by a lock.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Refuse to start a recording when free space is below this threshold
MIN_FREE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

QUALITY_FORMATS: dict[str, str] = {
    "best": "best",
    "1080p": "best[height<=1080]/best",
    "720p": "best[height<=720]/best",
    "480p": "best[height<=480]/best",
    "worst": "worst",
}


class RecordingStartError(RuntimeError):
    """Raised when a recording cannot be started (e.g. low disk space)."""


def quality_to_format(quality: str) -> str:
    """Map a settings quality label to a yt-dlp ``-f`` format expression."""
    return QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])


@dataclass
class RecordingInfo:
    """Metadata for one in-progress (or just-exited) yt-dlp recording.

    Attributes:
        slug: Channel being recorded.
        output_path: Target ``.mp4`` path after remux.
        process: The yt-dlp :class:`~subprocess.Popen` handle.
        started_at: ``time.time()`` when recording started.
        log_file: Open file handle receiving yt-dlp stdout/stderr.
        log_path: Path to the ``.ytdlp.log`` sidecar file.
    """

    slug: str
    output_path: Path
    process: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    log_file: object = field(default=None, repr=False)
    log_path: Path | None = None

    def elapsed_seconds(self) -> float:
        """Seconds since this recording started."""
        return time.time() - self.started_at

    def is_alive(self) -> bool:
        """True if the yt-dlp process has not exited yet."""
        return self.process.poll() is None


@dataclass
class FinishedRecording:
    """Result of finalizing a yt-dlp process that has exited.

    Attributes:
        slug: Channel that was recorded.
        exit_code: Process return code (``-1`` if unknown).
        elapsed_seconds: How long the process ran.
        output: Tail of the yt-dlp log for diagnostics.
        failed: True when the exit looks like a failure (non-zero or
            very short), unless the stop was expected/manual.
    """

    slug: str
    exit_code: int
    elapsed_seconds: float
    output: str
    failed: bool


class Recorder:
    """Manages one yt-dlp subprocess per streamer.

    ``is_recording`` is read-only; call :meth:`reap_finished` (or
    :meth:`stop`) to remux and remove dead entries.
    """

    def __init__(
        self,
        output_dir: str,
        filename_template: str,
        quality: str = "best",
        min_free_bytes: int = MIN_FREE_BYTES,
    ) -> None:
        """Create a recorder writing under ``output_dir``.

        Args:
            output_dir: Root folder for per-channel recording directories.
            filename_template: Format with ``{channel}``, ``{date}``, ``{time}``.
            quality: Preferred quality label (see :data:`QUALITY_FORMATS`).
            min_free_bytes: Minimum free disk space required to start.
        """
        self.output_dir = Path(output_dir)
        self.filename_template = filename_template
        self.quality = quality
        self.min_free_bytes = min_free_bytes
        self._active: dict[str, RecordingInfo] = {}
        self._lock = threading.Lock()

    def is_recording(self, slug: str) -> bool:
        """Return True if a yt-dlp process for ``slug`` is still running.

        This check has no side effects (no remux or cleanup).
        """
        with self._lock:
            info = self._active.get(slug)
            return info is not None and info.is_alive()

    def get_info(self, slug: str) -> RecordingInfo | None:
        """Return the active :class:`RecordingInfo` for ``slug``, if any."""
        with self._lock:
            return self._active.get(slug)

    def active_slugs(self) -> list[str]:
        """Return slugs currently tracked in the active map (alive or stale)."""
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
        """Start recording a channel.

        If already recording, returns the existing output path. A stale
        dead entry for the same slug is finalized before a new start.

        Raises:
            RecordingStartError: If free disk space is below the threshold.

        Returns:
            Path to the eventual ``.mp4`` output file.
        """
        with self._lock:
            existing = self._active.get(slug)
            if existing is not None and existing.is_alive():
                return existing.output_path
            if existing is not None:
                # Stale dead entry — finalize before starting a new one
                self._finalize_locked(existing, expected_stop=False)

            self.output_dir.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.output_dir).free
            if free < self.min_free_bytes:
                free_gib = free / (1024**3)
                need_gib = self.min_free_bytes / (1024**3)
                raise RecordingStartError(
                    f"Insufficient disk space ({free_gib:.1f} GiB free, "
                    f"need {need_gib:.1f} GiB) in {self.output_dir}"
                )

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

            fmt = quality_to_format(self.quality)
            cmd = [
                "yt-dlp",
                f"https://kick.com/{slug}",
                "-f", fmt,
                "-o", str(ts_path),
                "--no-part",
                "--no-live-from-start",
                "--wait-for-video", "30",
            ]

            log_path = channel_dir / (filename + ".ytdlp.log")
            log.info(
                "Starting recording for '%s' → %s (quality=%s)",
                slug,
                output_path,
                self.quality,
            )
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
        """Gracefully stop recording a channel (SIGTERM, then kill).

        Returns:
            Finalize result, or None if nothing was active for ``slug``.
        """
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
        """Stop every active recording."""
        with self._lock:
            slugs = list(self._active)
        for slug in slugs:
            self.stop(slug)

    def _finalize_locked(
        self, info: RecordingInfo, *, expected_stop: bool
    ) -> FinishedRecording:
        """Close log, capture state, and remove ``info`` from ``_active``.

        Caller must hold ``self._lock``. The lock is released before remux
        (which can block) and re-acquired afterward.
        """
        elapsed = info.elapsed_seconds()
        self._close_log(info)
        exit_code = info.process.returncode
        if exit_code is None:
            exit_code = -1
        output = self._read_log_tail(info)

        # Remove from active dict before releasing lock
        self._active.pop(info.slug, None)

        # Capture process and cleanup state before releasing lock
        process = info.process
        log_path = info.log_path
        should_cleanup_log = (
            (exit_code == 0 or expected_stop) and log_path and log_path.exists()
        )

        # Release lock before blocking operations
        self._lock.release()
        try:
            # Remux outside the lock (can take significant time)
            self._remux_to_mp4(info)

            # Kill process if still alive
            if process.poll() is None:
                process.kill()

            # Keep yt-dlp log on failure for debugging
            if should_cleanup_log:
                log_path.unlink(missing_ok=True)
        finally:
            self._lock.acquire()

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
        """Return the last lines of the yt-dlp log for error reporting."""
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
        """Remux the recorded ``.ts`` to a QuickTime-compatible ``.mp4``.

        Only deletes the ``.ts`` when ffmpeg exits 0 and the ``.mp4`` is
        non-empty. On failure the ``.ts`` is kept.
        """
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
        """Flush/close the yt-dlp log file handle if still open."""
        if info.log_file and not info.log_file.closed:
            info.log_file.close()
