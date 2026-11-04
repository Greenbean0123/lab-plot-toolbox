"""Self-test for packaged builds: `Lab Plot Studio --self-test report.json`.

Drives all three tools with synthetic data inside the frozen app, writes a JSON report and returns 0 on success.
Used on the build machines (Windows and macOS) so every published package has been exercised once.
"""
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(report_path):
    report = {'success': False, 'checks': [], 'platform': platform.platform(), 'machine': platform.machine(),
              'python': sys.version.split()[0], 'frozen': bool(getattr(sys, 'frozen', False))}
    work = Path(tempfile.mkdtemp(prefix='labstudio-selftest-'))
    os.environ['LABSTUDIO_DATA_DIR'] = str(work / 'data')  # never touch the user's preferences
    app = None

    def check(name, ok, detail=''):
        report['checks'].append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
        if not ok:
            raise RuntimeError(f'{name} failed: {detail}')

    def pump(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            app.update()

    try:
        from tkinter import messagebox

        def refuse(*args, **kwargs):  # a dialog would block an unattended run
            raise RuntimeError('Unexpected dialog: ' + ' / '.join(str(a) for a in args))
        messagebox.showerror = messagebox.showwarning = refuse
        messagebox.showinfo = lambda *a, **k: None
        messagebox.askyesno = lambda *a, **k: True

        import numpy as np
        from .app import LabStudio
        from .paths import APP_VERSION, DEMO_DATA
        report['version'] = APP_VERSION
        app = LabStudio('xrd')
        pump(1.0)
        check('window and theme', app.theme.elements_ready and app.current == 'xrd')

        # XRD: demo data, figure, exports, phase matching, Scherrer broadening
        xrd = app.views['xrd']
        xrd.layers = []
        xrd.load_defaults(DEMO_DATA / 'xrd', shorten_labels=False)
        pump()
        check('XRD demo data', len(xrd.layers) == 5, f'{len(xrd.layers)} layers')
        check('XRD figure', xrd.draw())
        from .xrd.xrd_core import make_figure, export_data
        r = xrd.rendered
        for fmt in ('png', 'pdf', 'svg'):
            path = work / f'xrd.{fmt}'
            make_figure(r['samples'], r['cards'], **r['settings']).savefig(path, format=fmt, dpi=150)
            check(f'XRD export {fmt.upper()}', path.stat().st_size > 1000)
        export_data(work / 'xrd_data', r['samples'], r['cards'], r['settings'])
        check('XRD processed data', (work / 'xrd_data' / 'processing.json').is_file())
        xrd.run_match()
        check('XRD phase matching', xrd.match is not None and xrd.match['results'], xrd.match_summary.get())
        xrd.broaden_on.set(True)
        check('XRD Scherrer broadening', xrd.draw() and all(c['kind'] == 'profile' for c in xrd.rendered['cards']))

        # TEM: synthetic image, calibration, inset + histogram overlay, exports
        app.show('tem')
        pump(0.6)
        tem = app.views['tem']
        tem.sample()
        pump()
        check('TEM calibration', tem.report is not None and tem.report['bar_pixels'] == 251, tem.info.get())
        tem.vars['inset_roi'].set('100,100,300,250')
        tem.vars['inset_enabled'].set('Yes')
        tem.vars['overlay_enabled'].set('Yes')
        tem.refresh_tem()
        check('TEM inset and overlay', tem.report.get('inset') and tem.report.get('distribution_overlay'))
        from .tem import tem_scalebar_distribution as core
        args = tem.get_args(out=work)
        args.tem_name, args.tem_formats = 'tem_check', ['png', 'tif', 'pdf', 'svg']
        result = core.redraw(tem.current, args)
        check('TEM export PNG/TIFF/PDF/SVG', len(result['output_files']) == 4, ', '.join(Path(f).name for f in result['output_files']))
        dist = tem.dist_args(work)
        dist.distribution_formats = ['png', 'pdf']
        stats = core.distribution(dist)
        check('TEM size distribution', stats['N'] == 103, f"mean {stats['mean_nm']:.2f} nm")

        # UV-Vis: synthetic spectra, normalization, exports
        app.show('uvvis')
        pump(0.6)
        uv = app.views['uvvis']
        x = np.arange(300, 1100, 2.0)
        paths = []
        for i, centre in enumerate((450, 620)):
            path = work / f'spectrum_{i}.csv'
            path.write_text('nm,abs\n' + '\n'.join(f'{a},{np.exp(-((a - centre) / 60) ** 2) + .05:.5f}' for a in x), encoding='utf-8')
            paths.append(str(path))
        uv.load_paths(paths)
        pump()
        processed = uv.update_plot()
        check('UV-Vis normalization', processed and all(abs(y.max() - 1) < 1e-9 for _, y, _ in processed))
        from matplotlib.figure import Figure
        figure = Figure(figsize=(6, 4), layout='tight')
        uv.draw_on(figure, 'export')
        figure.savefig(work / 'uv.svg')
        check('UV-Vis export', (work / 'uv.svg').stat().st_size > 1000)

        # Interface: language and theme switching
        app.change_language('zh')
        pump(0.5)
        check('Chinese interface', app.title().startswith('科研绘图工具箱'))
        app.change_language('en')
        app.set_theme('dark')
        pump(0.5)
        check('English interface, dark theme', app.title().startswith('Lab Plot Studio') and app.theme.mode == 'dark')
        report['success'] = True
    except Exception as error:
        report['error'] = str(error)
        report['traceback'] = traceback.format_exc()
    finally:
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return 0 if report['success'] else 1
