---
name: noelle
description: Run, check and ship changes to Noelle HMI. Covers starting it from source, driving the editor/runtime in headless Edge with screenshots and console errors, running the tests, rebuilding dist\Noelle.exe, and logging the change in HISTORY.md. Use after changing anything in web/ or noelle/, or when asked to run, screenshot, test or build Noelle.
---

# Run, verify and ship Noelle

All commands run from the repo root (`d:\Projects\Noelle`) with the venv Python, `.venv\Scripts\python`.

## 1. See a UI change working (headless, the default)

```
.venv\Scripts\python .claude\skills\noelle\scripts\ui_check.py --size 1600x950,1100x700 --out <scratch dir> --eval "document.querySelector('.toolbar .zoom').textContent"
```

- It copies the project (default `projects\Demo`) to a temp folder, starts a Noelle server on a free port, opens the page in headless Edge, and runs each `--eval` at each size. It saves `shot_<W>x<H>.png` into `--out` and prints console errors and warnings. The exit code is 1 when the page logged errors.
- `--url` picks the page: `/?mode=edit&display=displays/X.nll` (editor), `/?mode=run&display=...` (runtime), `/?mode=historian`, `/?render=1&display=...` (bare 1:1 render).
- **Open the screenshots with Read and look at them.** A clean console doesn't prove the layout is right.
- For clicks, page switches or step sequences, write a short script in the scratchpad on top of `noelle.cdp.Browser` (`goto`, `eval`, `screenshot`, `click`, `send("Emulation.setDeviceMetricsOverride", ...)`) and `noelle.importers.visual._TempServer(project_copy)`. ui_check.py is the template.
- Lines like `[PLC1] connect failed` are expected, because no PLCs are reachable in development.
- Never point a check at the user's own projects (for example `Documents\Noelle\...`). Use a copy.

## 2. Run it for real

- From source: `.venv\Scripts\python -m noelle projects\Demo`. This opens an Edge `--app` window and quits about 20 s after the last window closes. Add `--no-browser` to only serve, then open the printed URL.
- Render one display to PNG: `.venv\Scripts\python -m noelle render projects\Demo displays/Main.nll out.png [--compare ref.png]`.

## 3. Tests

```
.venv\Scripts\python -m tests.test_drivers      Modbus fake slave, S7 via snap7 server, address parsing
.venv\Scripts\python -m tests.test_importers    Weintek, GraphWorX converter, SimaticML, vectorizer
```

These are plain scripts that print `ok` / `FAIL` lines. Run them when you touch `noelle/drivers`, `address.py`, `engine.py` or `importers/`. They don't cover `web/`, so use step 1 for that.

## 4. Rebuild Noelle.exe

The user normally runs `dist\Noelle.exe`, which bundles `web/` and `noelle/`. A change is invisible there until you rebuild.

1. Check that it isn't running: `Get-Process Noelle -ErrorAction SilentlyContinue`. If it is, ask the user to close it. **Never stop their process.** Their open window may hold unsaved edits.
2. `.venv\Scripts\python tools\build_exe.py`. This is PyInstaller one-file and takes a few minutes, so run it in the background.
3. A new runtime data file outside `web/` (`.ps1`, `.json`, assets) needs its own `--add-data` line in `tools/build_exe.py`.
4. `dist/Noelle.exe` is tracked in git, the only file from `dist/` that is. When the user asks for a commit, include the rebuilt exe with the source change that produced it.

## 5. Record it

Add an entry at the top of `HISTORY.md` under today's date. Say what changed and why, the files touched, how you verified it, and whether the exe still needs a rebuild. If user-visible behavior changed, update `README.md` too.
