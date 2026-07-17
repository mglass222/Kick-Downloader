"""yt-dlp Python API management for recording Kick live streams.

Each active channel runs ``YoutubeDL.download`` on a dedicated daemon
thread. On stop or natural exit the file is remuxed to QuickTime-friendly
``.mp4`` via ffmpeg. Mutations of the active-recording map are lock-guarded;
the lock is not held across download or remux.
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

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

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
    """Map a settings quality label to a yt-dlp format expression."""
    return QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])


class _FileLogger:
    """Minimal yt-dlp logger that appends messages to a sidecar log file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _write(self, level: str, msg: str) -> None:
        line = f"[{level}] {msg}\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            return
        self._write("info", msg)

    def info(self, msg: str) -> None:
        self._write("info", msg)

    def warning(self, msg: str) -> None:
        self._write("warning", msg)

    def error(self, msg: str) -> None:
        self._write("error", msg)


@dataclass
class RecordingInfo:
    """Metadata for one in-progress (or just-exited) yt-dlp recording.

    Attributes:
        slug: Channel being recorded.
        output_path: Target ``.mp4`` path after remux.
        thread: Worker thread running YoutubeDL.
        cancel: Set to request cooperative cancel via progress hook.
        started_at: ``time.time()`` when recording started.
        log_path: Path to the ``.ytdlp.log`` sidecar file.
        exit_code: 0 success/cancel, 1 error, None while running.
        error: Last error message from the worker, if any.
        ts_path: Intermediate transport-stream (or native) path for remux.
    """

    slug: str
    output_path: Path
    thread: threading.Thread
    cancel: threading.Event
    started_at: float = field(default_factory=time.time)
    log_path: Path | None = None
    exit_code: int | None = None
    error: str | None = None
    ts_path: Path | None = None

    def elapsed_seconds(self) -> float:
        """Seconds since this recording started."""
        return time.time() - self.started_at

    def is_alive(self) -> bool:
        """True if the download worker thread is still running."""
        return self.thread.is_alive()


