# Kick Stream Recorder

A Python desktop application that monitors your favorite Kick.com streamers and automatically records their live streams for watching later.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux-lightgrey)

## Features

- **Streamer watchlist** — Build and manage a list of Kick.com streamers to monitor
- **Auto-start** — Monitoring begins as soon as you launch the app — no button clicks needed
- **Automatic recording** — Streams are recorded automatically when a streamer goes live
- **Manual recording** — Start recording a live streamer on demand with the **Record** button
- **Multi-stream support** — Record multiple streamers simultaneously, each on its own worker thread
- **Auto-stop** — Recording ends when the stream goes offline or the streamer raids another channel
- **QuickTime-compatible MP4** — Recordings are remuxed from `.ts` to `.mp4` with `faststart` for native playback on macOS
- **Live status display** — See which streamers are live, their stream title, viewer count, and recording duration in real time
- **Instant live check** — Adding a streamer immediately checks if they're live and shows the Record button
- **Randomized polling** — Poll interval is configurable; each wait is randomized around that value to avoid detection
- **Per-streamer enable** — Toggle monitoring on/off for individual channels without removing them
- **Quality selector** — Prefer best / 1080p / 720p / 480p / worst when recording
- **Configurable settings** — Adjustable poll interval, quality, and output directory
- **Open recordings folder** — Jump to the output directory from Settings
- **Persistent watchlist** — Your streamer list and settings are saved between sessions
- **Activity log** — Timestamped log of all events (polls, live detection, recording start/stop, errors)

## Screenshots

The GUI features a dark-themed interface with:
- An input bar to add streamers by their Kick channel name
- A streamer table showing live/offline status and recording state
- Settings for poll interval, quality, and recording output directory
- A scrollable activity log

## Requirements

- **Python 3.10+**
- **ffmpeg** — Used by yt-dlp for muxing recorded streams
- **tkinter** — Python GUI toolkit (system package)

### Installing system dependencies

**macOS (Homebrew):**
```bash
brew install ffmpeg python-tk@X.Y
```
> Match `X.Y` to your Homebrew Python version (e.g. `python-tk@3.12` for Python 3.12).

**Ubuntu/Debian:**
```bash
sudo apt install python3-tk ffmpeg
```

**Fedora:**
```bash
sudo dnf install python3-tkinter ffmpeg
```

**Arch Linux:**
```bash
sudo pacman -S tk ffmpeg
```

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/mglass222/Kick-downloader.git
   cd Kick-downloader
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

## Usage

1. **Activate the virtual environment and launch:**
   ```bash
   cd Kick-downloader
   source .venv/bin/activate
   python -m src.main
   ```

2. **Add streamers** — Type a Kick channel slug (e.g. `xqc`, `gmhikaru`) into the input field and click **Add**. The app immediately checks if the streamer is live.

3. **Automatic monitoring** — Monitoring starts automatically on launch. Use the **Stop Monitoring** / **Start Monitoring** toggle to pause. Uncheck a streamer to skip them without removing them.

4. **Automatic recording** — When an enabled streamer goes live, recording starts automatically. The streamer's row will show a red `REC` indicator with elapsed time.

5. **Manual record/stop** — If a streamer is live but not being recorded, click the **Record** button to start. Click **Stop** to end a recording early.

6. **Settings** — Adjust the poll interval, quality, and output directory in the Settings panel. Click **Browse** to select a folder, or **Open Folder** to reveal recordings in Finder/Files.

7. **Closing** — When you close the window, all active recordings are gracefully stopped and finalized before the app exits.

## Configuration

Settings and your streamer list are stored in `streamers.json` (created automatically on first run):

```json
{
  "settings": {
    "poll_interval_seconds": 60,
    "output_dir": "./recordings",
    "filename_template": "{channel}_{date}_{time}",
    "quality": "best"
  },
  "streamers": [
    { "slug": "xqc", "enabled": true },
    { "slug": "gmhikaru", "enabled": true }
  ]
}
```

## Recordings

- **Format:** MP4
- **Quality:** Configurable (`best`, `1080p`, `720p`, `480p`, `worst`)
- **Filename pattern:** `{channel}_{YYYY-MM-DD}_{HH-MM-SS}.mp4`
- **Default location:** `./recordings/`
- **Disk space:** Recording will not start if free space is under 1 GiB

## Project Structure

```
Kick-downloader/
├── requirements.txt          # Python dependencies (incl. pytest, ruff)
├── pyproject.toml            # Pytest / ruff config
├── LICENSE                   # MIT
├── .github/workflows/ci.yml  # CI: ruff + pytest
├── streamers.json            # Streamer list & settings (created at runtime)
├── tests/
│   └── test_core.py          # Unit tests (config, API status, recorder)
├── src/
│   ├── main.py               # Entry point
│   ├── config.py             # Settings and streamer list persistence
│   ├── kick_api.py           # Kick.com API client (live detection)
│   ├── monitor.py            # Background polling loop
│   ├── recorder.py           # yt-dlp Python API recording workers
│   └── gui/
│       ├── app.py            # Main application window
│       ├── streamer_list.py  # Streamer table widget
│       ├── add_streamer.py   # Add streamer input bar
│       ├── settings_panel.py # Settings controls
│       └── log_panel.py      # Activity log panel
└── recordings/               # Recorded streams (created at runtime)
```

## Tests

```bash
source .venv/bin/activate
pytest -q
ruff check src tests
```

## How It Works

1. **Polling** — A background thread queries `https://kick.com/api/v2/channels/{slug}` for each enabled streamer. Wait time is randomized around your configured poll interval. Requests use `curl_cffi` to impersonate a Chrome browser TLS fingerprint, which is necessary to avoid Kick's bot detection (403 responses). Transient API errors are retried with short exponential backoff and treated as unknown (not offline) so active recordings are not stopped. Confirmed offline requires two consecutive offline polls before a recording is stopped.

2. **Recording** — When a channel's `livestream` field is non-null, the app starts a daemon worker thread that embeds the yt-dlp Python API (`YoutubeDL`) against `https://kick.com/{slug}`. The stream is written to disk (typically `.ts`); stopping cooperatively cancels via a progress hook.

3. **Remux** — When a recording ends (stream goes offline, manual stop, or app close), the file is remuxed to a QuickTime-compatible `.mp4` using `ffmpeg -c copy -movflags +faststart`. The original source is deleted after a successful remux.

4. **Stop detection** — yt-dlp exits when the live playlist ends; the app can also cancel mid-download. The polling loop detects confirmed offline status via the API (two consecutive offline polls) as a secondary check. Both mechanisms handle raids.

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'tkinter'` | macOS: `brew install python-tk@X.Y` (match `X.Y` to your Homebrew Python version). Linux: `sudo apt install python3-tk` |
| `403 Forbidden` from Kick API | Ensure `curl_cffi` is installed via `pip install -r requirements.txt`. Plain HTTP clients are blocked by Kick's bot detection. |
| Timeouts when polling | Kick's API can be slow. The default 30-second timeout handles most cases. Check your network connection. |
| `yt-dlp` / import errors | Ensure the venv has yt-dlp: `pip install -r requirements.txt` (the app embeds the Python API; a separate CLI binary on PATH is not required). |
| Recording file is 0 bytes | The stream may have ended before data was captured. Check that ffmpeg is installed. |
| Insufficient disk space | Free at least 1 GiB on the output drive, or change the output directory. |
| MP4 not compatible with QuickTime | Ensure ffmpeg is installed. The remux step requires it to produce a valid `.mp4` container. |

## License

This project is open source and available under the [MIT License](LICENSE).
