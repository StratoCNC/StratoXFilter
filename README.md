# StratoXFilter — a directional G-code correction post-processor for CNC

[![License: MIT](https://img.shields.io/badge/license-MIT-2E7D32.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows-3D5A80.svg)
![Python](https://img.shields.io/badge/python-3-3776AB.svg?logo=python&logoColor=white)
![Axes](https://img.shields.io/badge/axes-X_Y_Z-3D5A80.svg)
![Status](https://img.shields.io/badge/status-stable-2E7D32.svg)

**StratoXFilter** compensates the *nonlinear, direction-dependent positional
error* of a CNC machine by warping the G-code of a job against a measured error
map. It turns parts that come out a millimetre off into parts that match the
drawing — without touching your CAM or controller.

It was written for a large-format rack-and-pinion router (StratoCNC, ~1530 ×
1530 mm, UCCNC + UC300ETH), but it is **machine- and CAM-agnostic**: measure
your machine, build a map, load it, done.

> ⚠️ **Safety:** this tool rewrites CNC G-code. Always verify with a dry run /
> air cut before cutting real material. See `LICENSE` for the full disclaimer.

---

## What it does
- **Directional position correction** on X, Y and/or Z from a measured error
  map (two curves per axis — one per travel direction).
- **Optional backlash compensation**, two values per axis (per direction).
- **Arc → polyline** splitting by a fixed chord tolerance (corrected arcs with
  unequal end offsets break some controllers; short line segments do not).
- **Single-move (MDI) helper** — get the corrected coordinate for a manual
  position move, with or without backlash.
- **Map builder** — turn a measurement table (`.xlsx` or `.csv`) straight into
  an error-map CSV (median across passes), so you never hand-write a map.
- **Automatic self-check** on every run (verifies untouched axes, sane
  correction magnitudes, chord tolerance, structure).
- Absolute coordinates (G90). Non-coordinate words (F, S, comments) preserved.
  Z is untouched unless you enable Z correction.

## Why it exists
Most CNC machines have a smooth, repeatable, **position-dependent** error that
is **not** backlash and **not** a linear scale error — so neither backlash
compensation nor steps-per-mm calibration removes it. It is also often
**different in each travel direction**. A measured map + directional correction
is the only thing that actually cancels it. See the companion article
(`ARTICLE_EN.md`) for the full story.

## Quick start (GUI)
1. Run `StratoXFilter.exe` (Windows, standalone — no Python needed).
2. **Input .TAP** → Browse. Output name is filled in automatically.
3. Tick the axis/axes to correct. For each: pick a **map CSV**, set the work
   zero anchor, and optionally the backlash values (0 = off).
4. **Filter →** writes the corrected file and runs the self-check.
5. **Single move…** opens the MDI helper; **Help** shows the built-in manual.

The X map is bundled as a default example. Swap in your own maps — no rebuild
needed.

## Error-map format
CSV, one file per axis, same layout:
```
commanded_mm, error_pos_mm, correction_pos_mm, error_neg_mm, correction_neg_mm
```
- `pos` = increasing coordinate (X: right, Y: forward, Z: up)
- `neg` = decreasing coordinate (X: left, Y: back, Z: down)
- `correction = -error`; applied as `out = commanded + correction`
- Nodes: at least 2, **strictly ascending and unique**. Step is free and may
  differ per axis. Values between nodes are linearly interpolated; beyond the
  ends, linearly extrapolated.

Zero-filled templates: `measurments/Y_axis_error_maps_TEMPLATE.csv`,
`measurments/Z_axis_error_maps_TEMPLATE.csv`.

## Building a map from your measurements
You don't have to write the CSV by hand. Record your scale readings in a
spreadsheet, then **Map builder…** turns it into a map:
- The table needs a header row with a `commanded` column plus pass columns
  named `pos*` / `right*` / `fwd*` / `up*` (+ direction) and `neg*` / `left*` /
  `back*` / `down*` (− direction). Any number of passes per direction.
- Any number of passes per direction, any step (uniform or not), any range.
- Pick how the cells are interpreted:
  - **Measured positions** — absolute positions on the axis.
  - **Raw scale readings (zeroed each pass)** — the scale value as read; the
    builder computes `measured = anchor + scale` (anchor = the pass start:
    lowest commanded for + passes, highest for −). Handy for the − direction
    when you can't preset the scale.
  - **Errors** — already `measured − commanded`.
- It takes the **median** across passes per node, writes the map, and can load
  it straight into an axis.
- Reads `.xlsx` directly (no Excel or extra libraries) or `.csv`; extra sheets
  (e.g. a READ ME tab) are skipped automatically.
- See the worked example (with a READ ME tab):
  `measurments/measurement_template_EXAMPLE.xlsx`.

## Command line (automation)
The same executable runs headless when given arguments:
```
StratoXFilter.exe "in.TAP" -o "out.TAP" [options]
```
Options: `--xmap --ymap --zmap`, `--no-x --enable-y --enable-z`,
`--x0 --y0 --z0`, `--xb-right --xb-left --yb-fwd --yb-back --zb-up --zb-down`,
`--chord-tol --min-seg --deadband`, `--plot`. Use absolute paths.

## Running from source / building
- Requires Python 3. Core has no third-party dependencies; `--plot` needs
  `numpy` + `matplotlib`.
- GUI: `python gui.py`  · CLI: `python stratofilter.py in.TAP`
- Build the exe: `pip install pyinstaller` then run `build.bat`.

## Files
| File | Purpose |
|---|---|
| `stratofilter.py` | core (parser, maps, arc splitter, correction, self-check) |
| `gui.py` | tkinter GUI + embedded help + single-move helper |
| `build.bat` | one-click PyInstaller build |
| `measurments/*.csv` | error maps (X example + Y/Z templates) |
| `ИНСТРУКЦИЯ.md` | user manual (Bulgarian) |
| `ARTICLE_EN.md` / `ARTICLE_BG.md` | the write-up (problem → diagnosis → fix) |

## License
MIT — see `LICENSE`. Free to use, modify and redistribute; keep the copyright
notice. No warranty.

Author: Ivan Topurov.
