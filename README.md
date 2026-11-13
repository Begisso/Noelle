<p align="center"><img src="web/img/noelle-logo.svg" width="120" alt="Noelle logo"></p>

# Noelle HMI

An HMI/SCADA graphics editor and runtime in the spirit of GraphWorX: direct addressing (Modbus, Siemens S7, OPC UA, OPC DA / Cogent DataHub), tag database, aliases, symbols/faceplates, alarms, trends, a viewer for your HTTP historian, Python + JavaScript scripting edited in VS Code, and importers for GraphWorX32, WinCC (TIA Openness) and Weintek. Every file is plain text (JSON / CSV), so you can read and edit it in Notepad or VS Code and diff it in git.

## Start

**Noelle.exe** (single file, no installation, no Python needed): `dist\Noelle.exe`

| | |
|---|---|
| double-click `Noelle.exe` | opens the last project (first start creates `Documents\Noelle\Demo`) |
| `Noelle.exe D:\Plant` | opens a project folder |
| `Noelle.exe D:\Plant\displays\Main.nll` | opens a display in the editor (finds its project) |
| `Noelle.exe --register` | double-clicking any `.nll` file opens it in Noelle (current user; `--unregister` undoes it) |
| `Noelle.exe D:\Plant --kiosk` | operator runtime only |
| `Noelle.exe D:\Plant --server --host 0.0.0.0` | runtime server without window; other PCs open `http://<pc>:8800` (turn on security first) |
| `Noelle.exe new D:\NewPlant` | empty project |

Closing the last Noelle window stops the program; starting it again for a project that is already open just opens another window. Logs: `%LOCALAPPDATA%\Noelle\noelle.log`.

From source: `setup.bat` once, then `start.bat [project]` (same options, `python -m noelle ...`). Build the exe with `.venv\Scripts\python tools\build_exe.py`.

## Project files

| file | content |
|---|---|
| `project.nll` | settings, devices, historian URL, security |
| `displays/*.nll` | displays |
| `symbols/*.nll` | symbols / faceplates: displays with `<#Alias#>` parameters |
| `tags.csv` | tag database: address, scaling, units, format, alarms, logging (Excel friendly, `,` or `;`) |
| `aliases.csv` | global alias sets (`Set,Alias,Value`), switchable at runtime from the top bar |
| `scripts/*.py` / `scripts/*.js` | server Python scripts (hot reloaded) / display scripts (ES modules) |
| `recipes/*.csv` | recipes (`Tag,Value`) |
| `logs/` | data logger, alarm history, audit trail (daily CSV) |
| `.history/` | automatic backup of every saved file (restore: right-click a display → Version history) |

**`.nll` is Noelle's file type**: plain JSON, readable and editable in Notepad or VS Code (each project gets `.vscode/nll.schema.json`, so VS Code shows IntelliSense and validation). Edits made outside are picked up immediately: the editor reloads the display and scripts reload. Older projects (`project.gfp` / `*.gfd`) are converted to `.nll` automatically on open; the originals are kept in `.history\legacy`.

## Direct addressing (inside `{{ }}` in any expression)

