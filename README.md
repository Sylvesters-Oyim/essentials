# essentials

Small tools I build for my own daily work. Each one lives in its own folder,
runs on its own, and has its own README.

| Tool | What it does |
| --- | --- |
| [pomodoro](pomodoro/) | A strict focus timer for Windows. Breaks black out the screen, skipping takes a deliberate hold, and every session is logged with what you were working on, so you can see where the hours actually go. |

More to come.

## Conventions

- **One folder per tool**, self-contained. Nothing in one tool depends on
  another.
- **Standard library first.** A tool only takes a dependency when it clearly
  earns it, and says so in its README.
- **Personal data never gets committed.** Logs, local settings and databases
  written at runtime are gitignored, either here or in the tool's own folder.
