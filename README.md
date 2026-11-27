# BentoFocus

Personal local productivity hub for Linux (X11): distraction-blocking focus blocks, guilt-free habit tracking, simple personal finances, daily Markdown/JSON summaries, and an optional Telegram AI companion.

> [!NOTE]
> **Built 100% with AI-assisted coding:** BentoFocus is a personal daily-driver tool created, guided, and iterated on entirely through AI-assisted coding rather than hand-written as a traditional software engineering showcase. Everything here is tuned around my own daily workflow on Linux X11.

## Features

- **Focus blocks & distraction blocking**: Pomodoro sessions with an intention. During an active block:
  - Distracting browser tabs (YouTube, Instagram, X/Twitter, Twitch, TikTok, Reddit, Discord web, WhatsApp web) are automatically closed (`Ctrl+W` via `xdotool`). An optional **Allow YouTube** toggle keeps YouTube open for lectures.
  - Desktop distractions and games (Discord, WhatsApp, Steam, Lutris, Heroic, Minecraft, RetroArch, osu!) are minimized whenever focused.
  - GNOME notification banners (`org.gnome.desktop.notifications show-banners`) are silenced and restored when the block ends.
- **Time & idle tracking**: Focus block time counts in full without deductions (idle at the desk = studying on paper). Outside blocks, the **"On PC today"** card splits screen time into active, distraction, cheap distraction, and idle (default: 3 min without input).
  - Idle detection reads raw XInput2 keyboard/mouse events (`xinput test-xi2 --root`), falling back to GNOME `IdleMonitor` / `xprintidle`. This avoids false activity from games calling `XResetScreenSaver` every ~30s.
  - When a distraction window is on screen with no input, the collector checks browser MPRIS playback via D-Bus: playing = watching, paused = idle. Gamepad input is invisible to X11 and counts as idle.
- **Cheap vs. regular distractions**: Instagram, X/Twitter, TikTok, Reddit, and YouTube home/Shorts (or YouTube videos totaling under 90s in the day) are tracked separately from long-form videos, games, and chat in both the daily card and the 14-day chart.
- **Sequences & flexible adjustments**: Chain N equal blocks (e.g., `2 × 45m`) with proportional breaks (`25m → 5m`, `35m → 10m`, `45m+ → 15m`); subsequent blocks start automatically, and stopping early ends the sequence. Use `+5 min` to extend a running block or resume during a break, and **Manual focus logging** (`+ foco sem timer`) to record offline study blocks (validated against overlaps and deletable).
- **Miniplayer**: Always-on-top GTK/WebKit overlay showing the countdown timer, Spotify playback controls via MPRIS/D-Bus (cover art, track info, ⏮ ⏯ ⏭, scroll-wheel volume), and switchable animated GIF scenes (with pause/play and `prefers-reduced-motion` support; the equalizer runs at 20 fps in JS for WebKit efficiency). Companion GIF/PNG pairs live locally in `bentofocus/static/companions/` (excluded from the repo for licensing and size reasons). Opens automatically when a block starts, closes when idle, and persists size and position.
- **Spotify integration (optional)**: Launches Spotify (`flatpak run com.spotify.Client` by default) and resumes playback on block start, or starts a fixed playlist via `SPOTIFY_FOCUS_URI` in [`bentofocus/config.py`](bentofocus/config.py).
- **Guilt-free habits**: Daily reading, weekly gym sessions (goal: 3), and university check-ins — tracked with heatmaps and "active days in the last 30" instead of fragile streaks.
- **Simple finances**: Monthly income, fixed monthly expenses, and variable transactions → free balance and daily allowance.
- **Daily summary & export**: In-app daily summary modal and `/api/export/daily` endpoint supporting `format=json` and `format=markdown`.
- **Telegram AI Agent (archived)**: Standalone Telegram + Gemini tool-calling companion kept in [`agent/`](agent/README.md) as a reference implementation (no longer actively deployed or synced with the desktop app).

## Installation

### Prerequisites

- Linux running an **X11** session
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- System tools: `xdotool`, `xprop`, `xinput`, `notify-send`
- System Python bindings for the native window wrapper: GTK 3 and WebKit2GTK 4.1 (`python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-webkit2-4.1` on Debian/Ubuntu)
- Optional: `gdbus` / `busctl` / `gsettings` / `pw-play` (GNOME notifications, MPRIS media detection, Spotify control, completion sounds)

### Setup

```bash
./scripts/install.sh
```

This syncs dependencies with `uv`, installs a user-level systemd service (`bentofocus.service`) that starts on graphical login and restarts on failure, adds a **BentoFocus** desktop launcher, and enables session autostart (`~/.config/autostart/bentofocus.desktop` — remove that file if you prefer launching manually). The systemd service runs the collector and blocker; closing the GUI windows does not stop tracking or active blocks.

```bash
systemctl --user status bentofocus      # check service status
journalctl --user -u bentofocus -f      # follow live logs
systemctl --user restart bentofocus     # restart after code changes
```

To bind a global keyboard shortcut in your desktop environment, point it to:

```bash
/usr/bin/python3 /path/to/BentoFocus/bentofocus/window.py
```

## Configuration & Development

All core settings live in [`bentofocus/config.py`](bentofocus/config.py): distraction matching rules (window title or `WM_CLASS` regexes; Slack and Spotify are intentionally excluded), browser classes, idle threshold, block durations, weekly habit goals, and `DAY_START_HOUR`.

Inspect what the window collector sees in real time without recording or blocking anything:

```bash
uv run python scripts/probe_window.py 20
```

Run an isolated dry-run instance (separate database, no tab closing, no GNOME notification changes):

```bash
BENTOFOCUS_DATA=/tmp/bf BENTOFOCUS_DRY_RUN=1 BENTOFOCUS_PORT=7788 uv run python -m bentofocus
```

Run the unit test suite:

```bash
uv run python -m unittest discover -s tests -v
```

## How It Works

```text
systemd --user (bentofocus.service)
└── FastAPI on 127.0.0.1:7777
    ├── collector (asyncio): xdotool/xprop every 3s (1s during focus) + XInput2 / IdleMonitor
    │   ├── records continuous window intervals (WindowSpan)
    │   └── during focus blocks: closes distracting browser tabs / minimizes apps
    ├── SQLite (~/.local/share/bentofocus/bentofocus.db)
    └── REST API + offline static UI (Alpine.js + local fonts)

bentofocus/window.py   GTK3/WebKit2 wrapper (--mini launches the floating miniplayer)
agent/                 Archived Telegram bot + Gemini agent + morning digest reference
```

## Limitations

- **X11 only**: Wayland does not provide reliable cross-window active title inspection and tab-closing hooks.
- **Active-tab detection**: Browser blocking inspects the focused window title, so background tabs (e.g., YouTube playing in another tab) are not detected.
- **Language**: The UI labels, exported Markdown summaries, and Telegram agent prompts are currently written in Brazilian Portuguese (`pt-BR`).
- **Friction, not a lockbox**: Since you configure the rules yourself, you can also bypass them — the goal is intentional friction.