```
sim:Sine(0,100,60)  sim:Ramp  sim:Bool(4)  sim:Walk(0,100,1)  sim:Random  sim:Count
mem:Speed  mem:Speed=50                     memory tags (retained in runtime/retain.json)
sys:Time sys:DateTime sys:AlarmsActive sys:AlarmsUnacked sys:Driver.<dev>.Connected
modbus:PLC1/40001    modbus:PLC1/40010:F    modbus:PLC1/40020.3    modbus:PLC1/00005
   0xxxx coil 1xxxx DI 3xxxx IR 4xxxx HR (or C1/DI1/IR1/HR1, 6-digit forms)
   :I :U :DI :DU :F :D :I64 :U64 :S20  :SW word swap  :BS byte swap  @1000 scan rate
modbus:[192.168.1.10:502,1]40001            no device config needed
modbus:[COM3:9600:8N1,5]40001               RTU serial          [host:port,unit,rtu] RTU over TCP
s7:PLC2/DB10.DBD4   s7:PLC2/DB10.DBX0.1   s7:PLC2/MW10   s7:PLC2/E0.0 (or I0.0)   s7:PLC2/DB1.DBB20:STRING30
   :INT :WORD :DINT :DWORD :REAL :LREAL :TIME     s7:[192.168.0.1,0,1]DB1.DBD0
opcua:UA/ns=2;s=Channel1.Device1.Tag1       opcua:[opc.tcp://host:4840]ns=3;i=1001
opcda:[Cogent.DataHub.1]default:Tank1       OPC DA (DataHub, ICONICS, Kepware, WinCC, CoDeSys…)
dh:[127.0.0.1:4502]default:Tank1            DataHub TCP API (needs a DataHub API/TCP license)
hist:<description>                          last value from the HTTP historian
Tank1.Level                                 tag database name
{{<#Pump#>.Speed}}                          aliases resolve anywhere (local → display → global set)
```

Expressions are JavaScript, plus `iif`, `bit`, `scale`, `clamp`, `fmt`, `round`, `min`, `max`, … For example: `iif({{Pump1.Run}}, 'RUN', 'STOP')`. Expression tags in `tags.csv` (`Address` starting with `=`) use Python syntax and run on the server.

**Cogent DataHub on this PC:** the DataHub TCP API answers `No license`, so use OPC DA: `opcda:[Cogent.DataHub.1]domain:point`. That route was tested here (3457 items browsed). It runs through a 32-bit PowerShell bridge (`noelle/drivers/opcda_bridge.ps1`), so no extra install is needed.

## Editor (QoL)

- **Tag/address builder** (⚡ in the Tags panel, "…" next to any tag field, "+ Tag (builder)" on the Tags page): pick Siemens S7, Modbus TCP / RTU / RTU-over-TCP, OPC UA, OPC DA (DataHub, Kepware, ICONICS…), memory or simulator, type the IP and the address (DB/offset/type, register/type/word swap, NodeId…), see the live value, then use it or add it to the tag database. OPC UA and OPC DA servers can be browsed.
- **Placing objects:** drag tags from the tag browser onto the canvas to create a label and a value, or drop a tag on an object to bind it. Drop symbols and images from the project tree.
- **Zoom:** a display opens fitted to the editor area and stays fitted when the window resizes. Ctrl+wheel or −/+ zooms by hand; Fit (Ctrl+0) fits it again.
- **Moving and sizing:** smart guides plus grid snap (G); Shift constrains, Ctrl disables snapping, Alt+drag duplicates, arrow keys nudge (Shift = grid step).
- **Selection and lines:** multi-select, marquee (Alt = touch), Tab cycles objects. Resize handles have aspect lock; rotate in 15° steps. Vertex editing: Ctrl+double-click inserts a point, Alt+click removes one.
- **Arranging:** align, distribute, same size, flip, z-order, group/ungroup, lock, hide, layers list with filter.
- **Clipboard:** undo/redo (300 steps). Copy/paste goes through the system clipboard as JSON, so you can paste objects into VS Code and back.
- **Find & replace** across every property. Replacing `Pump1` with `<#Pump#>` turns a screen into a symbol; **Make symbol** creates the symbol file and an instance.
- **Inspector:** live value preview next to every expression, plus dynamics (fill/line/text color, visibility, blink, text, move, rotate, size, level fill, opacity, enable) and actions (write, toggle, data entry, open display/popup/window, script, Python, alias set, ack, recipe, URL, login).
- **Working the file:** F5 test-runs the unsaved display with live data, Ctrl+S saves, and "{ } VS Code" opens the file.

## Runtime and other features

