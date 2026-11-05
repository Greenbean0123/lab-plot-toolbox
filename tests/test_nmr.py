"""NMR tool: Bruker reader, referencing, figure geometry and the preview's mouse interactions.

Run:  .venv\\Scripts\\python.exe tests\\test_nmr.py
Real spectra are compared with nmrglue when both are available (set NMR_TEST_ROOT to a Bruker data folder).
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from labstudio.nmr import core  # noqa: E402

results = []


def check(name, condition, detail=''):
    results.append(bool(condition))
    print(('PASS ' if condition else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


def unit(tmp):
    # reader round trip and CDCl3 referencing (residual written at 7.20 -> shift +0.06)
    p = core.write_demo_bruker(str(tmp / 's1' / '1'), solvent='CDCl3', peaks=((7.20, 1.0, 0.003), (5.0, 0.5, 0.004)))
    sp = core.read_bruker(p)
    check('reader: ppm axis spans OFFSET to OFFSET - SW', abs(sp['ppm'][0] - 11.0) < 1e-9 and abs(sp['ppm'][-1] - (11.0 - 12.0 * (1 - 1 / sp['ppm'].size))) < 1e-9)
    check('reader: peak position', abs(sp['ppm'][np.argmax(sp['d'])] - 7.20) < 0.002)
    shift, note = core.auto_reference(p)
    check('CDCl3 referencing', abs(shift - 0.06) < 0.002, note)
    # acetone-d5 quintet next to a taller singlet: the quintet must win
    sf, j = 500.13, 2.2 / 500.13
    quint = tuple((1.92 + k * j, h, 0.0006) for k, h in zip((-2, -1, 0, 1, 2), (0.08, 0.16, 0.24, 0.16, 0.08)))
    p2 = core.write_demo_bruker(str(tmp / 's2' / '1'), solvent='Acetone', peaks=quint + ((1.878, 1.0, 0.0006),), n=65536)
    shift, note = core.auto_reference(p2)
    check('acetone-d5 quintet beats a taller singlet', abs(shift - 0.13) < 0.002, f'{shift:+.4f}')
    # 77Se: no solvent rule, TopSpin referencing kept
    p3 = core.write_demo_bruker(str(tmp / 's3' / '2'), nuc='77Se', solvent='Acetone', sw=800, offset=760, sf=95.4,
                                peaks=((302.8, 1.0, 0.5), (413.7, 0.6, 0.5)))
    shift, note = core.auto_reference(p3)
    check('77Se keeps TopSpin referencing', shift == 0.0, note)
    # DEPT-135 phased upside down: chain-end CH3 negative
    p4 = core.write_demo_bruker(str(tmp / 's4' / '3'), nuc='13C', solvent='Acetone', pulprog='deptsp135', sw=200,
                                offset=190, sf=125.7, peaks=((14.1, -1.0, 0.05), (23.0, 0.8, 0.05), (139.8, -0.5, 0.05)))
    check('DEPT-135 inverted phase detected', core.auto_flip(p4, 0.0))
    # peak runs colour peaks only, not the noise between them
    y = np.zeros(400)
    y[100:104] = 10
    y[300:303] = 8
    runs = core.peak_runs(y + np.random.default_rng(1).normal(0, 0.1, 400), np.ones(400, bool), 6 * 0.1, 0.1)
    check('peak runs: two separate peaks', len(runs) == 2 and all(r[1] - r[0] < 20 for r in runs), runs)
    check('break axis splits a segment', core.break_segment([[10, 0]], 0, 6, 3) == [[10, 6], [3, 0]])
    check('break axis drops slivers', core.break_segment([[10, 0]], 0, 10, 3) == [[3, 0]])
    # figure: labels exist, overlap check finds two labels placed on each other
    from matplotlib.figure import Figure
    doc = core.default_doc()
    doc['traces'] = [core.new_trace(p)]
    doc['segments'] = [[8, 4]]
    doc['assignments'] = [{'id': 'a', 'label': 'A', 'hi': 5.1, 'lo': 4.9, 'color': 'blue', 'sigma': 6},
                          {'id': 'b', 'label': 'B', 'hi': 5.1, 'lo': 4.9, 'color': 'pink', 'sigma': 6}]
    meta = core.render(doc, Figure(figsize=doc['figsize']))
    check('render: trace and labels drawn', meta['traces'] and len(meta['labels']) >= 3)
    check('overlap check reports stacked labels', any('overlaps' in i['msg'] for i in meta['issues']), meta['issues'][:1])
    doc['offsets'] = {'assign:b': [0, 30]}
    meta = core.render(doc, Figure(figsize=doc['figsize']))
    check('offset in points moves a label clear', not any('"A' in i['msg'] and '"B' in i['msg'] for i in meta['issues']), meta['issues'][:1])
    integration(tmp)


def integration(tmp):
    # singlet (area 1) and a 1:2:1 triplet with J = 7 Hz (area 2); Lorentzian area = pi * height * width
    sf, j, w = 500.13, 7.0 / 500.13, 0.0015
    peaks = ((3.0, 1.0, w), (1.0 + j, 0.5, w), (1.0, 1.0, w), (1.0 - j, 0.5, w))
    p = core.write_demo_bruker(str(tmp / 's5' / '1'), solvent='CDCl3', peaks=peaks + ((7.26, 0.3, w),), n=65536, noise=0.0005)
    ppm, d, sig, sf_ = core.spectrum(p)
    ranges = core.auto_ranges(ppm, d, sig, sf_, window=(4, 0.5))
    check('auto ranges: singlet and triplet found', len(ranges) == 2, [(r['center'], r['mult']) for r in ranges])
    if len(ranges) == 2:
        s, t = ranges
        check('multiplicity: singlet', s['mult'] == 's', s['mult'])
        check('multiplicity: triplet with J = 7 Hz', t['mult'] == 't' and abs(t['J'] - 7.0) < 0.3, (t['mult'], t['J']))
        ratio = t['area'] / s['area']
        check('integral ratio triplet : singlet = 2', abs(ratio - 2) < 0.08, f'{ratio:.3f}')
        vals = core.relative_integrals([s['area'], t['area']], 0, 3.0)
        check('relative integrals in H (singlet = 3 H)', abs(vals[1] - 6.0) < 0.25, [round(v, 2) for v in vals])
        mm = core.relative_integrals([s['area'], t['area']], 0, 0.05, 'mmol', nuclei=[1, 2], ref_nuclei=1)
        check('amount mode: equal mmol per nucleus', abs(mm[1] - 0.05) < 0.003, [round(v, 4) for v in mm])
    ranges = core.auto_ranges(ppm, d, sig, sf_, window=(8, 6.5), solvent_ppm=7.26)
    check('solvent range flagged', ranges and ranges[0]['solvent'])
    # baseline: a constant offset does not change the integral
    base = integrate_offset(ppm, d)
    check('linear baseline removes a constant offset', base < 0.01, f'{base:.4f}')


def integrate_offset(ppm, d):
    a = core.integrate(ppm, d, 3.05, 2.95)
    b = core.integrate(ppm, d + 0.05 * d.max(), 3.05, 2.95)
    return abs(b - a) / abs(a)


def real_data():
    root = os.environ.get('NMR_TEST_ROOT', r'C:/Users/Ryanz/OneDrive - Vanderbilt/NMR/zhuoh1/501/nmr')
    try:
        import nmrglue as ng
    except ImportError:
        print('skip real-data comparison (nmrglue not installed)')
        return
    if not os.path.isdir(root):
        print('skip real-data comparison (no data folder)')
        return
    items, worst, worst_ppm = [], 0.0, 0.0
    for it in core.scan(root):
        if it['kind'] == 'offline' or len(items) >= 40:
            continue
        try:
            dic, d = ng.bruker.read_pdata(it['path'])
        except OSError:  # cloud-only file (OneDrive placeholder)
            continue
        items.append(it)
        ppm = ng.fileiobase.uc_from_udic(ng.bruker.guess_udic(dic, d)).ppm_scale()
        sp = core.read_bruker(it['path'])
        worst = max(worst, float(np.abs(sp['d'] - d).max()))
        worst_ppm = max(worst_ppm, float(np.abs(sp['ppm'] - ppm).max()))
    check(f'intensities identical to nmrglue on {len(items)} real spectra', worst == 0.0, f'max deviation {worst:g}')
    # nmrglue builds the axis from slightly different parameters; the difference is far below one data point
    check('ppm axis matches nmrglue within 1e-4 ppm', worst_ppm < 1e-4, f'{worst_ppm:.1e} ppm')


def gui(tmp):
    messagebox.showerror = lambda *a, **k: print('error dialog:', a)
    messagebox.askyesno = lambda *a, **k: True
    filedialog.asksaveasfilename = lambda **k: ''
    os.environ['LABSTUDIO_DATA_DIR'] = str(tmp / 'prefs')
    os.environ['LABSTUDIO_NO_UPDATE_CHECK'] = '1'
    from matplotlib.backend_bases import MouseEvent
    from labstudio.app import LabStudio
    errors = []
    app = LabStudio('nmr')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    import time
    end = time.time() + 15  # the shell opens the tool from a timer; wait for it, not for a number of updates
    while 'nmr' not in app.views and time.time() < end:
        app.update()
        time.sleep(0.01)
    for _ in range(10):
        app.update()
    view = app.views['nmr']
    view.folder.set(str(tmp))
    view.scan()
    check('scan lists the synthetic experiments', len(view.items) == 5, len(view.items))
    p = next(i['path'] for i in view.items if i['sample'] == 's1')
    view.add_trace(p)
    view.doc['segments'] = [[8, 4]]
    view.doc['assignments'] = [{'id': 'a', 'label': '=CH$_2$', 'hi': 5.1, 'lo': 4.9, 'color': 'blue', 'sigma': 6}]
    view.sync_figure_form()
    view.update_plot()
    for _ in range(10):
        app.update()
    canvas = view.canvas

    def mouse(name, x, y):
        event = MouseEvent(name, canvas, x, y, button=1)
        canvas.callbacks.process(name, event)
    # drag the header label 20 px to the right
    label = next(t for t in view.meta['labels'] if t._nmr_id == 'assign:a')
    b = label.get_window_extent(canvas.get_renderer())
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    mouse('button_press_event', cx, cy)
    mouse('motion_notify_event', cx + 20, cy)
    mouse('button_release_event', cx + 20, cy)
    want = 20 * 72 / view.figure.dpi
    got = view.doc['offsets'].get('assign:a', [0, 0])[0]
    check('dragging a label stores its offset in points', abs(got - want) < 0.2, f'{got:.1f} vs {want:.1f} pt')
    # drag the trace up by a data distance
    view.update_plot()
    ax = view.meta['axes'][0]
    x0, y0 = ax.transData.transform((7.5, 0.2))
    x1, y1 = ax.transData.transform((7.5, 0.5))
    mouse('button_press_event', x0, y0)
    mouse('motion_notify_event', x1, y1)
    mouse('button_release_event', x1, y1)
    check('dragging a trace changes its offset', abs(view.doc['traces'][0]['dy'] - 0.3) < 0.02, view.doc['traces'][0]['dy'])
    # break axis: cut 6.5-5.5 ppm out of the segment
    view.update_plot()
    view.set_mode('break')
    ax = view.meta['axes'][0]
    xa, ya = ax.transData.transform((6.5, 0.5))
    xb, _ = ax.transData.transform((5.5, 0.5))
    mouse('button_press_event', xa, ya)
    mouse('motion_notify_event', xb, ya)
    mouse('button_release_event', xb, ya)
    segs = view.doc['segments']
    check('break-axis tool makes two segments', len(segs) == 2 and abs(segs[0][1] - 6.5) < 0.05 and abs(segs[1][0] - 5.5) < 0.05, segs)
    view.undo()
    check('undo restores one segment', len(view.doc['segments']) == 1)
    out = tmp / 'nmr_export.png'
    meta = view.export_figure(str(out), dpi=200)
    check('export PNG', out.stat().st_size > 5000 and meta is not None)
    # integration page: automatic ranges, values on the figure, CSV export
    view.pick['hi'].set('8')
    view.pick['lo'].set('4')
    view.auto_integrate()
    integ = view.doc['integration']
    check('auto integration ranges', len(integ['ranges']) >= 2 and integ['ref'], len(integ['ranges']))
    view.ib['show_values'].set(True)
    view.ib['show_curves'].set(True)
    view.integ_settings_changed()
    view.update_plot()
    check('integral values drawn', any(t._nmr_id.startswith('integ:') for t in view.meta['labels']))
    csv_path = view.export_integrals(str(tmp / 'integrals.csv'))
    rows = open(csv_path, encoding='utf-8-sig').read().strip().splitlines()
    check('integral table exported', len(rows) == 1 + len(integ['ranges']), len(rows))
    view.find_peaks()
    check('peak list filled', len(view.peak_list) >= 2, len(view.peak_list))
    app.change_language('zh')
    for _ in range(20):
        app.update()
    check('Chinese interface builds the NMR view', 'nmr' in app.views and app.title().endswith('NMR 谱图'))
    check('no callback errors', not errors, errors[:1])
    app.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        unit(tmp)
        real_data()
        gui(tmp)
    print(f'{sum(results)}/{len(results)} passed')
    sys.exit(0 if all(results) else 1)