@dataclass
class FinishedRecording:
    """Result of finalizing a yt-dlp download that has exited.

    Attributes:
        slug: Channel that was recorded.
        exit_code: Worker result code (``-1`` if unknown).
        elapsed_seconds: How long the download ran.
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
    """Manages one yt-dlp YoutubeDL worker thread per streamer.

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
        """Return True if a download worker for ``slug`` is still running.

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
        """Remux and clean up any recordings whose worker has exited."""
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
        """Start recording a channel on a daemon worker thread.

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
            # Prefer .ts container; yt-dlp may still choose another ext
            outtmpl = str(channel_dir / (filename + ".%(ext)s"))
            ts_path = channel_dir / (filename + ".ts")
            output_path = channel_dir / (filename + ".mp4")
            log_path = channel_dir / (filename + ".ytdlp.log")
            if log_path.exists():
                log_path.unlink()

            cancel = threading.Event()
            # Placeholder thread replaced immediately after info is constructed
            info_box: dict[str, RecordingInfo] = {}

            def worker() -> None:
                self._run_download(info_box["info"], outtmpl)

            thread = threading.Thread(
                target=worker,
                name=f"ytdlp-{slug}",
                daemon=True,
            )
            info = RecordingInfo(
                slug=slug,
                output_path=output_path,
                thread=thread,
                cancel=cancel,
                log_path=log_path,
                ts_path=ts_path,
            )
            info_box["info"] = info
            self._active[slug] = info

            log.info(
                "Starting recording for '%s' → %s (quality=%s)",
                slug,
                output_path,
                self.quality,
            )
            thread.start()
            return output_path

    def _run_download(self, info: RecordingInfo, outtmpl: str) -> None:
        """Worker entry: run YoutubeDL until complete, cancelled, or error."""
        cancel = info.cancel

        def progress_hook(status: dict) -> None:
            if cancel.is_set():
                raise DownloadCancelled("stopped by user")

        file_logger = _FileLogger(info.log_path) if info.log_path else None
        opts: dict = {
            "format": quality_to_format(self.quality),
            "outtmpl": outtmpl,
            "nopart": True,
            "live_from_start": False,
            "wait_for_video": 30,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [progress_hook],
        }
        if file_logger is not None:
            opts["logger"] = file_logger

        url = f"https://kick.com/{info.slug}"
        try:
            with YoutubeDL(opts) as ydl:
                code = ydl.download([url])
            info.exit_code = 0 if code == 0 else int(code)
            if info.exit_code != 0:
                info.error = f"yt-dlp exited with code {info.exit_code}"
            # Discover actual download path if ext differed from .ts
            self._resolve_ts_path(info, outtmpl)
        except DownloadCancelled:
            info.exit_code = 0
            info.error = None
            self._resolve_ts_path(info, outtmpl)
            log.info("Recording cancelled for '%s'", info.slug)
        except Exception as exc:
            info.exit_code = 1
            info.error = str(exc)
            self._resolve_ts_path(info, outtmpl)
            log.warning("Recording failed for '%s': %s", info.slug, exc)

    @staticmethod
    def _resolve_ts_path(info: RecordingInfo, outtmpl: str) -> None:
        """Point ``ts_path`` at the file yt-dlp actually wrote, if any."""
        # outtmpl like /dir/name.%(ext)s → search for name.*
        base = outtmpl.replace(".%(ext)s", "")
        parent = Path(base).parent
        stem = Path(base).name
        if info.ts_path and info.ts_path.exists():
            return
        candidates = sorted(
            parent.glob(stem + ".*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            if path.suffix.lower() in {".log", ".mp4"}:
                continue
            info.ts_path = path
            return

    def stop(self, slug: str) -> FinishedRecording | None:
        """Request cancel and finalize recording for ``slug``.

        Returns:
            Finalize result, or None if nothing was active for ``slug``.
        """
        with self._lock:
            info = self._active.get(slug)
            if info is None:
                return None
            if info.is_alive():
                log.info("Stopping recording for '%s'", slug)
                info.cancel.set()
                thread = info.thread
            else:
                thread = None

        if thread is not None:
            thread.join(timeout=30)
            if thread.is_alive():
                log.warning("Download worker did not exit for '%s'", slug)

        with self._lock:
            info = self._active.get(slug)
            if info is None:
                return None
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
        """Capture state, remux, and remove ``info`` from ``_active``.

        Caller must hold ``self._lock``. The lock is released before remux.
        """
        elapsed = info.elapsed_seconds()
        exit_code = info.exit_code if info.exit_code is not None else -1
        output = self._read_log_tail(info)
        if info.error and info.error not in output:
            output = (output + "\n" + info.error).strip()

        self._active.pop(info.slug, None)
        log_path = info.log_path
        should_cleanup_log = (
            (exit_code == 0 or expected_stop) and log_path and log_path.exists()
        )

        self._lock.release()
        try:
            self._remux_to_mp4(info)
            if should_cleanup_log and log_path is not None:
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
        """Remux the recorded media to a QuickTime-compatible ``.mp4``.

        Only deletes the source when ffmpeg exits 0 and the ``.mp4`` is
        non-empty. On failure the source is kept.
        """
        src = info.ts_path
        if src is None or not src.exists():
            # Fall back to conventional .ts next to mp4
            candidate = info.output_path.with_suffix(".ts")
            src = candidate if candidate.exists() else None
        if src is None or not src.exists() or src.stat().st_size == 0:
            return
        mp4_path = info.output_path
        log.info("Remuxing %s → %s", src, mp4_path)
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(src),
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
                if src.resolve() != mp4_path.resolve():
                    src.unlink(missing_ok=True)
                log.info("Remux complete: %s", mp4_path)
            else:
                err = (result.stderr or "").strip().splitlines()
                last = err[-1] if err else f"exit {result.returncode}"
                log.error(
                    "Remux failed for %s (%s), keeping source",
                    src,
                    last,
                )
                if mp4_path.exists() and mp4_path.stat().st_size == 0:
                    mp4_path.unlink(missing_ok=True)
        except FileNotFoundError:
            log.error(
                "ffmpeg not found — install it to get QuickTime-compatible .mp4 files"
            )
        except subprocess.TimeoutExpired:
            log.error("Remux timed out for %s", src)