- **Displays:** popups and faceplates with aliases, navigation history, multi-window, kiosk mode. A numeric keypad handles data entry with min/max limits and confirmation.
- **Quality:** objects show bad quality with a red dashed outline; hover or the tag browser shows the reason.
- **Alarms:** HiHi/Hi/Lo/LoLo with deadband, plus digital alarms. Banner, viewer, acknowledge, sound (mute with 🔔 in the status bar, remembered per PC), daily history CSV.
- **Security and audit:** roles viewer, operator, engineer and admin (`python -m noelle adduser PROJECT NAME ROLE`, then set `"security":{"enabled":true}`). Every write, save, login and acknowledgement goes to the audit CSV.
- **Data:** data logger (`Log` column: `change` or a period in ms), recipes, and live backfill of real-time trends from the server's history.
- **Diagnostics page:** per device it shows scans, round-trip time, errors, and blocks/requests per scan.

## Historian viewer

The viewer works against the HTTP historian set in `project.nll` (`"historian": {"url": "http://<host>:<port>"}`). It uses `/get_groups`, `/get_subgroups`, `/get_descriptions`, `/search_tree` and `/chart_data`. It has a lazy tree, server-side search, pens with colors, quick ranges, a custom range, follow-now, wheel zoom, drag pan, Shift+drag box zoom, a cursor readout, min/max/avg statistics, and CSV/PNG export. It also appears as the *Historian trend* object inside displays.

The upstream server is slow for older data (measured about 20 s per pen-day). Noelle mitigates that:

- Data is cached in **hour chunks**. Completed hours are also persisted to `runtime/histcache.sqlite`, so they are instant even after a restart.
- Requests are **deduplicated per chunk**, and pens are fetched **in parallel**.
- The page shows each pen as soon as it arrives.
- Data is **downsampled per pixel column** on the server (first/min/max/last), so a year of data still sends only about 4 points per pixel.

## Importers (page *Import* with 📂 Browse buttons, or CLI)

```
python -m noelle import gwx PROJECT D:\screens\*.gdf [--tagmap "Old=New;…"] [--mode native|hybrid]
python -m noelle import tia PROJECT D:\plant\plant.ap17 [--device HMI_1] [--plc "HMI_Connection_1=S7_1500"]
python -m noelle import simatic-xml PROJECT D:\openness_export
python -m noelle import weintek PROJECT D:\tags.csv [--devicemap "MODBUS TCP/IP=PLC1"] [--prefix WT_]
python -m noelle render PROJECT displays/x.nll out.png [--compare reference.png]
```

**GraphWorX32 (.gdf → .nll):** converts every object into native, editable Noelle objects, as close to the original as possible. No screenshots are imported.

- Drives a private GraphWorX32 instance through COM. A watchdog answers "save changes?" with No, the source files are never saved, and your own GraphWorX windows are not touched.
- Objects are enumerated by hit-testing and "peeling" (`GetHeadObject()` is empty at display level); the stacking order comes from the measured occlusion between objects.
- Rectangles, ellipses, arcs, text, buttons, symbols/groups, colours, fonts, 3D frames and all dynamics are converted directly. Rotated objects are un-rotated from GraphWorX's bounding box.
- **Polylines and polygons:** the API exposes no vertices, so each one is rendered alone on a transparent background and vectorized (outline contour or line skeleton) into real points; concave shapes and multi-segment lines are handled.
- **Images stay images only where the .gdf really contains raster content:** embedded bitmaps, metafiles, OLE controls and pattern/bitmap brushes (e.g. the "fish scale" bunker fill), each captured individually with transparency.
- **ICONICS global aliases** `<<Name>>` become Noelle aliases `<#Name#>`; define their values in `aliases.csv`.
- **Identity test:** GraphWorX's own rendering of each display (`ExportBitmapCaptureOfView`) is used only as a reference in `runtime\identity\` and compared with Noelle's rendering; `<name>.diff.png` marks differences in red. Every data source is checked to be converted, approximated or unsupported.
- **Results (native conversion, everything editable):** your displays Temper 98.3, shixta 95.7, BunkerLevel 94.3, cupper 93.5, floc_17 83.2; 518 of 519 bindings converted. GENESIS32 samples: 88.5, 83.6, 75.2. The main remaining gaps are gradient fills (the API does not expose them, so they become flat colours unless detected as a pattern) and small font metric differences.
- **Known limits:** VBA becomes a script stub; pick "popup menus" keep only their first item; "Animator" frame animations become the *Animate frames* dynamic.
- `--mode hybrid` (CLI only) additionally bakes static artwork into a background image, if pixel identity matters more than editability.

