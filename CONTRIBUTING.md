# Working on Glide

How to run the engine, the tests and the checks, and how a change reaches players. The project's parts and the
interfaces between them are in [docs/REFACTOR-CONTRACTS.md](docs/REFACTOR-CONTRACTS.md); AI agents also follow
[CLAUDE.md](CLAUDE.md).

## Setup

- Python 3.12 or newer (`winget install -e --id Python.Python.3.12`), then
  `py -3 -m pip install -r requirements-dev.txt` (the tests' and linters' packages, pinned; `requirements.txt` holds
  what the engine itself uses: numpy, and faster-whisper for push-to-talk).
- Node 24 and the desktop packages: `cd desktop` then `npm ci`.
- luacheck 1.2.0 for the addon's linter: the Windows `luacheck.exe` from the
  [luacheck releases](https://github.com/lunarmodules/luacheck/releases/tag/v1.2.0) on your PATH (CI downloads the
  same file and checks its SHA-256), or `luarocks install luacheck 1.2.0`.

## Run the engine

The engine is the Python process in `companion/` (`companion/app.py`); the desktop app and `start-glide.cmd` both
run it. For development, run it simulated and without model keys, so nothing reaches a paid API and the coach falls
back to its offline callouts:

```
set ANTHROPIC_API_KEY=
set GEMINI_API_KEY=
set GOOGLE_API_KEY=
set OPENROUTER_API_KEY=
py -3 companion\app.py --sim --no-voice --no-overlay --no-ptt --port 8766
```

`GLIDE_SIM_SPEED=10` runs the simulation faster. The dashboard is then at http://127.0.0.1:8766. Pick a free port of
your own: never 8797, where the player's own copy runs. `cd desktop` then `npm run sim` runs the desktop app on the
simulation; a test copy of the app runs muted and hidden (`"muted": true`, `"resumeHidden": true` and
`"closeToTray": true` in its `app-settings.json`) with its own engine home (`GLIDE_ENGINE_HOME`), never on the
player's install. Stop what you started by its process id, never by name.

## Tests

```
py -3 tests/run_all.py              every test, each in its own process, with a summary
py -3 tests/run_all.py trash kb     only the tests whose path contains one of the words
py -3 tests/run_all.py --smoke      the report-only checks against your own data (never fail)
```

A test that can't run on this machine (no `data/kb.db`, no game data, no node, no Edge, no linter) prints
`SKIP: <reason>` and exits with 77; the summary lists it apart from passes and failures. A new test that needs data
not in the repository does the same, and tests the rest of what it can. Tests never read this machine's
`config.json` (they pass a temporary root) and never call a model.

On the main development PC several test runs share fixed local ports, so wrap every run in the shared lock (it also
blanks the model keys): `py -3 %USERPROFILE%\Documents\glide-test-lock.py py -3 tests/run_all.py`.

The tests also hold the project's ratchets: `tests/test_lint.py` (`ruff check .` and `luacheck addon/Glide` stay
clean), `tests/test_types.py` (pyright over the engine: a file may not gain type errors; lower its cap when it loses
some), `tests/test_strip_spec.py` (`docs/STRIP.md` is generated from `companion/strip_spec.py`:
`py -3 companion/strip_spec.py --write`), the settings manifest and the desktop's types of the engine state and IPC.

## Golden masters

`tests/golden/` records what each part does today and compares after every change:

```
py -3 tests/golden/golden.py check              all six probes (addon, desktop, dashboard, engine, knowledge, parser)
py -3 tests/golden/golden.py check --portable   only addon, desktop and dashboard: they need just the repository
                                                (plus node and Edge), so their snapshots hold on any machine
```

The other three ("machine" tier) read this PC's `data/glide.db` and `data/kb.db`. The check also builds the desktop
app. A difference is either a bug (fix it) or an intended change: record it in `docs/REFACTOR-CONTRACTS.md` under
"Recorded changes" (what, why, which recommendation), then re-record only that probe:
`py -3 tests/golden/golden.py record <probe>`. Never re-record to make an unexplained difference go away.

## Linters and builds

- `ruff check .` (settings in `pyproject.toml`), `luacheck addon/Glide` (`.luacheckrc`).
- `cd desktop` then `npm run build` (generates the Settings schema from `companion/settings_manifest.json`, then
  compiles).
- CI (`.github/workflows/ci.yml`, on every push and pull request) runs all of these, `tests/run_all.py` and the
  portable golden masters on a Windows runner with no game data.

## Releases

A release happens when `version` in `desktop/package.json` changes on `main`: `.github/workflows/release.yml` runs the
CI checks, builds the installer and publishes it to `Fermin7c1/Glide-releases`, and installed apps update from there.
So change that version only to release, and only as its own commit. The engine, knowledge, tools, `wowfiles/` and the
addon reach players only through a release; CI warns when they changed since the last version bump.

The installer ships only what the engine runs (`build.extraResources` in `desktop/package.json`): a new
development-only script in `tools/` or `knowledge/` belongs in its exclusions.

An addon change bumps the addon's version once, in `addon/Glide/Glide.toc` (the engine reads the expected version
from there).

## Knowledge refresh

The daily community scan commits its results as `Knowledge refresh <YYYY-MM-DD>`: the seed snapshot
(`data/seed/`), the day's intel and digest (`knowledge/community/`), `sources.json`, and
`addon/Glide/Data/QuestIDs.lua` when the quest ID list moved. When QuestIDs.lua changes, the same commit bumps the
addon version in `Glide.toc`, so installed addons pick up the new list.
