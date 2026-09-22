# Changelog

Versions follow `MAJOR.MINOR.PATCH`: a new feature bumps MINOR, a bug fix bumps
PATCH, and a change that breaks existing use (such as an old `sessions.csv` no
longer loading) bumps MAJOR. Each release is tagged in git as
`pomodoro-vX.Y.Z`.

## 1.2.0 — 2026-09-22

### Added
- Chimes on Linux and macOS. There is no stdlib tone generator outside
  Windows, so the pattern is rendered to a temporary WAV and played with
  whichever of `paplay`, `aplay`, `afplay` or `play` is installed. Silent, as
  before, when none is.

### Fixed
- The break blackout no longer assumes 1920x1080 off Windows. `ctypes.windll`
  does not exist there, so the old fallback left any taller or wider screen
  partly uncovered — you could keep working through a break. Tk now reports
  the real geometry, using the virtual root so multi-monitor still works.

## 1.1.0 — 2026-09-13

### Added
- Todo list (`Ctrl+T`). Pick a task to make it the working-on tag; ticking off
  the current task moves to the next one. Unfinished tasks persist in
  `todos.json`, and each shows the time already logged against it.
- Open todos in the recent menu and on the break screen.
- `--version` flag.

## 1.0.0 — 2026-09-10

First release: strict focus timer with screen-blackout breaks, hold-to-skip,
pause, restart, adjustable durations with presets, per-task tagging and
history, all logged to a local `sessions.csv`.
