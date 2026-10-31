# Rules for AI agents working on Glide

Glide to 60 is a WoW Forever leveling coach: the addon (`addon/Glide`, Lua 5.1), the engine (`companion/`, Python
3.12, the process the desktop app runs), the dashboard (`companion/static`), the desktop app (`desktop/`, Electron,
TypeScript) and the knowledge layer and tools (`knowledge/`, `tools/`, `wowfiles/`). How to run and test it:
[CONTRIBUTING.md](CONTRIBUTING.md). These rules stand for every task.

## Never

- **No paid models in development or tests.** Blank `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` and
  `OPENROUTER_API_KEY` for every engine, test or script run (the test lock wrapper does it). The coach runs
  offline without them.
- **Never touch the player's running copy or WoW.** Don't start, stop, configure or send input to the installed
  app, its engine or the game. A test copy of the app runs muted and hidden (`"muted": true`, `"resumeHidden":
  true`, `"closeToTray": true` in its own `app-settings.json`, re-set before every launch) with its own engine home;
  a scratch engine or server never uses port 8797.
- **Stop processes only by exact executable path or by the PID you started.** Never by name or command-line match:
  that has killed unrelated programs.
- **No commit, push, merge, tag or release unless asked.** Every push to `main` that touches what the installer
  ships releases to every player (CONTRIBUTING.md, Releases), so a push to `main` is a release.
- **API keys are never written to `config.json`** (or any file in the repository). The app keeps them encrypted;
  the engine reads them from the environment.
- **No Blizzard assets in the repository or the installer**, except ones granted for it. Game data the app needs
  (buff and spell icons, maps, terrain) is read at runtime from the player's own client and never shipped.
- **Repo-local git identity only.** Never change global git config or credentials.

## Always

- **Design and check UI at 2560x1440 first**, then smaller sizes.
- **Keep the frozen interfaces** in [docs/REFACTOR-CONTRACTS.md](docs/REFACTOR-CONTRACTS.md) (the strip, the
  engine's HTTP, the dashboard state, the desktop IPC, files on the player's disk, the databases, command lines,
  player-visible text). An approved change to one is recorded there under "Recorded changes" (one bullet: what,
  why, which recommendation), and a golden probe it changes is re-recorded alone (`golden.py record <probe>`).
- **Keep the checks green**: `tests/run_all.py` and `tests/golden/golden.py check` (through the test lock on the
  development PC), `ruff check .`, `luacheck addon/Glide`, `npm run build` in `desktop/`. A bug fix comes with a
  regression test; a test that needs data not in the repository skips with exit 77.
- **An addon change bumps the addon version** once in `addon/Glide/Glide.toc`.
- **Settings** go through `companion/settings_manifest.json` (read with `companion/settings.py`), never an inline
  default; strip keys through `companion/strip_spec.py`; engine state and IPC types through
  `desktop/src/shared/engine.ts` and `desktop/src/shared/ipc.ts`.
- **No new runtime dependencies** without a decision; pins live in `requirements.txt` / `requirements-dev.txt`.
- Call the Python process "the engine" in docs and comments (the folder stays `companion/`).
- **Glide is not a dashboard, and is never designed like one.** Don't think in dashboard terms at all: not
  "what else can we show", not panels, widgets, metrics, readouts or a screen to fill. Glide's job is to enable the
  player, in ways that aren't framed inside a box of text and images: what it says and when, what it does for them,
  how it changes the next minute of their play, the game itself. When an idea starts drifting toward displaying
  more, stop: it is better to wander without a direction than to head toward a dashboard. Follow from that: the
  word isn't used for Glide (conversation, docs, comments, commits, player text); the window is the coach screen.
  Legacy names (`companion/dashboard.py`, `companion/static/dashboard.*`, `tests/test_dashboard_*`) are frozen
  interfaces and stay until a rename is decided; nothing new takes the word.
