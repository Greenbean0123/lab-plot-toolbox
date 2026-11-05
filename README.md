# Lab Plot Toolbox

TEM scale bars and particle-size distributions, stacked XRD patterns with phase identification, and UV-Vis spectra — in one
desktop app for Windows and macOS. English and Chinese interface, light and dark theme.

Lab Plot Toolbox combines three earlier standalone programs —
[TEM-graph-tool](https://github.com/Greenbean0123/TEM-graph-tool), [xrd-plotter](https://github.com/Greenbean0123/xrd-plotter)
and [uvvis-plotter](https://github.com/Greenbean0123/uvvis-plotter) — into a single app.

## Download

Get the latest version from [Releases](https://github.com/Greenbean0123/lab-plot-toolbox/releases/latest):

| System | File |
|---|---|
| Windows 10 / 11 (64-bit) | `LabPlotToolbox-<version>-Windows-x64.exe` (single file) or `…-Windows-x64.zip` (folder, starts faster) |
| macOS 11+ on Apple silicon (M1–M4) | `LabPlotToolbox-<version>-macOS-arm64.dmg` |
| macOS 11+ on Intel | `LabPlotToolbox-<version>-macOS-x86_64.dmg` |

- **macOS:** the app is not notarized by Apple. On first launch, right-click the app → **Open** → **Open**
  (or System Settings → Privacy & Security → **Open Anyway**).
- **Windows:** SmartScreen may warn about an unknown publisher; choose **More info** → **Run anyway**.
- The `…-XRD-Plotter-update.zip` files are only used by XRD Plotter's built-in updater; you don't need them.

Every package is started once on its build machine and passes a self-test of all four tools (`--self-test`).

## Updates

The app checks for new versions at startup and asks before installing (Settings → Updates). Updates are signed
(Ed25519) and checked by size and SHA-256 before anything is replaced; a failed update rolls back automatically.
Existing XRD Plotter installations on Windows and macOS are offered Lab Plot Toolbox through XRD Plotter's own updater.

## Features

| Tool | What it does |
|---|---|
| **XRD** | Stack sample patterns with reference cards (PDF / CIF / XY / CSV); drag to reorder and toggle layers; label styles, palettes, line widths; export figures and processed data; auto-load a startup folder |
| **XRD phase ID** | Multi-phase matching with (hkl) assignment; Scherrer crystallite size; draw reference cards as nanocrystal-broadened peaks for a chosen size |
| **TEM** | Read and recalibrate the original scale bar automatically; draggable new scale bar; zoomed inset; diameter histogram with Gaussian fit, optionally overlaid on the image; batch export; project files |
| **UV-Vis** | Import multiple spectra; three normalization modes; export figures and data |
| **NMR** | Scan a folder of Bruker experiments (1H, 13C, DEPT, 77Se …); automatic solvent referencing and DEPT phase check; stacked traces with broken axes, colour windows and letter labels; drag labels and traces in the preview; automatic peak picking and integration ranges with multiplicity, relative integrals (nuclei or mmol against an internal standard) and CSV export; overlap warnings; settings saved as JSON |

The calculations reuse the original projects' code; `tests/test_equivalence.py` compares the output with the original
programs pixel by pixel.

## Run from source

On Windows, double-click **`启动.bat`** (the first run creates `.venv` and installs dependencies). Or manually:

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py          # optionally pass xrd | tem | uvvis
```

Requires Python 3.9+ with Tkinter. Runtime dependencies are about 150 MB; SciPy is not needed.

## Build

```sh
pip install -r requirements-build.txt
python packaging/build.py            # Windows: single exe; macOS: .app → .zip + .dmg
python packaging/build.py --onedir   # Windows: folder version as a zip
```

Every push to `main` is tested, packaged and self-tested by GitHub Actions on Windows, macOS (Apple silicon) and
macOS (Intel). Pushing a `v*` tag that matches the version in `labstudio/paths.py` publishes a release.

## Tests

```sh
python tests/test_gui.py          # interface actions and exports
python tests/test_phase.py        # phase matching and Scherrer size
python tests/test_nmr.py          # NMR reader, referencing and preview interactions
python tests/test_i18n.py         # every interface string has an English translation
python tests/test_updates.py      # signed updates, install and rollback
pip install -r requirements-dev.txt
python tests/test_numerics.py     # compare with SciPy
python tests/test_equivalence.py  # compare with the original programs (needs them in sibling folders)
```

## Language and settings

English by default. The gear in the bottom-right corner (or `Ctrl+,`) opens Settings: theme, interface size, language
and updates. Preferences are stored in `%LOCALAPPDATA%\LabPlotStudio` (macOS: `~/Library/Application Support/LabPlotStudio`),
never in the program folder.

Shortcuts: `Ctrl+O` open · `Ctrl+S` export · `F5` refresh preview · in the TEM preview, scroll to zoom and right-drag to pan

MIT License.
