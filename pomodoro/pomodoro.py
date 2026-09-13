#!/usr/bin/env python3
"""Strict Pomodoro - a small, unforgiving focus timer for Windows.

Rules of the house:
  * Phases run back to back. Nothing waits for you to click "next".
  * Breaks black out every monitor so you cannot work through them.
  * Skipping costs you a 3 second press-and-hold. Friction is the point.
  * Pausing is one click, because an interruption is not the moment for
    friction - but paused time is logged in its own column and shown in the
    history. You can stop the clock; you cannot pretend you didn't.
  * Every finished phase is written to sessions.csv so the day is on record.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox

APP_NAME = "Strict Pomodoro"
__version__ = "1.1.0"   # MAJOR.MINOR.PATCH - see CHANGELOG.md
DATA_FILE = Path(__file__).resolve().parent / "sessions.csv"
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
TODO_FILE = Path(__file__).resolve().parent / "todos.json"

DEFAULTS = {"work": 25.0, "short": 5.0, "long": 15.0, "cycle": 4, "strict": True}
LIMITS = {"work": (0.01, 600), "short": (0.01, 600), "long": (0.01, 600),
          "cycle": (1, 12)}
PRESETS = [
    ("Classic", 25, 5, 15, 4),
    ("Deep work", 50, 10, 30, 2),
    ("Long haul", 90, 20, 30, 2),
]
CSV_HEADER = ["date", "start", "end", "kind", "planned_min", "actual_min", "completed",
              "label", "paused_min"]

HOLD_SECONDS = 3.0
PAUSE_NAG_MIN = 15      # after this long paused, remind you the clock is stopped
PAUSED = "#edb95e"      # amber: stopped, not running, not finished

BG = "#14161a"
PANEL = "#1b1e24"
LINE = "#2b3038"
FG = "#e9ebee"
MUTED = "#767d88"
COLORS = {"work": "#ff6b5a", "short": "#3ac9a4", "long": "#5aa9ff"}
LABELS = {"work": "FOCUS", "short": "SHORT BREAK", "long": "LONG BREAK"}
BREAK_LINES = {
    "short": "Stand up. Look at something 6 metres away.",
    "long": "Leave the desk. Water, stretch, daylight.",
}
UNTAGGED = "untagged"


def pick_font(*candidates: str) -> str:
    """Return the first font family the system actually has."""
    from tkinter import font as tkfont

    available = {f.lower() for f in tkfont.families()}
    for name in candidates:
        if name.lower() in available:
            return name
    return candidates[-1]


def fmt(seconds: float) -> str:
    seconds = max(0, int(seconds + 0.5))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def human(minutes: float) -> str:
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def beep(pattern) -> None:
    """Play a short tone pattern off the UI thread. Silent if unsupported."""
    try:
        import winsound
    except ImportError:
        return

    def run():
        for freq, dur in pattern:
            try:
                winsound.Beep(freq, dur)
            except Exception:
                return

    threading.Thread(target=run, daemon=True).start()


CHIME_WORK_DONE = [(660, 140), (880, 140), (1046, 260)]
CHIME_BREAK_DONE = [(523, 130), (392, 300)]
CHIME_STILL_PAUSED = [(440, 110), (440, 110)]


def clamp(key: str, value):
    """Coerce a settings value into range, or raise ValueError saying why."""
    low, high = LIMITS[key]
    number = int(value) if key == "cycle" else float(value)
    if not low <= number <= high:
        raise ValueError(f"{key} must be between {low:g} and {high:g}")
    return number


def load_settings() -> dict:
    """Saved settings, ignoring anything corrupt rather than refusing to start."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for key in DEFAULTS:
        if key not in raw:
            continue
        if key == "strict":
            clean[key] = bool(raw[key])
            continue
        try:
            clean[key] = clamp(key, raw[key])
        except (TypeError, ValueError):
            pass
    return clean


def save_settings(values: dict) -> bool:
    try:
        SETTINGS_FILE.write_text(json.dumps(values, indent=2) + "\n",
                                 encoding="utf-8")
        return True
    except OSError:
        return False


def virtual_screen():
    """Bounding box of all monitors: (x, y, width, height)."""
    try:
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)
    except Exception:
        return 0, 0, 1920, 1080


