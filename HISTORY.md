# History

Newest first. One entry per change: what changed and why, the files touched, how it was verified, and whether `dist\Noelle.exe` still needs a rebuild.

## 2026-11-13

### Editor: displays fill the canvas by default
- **Problem:** the editor fit the display once, at the moment the file opened. If the window was still sizing itself at that moment, the display stayed tiny (20 %) in the corner, and resizing the window never re-fit it.
- **Now:** opening a display zooms it to fill the editor area and centers it. Fit mode follows window and panel resizes and switches back to the Editor page. Zooming by hand (Ctrl+wheel, +/−) leaves fit mode, and **Fit** or Ctrl+0 returns to it. The zoom is no longer rounded down to 5 % steps, and the padding around the display went from 40 px to 12 px.
- **Files:** `web/js/editor.js` (`autoFit`, `_fitZoom`, `ResizeObserver`), `web/css/app.css` (`.ed-scroller` grid + padding, `.ed-stage` margin auto).
- **Verified:** headless Edge on a copy of the Demo project. Main.nll filled the canvas at 1600×950 (78 %), 1100×700 (39 %) and 1000×650 (31 %), with no scrollbars. A manual zoom survived a resize, and Fit restored fit mode. The console stayed clean.
- **Exe:** rebuilt (`dist\Noelle.exe`, 24.8 MB). Smoke test: `Noelle.exe <copy of Demo> --server` served the new `editor.js`, and `/api/me` answered 200.

### Git
- Published as https://github.com/Begisso/Noelle (public, branch `main`). `dist/Noelle.exe` is committed, the same convention as Nilou.
- Kept out of git via `.gitignore`: plant-derived data (imported GraphWorX displays and their images, `.history/` with the original `.gdf` files, the ImportTest and MnemoImport projects), project runtime/logs/accounts, and the build folders.

### Docs and tooling for Claude
- Added `CLAUDE.md` (codebase notes), this `HISTORY.md`, and the project skill `.claude/skills/noelle/` with `scripts/ui_check.py`. The script runs a headless screenshot and console check against a throwaway copy of a project.

## 1.0.0 (baseline, before this log)

Reconstructed from the code. There is no earlier history.

- Editor and runtime for `.nll` displays (plain JSON with a schema for VS Code), symbols and faceplates with `<#Alias#>` parameters, popups, multi-window, kiosk mode.
- Direct addressing: Modbus TCP/RTU/RTU-over-TCP, Siemens S7, OPC UA, OPC DA (32-bit PowerShell bridge, tested with Cogent DataHub), DataHub TCP, simulator, memory and system tags, the HTTP historian. Tag database in `tags.csv`, global alias sets in `aliases.csv`.
- Alarms (analog and digital, history CSV, sound), data logger, recipes, security roles and audit trail, Python server scripts and JS display scripts (hot reload), diagnostics page.
- Historian viewer with hour-chunk cache (memory + SQLite), parallel pens, M4 downsampling.
- Importers: GraphWorX32 `.gdf` (native objects, polyline vectorization, identity test against GraphWorX renderings), WinCC/TIA Openness (SimaticML, S7 offset rules), Weintek tag CSV.
- Legacy projects (`project.gfp` / `*.gfd`) are converted to `.nll` on open, with the originals kept in `.history\legacy`.
- Single-file `Noelle.exe` (PyInstaller): `.nll` file association, reuse of a running instance, exit when idle.
- Measured: 2,000 live objects on 600 tags at 59.6 fps in headless Edge.
