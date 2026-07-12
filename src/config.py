"""Settings and streamer list persistence via ``streamers.json``.

The config file lives at the project root by default and stores both
recording settings and the watchlist. Invalid or corrupt files fall back
to defaults rather than crashing the app.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "streamers.json"

# Kick channel slugs are lowercase alphanumeric with underscores/hyphens
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class StreamerEntry:
    """One channel on the watchlist.

    Attributes:
        slug: Kick channel name (lowercase).
        enabled: When False, the monitor skips this channel.
    """

    slug: str
    enabled: bool = True


@dataclass
class Settings:
    """User-configurable recording and polling options.

    Attributes:
        poll_interval_seconds: Base seconds between poll cycles (randomized
            around this value by the monitor; minimum 10).
        output_dir: Directory where per-channel recording folders are created.
        filename_template: Format string with ``{channel}``, ``{date}``,
            and ``{time}`` placeholders.
    """

    poll_interval_seconds: int = 60
    output_dir: str = "./recordings"
    filename_template: str = "{channel}_{date}_{time}"


@dataclass
class AppConfig:
    """In-memory app configuration with JSON load/save helpers."""

    settings: Settings = field(default_factory=Settings)
    streamers: list[StreamerEntry] = field(default_factory=list)

    # ── Persistence ──────────────────────────────────────────

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        """Write settings and streamers to ``path`` as indented JSON."""
        data = {
            "settings": asdict(self.settings),
            "streamers": [asdict(s) for s in self.streamers],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        """Load config from ``path``, creating defaults if missing.

        Corrupt JSON is moved to ``*.json.bak`` and replaced with defaults.
        Unknown settings keys and invalid streamer slugs are skipped.
        """
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Corrupt config %s (%s) — using defaults", path, exc)
            backup = path.with_suffix(".json.bak")
            try:
                path.replace(backup)
                log.warning("Backed up corrupt config to %s", backup)
            except OSError:
                pass
            cfg = cls()
            cfg.save(path)
            return cfg

        if not isinstance(raw, dict):
            log.warning("Invalid config root in %s — using defaults", path)
            return cls()

        settings = _settings_from_dict(raw.get("settings") or {})
        streamers: list[StreamerEntry] = []
        for item in raw.get("streamers") or []:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug", "")).strip().lower()
            if not is_valid_slug(slug):
                log.warning("Skipping invalid slug in config: %r", slug)
                continue
            streamers.append(
                StreamerEntry(slug=slug, enabled=bool(item.get("enabled", True)))
            )
        return cls(settings=settings, streamers=streamers)

    # ── Streamer list helpers ────────────────────────────────

    def add_streamer(self, slug: str) -> bool:
        """Add a streamer and persist. Returns False if invalid or duplicate."""
        slug = slug.strip().lower()
        if not is_valid_slug(slug):
            return False
        if any(s.slug == slug for s in self.streamers):
            return False
        self.streamers.append(StreamerEntry(slug=slug))
        self.save()
        return True

    def remove_streamer(self, slug: str) -> bool:
        """Remove a streamer and persist. Returns False if not found."""
        before = len(self.streamers)
        self.streamers = [s for s in self.streamers if s.slug != slug]
        if len(self.streamers) < before:
            self.save()
            return True
        return False

    def set_enabled(self, slug: str, enabled: bool) -> bool:
        """Enable or disable monitoring for a streamer. Returns False if missing."""
        for entry in self.streamers:
            if entry.slug == slug:
                if entry.enabled != enabled:
                    entry.enabled = enabled
                    self.save()
                return True
        return False

    def get_enabled_slugs(self) -> list[str]:
        """Return slugs that should be polled by the monitor."""
        return [s.slug for s in self.streamers if s.enabled]


def is_valid_slug(slug: str) -> bool:
    """Return True if ``slug`` matches Kick-safe channel name rules."""
    return bool(slug) and _SLUG_RE.fullmatch(slug) is not None


def _settings_from_dict(raw: dict) -> Settings:
    """Build :class:`Settings` from a dict, ignoring unknown keys."""
    allowed = {f.name for f in fields(Settings)}
    cleaned = {k: v for k, v in raw.items() if k in allowed}
    # Coerce poll_interval_seconds to int if present
    if "poll_interval_seconds" in cleaned:
        try:
            cleaned["poll_interval_seconds"] = int(cleaned["poll_interval_seconds"])
        except (ValueError, TypeError):
            cleaned["poll_interval_seconds"] = 60
    try:
        settings = Settings(**cleaned)
    except TypeError:
        return Settings()
    if settings.poll_interval_seconds < 10:
        settings.poll_interval_seconds = 10
    return settings
