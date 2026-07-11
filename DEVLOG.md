# Kick Stream Recorder — Development Log

> Historical notes from initial development (March 2026). Behavior described below may be outdated — see `README.md` for current docs.

**Date:** March 13, 2026

## Project Overview

Built a Python GUI application that monitors Kick.com streamers and automatically records their live streams. The user manages a list of streamers to watch, and the program polls each channel every 5 minutes. When a streamer goes live, recording begins automatically via yt-dlp and stops when the stream ends (including raids).

## What Was Built

### Backend Modules (`src/`)

- **`config.py`** — `AppConfig` dataclass with JSON persistence (`streamers.json`). Handles saving/loading the streamer list and settings (poll interval, output directory, filename template) between sessions.

- **`kick_api.py`** — Kick.com API client that queries `/api/v2/channels/{slug}` to check if a streamer is live. Uses `curl_cffi` with Chrome TLS fingerprint impersonation to bypass Kick's bot detection (plain `httpx` requests get 403'd). Returns a `ChannelStatus` dataclass with live state, playback URL, title, viewer count, and start time.

- **`recorder.py`** — Manages one yt-dlp subprocess per streamer. Supports simultaneous recordings. Starts recording with `--live-from-start` and `--no-part` flags. Gracefully stops via SIGINT so yt-dlp can finalize the output file. Tracks recording start time for elapsed duration display.

- **`monitor.py`** — Background polling loop running in a daemon thread. Maintains per-streamer state machines (IDLE/RECORDING). Each poll cycle checks all enabled streamers with a 1.5-second stagger between API requests. Reports channel status (live/offline) and next check countdown to the GUI log. Communicates with the GUI via thread-safe `tkinter.after()` callbacks.

### GUI Modules (`src/gui/`)

- **`app.py`** — Main application window built with CustomTkinter (dark mode). Wires together all panels and the backend monitor. Handles add/remove streamers, start/stop monitoring, settings changes, and graceful shutdown (stops all recordings on window close). Updates recording elapsed timers every second.

- **`streamer_list.py`** — Scrollable table of streamer rows. Each row shows: channel name, live/offline status indicator, recording state with elapsed time, a Stop button (visible only while recording), and a Remove button.

- **`add_streamer.py`** — Input bar with a text field and Add button. Accepts Kick channel slugs. Supports Enter key submission.

- **`settings_panel.py`** — Controls for poll interval (seconds) and output directory with a Browse folder dialog. Minimum poll interval enforced at 10 seconds. Changes are saved immediately to `streamers.json`.

- **`log_panel.py`** — Scrollable text area showing timestamped log entries (poll events, status changes, recording start/stop, errors).

### Entry Point

- **`src/main.py`** — Sets up logging and launches the GUI. Run via `python -m src.main` from the project root with the venv activated.

## Technical Decisions

| Decision | Reasoning |
|---|---|
| **`curl_cffi` over `httpx`** | Kick.com returns 403 for standard HTTP clients. `curl_cffi` impersonates Chrome's TLS fingerprint to bypass bot detection. |
| **yt-dlp as subprocess** | Stable CLI interface, crash isolation from the monitor, clean signal-based termination, built-in Kick extractor. |
| **Background thread (not asyncio)** | Simpler integration with tkinter's main loop. Thread communicates via `root.after()` callbacks. |
| **CustomTkinter** | Modern-looking UI with minimal dependencies. No need for heavy frameworks like PyQt. |
| **JSON file for persistence** | Simple, human-readable, no database dependency. Suitable for a small streamer list. |
| **30-second API timeout** | Kick's API can be slow to respond; the original 15-second timeout caused frequent timeouts. |

## Dependencies

- `yt-dlp` — Stream recording with native Kick.com support
- `curl_cffi` — Browser TLS fingerprint impersonation for API requests
- `httpx` — Installed but replaced by curl_cffi for Kick API calls
- `customtkinter` — Modern tkinter GUI wrapper
- **System:** Python 3.12, ffmpeg, tkinter (`sudo apt install python3-tk`)

## How to Run

```bash
cd /home/mglass222/Documents/Code/Kick-downloader
source .venv/bin/activate
python -m src.main
```

## Recording Defaults

- **Quality:** Best available (yt-dlp default)
- **Format:** MP4
- **Filename:** `{channel}_{YYYY-MM-DD}_{HH-MM-SS}.mp4`
- **Output directory:** `./recordings/` (configurable in settings)
- **Poll interval:** 300 seconds / 5 minutes (configurable, minimum 10 seconds)