class Log:
    """Append-only session record, plus today's totals."""

    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(CSV_HEADER)
        else:
            self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Widen the header in place when this file predates a new column.

        Old rows keep their original width; a reader simply sees the new field
        as empty for them, which is the truth.
        """
        try:
            # newline="" keeps each line's own terminator, so rewriting the
            # header cannot quietly convert the rest of the file.
            with self.path.open(newline="", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return
        if not lines:
            return
        existing = next(csv.reader([lines[0]]), [])
        if existing == CSV_HEADER or not existing:
            return
        if not set(existing).issubset(CSV_HEADER):
            return  # unfamiliar file: leave it alone rather than corrupt it
        ending = "\r\n" if lines[0].endswith("\r\n") else "\n"
        lines[0] = ",".join(CSV_HEADER) + ending
        try:
            with self.path.open("w", newline="", encoding="utf-8") as fh:
                fh.writelines(lines)
        except OSError:
            pass

    def append(self, kind, started_at, planned_min, actual_sec, completed,
               label="", paused_sec=0.0) -> None:
        ended_at = datetime.now()
        row = [
            started_at.strftime("%Y-%m-%d"),
            started_at.strftime("%H:%M:%S"),
            ended_at.strftime("%H:%M:%S"),
            kind,
            f"{planned_min:g}",
            f"{actual_sec / 60:.2f}",
            "1" if completed else "0",
            (label or "").strip(),
            f"{(paused_sec or 0.0) / 60:.2f}",
        ]
        try:
            with self.path.open("a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(row)
        except OSError:
            pass

    def rows(self) -> list[dict]:
        try:
            with self.path.open(newline="", encoding="utf-8") as fh:
                return list(csv.DictReader(fh))
        except OSError:
            return []

    def work_rows(self):
        """Work sessions as (day, minutes, task, completed), bad rows skipped."""
        for row in self.rows():
            if row.get("kind") != "work":
                continue
            try:
                day = date.fromisoformat(row["date"])
                minutes = float(row.get("actual_min") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            task = (row.get("label") or "").strip() or UNTAGGED
            yield day, minutes, task, row.get("completed") == "1"

    def today(self):
        """(completed pomodoros, focused minutes) for today."""
        count, minutes, _ = self.breakdown(date.today())
        return count, minutes

    def breakdown(self, since: date):
        """(pomodoros, minutes, [(task, minutes, pomodoros)]) on or after `since`."""
        count = 0
        total = 0.0
        per: dict[str, list] = {}
        for day, minutes, task, done in self.work_rows():
            if day < since:
                continue
            total += minutes
            count += 1 if done else 0
            entry = per.setdefault(task, [0.0, 0])
            entry[0] += minutes
            entry[1] += 1 if done else 0
        tasks = sorted(((t, m, c) for t, (m, c) in per.items()), key=lambda r: -r[1])
        return count, total, tasks

    def daily(self, days: int = 7):
        """[(day, minutes)] for the last `days` days, oldest first."""
        start = date.today() - timedelta(days=days - 1)
        per: dict[date, float] = {}
        for day, minutes, _task, _done in self.work_rows():
            if day >= start:
                per[day] = per.get(day, 0.0) + minutes
        return [(start + timedelta(days=i), per.get(start + timedelta(days=i), 0.0))
                for i in range(days)]

    def paused_minutes(self, since: date) -> float:
        """Time spent paused on or after `since`, across work and breaks alike."""
        total = 0.0
        for row in self.rows():
            try:
                day = date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if day < since:
                continue
            try:
                total += float(row.get("paused_min") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def last_task(self) -> str:
        """What you said you were working on most recently."""
        for row in reversed(self.rows()):
            task = (row.get("label") or "").strip()
            if task:
                return task
        return ""

    def recent_tasks(self, limit: int = 8) -> list[str]:
        """Distinct recent tasks, newest first, for the quick-pick menu."""
        seen: list[str] = []
        for row in reversed(self.rows()):
            task = (row.get("label") or "").strip()
            if task and task not in seen:
                seen.append(task)
            if len(seen) >= limit:
                break
        return seen


class TodoStore:
    """What you mean to work on, kept across restarts in todos.json.

    Unfinished items stay until you tick them off or delete them. A file that
    cannot be read is set aside under a new name rather than overwritten, so a
    bad hand edit or a sync conflict never silently wipes the list.
    """

    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = []
        self.set_aside_as: Path | None = None
        self.last_save_ok = True
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._set_aside()
            return
        entries = raw.get("todos", []) if isinstance(raw, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            try:
                item_id = int(entry.get("id"))
            except (TypeError, ValueError):
                item_id = self._next_id()
            self.items.append({
                "id": item_id,
                "text": text,
                "done": bool(entry.get("done")),
                "created": str(entry.get("created") or ""),
                "done_at": entry.get("done_at") or None,
            })

    def _set_aside(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"todos.unreadable-{stamp}.json")
        try:
            self.path.rename(target)
            self.set_aside_as = target
        except OSError:
            pass

    def save(self) -> bool:
        """Write to a temporary file first, then swap it in, so a crash
        mid-write can never leave a half-written list behind."""
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps({"todos": self.items}, indent=2) + "\n",
                            encoding="utf-8")
            os.replace(temp, self.path)
            self.last_save_ok = True
        except OSError:
            self.last_save_ok = False
        return self.last_save_ok

    def _next_id(self) -> int:
        return max((item["id"] for item in self.items), default=0) + 1

    @staticmethod
    def same(a: str, b: str) -> bool:
        return a.strip().casefold() == b.strip().casefold()

    def open_items(self) -> list[dict]:
        return [item for item in self.items if not item["done"]]

    def done_items(self) -> list[dict]:
        return [item for item in self.items if item["done"]]

    def add(self, text: str):
        text = " ".join(text.split())
        if not text:
            return None
        for item in self.open_items():
            if self.same(item["text"], text):
                return item            # already on the list; do not duplicate
        item = {"id": self._next_id(), "text": text, "done": False,
                "created": datetime.now().isoformat(timespec="minutes"),
                "done_at": None}
        self.items.append(item)
        self.save()
        return item

    def set_done(self, item_id: int, done: bool) -> None:
        for item in self.items:
            if item["id"] == item_id:
                item["done"] = done
                item["done_at"] = (datetime.now().isoformat(timespec="minutes")
                                   if done else None)
        self.save()

    def remove(self, item_id: int) -> None:
        self.items = [item for item in self.items if item["id"] != item_id]
        self.save()

    def clear_done(self) -> None:
        self.items = self.open_items()
        self.save()

    def next_open(self, excluding: str = ""):
        for item in self.open_items():
            if not self.same(item["text"], excluding):
                return item
        return None


class HoldButton(tk.Canvas):
    """A button that only fires after being held down for `seconds`."""

    def __init__(self, parent, text, command, *, seconds=HOLD_SECONDS,
                 width=200, height=38, accent=MUTED, bg=PANEL, font=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=1, highlightbackground=LINE, cursor="hand2")
        self.command = command
        self.seconds = seconds
        self.base_text = text
        self._bw, self._bh = width, height
        self._job = None
        self._t0 = 0.0
        self._fill = self.create_rectangle(0, 0, 0, height, fill=accent, outline="")
        self._text = self.create_text(width / 2, height / 2 + 1, text=text,
                                      fill=FG, font=font or ("Segoe UI", 10))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._cancel)
        self.bind("<Leave>", self._cancel)

    def _press(self, _event=None):
        self._t0 = time.monotonic()
        self._tick()

    def _tick(self):
        progress = min(1.0, (time.monotonic() - self._t0) / self.seconds)
        self.coords(self._fill, 0, 0, self._bw * progress, self._bh)
        if progress >= 1.0:
            self._cancel()
            self.command()
            return
        # Swap the label rather than appending to it: appended text overflowed
        # every button narrower than about 280px and got clipped at both ends.
        self.itemconfigure(self._text, text="keep holding…")
        self._job = self.after(16, self._tick)

    def _cancel(self, _event=None):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.coords(self._fill, 0, 0, 0, self._bh)
        self.itemconfigure(self._text, text=self.base_text)


class Pomodoro:
    def __init__(self, args):
        self.args = resolve_settings(args)
        self.log = Log(DATA_FILE)
        self.durations = {"work": args.work, "short": args.short, "long": args.long}

        self.running = False
        self.kind = "work"
        self.done_in_cycle = 0
        self.deadline = 0.0
        self.phase_started = None
        self.phase_t0 = 0.0
        self.overlay = None
        self._lift_job = None
        self._history = None
        self._settings = None
        self._todo_win = None
        self._todo_render_pending = False
        self.todos = TodoStore(TODO_FILE)
        self.paused_at = None       # monotonic time the pause started
        self.paused_total = 0.0     # seconds paused so far this phase
        self._nagged = False

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.digit_font = pick_font("Cascadia Mono", "Consolas", "Courier New")
        self.ui_font = pick_font("Segoe UI Variable Display", "Segoe UI", "Arial")

        self._build_ui()
        self.task_var.set(self.log.last_task())  # carry on where you left off
        self._place_bottom_right()
        self.refresh_stats()
        self.render_idle()
        self.root.after(200, self.tick)

    # ---------------------------------------------------------------- layout

    def _build_ui(self):
        pad = tk.Frame(self.root, bg=BG)
        pad.pack(padx=22, pady=18)

        # What this block is for. Written to every logged row, which is the
        # whole basis of the history: no tag, no idea where the hours went.
        task_head = tk.Frame(pad, bg=BG)
        task_head.pack(fill="x")
        tk.Label(task_head, text="WORKING ON", bg=BG, fg=MUTED,
                 font=(self.ui_font, 8, "bold")).pack(side="left")
        recent = tk.Label(task_head, text="recent ▾", bg=BG, fg="#4a505a",
                          font=(self.ui_font, 8), cursor="hand2")
        recent.pack(side="right")
        recent.bind("<Button-1>", self.show_recent_menu)

        self.task_var = tk.StringVar()
        self.task_entry = tk.Entry(pad, textvariable=self.task_var, bg=PANEL, fg=FG,
                                   insertbackground=FG, relief="flat", justify="center",
                                   font=(self.ui_font, 11))
        self.task_entry.pack(fill="x", ipady=6, pady=(3, 12))

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        self.phase_label = tk.Label(head, text="READY", bg=BG, fg=MUTED,
                                    font=(self.ui_font, 10, "bold"))
        self.phase_label.pack(side="left")
        self.dots = tk.Label(head, text="", bg=BG, fg=MUTED, font=(self.ui_font, 12))
        self.dots.pack(side="right")

        self.time_label = tk.Label(pad, text="25:00", bg=BG, fg=FG,
                                   font=(self.digit_font, 56, "bold"))
        self.time_label.pack(pady=(2, 10))

        self.bar = tk.Canvas(pad, width=300, height=5, bg=PANEL, highlightthickness=0)
        self.bar.pack()
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 5, fill=COLORS["work"], outline="")

        foot = tk.Frame(pad, bg=BG)
        foot.pack(fill="x", pady=(12, 12))
        self.stats_label = tk.Label(foot, text="", bg=BG, fg=MUTED,
                                    font=(self.ui_font, 9))
        self.stats_label.pack(side="left")
        history = tk.Label(foot, text="history", bg=BG, fg="#4a505a",
                           font=(self.ui_font, 9), cursor="hand2")
        history.pack(side="right")
        history.bind("<Button-1>", self.show_history)
        settings = tk.Label(foot, text="settings  ·", bg=BG, fg="#4a505a",
                            font=(self.ui_font, 9), cursor="hand2")
        settings.pack(side="right", padx=(0, 6))
        settings.bind("<Button-1>", self.show_settings)
        todos = tk.Label(foot, text="todos  ·", bg=BG, fg="#4a505a",
                         font=(self.ui_font, 9), cursor="hand2")
        todos.pack(side="right", padx=(0, 6))
        todos.bind("<Button-1>", self.show_todos)

        self.buttons = tk.Frame(pad, bg=BG)
        self.buttons.pack()

        self.start_btn = tk.Button(
            self.buttons, text="Start focus", command=self.start,
            bg=COLORS["work"], fg="#14161a", activebackground=COLORS["work"],
            activeforeground="#14161a", relief="flat", bd=0, cursor="hand2",
            font=(self.ui_font, 11, "bold"), width=26, pady=8,
        )
        # Running controls sit in their own frame so they can use a grid while
        # the idle Start button uses pack; Tk forbids mixing the two in one parent.
        self.run_controls = tk.Frame(self.buttons, bg=BG)
        self.pause_btn = tk.Button(
            self.run_controls, text="Pause", command=self.toggle_pause,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            relief="flat", bd=0, cursor="hand2",
            font=(self.ui_font, 10, "bold"),
        )
        self.restart_btn = HoldButton(self.run_controls, "Hold to restart",
                                      self.restart, width=145, accent="#2f4a5a",
                                      bg=BG, font=(self.ui_font, 9))
        self.skip_btn = HoldButton(self.run_controls, "Hold to skip", self.skip,
                                   width=145, accent="#3a4048", bg=BG,
                                   font=(self.ui_font, 9))
        self.stop_btn = HoldButton(self.run_controls, "Hold to stop", self.stop,
                                   width=145, accent="#5a2f2f", bg=BG,
                                   font=(self.ui_font, 9))
        self.pause_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 8))
        self.restart_btn.grid(row=0, column=1, pady=(0, 8))
        self.skip_btn.grid(row=1, column=0, padx=(0, 10))
        self.stop_btn.grid(row=1, column=1)
        self._layout = None  # which control set is currently shown

        self.root.bind("<Control-p>", self.toggle_pause)
        self.root.bind("<Control-comma>", self.show_settings)
        self.root.bind("<Control-t>", self.show_todos)
        self.task_var.trace_add("write", lambda *_: self._queue_todo_render())

        self.pin = tk.Label(self.root, text="pinned on top", bg=BG, fg="#4a505a",
                            font=(self.ui_font, 8), cursor="hand2")
        self.pin.place(relx=1.0, y=4, x=-10, anchor="ne")
        self.pin.bind("<Button-1>", self.toggle_pin)

    def current_label(self) -> str:
        return self.task_var.get().strip()

    def quick_pick(self):
        """(open todos, recent tags not already on the todo list)."""
        todo_texts = [item["text"] for item in self.todos.open_items()]
        recent = [task for task in self.log.recent_tasks()
                  if not any(TodoStore.same(task, t) for t in todo_texts)]
        return todo_texts, recent

    def show_recent_menu(self, event):
        """Quick-pick: open todos first, then what you have worked on lately."""
        todo_texts, recent = self.quick_pick()
        if not todo_texts and not recent:
            return
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=FG, bd=0,
                       activebackground=COLORS["work"], activeforeground=BG,
                       font=(self.ui_font, 9))
        if todo_texts:
            menu.add_command(label="TODO", state="disabled")
            for task in todo_texts:
                menu.add_command(label="   " + task,
                                 command=lambda v=task: self.task_var.set(v))
        if recent:
            if todo_texts:
                menu.add_separator()
            menu.add_command(label="RECENT", state="disabled")
            for task in recent:
                menu.add_command(label="   " + task,
                                 command=lambda v=task: self.task_var.set(v))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _place_bottom_right(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{sw - w - 40}+{sh - h - 90}")

    def toggle_pin(self, _event=None):
        on = not bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", on)
        self.pin.configure(text="pinned on top" if on else "not pinned",
                           fg="#4a505a" if on else "#6b3a3a")

    # ----------------------------------------------------------- phase logic

    def next_kind(self) -> str:
        if self.kind != "work":
            return "work"
        if self.done_in_cycle >= self.args.cycle:
            return "long"
        return "short"

    def begin(self, kind: str):
        self.kind = kind
        minutes = self.durations[kind]
        self.phase_started = datetime.now()
        self.phase_t0 = time.monotonic()
        self.deadline = self.phase_t0 + minutes * 60
        self.paused_at = None
        self.paused_total = 0.0
        self._nagged = False
        self.running = True
        if kind == "work":
            self.close_overlay()
            self.root.deiconify()
        elif self.args.strict:
            self.open_overlay()
        self.render()

    def finish(self, completed: bool):
        if not self.running:
            return
        self.log.append(self.kind, self.phase_started, self.durations[self.kind],
                        self.elapsed_seconds(), completed, self.current_label(),
                        self.paused_seconds())
        if self.kind == "work":
            self.done_in_cycle += 1
        elif self.kind == "long":
            self.done_in_cycle = 0
        beep(CHIME_WORK_DONE if self.kind == "work" else CHIME_BREAK_DONE)
        self.refresh_stats()
        self.begin(self.next_kind())

    def start(self):
        if not self.running:
            self.begin("work")

    def skip(self):
        self.finish(completed=False)

    def restart(self):
        """Run the current phase again from its full length.

        The time already spent is logged as an unfinished phase first, so a
        restarted focus block still counts towards the hours you worked; it
        just does not count as a completed pomodoro. The cycle position is
        untouched: restarting your third block leaves you on your third block.
        """
        if not self.running:
            return
        self.log.append(self.kind, self.phase_started, self.durations[self.kind],
                        self.elapsed_seconds(), False, self.current_label(),
                        self.paused_seconds())
        self.refresh_stats()
        self.begin(self.kind)

    def stop(self):
        if self.running:
            self.log.append(self.kind, self.phase_started, self.durations[self.kind],
                            self.elapsed_seconds(), False, self.current_label(),
                            self.paused_seconds())
        self.running = False
        self.paused_at = None
        self.paused_total = 0.0
        self.close_overlay()
        self.root.deiconify()
        self.kind = "work"
        self.refresh_stats()
        self.render_idle()

    # ----------------------------------------------------------------- pause

    def toggle_pause(self, _event=None):
        if not self.running:
            return
        self.resume() if self.paused_at is not None else self.pause()

    def pause(self) -> None:
        """Freeze the clock. One click, because an interruption is not the
        moment for friction — the honesty comes from logging the paused time."""
        if not self.running or self.paused_at is not None:
            return
        self.paused_at = time.monotonic()
        # A break you paused should not go on blocking the screen; you almost
        # certainly paused it because you need the computer.
        if self.kind != "work":
            self.close_overlay()
            self.root.deiconify()
        self.render()

    def resume(self) -> None:
        if not self.running or self.paused_at is None:
            return
        held = time.monotonic() - self.paused_at
        self.deadline += held           # the phase owes you the time back
        self.paused_total += held
        self.paused_at = None
        self._nagged = False
        if self.kind != "work" and self.args.strict:
            self.open_overlay()
        self.render()

    def paused_seconds(self) -> float:
        """Total paused time this phase, including a pause still in progress."""
        live = (time.monotonic() - self.paused_at) if self.paused_at is not None else 0.0
        return self.paused_total + live

    def elapsed_seconds(self) -> float:
        """Wall time in this phase minus anything spent paused."""
        return max(0.0, time.monotonic() - self.phase_t0 - self.paused_seconds())

    def tick(self):
        if self.running:
            if self.paused_at is not None:
                # A pause you forgot about is the failure mode. Say so once.
                if not self._nagged and self.paused_seconds() >= PAUSE_NAG_MIN * 60:
                    self._nagged = True
                    beep(CHIME_STILL_PAUSED)
                self.render()
            elif self.remaining() <= 0:
                self.finish(completed=True)
            else:
                self.render()
        self.root.after(200, self.tick)

    # -------------------------------------------------------------- painting

    def remaining(self) -> float:
        # While paused the clock is frozen at the moment you stopped it.
        now = self.paused_at if self.paused_at is not None else time.monotonic()
        return max(0.0, self.deadline - now)

    def progress(self) -> float:
        total = self.durations[self.kind] * 60
        return 0.0 if total <= 0 else min(1.0, 1 - self.remaining() / total)

    def dot_string(self) -> str:
        return "".join("●" if i < self.done_in_cycle else "○"
                       for i in range(self.args.cycle))

    def _layout_controls(self, mode: str) -> None:
        """Swap the button set only when the mode actually changes."""
        if self._layout == mode:
            return
        self._layout = mode
        self.start_btn.pack_forget()
        self.run_controls.pack_forget()
        if mode == "idle":
            self.start_btn.pack()
        else:
            self.run_controls.pack()

    def render_idle(self):
        self.time_label.configure(text=fmt(self.durations["work"] * 60), fg=FG)
        self.phase_label.configure(text="READY", fg=MUTED)
        self.dots.configure(text=self.dot_string(), fg=MUTED)
        self.bar.coords(self.bar_fill, 0, 0, 0, 5)
        self.root.title(APP_NAME)
        self._layout_controls("idle")

    def render(self):
        paused = self.paused_at is not None
        color = PAUSED if paused else COLORS[self.kind]
        text = fmt(self.remaining())
        self._layout_controls("running")

        self.pause_btn.configure(
            text="Resume" if paused else "Pause",
            bg=PAUSED if paused else PANEL,
            fg=BG if paused else FG,
            activebackground=PAUSED if paused else PANEL,
            activeforeground=BG if paused else FG,
        )

        phase = LABELS[self.kind]
        if paused:
            phase += "  PAUSED " + fmt(self.paused_seconds())
        self.time_label.configure(text=text, fg=color)
        self.phase_label.configure(text=phase, fg=color)
        self.dots.configure(text=self.dot_string(), fg=color)
        self.bar.itemconfigure(self.bar_fill, fill=color)
        self.bar.coords(self.bar_fill, 0, 0, 300 * self.progress(), 5)
        self.root.title(("PAUSED " if paused else "") + text
                        + " - " + LABELS[self.kind].title())
        if self.overlay is not None:
            self.ov_time.configure(text=text)
            self.ov_bar.coords(self.ov_bar_fill, 0, 0, self.ov_bar_w * self.progress(), 6)

    def refresh_stats(self):
        count, minutes = self.log.today()
        word = "pomodoro" if count == 1 else "pomodoros"
        text = f"Today: {count} {word} · {human(minutes)} focused"
        paused = self.log.paused_minutes(date.today())
        if paused >= 1:
            text += f" · {human(paused)} paused"
        self.stats_label.configure(text=text)

    # -------------------------------------------------------------- settings

    def show_settings(self, _event=None):
        """Adjust the durations without editing a file or a shortcut."""
        if self._settings is not None and self._settings.winfo_exists():
            self._settings.lift()
            self._settings.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._settings = win
        win.title("Settings")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

        pad = tk.Frame(win, bg=BG)
        pad.pack(padx=22, pady=18)

        fields = [("work", "Focus minutes"), ("short", "Short break minutes"),
                  ("long", "Long break minutes"),
                  ("cycle", "Focus blocks before a long break")]
        self._setting_vars = {}
        for key, caption in fields:
            tk.Label(pad, text=caption.upper(), bg=BG, fg=MUTED,
                     font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(8, 2))
            var = tk.StringVar(value=f"{getattr(self.args, key):g}")
            self._setting_vars[key] = var
            tk.Entry(pad, textvariable=var, bg=PANEL, fg=FG, insertbackground=FG,
                     relief="flat", font=(self.digit_font, 12), width=26,
                     justify="center").pack(ipady=5)

        self._strict_var = tk.BooleanVar(value=bool(self.args.strict))
        tk.Checkbutton(pad, text="  Black out the screen during breaks",
                       variable=self._strict_var, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, bd=0,
                       highlightthickness=0, anchor="w",
                       font=(self.ui_font, 9)).pack(anchor="w", pady=(14, 4))

        tk.Label(pad, text="PRESETS", bg=BG, fg=MUTED,
                 font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(12, 4))
        presets = tk.Frame(pad, bg=BG)
        presets.pack(fill="x")
        for name, work, short, long_, cycle in PRESETS:
            tk.Button(presets, text=f"{name}\n{work:g}/{short:g}/{long_:g}",
                      command=lambda w=work, s=short, l=long_, c=cycle:
                          self._fill_settings(w, s, l, c),
                      bg=PANEL, fg=FG, activebackground=LINE, activeforeground=FG,
                      relief="flat", bd=0, cursor="hand2", padx=10, pady=6,
                      font=(self.ui_font, 8)).pack(side="left", padx=(0, 6))

        self._settings_error = tk.Label(pad, text="", bg=BG, fg=COLORS["work"],
                                        font=(self.ui_font, 9), wraplength=240,
                                        justify="left")
        self._settings_error.pack(anchor="w", pady=(12, 0))

        tk.Label(pad, text="Applies straight away, including the block you are in.",
                 bg=BG, fg="#3f454e", font=(self.ui_font, 8),
                 wraplength=240, justify="left").pack(anchor="w", pady=(8, 12))

        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x")
        tk.Button(row, text="Save", command=self.apply_settings,
                  bg=COLORS["work"], fg=BG, activebackground=COLORS["work"],
                  activeforeground=BG, relief="flat", bd=0, cursor="hand2",
                  font=(self.ui_font, 10, "bold"), padx=22,
                  pady=7).pack(side="left")
        tk.Button(row, text="Cancel", command=self._close_settings,
                  bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2",
                  font=(self.ui_font, 10), padx=14, pady=7).pack(side="left")

    def _fill_settings(self, work, short, long_, cycle):
        for key, value in (("work", work), ("short", short),
                           ("long", long_), ("cycle", cycle)):
            self._setting_vars[key].set(f"{value:g}")
        self._settings_error.configure(text="")

    def apply_settings(self):
        values = {}
        for key, var in self._setting_vars.items():
            try:
                values[key] = clamp(key, var.get().strip())
            except (TypeError, ValueError) as exc:
                message = str(exc)
                if "could not convert" in message or "invalid literal" in message:
                    message = f"{key} must be a number"
                self._settings_error.configure(text=message)
                return
        values["strict"] = bool(self._strict_var.get())

        was_strict = bool(self.args.strict)
        for key, value in values.items():
            setattr(self.args, key, value)
        self.durations = {"work": self.args.work, "short": self.args.short,
                          "long": self.args.long}

        if self.running:
            # Give the phase you are in the new length, measured from when it
            # actually began. Shortening below what you have done ends it at
            # the next tick, which is the honest outcome.
            self.deadline = (self.phase_t0 + self.paused_seconds()
                             + self.durations[self.kind] * 60)
            if self.kind != "work":
                if was_strict and not self.args.strict:
                    self.close_overlay()
                    self.root.deiconify()
                elif not was_strict and self.args.strict and self.paused_at is None:
                    self.open_overlay()
            self.render()
        else:
            self.render_idle()

        if not save_settings(values):
            self._settings_error.configure(
                text="Applied, but could not write settings.json")
            return
        self._close_settings()

    def _close_settings(self):
        if self._settings is not None:
            self._settings.destroy()
            self._settings = None

    # ----------------------------------------------------------------- todos

    def show_todos(self, _event=None):
        """A list of what you mean to work on, feeding the working-on box."""
        if self._todo_win is not None and self._todo_win.winfo_exists():
            self._todo_win.deiconify()
            self._todo_win.lift()
            self._todo_entry.focus_set()
            return

        win = tk.Toplevel(self.root)
        self._todo_win = win
        win.title("Todo")
        win.configure(bg=BG)
        win.geometry("420x540")
        win.minsize(340, 300)
        win.protocol("WM_DELETE_WINDOW", self._close_todos)

        pad = tk.Frame(win, bg=BG)
        pad.pack(fill="both", expand=True, padx=20, pady=16)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="TODO", bg=BG, fg=MUTED,
                 font=(self.ui_font, 9, "bold")).pack(side="left")
        self._todo_counts = tk.Label(head, text="", bg=BG, fg="#4a505a",
                                     font=(self.ui_font, 9))
        self._todo_counts.pack(side="right")

        add = tk.Frame(pad, bg=BG)
        add.pack(fill="x", pady=(8, 4))
        self._todo_entry = tk.Entry(add, bg=PANEL, fg=FG, insertbackground=FG,
                                    relief="flat", font=(self.ui_font, 11))
        self._todo_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self._todo_entry.bind("<Return>", lambda _e: self._add_todo())
        tk.Button(add, text="Add", command=self._add_todo, bg=COLORS["work"],
                  fg=BG, activebackground=COLORS["work"], activeforeground=BG,
                  relief="flat", bd=0, cursor="hand2", padx=16,
                  font=(self.ui_font, 10, "bold")).pack(side="left", padx=(8, 0),
                                                        fill="y")

        tk.Label(pad, text="click a task to work on it  ·  tick it when it is done",
                 bg=BG, fg="#3f454e", font=(self.ui_font, 8)).pack(anchor="w",
                                                                   pady=(2, 10))

        self._todo_notice = tk.Label(pad, text="", bg=BG, fg=PAUSED,
                                     font=(self.ui_font, 9), wraplength=360,
                                     justify="left")
        self._todo_notice.pack(anchor="w")
        if self.todos.set_aside_as is not None:
            self._todo_notice.configure(
                text=f"todos.json could not be read, so it was kept as "
                     f"{self.todos.set_aside_as.name} and a fresh list started.")

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        bar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self._todo_list = tk.Frame(canvas, bg=BG)
        self._todo_list.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        list_window = canvas.create_window((0, 0), window=self._todo_list,
                                           anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(list_window, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-e.delta / 120), "units"))

        clear = tk.Label(pad, text="clear completed", bg=BG, fg="#4a505a",
                         cursor="hand2", font=(self.ui_font, 9))
        clear.pack(anchor="e", pady=(10, 0))
        clear.bind("<Button-1>", lambda _e: self._clear_done_todos())

        self._render_todos()
        self._todo_entry.focus_set()

    def _close_todos(self):
        if self._todo_win is not None:
            self._todo_win.destroy()
            self._todo_win = None

    def _queue_todo_render(self):
        """Coalesce redraws, and never rebuild a row from inside its own click."""
        if self._todo_render_pending or self._todo_win is None:
            return
        self._todo_render_pending = True
        self.root.after_idle(self._render_todos)

    def _render_todos(self):
        self._todo_render_pending = False
        if self._todo_win is None or not self._todo_win.winfo_exists():
            return
        for child in self._todo_list.winfo_children():
            child.destroy()

        _count, _total, spent = self.log.breakdown(date.min)
        minutes = {task.casefold(): mins for task, mins, _c in spent}
        current = self.current_label()
        open_items, done_items = self.todos.open_items(), self.todos.done_items()

        self._todo_counts.configure(
            text=f"{len(open_items)} open  ·  {len(done_items)} done")

        if not open_items:
            tk.Label(self._todo_list, text="Nothing open. Add what you mean to work on.",
                     bg=BG, fg="#3f454e", font=(self.ui_font, 10)).pack(
                         anchor="w", pady=6)
        for item in open_items:
            self._todo_row(item, minutes.get(item["text"].casefold(), 0),
                           TodoStore.same(item["text"], current))

        if done_items:
            tk.Label(self._todo_list, text="DONE", bg=BG, fg="#4a505a",
                     font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(16, 2))
            for item in done_items:
                self._todo_row(item, minutes.get(item["text"].casefold(), 0), False)

    def _todo_row(self, item: dict, minutes: float, is_current: bool):
        done = item["done"]
        row = tk.Frame(self._todo_list, bg=PANEL if is_current else BG)
        row.pack(fill="x", pady=1)

        var = tk.BooleanVar(value=done)
        tk.Checkbutton(row, variable=var, bg=row["bg"], activebackground=row["bg"],
                       selectcolor=PANEL, bd=0, highlightthickness=0, cursor="hand2",
                       command=lambda: self._set_todo_done(item, var.get())).pack(
                           side="left", padx=(4, 2))

        remove = tk.Label(row, text="✕", bg=row["bg"], fg="#4a505a", cursor="hand2",
                          font=(self.ui_font, 9))
        remove.pack(side="right", padx=(6, 8))
        remove.bind("<Button-1>", lambda _e: self._delete_todo(item))

        if minutes >= 1:
            tk.Label(row, text=human(minutes), bg=row["bg"], fg="#5b626c",
                     font=(self.ui_font, 9)).pack(side="right")

        font = (self.ui_font, 10, "bold" if is_current else "normal")
        if done:
            font = (self.ui_font, 10, "overstrike")
        text = ("▶  " if is_current else "") + item["text"]
        label = tk.Label(row, text=text, bg=row["bg"], anchor="w", justify="left",
                         fg=COLORS["work"] if is_current else ("#5b626c" if done else FG),
                         font=font, wraplength=250, cursor="" if done else "hand2")
        label.pack(side="left", fill="x", expand=True, pady=6)
        if not done:
            label.bind("<Button-1>", lambda _e: self.task_var.set(item["text"]))

    def _add_todo(self):
        item = self.todos.add(self._todo_entry.get())
        self._todo_entry.delete(0, "end")
        if item is None:
            return
        self._check_todo_saved()
        if not self.current_label():
            self.task_var.set(item["text"])   # nothing on the go: start with this
        self._queue_todo_render()

    def _set_todo_done(self, item: dict, done: bool):
        self.todos.set_done(item["id"], done)
        self._check_todo_saved()
        # Finishing the thing you are on moves you to the next thing on the list.
        if done and TodoStore.same(item["text"], self.current_label()):
            upcoming = self.todos.next_open(excluding=item["text"])
            self.task_var.set(upcoming["text"] if upcoming else "")
        self._queue_todo_render()

    def _delete_todo(self, item: dict):
        self.todos.remove(item["id"])
        self._check_todo_saved()
        self._queue_todo_render()

    def _clear_done_todos(self):
        self.todos.clear_done()
        self._check_todo_saved()
        self._queue_todo_render()

    def _check_todo_saved(self):
        if not self.todos.last_save_ok:
            self._todo_notice.configure(text="Could not write todos.json; "
                                             "changes will be lost on restart.")

    # --------------------------------------------------------------- history

    def show_history(self, _event=None):
        """Where the hours went: today, this week, and the last seven days."""
        if self._history is not None and self._history.winfo_exists():
            self._history.lift()
            self._history.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._history = win
        win.title("Where the time went")
        win.configure(bg=BG)
        win.geometry("470x600")
        win.protocol("WM_DELETE_WINDOW", self._close_history)

        pad = tk.Frame(win, bg=BG)
        pad.pack(padx=22, pady=16, fill="both", expand=True)

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        self._history_tasks(pad, "TODAY", *self.log.breakdown(today),
                            paused=self.log.paused_minutes(today))
        self._history_tasks(pad, "THIS WEEK", *self.log.breakdown(week_start),
                            paused=self.log.paused_minutes(week_start))
        self._history_days(pad)

        tk.Label(pad, text=f"snapshot · reopen to refresh · {DATA_FILE.name}",
                 bg=BG, fg="#3f454e", font=(self.ui_font, 8)).pack(anchor="w",
                                                                   pady=(16, 0))

    def _close_history(self):
        if self._history is not None:
            self._history.destroy()
            self._history = None

    def _bar(self, parent, fraction, height=4, color=None, **pack_kw):
        """A bar that redraws itself to whatever width it is given."""
        canvas = tk.Canvas(parent, height=height, bg=PANEL, highlightthickness=0)
        canvas.pack(**pack_kw)
        fill = color or COLORS["work"]
        canvas.bind("<Configure>", lambda e: (
            e.widget.delete("all"),
            e.widget.create_rectangle(0, 0, max(0, e.width * fraction), height,
                                      fill=fill, outline="")))
        return canvas

    def _history_tasks(self, parent, title, count, minutes, tasks, limit=8,
                       paused=0.0):
        tk.Label(parent, text=title, bg=BG, fg=MUTED,
                 font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(10, 2))

        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=human(minutes), bg=BG, fg=FG,
                 font=(self.digit_font, 19, "bold")).pack(side="left")
        summary = f"   {count} pomodoro{'' if count == 1 else 's'}"
        if paused >= 1:
            summary += f"  ·  {human(paused)} paused"
        tk.Label(head, text=summary, bg=BG, fg=MUTED,
                 font=(self.ui_font, 9)).pack(side="left", pady=(7, 0))

        if not tasks:
            tk.Label(parent, text="nothing logged yet", bg=BG, fg="#3f454e",
                     font=(self.ui_font, 9)).pack(anchor="w", pady=(2, 6))
            return

        peak = max(mins for _t, mins, _c in tasks) or 1
        for task, mins, _done in tasks[:limit]:
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=(7, 2))
            tk.Label(row, text=task, bg=BG, fg=FG, font=(self.ui_font, 10),
                     anchor="w").pack(side="left")
            tk.Label(row, text=human(mins), bg=BG, fg=MUTED,
                     font=(self.ui_font, 10)).pack(side="right")
            self._bar(parent, mins / peak, fill="x")

    def _history_days(self, parent):
        tk.Label(parent, text="LAST 7 DAYS", bg=BG, fg=MUTED,
                 font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(18, 4))
        days = self.log.daily(7)
        peak = max((mins for _d, mins in days), default=0) or 1
        for day, mins in days:
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=day.strftime("%a %d %b"), bg=BG, fg=MUTED, width=11,
                     anchor="w", font=(self.digit_font, 9)).pack(side="left")
            tk.Label(row, text=human(mins), bg=BG, fg=FG if mins else "#3f454e",
                     width=8, anchor="e", font=(self.ui_font, 9)).pack(side="right")
            self._bar(row, mins / peak, height=8,
                      side="left", fill="x", expand=True, padx=8)

    # --------------------------------------------------------- break overlay

    def open_overlay(self):
        if self.overlay is not None:
            return
        x, y, w, h = virtual_screen()
        color = COLORS[self.kind]

        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{w}x{h}+{x}+{y}")
        ov.configure(bg="#0b0d10")
        ov.attributes("-topmost", True)
        self.overlay = ov
        self.root.withdraw()

        box = tk.Frame(ov, bg="#0b0d10")
        box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(box, text=LABELS[self.kind], bg="#0b0d10", fg=color,
                 font=(self.ui_font, 20, "bold")).pack(pady=(0, 6))
        self.ov_time = tk.Label(box, text=fmt(self.remaining()), bg="#0b0d10", fg=FG,
                                font=(self.digit_font, 140, "bold"))
        self.ov_time.pack()
        tk.Label(box, text=BREAK_LINES[self.kind], bg="#0b0d10", fg=MUTED,
                 font=(self.ui_font, 15)).pack(pady=(4, 26))

        self.ov_bar_w = 520
        self.ov_bar = tk.Canvas(box, width=self.ov_bar_w, height=6, bg="#1b1e24",
                                highlightthickness=0)
        self.ov_bar.pack()
        self.ov_bar_fill = self.ov_bar.create_rectangle(0, 0, 0, 6, fill=color, outline="")

        # A long break is where work actually changes, so ask there rather than
        # letting the next block inherit a stale tag by default.
        switching = self.kind == "long"
        prompt = ("What will you work on next?" if switching
                  else f"Next: {self.durations['work']:g} minutes on")
        tk.Label(box, text=prompt, bg="#0b0d10",
                 fg=color if switching else "#4a505a",
                 font=(self.ui_font, 12 if switching else 10,
                       "bold" if switching else "normal")).pack(pady=(28, 6))

        entry = tk.Entry(box, textvariable=self.task_var, bg="#161a1f", fg=FG,
                         insertbackground=FG, relief="flat", justify="center",
                         font=(self.ui_font, 13), width=42)
        entry.pack(ipady=7)
        if switching:
            entry.configure(highlightthickness=1, highlightbackground=color,
                            highlightcolor=color)

        if self.todos.open_items():
            pick = tk.Label(box, text="choose from your todos ▾", bg="#0b0d10",
                            fg=color if switching else "#5b626c", cursor="hand2",
                            font=(self.ui_font, 9))
            pick.pack(pady=(8, 0))
            pick.bind("<Button-1>", self.show_recent_menu)

        tk.Label(box, text="whatever this says when the break ends is what gets logged",
                 bg="#0b0d10", fg="#3f454e", font=(self.ui_font, 9)).pack(pady=(7, 22))

        controls = tk.Frame(box, bg="#0b0d10")
        controls.pack()
        tk.Button(controls, text="Pause break", command=self.pause,
                  bg="#1b1e24", fg=FG, activebackground=PAUSED,
                  activeforeground=BG, relief="flat", bd=0, cursor="hand2",
                  font=(self.ui_font, 9), padx=18, pady=9).pack(side="left",
                                                                padx=(0, 10))
        HoldButton(controls, "Hold to cut the break short", self.skip, width=260,
                   accent="#3a4048", bg="#0b0d10", font=(self.ui_font, 9)).pack(
                       side="left")

        tk.Label(box, text="pausing a break releases the screen and stops its clock",
                 bg="#0b0d10", fg="#3f454e", font=(self.ui_font, 9)).pack(pady=(12, 0))

        ov.bind("<Control-p>", self.toggle_pause)
        ov.focus_force()
        if switching:
            entry.focus_set()
            entry.selection_range(0, "end")
        self._keep_on_top()

    def _keep_on_top(self):
        if self.overlay is None:
            return
        try:
            self.overlay.lift()
            self.overlay.attributes("-topmost", True)
            # Never steal focus back while the task field is being typed into.
            if self.overlay.focus_get() is None:
                self.overlay.focus_force()
        except tk.TclError:
            return
        self._lift_job = self.root.after(1200, self._keep_on_top)

    def close_overlay(self):
        if self._lift_job is not None:
            self.root.after_cancel(self._lift_job)
            self._lift_job = None
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None

    # ------------------------------------------------------------- lifecycle

    def on_close(self):
        if self.running:
            ok = messagebox.askyesno(
                APP_NAME,
                LABELS[self.kind].title() + " is still running with "
                + fmt(self.remaining()) + " left.\n\nQuit anyway?",
                parent=self.root,
            )
            if not ok:
                return
            elapsed = time.monotonic() - self.phase_t0
            self.log.append(self.kind, self.phase_started, self.durations[self.kind],
                            elapsed, False, self.current_label())
        self.close_overlay()
        self.root.destroy()

    def run(self):
        if self.args.selftest:
            self.start()
            self.root.after(int(self.args.selftest * 1000), self.on_selftest_end)
        self.root.mainloop()

    def on_selftest_end(self):
        state = {
            "kind": self.kind,
            "done_in_cycle": self.done_in_cycle,
            "overlay": self.overlay is not None,
            "remaining": round(self.remaining(), 1),
        }
        print("selftest:", state)
        self.close_overlay()
        self.root.destroy()


def parse_args(argv=None):
    """Flags default to None so a flag you actually typed can be told apart
    from one you did not, letting saved settings fill the gaps."""
    p = argparse.ArgumentParser(description=APP_NAME)
    p.add_argument("--work", type=float, help="focus minutes (default 25)")
    p.add_argument("--short", type=float, help="short break minutes (default 5)")
    p.add_argument("--long", type=float, help="long break minutes (default 15)")
    p.add_argument("--cycle", type=int,
                   help="focus blocks before a long break (default 4)")
    p.add_argument("--soft", dest="strict", action="store_const", const=False,
                   default=None, help="do not black out the screen during breaks")
    p.add_argument("--selftest", type=float, default=0, help=argparse.SUPPRESS)
    p.add_argument("--version", action="version",
                   version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def resolve_settings(args):
    """Precedence: a flag you typed, then the saved settings, then the default."""
    saved = load_settings()
    for key, fallback in DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, saved.get(key, fallback))
    return args


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    Pomodoro(parse_args()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
