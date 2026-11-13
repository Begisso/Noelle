# Noelle HMI: notes for Claude

Noelle is an HMI/SCADA graphics editor and runtime, similar to GraphWorX. A Python asyncio backend (aiohttp) serves a vanilla-JS browser client, and the app window is Edge (or Chrome) in `--app` mode. Windows only.

- `README.md` is the end-user documentation. Update it when user-visible behavior changes.
- `HISTORY.md` is the change log. **Add an entry for every change**, in the same commit as the change.
- Git: https://github.com/Begisso/Noelle (public, branch `main`). Commit as the repo-local identity (Begisso, GitHub noreply address), and commit or push only when the user asks. `dist/Noelle.exe` is committed, so commit it again after every rebuild. Plant-derived data is git-ignored on purpose and must never be committed: `projects/Demo/displays/imported/`, `projects/Demo/images/`, `projects/ImportTest/`, `projects/MnemoImport/`, the `.history/` folders (they hold backups of the imported displays).
- `.claude/skills/noelle/SKILL.md` covers running, headless UI checks with screenshots, tests and the exe build. Follow it to verify changes.

## Layout

- `noelle/`: backend
  - `__main__.py`: CLI and exe entry point (open project / `.nll`, `--kiosk`, `--server`, `new`, `adduser`, `import`, `render`, `--register`)
  - `server.py`: REST API + WebSocket + static `web/`. `_open_app_window` launches Edge `--app`.
  - `engine.py`: tag engine (ref-counted tags, deadband, coalesced fan-out). `address.py` parses refs such as `modbus:`, `s7:`, `opcua:`, `sim:`, `mem:`.
  - `drivers/`: modbus, s7, opcua, opcda (through the 32-bit PowerShell bridge `opcda_bridge.ps1`), datahub, local (`sim:`/`mem:`/`sys:`). `base.PollingDriver` plans the block reads.
  - `project.py`: the project folder, `tags.csv`/`aliases.csv`, file writes with `.history/` backups, migration of legacy `.gfp`/`.gfd` files
  - `alarms.py`, `datalog.py`, `security.py`; `scripting.py` (hot-reloaded `scripts/*.py`, `hmi` API); `historian.py` (HTTP historian proxy with hour-chunk cache and M4 downsampling)
  - `importers/`: `gwx` (GraphWorX32 through COM + PowerShell), `tia` (TIA Openness), `weintek`, `vectorize`, `visual` (render + identity compare)
  - `demo.py` generates the Demo project. `schema/` holds the JSON schemas for `.nll`. `cdp.py` is a minimal DevTools client for headless Edge.
- `web/`: client in plain ES modules. **No framework, no bundler, no npm.**
  - `app.js`: shell and pages
  - `editor.js`: canvas editor (zoom, selection, undo)
  - `view.js`: `DisplayView`, which renders a display as SVG and binds dynamics in run mode
  - `objects.js`: object type registry (defaults, inspector props, render)
  - `panels.js`: side panels and inspector
  - `expr.js`, `net.js`, `trend.js`, `historian.js`, `tables.js`, `dialogs.js`, `util.js`
  - Styles are in `web/css/app.css`.
- `projects/Demo/`: dev project. The base comes from `tools/make_demo.py`; `displays/imported/` and `images/` come from the GraphWorX importer.
- `tools/`: `build_exe.py`, `make_demo.py`, `make_icon.py`. `tests/`: script-style tests.
- `build/`, `dist/`: PyInstaller output. Don't edit these.

## Commands (repo root, venv Python 3.12)

```
.venv\Scripts\python -m noelle projects\Demo [--no-browser]     run from source
.venv\Scripts\python -m tests.test_drivers                       driver tests
.venv\Scripts\python -m tests.test_importers                     importer tests
.venv\Scripts\python -m noelle render projects\Demo displays/Main.nll out.png
.venv\Scripts\python tools\build_exe.py                          -> dist\Noelle.exe
```

## Things to know

- The user normally runs `dist\Noelle.exe`, which bundles `web/` and `noelle/`. Changes only reach the exe after a rebuild. A running exe locks the file and makes the build fail: ask the user to close it, never kill it.
- A project is entirely plain text: `.nll` is JSON (`indent=2`, `\n` line endings), plus CSV. Keep it that way. Write project files through `Project.write_text` so a backup lands in `.history/`.
- Adding or changing an object type or property touches several files:
  - `web/js/objects.js` (`TYPES`)
  - `noelle/schema/nll-display.schema.json` (type enum and props)
  - `noelle/web_geometry.py`, the Python twin of `bbox`, if geometry changes
  - the importers, if they emit that type
- A runtime file outside `web/` (`.ps1`, `.json`, assets) needs an `--add-data` line in `tools/build_exe.py`, or the exe won't contain it.
- Frontend style is dense: one-line methods, short names, the `el()`/`svg()` helpers from `util.js`. Match the surrounding code.
- Editor zoom: `Editor.autoFit` (on by default and on every open) keeps the display fitted to the canvas through a `ResizeObserver`. Zooming by hand (Ctrl+wheel, +/−) turns it off; Fit or Ctrl+0 turns it back on.
- No PLCs are reachable in development, so `[PLC1] connect failed` / `[S7_1500] connect failed` is normal. Test with `sim:` and `mem:` refs.
- The historian is on the user's plant network and slow (~20 s per pen-day), so don't hammer it. Its URL lives only in the user's own `project.nll`. **Never write it (or any plant address) into tracked files.** The repo's Demo has `"historian": {"url": ""}`.
- Running Noelle through `__main__` (the exe, or `python -m noelle`) overwrites `lastProject` in `%LOCALAPPDATA%\Noelle\settings.json`. Back that file up before a test run and restore it afterwards, or the user's exe opens your scratch copy.
- The `gf` / `GF_` prefixes (`gf.aliasSet` localStorage key, `GF_SERVER_URL`, `.gfd`/`.gfp`) come from the old name. Keep them for compatibility.
