"""Scripted UI scenarios with synthetic data, used by snapshot.py for visual checks."""
import tempfile
from pathlib import Path

import numpy as np

TMP = Path(tempfile.gettempdir()) / 'labstudio-scenarios'
TMP.mkdir(exist_ok=True)


def uvvis_files():
    paths = []
    x = np.arange(300, 1400, 2.0)
    for i, (center, width) in enumerate([(520, 60), (640, 90), (780, 120)]):
        y = 0.08 + 0.9 * np.exp(-((x - center) / width) ** 2) * (1 + i * .3) + 0.15 * np.exp(-(x - 300) / 80)
        path = TMP / f'Sample {chr(65 + i)} spectrum.csv'
        path.write_text('Wavelength,Abs\n' + '\n'.join(f'{a},{b:.5f}' for a, b in zip(x, y)), encoding='utf-8')
        paths.append(str(path))
    return paths


def run(name, app, view):
    if name == 'uvvis':
        view.load_paths(uvvis_files())
    elif name == 'uvvis-range':
        view.load_paths(uvvis_files())
        view.mode.set('Maximum in wavelength range')
        view.update_plot()
    elif name.startswith('xrd-phase'):
        view.layers = []
        import os  # folder with real patterns and cards, if available on this machine
        view.load_defaults(Path(os.environ.get('LABSTUDIO_XRD_DATA', 'demo-data/xrd')))
        view.pages.select(1)
        report = []
        for choice in view.sample_choices():
            view.match_sample.set(choice)
            view.run_match()
            report.append(view.match_summary.get())
            report += [f"  {r['label']}: score {r['score']:.2f}, found {r['lines_found']}/{r['lines']}, "
                       f"explained {r['explained']:.2f}, shift {r['shift']}" for r in view.match['results']]
            report += [f"    {a['two_theta']:.2f}  fwhm {a['fwhm']:.3f}  size {s:.1f} nm  -> "
                       + (f"{a['matches'][0]['label']} {a['matches'][0]['hkl']}" if a['matches'] else 'UNASSIGNED')
                       for a, s in zip(view.match['assignments'], view.match['sizes'])]
        if name == 'xrd-phase-broad':
            view.phase_vars['size'].set('12')
            view.broaden_on.set(True)
            view.draw()
        (TMP / 'phase_report.txt').write_text('\n'.join(report), encoding='utf-8')
    elif name == 'settings':
        view.load_paths(uvvis_files())
        if app.sidebar_expanded:
            app.toggle_sidebar()
        app.open_settings()
    elif name == 'xrd-pale':
        from labstudio.paths import DEMO_DATA
        view.layers = []
        view.load_defaults(DEMO_DATA / 'xrd', shorten_labels=False)
        view.layers[0]['color'] = '#fff27a'  # pale yellow: nearly invisible on white without the chip ring
        view.layers[2]['visible'] = False
        view.draw()
    elif name.startswith('xrd-'):
        from labstudio.paths import DEMO_DATA
        if name != 'xrd-real':
            view.layers = []
            view.load_defaults(DEMO_DATA / 'xrd', shorten_labels=False)
        view.pages.select({'xrd-figure': 1, 'xrd-colors': 2, 'xrd-startup': 3}.get(name, 0))
    elif name in ('tem-sample', 'tem-scale', 'tem-size', 'tem-inset', 'tem-dist', 'tem-overlay'):
        view.sample()
        page = {'tem-scale': 1, 'tem-size': 2, 'tem-dist': 2, 'tem-overlay': 2, 'tem-inset': 3}.get(name, 0)
        view.pages.select(page)
        if name == 'tem-dist':
            view.refresh_distribution()
        if name == 'tem-overlay':
            view.vars['overlay_enabled'].set('Yes')
            view.refresh_tem()
    app.update()
