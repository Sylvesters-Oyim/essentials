# Changelog

Versions follow `MAJOR.MINOR.PATCH`: a new feature bumps MINOR, a bug fix bumps
PATCH, and a change that breaks existing use (such as an old `sessions.csv` no
longer loading) bumps MAJOR. Each release is tagged in git as
`pomodoro-vX.Y.Z`.

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
