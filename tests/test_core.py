"""Unit tests for config, Kick API, monitor wait, and recorder helpers."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.config import AppConfig, Settings, StreamerEntry, is_valid_slug
from src.kick_api import (
    MAX_ATTEMPTS,
    ChannelStatus,
    LiveState,
    get_channel_status,
)
from src.monitor import StreamMonitor
from src.recorder import (
    MIN_FREE_BYTES,
    Recorder,
    RecordingStartError,
    quality_to_format,
)


@pytest.mark.parametrize(
    "slug",
    ["xqc", "gmhikaru", "a", "user_1", "cool-name"],
)
def test_valid_slugs(slug: str) -> None:
    assert is_valid_slug(slug)


@pytest.mark.parametrize(
    "slug",
    ["", "../etc", "has space", "-leading", "BadCase", "a/b"],
)
def test_invalid_slugs(slug: str) -> None:
    assert not is_valid_slug(slug)


def test_load_ignores_unknown_settings_and_bad_slugs(tmp_path: Path) -> None:
    path = tmp_path / "streamers.json"
    path.write_text(
        json.dumps(
            {
                "settings": {
                    "poll_interval_seconds": 90,
                    "quality": "720p",
                    "unknown_key": True,
                },
                "streamers": [
                    {"slug": "ok", "enabled": True},
                    {"slug": "../bad"},
                ],
            }
        )
    )
    cfg = AppConfig.load(path)
    assert cfg.settings.poll_interval_seconds == 90
    assert cfg.settings.quality == "720p"
    assert [s.slug for s in cfg.streamers] == ["ok"]


def test_corrupt_json_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "streamers.json"
    path.write_text("{not json")
    cfg = AppConfig.load(path)
    assert cfg.settings.poll_interval_seconds == 60
    assert path.with_suffix(".json.bak").exists()


def test_set_enabled(tmp_path: Path) -> None:
    path = tmp_path / "streamers.json"
    cfg = AppConfig(
        settings=Settings(),
        streamers=[StreamerEntry(slug="xqc", enabled=True)],
    )
    cfg.save(path)
    with patch.object(cfg, "save", lambda: AppConfig.save(cfg, path)):
        assert cfg.set_enabled("xqc", False)
    reloaded = AppConfig.load(path)
    assert reloaded.get_enabled_slugs() == []
    assert not reloaded.streamers[0].enabled


def test_invalid_quality_falls_back_to_best(tmp_path: Path) -> None:
    path = tmp_path / "streamers.json"
    path.write_text(
        json.dumps({"settings": {"quality": "4k"}, "streamers": []})
    )
    cfg = AppConfig.load(path)
    assert cfg.settings.quality == "best"


def test_error_state_on_request_failure() -> None:
    with patch("src.kick_api.curl_requests.get", side_effect=Exception("boom")):
        with patch("src.kick_api.time.sleep"):
            status = get_channel_status("xqc")
    assert status.is_error
    assert not status.is_live
    assert not status.is_offline


def test_retry_then_success() -> None:
    fail = Exception("timeout")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "livestream": {
            "playback_url": "https://example/m3u8",
            "session_title": "Hello",
            "viewer_count": 42,
            "created_at": "2026-01-01",
        }
    }
    resp.raise_for_status = MagicMock()
    with patch(
        "src.kick_api.curl_requests.get", side_effect=[fail, resp]
    ) as mock_get:
        with patch("src.kick_api.time.sleep") as mock_sleep:
            status = get_channel_status("xqc")
    assert status.is_live
    assert status.title == "Hello"
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()


def test_retry_exhausted_returns_error() -> None:
    with patch(
        "src.kick_api.curl_requests.get", side_effect=Exception("boom")
    ) as mock_get:
        with patch("src.kick_api.time.sleep") as mock_sleep:
            status = get_channel_status("xqc")
    assert status.is_error
    assert mock_get.call_count == MAX_ATTEMPTS
    assert mock_sleep.call_count == MAX_ATTEMPTS - 1


def test_http_404_does_not_retry() -> None:
    resp = MagicMock()
    resp.status_code = 404
    with patch("src.kick_api.curl_requests.get", return_value=resp) as mock_get:
        with patch("src.kick_api.time.sleep") as mock_sleep:
            status = get_channel_status("missing")
    assert status.is_error
    assert "404" in (status.error or "")
    assert mock_get.call_count == 1
    mock_sleep.assert_not_called()


def test_offline_when_no_livestream() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"livestream": None}
    resp.raise_for_status = MagicMock()
    with patch("src.kick_api.curl_requests.get", return_value=resp):
        status = get_channel_status("xqc")
    assert status.is_offline
    assert not status.is_live


def test_live_parses_fields() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "livestream": {
            "playback_url": "https://example/m3u8",
            "session_title": "Hello",
            "viewer_count": 42,
            "created_at": "2026-01-01",
        }
    }
    resp.raise_for_status = MagicMock()
    with patch("src.kick_api.curl_requests.get", return_value=resp):
        status = get_channel_status("xqc")
    assert status.is_live
    assert status.title == "Hello"
    assert status.viewer_count == 42


def test_wait_uses_configured_interval() -> None:
    cfg = AppConfig(settings=Settings(poll_interval_seconds=60))
    monitor = StreamMonitor(cfg)
    for _ in range(20):
        wait = monitor._next_wait_seconds()
        assert 45 <= wait <= 90


def test_is_recording_read_only_on_empty(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}")
    assert rec.is_recording("nobody") is False
    assert rec.reap_finished() == []


def test_quality_to_format_mapping() -> None:
    assert quality_to_format("best") == "best"
    assert quality_to_format("1080p") == "best[height<=1080]/best"
    assert quality_to_format("720p") == "best[height<=720]/best"
    assert quality_to_format("480p") == "best[height<=480]/best"
    assert quality_to_format("worst") == "worst"
    assert quality_to_format("unknown") == "best"


def test_start_rejects_low_disk_space(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES - 1, total=10**12, used=10**12)
    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with pytest.raises(RecordingStartError, match="Insufficient disk space"):
            rec.start("xqc")
    assert not rec.is_recording("xqc")


def test_start_uses_youtubedl(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="720p")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    ydl = MagicMock()
    ydl.download.return_value = 0
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)

    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with patch("src.recorder.YoutubeDL", return_value=ydl) as ydl_cls:
            path = rec.start("xqc")
            # Allow worker thread to run
            import time as _time

            for _ in range(50):
                if ydl.download.called:
                    break
                _time.sleep(0.01)
            rec.stop("xqc")

    assert path.suffix == ".mp4"
    assert ydl_cls.called
    opts = ydl_cls.call_args.args[0]
    assert opts["format"] == "best[height<=720]/best"
    assert opts["nopart"] is True
    assert opts["live_from_start"] is False
    ydl.download.assert_called()
    assert ydl.download.call_args.args[0] == ["https://kick.com/xqc"]


def test_stop_cancels_download(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    started = threading.Event()

    def fake_download(urls):  # noqa: ARG001
        started.set()
        # Simulate progress hooks being invoked by YoutubeDL
        # The real hook is inside opts; just block until cancel
        info = rec.get_info("xqc")
        assert info is not None
        while not info.cancel.is_set():
            time.sleep(0.01)
        from yt_dlp.utils import DownloadCancelled

        raise DownloadCancelled("stopped by user")

    ydl = MagicMock()
    ydl.download.side_effect = fake_download
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)

    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with patch("src.recorder.YoutubeDL", return_value=ydl):
            rec.start("xqc")
            assert started.wait(timeout=2)
            assert rec.is_recording("xqc")
            result = rec.stop("xqc")

    assert result is not None
    assert not rec.is_recording("xqc")
    assert result.failed is False


def test_worker_exception_marks_failed(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    ydl = MagicMock()
    ydl.download.side_effect = RuntimeError("boom")
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)

    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with patch("src.recorder.YoutubeDL", return_value=ydl):
            rec.start("xqc")
            import time as _time

            for _ in range(50):
                info = rec.get_info("xqc")
                if info is not None and not info.is_alive():
                    break
                _time.sleep(0.01)
            finished = rec.reap_finished()

    assert len(finished) == 1
    assert finished[0].failed is True
    assert finished[0].exit_code == 1


def test_malformed_json_shapes_return_error() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()

    resp.json.return_value = ["not", "an", "object"]
    with patch("src.kick_api.curl_requests.get", return_value=resp):
        status = get_channel_status("xqc")
    assert status.is_error
    assert "JSON object" in (status.error or "")


@pytest.mark.parametrize("livestream", [["bad"], [], "", 0, False])
def test_malformed_livestream_returns_error(livestream: object) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"livestream": livestream}
    with patch("src.kick_api.curl_requests.get", return_value=resp):
        status = get_channel_status("xqc")
    assert status.is_error
    assert "livestream" in (status.error or "")


def test_null_livestream_is_offline() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"livestream": None}
    with patch("src.kick_api.curl_requests.get", return_value=resp):
        status = get_channel_status("xqc")
    assert status.is_offline


def test_start_wraps_setup_errors(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{missing}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with pytest.raises(RecordingStartError, match="Cannot start recording"):
            rec.start("xqc")


def test_remux_failure_marks_failed(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    ydl = MagicMock()
    ydl.download.return_value = 0
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)

    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with patch("src.recorder.YoutubeDL", return_value=ydl):
            path = rec.start("xqc")
            for _ in range(50):
                info = rec.get_info("xqc")
                if info is not None and not info.is_alive():
                    break
                time.sleep(0.01)
            # Simulate downloaded media that needs remux
            info = rec.get_info("xqc")
            assert info is not None
            ts = path.with_suffix(".ts")
            ts.write_bytes(b"fake-ts-data")
            info.ts_path = ts
            with patch(
                "src.recorder.subprocess.run",
                return_value=MagicMock(returncode=1, stderr="ffmpeg boom"),
            ):
                finished = rec.reap_finished()

    assert len(finished) == 1
    assert finished[0].failed is True
    assert "Remux failed" in finished[0].output
    assert ts.exists()


def test_stop_defers_finalize_if_worker_alive(tmp_path: Path) -> None:
    rec = Recorder(str(tmp_path), "{channel}_{date}_{time}", quality="best")
    usage = MagicMock(free=MIN_FREE_BYTES + 1, total=10**12, used=0)
    release = threading.Event()

    def fake_download(urls):  # noqa: ARG001
        release.wait(timeout=5)
        return 0

    ydl = MagicMock()
    ydl.download.side_effect = fake_download
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)

    with patch("src.recorder.shutil.disk_usage", return_value=usage):
        with patch("src.recorder.YoutubeDL", return_value=ydl):
            with patch.object(threading.Thread, "join", lambda self, timeout=None: None):
                rec.start("xqc")
                time.sleep(0.05)
                result = rec.stop("xqc")

            assert result is None
            assert rec.is_recording("xqc")
            release.set()
            # Let worker finish, then reap
            for _ in range(50):
                info = rec.get_info("xqc")
                if info is not None and not info.is_alive():
                    break
                time.sleep(0.01)
            finished = rec.reap_finished()

    assert len(finished) == 1
    assert finished[0].slug == "xqc"
    assert not rec.is_recording("xqc")


def test_channel_status_properties() -> None:
    err = ChannelStatus(slug="x", state=LiveState.ERROR, error="e")
    assert err.is_error
    off = ChannelStatus(slug="x", state=LiveState.OFFLINE)
    assert off.is_offline
    live = ChannelStatus(slug="x", state=LiveState.LIVE)
    assert live.is_live
