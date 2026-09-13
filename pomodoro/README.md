# Strict Pomodoro

A single-file focus timer for Windows. 25 minutes of work, 5 minutes off, and a
15 minute break after every 4th block. No dependencies beyond the Python
standard library.

## Run it

Double-click `Pomodoro.bat`, or from a terminal:

```
python pomodoro.py
```

## What makes it strict

- **Phases auto-advance.** When focus ends the break starts by itself, and when
  the break ends focus starts again. There is nothing to click, so there is
  nothing to negotiate.
- **Pause is available but accounted for.** See below.
- **Breaks take the screen.** A fullscreen black overlay covers every monitor
  and stays on top, so you cannot quietly keep working through your break.
- **Skipping costs three seconds.** "Hold to skip" and "Hold to stop" only fire
  after a continuous 3-second press. Enough friction to make it a decision.
- **Everything is logged.** Each phase is appended to `sessions.csv`
  (date, start, end, kind, planned minutes, actual minutes, completed, label,
  paused minutes).
  The main window shows today's running total, so the day is always on the record.

The timer window is small, pinned on top, and parks itself bottom-right during
focus. Click "pinned on top" in the corner to unpin it.

## Pausing

Both focus blocks and breaks can be paused, with the **Pause** button or
`Ctrl+P`. The design principle is *available but accounted for*: an
unconditional pause is what makes most pomodoro apps useless, so the cost is
not friction, it is the record.

- **Pausing is one click**, deliberately. An interruption — the phone, someone
  at the door — is not the moment to make you hold a button for three seconds.
- **The clock genuinely freezes.** The phase owes you the time back: pause a
  25 minute block at 10:00 remaining and you still get 10:00 when you resume.
- **Paused time is logged** in its own `paused_min` column, separate from
  `actual_min`. Work time never includes time you were away, so the history
  stays honest in both directions.
- **Today's paused total** appears next to your focus total in the window, and
  per period in the history view. You can stop the clock; you cannot pretend
  you didn't.
- **Pausing a break releases the screen.** The blackout overlay closes so you
  can actually use the computer, and the break clock stops where it was.
  Resuming brings the overlay back. The escape hatch is real, but it costs you
  progress toward the next focus block rather than letting you skip the break.
- **After 15 minutes paused** you get a quiet double beep, because a pause you
  forgot about is the real failure mode. Change `PAUSE_NAG_MIN` to taste.

Skipping and stopping still cost a three second hold. That friction is aimed at
abandoning work, not at handling an interruption.

## Restarting

**Hold to restart** runs the current phase again from its full length — for
when a block got derailed and you want a clean 25 minutes rather than limping
through the remaining 12.

- **The time you already spent is kept.** It is logged as an unfinished phase
  before the restart, so it still counts towards the hours you worked; it just
  does not count as a completed pomodoro.
- **Your place in the cycle is unchanged.** Restarting your third block leaves
  you on your third block, so the long break still arrives when it should.
- **It clears a pause.** Restarting a paused phase gives you the full length,
  running.
- **It is a hold, not a click**, because it throws away progress: a stray click
  twenty minutes into a block would cost you the pomodoro. That is protection
  against accidents, not friction for its own sake.

During a break the controls live in the main window, which the blackout hides;
pause the break first if you want to restart it.

## Tracking what you were working on

The **working on** box at the top of the window is written to every logged row.
That tag is the whole basis of the history: no tag, no idea where the hours went.

- It is **remembered between launches** — the app reloads whatever you last
  tagged, so restarting mid-project costs you nothing.
- **recent ▾** pops a menu of tags you have actually used lately, so you are not
  retyping "star-allele calling" every morning.
- The tag is **editable during breaks too**. The box appears on the break
  overlay, and whatever it says when the break ends is what the next block logs.
- After a **long break** the overlay asks "What will you work on next?" outright
  and puts the cursor in the box, because a long break is where the work
  actually changes. Short breaks just show the tag quietly carrying on.

Click **history** for where the time went: today and this week broken down per
task, plus the last seven days. It is a snapshot — reopen it to refresh.

## Todo list

Click **todos** in the window, or press `Ctrl+T`, for a list of what you mean to
work on. It feeds the working-on box, so planning and tracking stay one thing.

- **Type a task and press Enter.** If nothing is being worked on yet, the new
  task becomes the working-on tag straight away.
- **Click a task to work on it.** It is marked with ▶ and every block you run
  is logged against it.
- **Tick it when it is done.** If it was the task you were on, the working-on
  box moves to the next open task, or clears if the list is empty. Ticking a
  task you are *not* on leaves your current tag alone.
- **Anything not ticked stays**, across breaks, restarts and days, until you
  finish it or delete it with ✕. Untick a finished task to reopen it.
- **Each task shows the time already logged against it**, so a todo that has
  quietly eaten five hours is visible as one.
- Open todos appear at the top of the **recent ▾** menu, and on the break screen
  under "choose from your todos ▾" — so after a long break you can pick the next
  task from the list instead of retyping it.
- **clear completed** removes the ticked ones when the list gets long.

The list lives in `todos.json`. It is written safely (to a temporary file, then
swapped in), and if the file is ever unreadable — a bad hand edit, a sync
conflict — it is renamed to `todos.unreadable-<date>.json` and kept, rather than
being overwritten by an empty list.

## Independence

This app depends on nothing but the Python standard library, and nothing reads
its files. It is not connected to any other tool in this collection, so it can
be copied out, changed or deleted on its own.

Your `sessions.csv`, `settings.json` and `todos.json` are personal and stay on your machine:
this folder's `.gitignore` keeps both out of the repository.

## Changing the durations

Click **settings** in the window, or press `Ctrl+,`. You can set the focus
length, both break lengths, how many focus blocks come before a long break, and
whether breaks black out the screen. Three presets are there for the common
rhythms: Classic 25/5/15, Deep work 50/10/30, Long haul 90/20/30.

- **Changes apply straight away, including the block you are already in.** The
  current phase is re-measured from when it actually began, so stretching a 25
  minute block to 50 gives you the extra 25 there and then. Shortening it below
  what you have already done ends it immediately, which is the honest outcome.
- **Settings persist** to `settings.json` next to the script, so they survive a
  restart.
- **A bad value is refused, not silently ignored** — the window stays open and
  tells you what is wrong.
- A corrupt or hand-edited `settings.json` is ignored rather than fatal: bad
  values fall back to defaults, good ones are kept.

## Options

Command line flags still work, and **override the saved settings for that run
only** — handy for a one-off long session without disturbing your normal rhythm.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--work N` | 25 | focus minutes |
| `--short N` | 5 | short break minutes |
| `--long N` | 15 | long break minutes |
| `--cycle N` | 4 | focus blocks before a long break |
| `--soft` | off | do not black out the screen during breaks |

Precedence is: a flag you typed, then `settings.json`, then the default. It is
per setting, so `--work 90` alone still uses your saved break lengths.

Examples:

```
python pomodoro.py --work 50 --short 10 --long 30 --cycle 2
```

```
python pomodoro.py --cycle 3
```

Values accept decimals (`--work 0.1` is 6 seconds), which is handy for testing.

## The log

`sessions.csv` opens in Excel as-is. Aborted phases are recorded with
`completed=0` and their real elapsed time, so skipped work shows up rather than
vanishing.
