"""Unit tests for config, Kick API status model, and monitor wait logic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow `python -m unittest` from repo root
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import AppConfig, Settings, is_valid_slug  # noqa: E402
from src.kick_api import ChannelStatus, LiveState, get_channel_status  # noqa: E402
from src.monitor import StreamMonitor  # noqa: E402
from src.recorder import Recorder  # noqa: E402


class SlugValidationTests(unittest.TestCase):
    def test_valid_slugs(self) -> None:
        for slug in ("xqc", "gmhikaru", "a", "user_1", "cool-name"):
            self.assertTrue(is_valid_slug(slug), slug)

    def test_invalid_slugs(self) -> None:
        for slug in ("", "../etc", "has space", "-leading", "BadCase", "a/b"):
            self.assertFalse(is_valid_slug(slug), slug)


class ConfigTests(unittest.TestCase):
    def test_load_ignores_unknown_settings_and_bad_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "streamers.json"
            path.write_text(
                json.dumps(
                    {
                        "settings": {
                            "poll_interval_seconds": 90,
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
            self.assertEqual(cfg.settings.poll_interval_seconds, 90)
            self.assertEqual([s.slug for s in cfg.streamers], ["ok"])

    def test_corrupt_json_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "streamers.json"
            path.write_text("{not json")
            cfg = AppConfig.load(path)
            self.assertEqual(cfg.settings.poll_interval_seconds, 60)
            self.assertTrue(path.with_suffix(".json.bak").exists())

    def test_set_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "streamers.json"
            from src.config import StreamerEntry
            import src.config as config_mod

            with patch.object(config_mod, "DEFAULT_CONFIG_PATH", path):
                cfg = AppConfig(
                    settings=Settings(),
                    streamers=[StreamerEntry(slug="xqc", enabled=True)],
                )
                cfg.save()
                self.assertTrue(cfg.set_enabled("xqc", False))
                reloaded = AppConfig.load()
                self.assertEqual(reloaded.get_enabled_slugs(), [])
                self.assertFalse(reloaded.streamers[0].enabled)


class KickApiTests(unittest.TestCase):
    def test_error_state_on_request_failure(self) -> None:
        with patch("src.kick_api.curl_requests.get", side_effect=Exception("boom")):
            # RequestsError path uses curl_requests.RequestsError; generic Exception
            # is also caught.
            status = get_channel_status("xqc")
            self.assertTrue(status.is_error)
            self.assertFalse(status.is_live)
            self.assertFalse(status.is_offline)

    def test_offline_when_no_livestream(self) -> None:
        resp = MagicMock()
        resp.json.return_value = {"livestream": None}
        resp.raise_for_status = MagicMock()
        with patch("src.kick_api.curl_requests.get", return_value=resp):
            status = get_channel_status("xqc")
            self.assertTrue(status.is_offline)
            self.assertFalse(status.is_live)

    def test_live_parses_fields(self) -> None:
        resp = MagicMock()
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
            self.assertTrue(status.is_live)
            self.assertEqual(status.title, "Hello")
            self.assertEqual(status.viewer_count, 42)


class MonitorWaitTests(unittest.TestCase):
    def test_wait_uses_configured_interval(self) -> None:
        cfg = AppConfig(settings=Settings(poll_interval_seconds=60))
        monitor = StreamMonitor(cfg)
        for _ in range(20):
            wait = monitor._next_wait_seconds()
            self.assertGreaterEqual(wait, 45)
            self.assertLessEqual(wait, 90)


class RecorderTests(unittest.TestCase):
    def test_is_recording_read_only_on_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(tmp, "{channel}_{date}_{time}")
            self.assertFalse(rec.is_recording("nobody"))
            self.assertEqual(rec.reap_finished(), [])


class ChannelStatusHelpers(unittest.TestCase):
    def test_properties(self) -> None:
        err = ChannelStatus(slug="x", state=LiveState.ERROR, error="e")
        self.assertTrue(err.is_error)
        off = ChannelStatus(slug="x", state=LiveState.OFFLINE)
        self.assertTrue(off.is_offline)
        live = ChannelStatus(slug="x", state=LiveState.LIVE)
        self.assertTrue(live.is_live)


if __name__ == "__main__":
    unittest.main()
