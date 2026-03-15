"""Settings and streamer list persistence via streamers.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "streamers.json"


@dataclass
class StreamerEntry:
    slug: str
    enabled: bool = True


@dataclass
class Settings:
    poll_interval_seconds: int = 60
    output_dir: str = "./recordings"
    filename_template: str = "{channel}_{date}_{time}"


@dataclass
class AppConfig:
    settings: Settings = field(default_factory=Settings)
    streamers: list[StreamerEntry] = field(default_factory=list)

    # ── Persistence ──────────────────────────────────────────

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        data = {
            "settings": asdict(self.settings),
            "streamers": [asdict(s) for s in self.streamers],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        raw = json.loads(path.read_text())
        settings = Settings(**raw.get("settings", {}))
        streamers = [StreamerEntry(**s) for s in raw.get("streamers", [])]
        return cls(settings=settings, streamers=streamers)

    # ── Streamer list helpers ────────────────────────────────

    def add_streamer(self, slug: str) -> bool:
        """Add a streamer. Returns False if already in list."""
        slug = slug.strip().lower()
        if not slug:
            return False
        if any(s.slug == slug for s in self.streamers):
            return False
        self.streamers.append(StreamerEntry(slug=slug))
        self.save()
        return True

    def remove_streamer(self, slug: str) -> bool:
        """Remove a streamer. Returns False if not found."""
        before = len(self.streamers)
        self.streamers = [s for s in self.streamers if s.slug != slug]
        if len(self.streamers) < before:
            self.save()
            return True
        return False

    def get_enabled_slugs(self) -> list[str]:
        return [s.slug for s in self.streamers if s.enabled]
