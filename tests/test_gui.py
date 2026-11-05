"""Drive every tool through its main actions and exports, with file dialogs stubbed.

Run:  .venv\\Scripts\\python.exe tests\\test_gui.py
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import numpy as np  # noqa: E402

from labstudio.app import LabStudio  # noqa: E402
from labstudio import widgets  # noqa: E402
import scenarios  # noqa: E402

results, errors = [], []


def check(name, condition, detail=''):
    results.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


def pump(app, n=15):
    for _ in range(n):
        app.update()


def main(out):
    messages = []
    messagebox.showinfo = lambda *a, **k: messages.append(('info', a))
    messagebox.showerror = lambda *a, **k: messages.append(('error', a))
    messagebox.showwarning = lambda *a, **k: messages.append(('warning', a))
    messagebox.askyesno = lambda *a, **k: True
    save_as = {}
    filedialog.asksaveasfilename = lambda **k: save_as.pop('next', '')
    filedialog.askdirectory = lambda **k: str(out)
    widgets.Dialog.show = lambda self: (self.apply(), self.destroy(), self.result)[2]  # accept dialogs as filled in

    app = LabStudio('uvvis')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    pump(app)

    # ---- UV-Vis ----
    uv = app.views['uvvis']
    uv.load_paths(scenarios.uvvis_files())
    pump(app)
    check('UV-Vis: three spectra listed', len(uv.tree.get_children()) == 3)
    uv.tree.selection_set('1')
    uv.spectra[1]['export'] = False
    save_as['next'] = str(out / 'uv.svg')
    uv.export_figure()
    check('UV-Vis: SVG figure exported', (out / 'uv.svg').stat().st_size > 1000)
    save_as['next'] = str(out / 'uv.csv')
    uv.export_data()
    rows = (out / 'uv.csv').read_text(encoding='utf-8-sig').splitlines()
    names = {r.split(',')[0] for r in rows[1:]}
    check('UV-Vis: data export skips unticked spectra', len(names) == 2, ', '.join(sorted(names)))
    uv.mode.set('Raw absorbance')
    uv.update_plot()
    check('UV-Vis: raw mode labels axis', 'Absorbance' in uv.figure.axes[0].get_ylabel())

    # ---- XRD ----
    app.show('xrd')
    pump(app, 30)
    xrd = app.views['xrd']
    xrd.layers = []
    xrd.load_defaults(ROOT / 'demo-data' / 'xrd', shorten_labels=False)
    pump(app)
    check('XRD: demo layers loaded', len(xrd.layers) == 5, f'{len(xrd.layers)} layers')
    first = xrd.layers[0]['id']
    xrd.tree.selection_set(first)
    xrd.move(1)
    check('XRD: move down changes plot order', xrd.layers[1]['id'] == first and xrd.rendered['settings']['display_order'][1] == first)
    xrd.layers[-1]['visible'] = False
    xrd.apply_palette()
    colors = [l['color'] for l in xrd.layers if l['kind'] == 'sample']
    check('XRD: palette applied to samples', colors == xrd.palette[:len(colors)], ', '.join(colors))
    check('XRD: hidden layer excluded', len(xrd.rendered['cards']) == 2)
    xrd.vars['width_cm'].set('18')
    xrd.draw()
    check('XRD: width setting reaches figure', abs(xrd.rendered['inches'][0] * 2.54 - 18) < 1e-6)
    save_as['next'] = str(out / 'xrd.pdf')
    xrd.export_image()
    check('XRD: PDF exported', (out / 'xrd.pdf').read_bytes()[:5] == b'%PDF-')
    xrd.export_processed()
    data = out / 'xrd_processed_data'
    check('XRD: processed data exported', (data / 'processing.json').is_file() and len(list(data.glob('*.csv'))) == 4)
    xrd.layers[0]['style'] = {'font_size': 14, 'x_percent': 50, 'y_offset': 1, 'alignment': 'center'}
    check('XRD: label style draws', xrd.draw())
    xrd.pages.select(1)
    xrd.layers[-1]['visible'] = True
    xrd.run_match()
    check('XRD: phase matching ranks every visible card', xrd.match is not None and len(xrd.match['results']) == 3,
          xrd.match_summary.get()[:80] if xrd.match else '')
    check('XRD: peak table filled', len(xrd.peak_tree.get_children()) == len(xrd.match['assignments']) > 0)
    xrd.phase_vars['size'].set('8')
    xrd.broaden_on.set(True)
    check('XRD: Scherrer-broadened cards draw', xrd.draw() and all(c['kind'] == 'profile' for c in xrd.rendered['cards']))
    save_as['next'] = str(out / 'match.csv')
    xrd.export_match()
    check('XRD: match results exported', 'two_theta_deg' in (out / 'match.csv').read_text(encoding='utf-8-sig'))
    xrd.broaden_on.set(False)

    # ---- TEM ----
    app.show('tem')
    pump(app, 20)
    tem = app.views['tem']
    tem.sample()
    pump(app)
    check('TEM: sample calibrated', tem.report is not None and tem.report['bar_pixels'] == 251, tem.info.get())
    tem.vars['bar_nm'].set('100')
    tem.refresh_tem()
    r = tem.report
    check('TEM: new bar length recalculates pixels', r['bar_nm'] == 100 and abs(r['bar_pixels'] - 100 / r['nm_per_pixel']) <= 1,
          f"{r['bar_pixels']} px for 100 nm")
    tem.vars['inset_roi'].set('100,100,300,250')
    tem.vars['inset_enabled'].set('Yes')
    tem.vars['overlay_enabled'].set('Yes')
    tem.refresh_tem()
    check('TEM: inset and histogram overlay rendered', bool(tem.report.get('inset')) and bool(tem.report.get('distribution_overlay')))
    tem.refresh_distribution()
    check('TEM: distribution preview', tem.view == 'distribution' and 'N = 103' in tem.info.get())
    tem.zoom_preview(1.5)
    tem.fit_preview()
    original_choose = tem.choose_export
    tem.choose_export = lambda kind, batch=False: dict(directory=out, name='', formats=['png', 'svg'] if kind == 'tem' else ['png', 'pdf'],
                                                      report=True, files=list(tem.files) if batch else None)
    tem.export_tem()
    check('TEM: export PNG + SVG + report', (out / 'synthetic_demo_scalebar.png').is_file() and (out / 'synthetic_demo_scalebar.svg').is_file()
          and (out / 'synthetic_demo_scalebar_report.json').is_file())
    tem.export_distribution()
    check('TEM: distribution PNG + PDF', (out / 'size_distribution.png').is_file() and (out / 'size_distribution.pdf').is_file())
    tem.choose_export = original_choose
    options = tem.choose_export('tem', batch=True)  # real dialog object, auto-accepted
    check('TEM: export dialog builds and validates', options is not None and options['files'] == tem.files)
    save_as['next'] = str(out / 'project.json')
    tem.save_project()
    project = json.loads((out / 'project.json').read_text(encoding='utf-8'))
    check('TEM: project saved in original format', project['version'] == 1 and len(project['images']) == 1)

    # ---- language ----
    check('Default language is English', app.title().startswith('Lab Plot Toolbox') and tem.pages.titles[0] == 'Calibrate')
    check('English interface text', app.nav_buttons['tem'].cget('text').strip().endswith('Scale bar · Size'))
    app.change_language('zh')
    pump(app, 30)
    check('Switch to Chinese rebuilds the window', app.title().startswith('科研绘图工具箱') and '标尺' in app.nav_buttons['tem'].cget('text'))
    tem_zh = app.views['tem']
    tem_zh.sample()
    pump(app)
    check('Chinese interface works after rebuild', tem_zh.report is not None and tem_zh.info.get().startswith('新标尺'))
    app.views['uvvis'] if 'uvvis' in app.views else None
    import time
    started = time.time()
    app.show('xrd')
    pump(app, 10)
    check('XRD page appears quickly after a rebuild', time.time() - started < 10, f'{time.time() - started:.1f} s')
    app.views['xrd'].vars['spacing'].set('1.4')  # schedules a redraw 0.6 s later...
    app.change_language('en')  # ...but the view is rebuilt immediately
    import time
    end = time.time() + 1.2
    while time.time() < end:
        app.update()
    check('Language switch with a pending redraw raises no errors', not errors, errors[0][-200:] if errors else '')
    pump(app, 30)
    check('Switch back to English', app.title().startswith('Lab Plot Toolbox') and app.prefs.get('language') == 'en')

    # ---- sidebar and settings ----
    check('XRD is the first tool in the sidebar', list(app.nav_buttons)[0] == 'xrd')
    app.show('uvvis')
    end = time.time() + 0.5
    while time.time() < end:
        app.update()
    target = app.nav_buttons['uvvis']
    bar = app.nav_indicator
    centre = bar.winfo_y() + bar.winfo_height() / 2
    check('Accent bar glides to the selected tool', bar.winfo_ismapped() and app._indicator_job is None and
          abs(centre - (target.winfo_y() + target.winfo_height() / 2)) <= 2, f'{centre:.0f} px')
    app.toggle_sidebar()
    while app._sidebar_job is not None:
        app.update()
    pump(app, 5)
    check('Sidebar collapses to icons', not app.sidebar_expanded and all(not b.cget('text') for b in app.nav_buttons.values())
          and app.sidebar.winfo_width() < 120 * app.dpi, f'{app.sidebar.winfo_width()} px')
    check('Active tool still highlighted when collapsed', app.nav_buttons[app.current].cget('style') == 'NavIconActive.TButton')
    app.nav_buttons['tem'].invoke()
    pump(app, 5)
    check('Collapsed icons navigate', app.current == 'tem')
    app.toggle_sidebar()
    while app._sidebar_job is not None:
        app.update()
    pump(app, 5)
    check('Sidebar expands again', app.sidebar_expanded and app.prefs.get('sidebar_expanded') is True)
    app.open_settings()
    pump(app, 5)
    settings = app.settings_window
    settings.set_mode('dark')
    pump(app, 5)
    check('Settings window switches theme', app.theme.mode == 'dark' and app.prefs.get('theme') == 'dark'
          and settings.mode_buttons['dark'].cget('style') == 'SegActive.TButton')
    settings.set_mode('light')
    settings.destroy()

    # ---- appearance ----
    for mode, scale in (('dark', '125%'), ('light', '90%'), ('light', '100%')):
        app.theme.set(mode, scale)
        pump(app, 5)
    check('Theme and scale switching', app.theme.mode == 'light' and not errors)
    app.close()
    unexpected = [m for m in messages if m[0] in ('error', 'warning')]
    check('No error dialogs', not unexpected, str(unexpected[:2]))
    check('No callback exceptions', not errors, errors[0][-300:] if errors else '')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        import os
        os.environ['LOCALAPPDATA'] = folder  # keep the user's real preferences untouched
        os.environ['LABSTUDIO_DATA_DIR'] = folder  # (macOS uses ~/Library otherwise)
        os.environ['LABSTUDIO_NO_UPDATE_CHECK'] = '1'  # no network during tests
        try:
            main(Path(folder))
        except Exception:
            traceback.print_exc()
            results.append(('test run', False))
    failed = [n for n, ok in results if not ok]
    print(f'\n{len(results) - len(failed)}/{len(results)} passed')
    sys.exit(1 if failed else 0)