**WinCC / TIA Portal (Openness):**

- Copies the project to a temp folder, opens the copy without UI, and exports screens, templates, HMI tags, connections, PLC tag tables and data blocks as SimaticML. Then it converts objects, tag connections, appearance, visibility and click events.
- **Symbolic addressing warning:** a Noelle S7 tag needs an absolute address. Absolute PLC tags (%M, %I, %Q, %DB…) and members of *standard-access* DBs, whose offsets are computed, convert directly. Tags in **optimized DBs or with symbolic access have no absolute address**. They are imported as OPC UA references (`opcua:<PLC>_UA/ns=3;s="DB"."Member"`, S7-1500 OPC UA server) and listed first in the warnings. Either enable the PLC's OPC UA server, or switch off "Optimized block access" for the DB and re-import.
- **Offsets for standard-access DBs** follow the S7 rules: bools packed, even alignment for multi-byte types, structs and arrays. UDTs are exported and expanded, system types (HW_*, DTL…) have their documented sizes. Tested on your real projects: 27,159 of 27,161 members resolved across 6 PLCs. Offsets that depend on the documented IEC_TIMER/IEC_COUNTER sizes are listed as **CHECK OFFSET** warnings.
- It needs the Windows group "Siemens TIA Openness" (you are in it). TIA may ask to allow Openness access the first time. Your local TIA projects contain only PLCs (no WinCC HMI), so screen conversion is so far tested on SimaticML fixtures only.

**Weintek EasyBuilder Pro:** imports the *Address Tag Library* exported as CSV, mapping LW/LB, Modbus 0x/1x/3x/4x, and Siemens DB/M/I/Q addresses. Project files (.emtp/.cmtp) are a closed binary format, so screens cannot be imported.

## Performance design

- **Server:** a tag is polled only while something uses it (ref-counted, 5 s grace on detach). Modbus reads merge into blocks with gap read-through and optional pipelining. S7 reads go into multi-item PDUs, so one request per scan is typical. OPC UA uses subscriptions; OPC DA reads the server cache. Deadband filtering happens once in the engine. WebSocket deltas are coalesced per client (default 100 ms) and the sender is backpressure-aware. orjson is used when installed.
- **Browser:** each tag is subscribed once per tab. Expressions are compiled once and cached, and updates go out in one batch per animation frame. The DOM is written only when a value changes, blinking and pipe flow are pure CSS, and trends draw on canvas with per-pixel min/max decimation. Thousands of OPC items stay fast in the tag browser through virtual scrolling. Editor drags re-render only the objects being edited.

**Measured** (`displays/Stress.nll`: 2,000 live objects on 600 tags updating 4×/s, headless Edge): display ready in 0.37 s, steady 59.6 fps, ~1,900 value updates/s delivered in ~4 WebSocket messages/s, a full repaint of all 2,000 bindings takes 9.6 ms, 6 MB JS heap. Server: ~3% of one CPU core and 50 MB RAM.

## Tests

```
.venv\Scripts\python -m tests.test_drivers      Modbus (fake slave) + S7 (snap7 server) + address parsing
.venv\Scripts\python -m tests.test_importers    Weintek mapping, GraphWorX converter
```
