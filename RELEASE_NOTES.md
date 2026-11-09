## Lab Plot Toolbox 0.1.7

**New look, and smoother switching.** A calmer, lighter interface, and tools and tabs now answer the click at once.

TEM scale bars and size distributions, stacked XRD patterns with phase identification, UV-Vis spectra and NMR spectra — one window.

### Downloads

| System | File |
|---|---|
| Windows 10 / 11 (64-bit) | `LabPlotToolbox-0.1.7-Windows-x64.exe` (single file) or `…-Windows-x64.zip` (folder, starts faster) |
| macOS 11+ on Apple silicon (M1–M4) | `LabPlotToolbox-0.1.7-macOS-arm64.dmg` |
| macOS 11+ on Intel | `LabPlotToolbox-0.1.7-macOS-x86_64.dmg` |

Every package was started once on its build machine and passed an automatic self-test of all four tools.

**macOS:** the app is not notarized by Apple. On first launch, right-click the app → **Open** → **Open**
(or System Settings → Privacy & Security → **Open Anyway**).

**Windows:** SmartScreen may warn about an unknown publisher; choose **More info** → **Run anyway**.

Installed 0.1.6 apps offer this version at startup (Settings → Updates).

### Interface

- Warm neutral greys and near-black text; blue now marks only selection and focus, and the main action button is dark
- Light sidebar; the current tool sits on a raised white pill
- Borderless filled buttons and fields, flat cards with a hairline edge instead of a drop shadow
- Figure previews sit on a plain sheet with a thin outline (no shadow)
- Dark theme reworked in the same palette

### Smoother

- Switching tools or tabs responds immediately instead of after the highlight animation
- No more ghost of the previous page while the new one draws
- Tab switches are several times faster: pages stay laid out and are brought to the front instead of being rebuilt
- Collapsing and expanding the sidebar slides instead of resizing, at a much higher frame rate
- Resizing the window no longer redraws the figures of hidden tools; each one refits when you open it

### Also in this toolbox

- XRD: stacked patterns with reference cards, multi-phase matching, Scherrer size
- TEM: automatic scale-bar reading, inset, size distribution
- UV-Vis: three normalization modes
- NMR: stacked Bruker spectra with automatic referencing, integration and peak picking
- English / Chinese interface, light / dark theme, signed automatic updates
