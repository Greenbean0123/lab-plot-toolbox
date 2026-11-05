## Lab Plot Toolbox 0.1.6

**New: NMR tool.** Stacked Bruker NMR figures (1H, 13C, DEPT, 77Se …) with automatic referencing, integration and peak picking.

TEM scale bars and size distributions, stacked XRD patterns with phase identification, UV-Vis spectra and NMR spectra — one window.

### Downloads

| System | File |
|---|---|
| Windows 10 / 11 (64-bit) | `LabPlotToolbox-0.1.6-Windows-x64.exe` (single file) or `…-Windows-x64.zip` (folder, starts faster) |
| macOS 11+ on Apple silicon (M1–M4) | `LabPlotToolbox-0.1.6-macOS-arm64.dmg` |
| macOS 11+ on Intel | `LabPlotToolbox-0.1.6-macOS-x86_64.dmg` |

Every package was started once on its build machine and passed an automatic self-test of all four tools.

**macOS:** the app is not notarized by Apple. On first launch, right-click the app → **Open** → **Open**
(or System Settings → Privacy & Security → **Open Anyway**).

**Windows:** SmartScreen may warn about an unknown publisher; choose **More info** → **Run anyway**.

Installed 0.1.5 apps offer this version at startup (Settings → Updates).

### NMR

- Scan a folder of Bruker experiments; nucleus, pulse program (13C, DEPT-135, DEPT-90 …) and solvent are detected
- Automatic referencing to the solvent signal (the acetone-d5 quintet is recognised next to other singlets); DEPT borrows the
  referencing of the sample's 13C spectrum and inverted DEPT phases are corrected; 77Se keeps the TopSpin referencing, or click a
  peak to set its shift
- Stacked traces, broken axes (drag across a region to cut it out), colour windows that colour peaks only, letter labels that
  appear only on resolved peaks
- Drag labels and traces in the preview, Ctrl+wheel scales a trace, undo / redo; labels that touch a spectrum or each other are
  marked
- Automatic integration ranges with multiplicity and J, relative integrals in nuclei or mmol against an internal standard,
  integral values and curves on the figure, CSV table for all traces; automatic peak list
- Normalisation per segment, common to all traces, or to a reference peak; colour-blind-safe palette; settings saved as JSON
- Structures with highlighted atoms when RDKit is installed (optional)

### Also in this toolbox

- XRD: stacked patterns with reference cards, multi-phase matching, Scherrer size
- TEM: automatic scale-bar reading, inset, size distribution
- UV-Vis: three normalization modes
- English / Chinese interface, light / dark theme, signed automatic updates
