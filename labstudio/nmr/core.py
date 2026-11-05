"""NMR: read Bruker processed spectra, reference them and draw stacked publication figures.

Only numpy and matplotlib are needed. A figure is described by a plain dict ("doc", see default_doc) so it can be
saved as JSON and redrawn identically. Text labels can be moved by an offset in points; render() returns every
label artist with its id, the trace geometry and a list of overlaps, so the view can offer dragging and warnings.
"""
import functools
import io
import json
import os
import re

import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import ScaledTranslation

# Okabe-Ito colour-blind-safe palette (no red / green pair)
PALETTE = {'black': '#000000', 'blue': '#0072B2', 'orange': '#E69F00', 'pink': '#CC79A7', 'sky': '#56B4E9',
           'vermillion': '#D55E00', 'yellow': '#F0E442', 'grey': '#555555'}
MARK_ORDER = ['blue', 'orange', 'pink', 'sky', 'vermillion', 'yellow', 'grey']

# (nucleus, solvent) -> (literature shift of the residual / solvent signal, how to find it)
SOLVENTS = {('1H', 'acetone'): (2.05, 'quintet'), ('1H', 'dmso'): (2.50, 'quintet'), ('1H', 'cd3od'): (3.31, 'quintet'),
            ('1H', 'cdcl3'): (7.26, 'tallest'), ('1H', 'c6d6'): (7.16, 'tallest'), ('1H', 'd2o'): (4.79, 'tallest'),
            ('1H', 'cd2cl2'): (5.32, 'tallest'), ('1H', 'thf'): (3.58, 'tallest'), ('1H', 'tol'): (2.08, 'quintet'),
            ('13C', 'acetone'): (29.84, 'tallest'), ('13C', 'cdcl3'): (77.16, 'tallest'),
            ('13C', 'dmso'): (39.52, 'tallest'), ('13C', 'c6d6'): (128.06, 'tallest'),
            ('13C', 'cd3od'): (49.00, 'tallest'), ('13C', 'cd2cl2'): (53.84, 'tallest')}


def hexc(c):
    return PALETTE.get(c, c) if c else '#000000'


def default_doc():
    return {'name': 'nmr_figure', 'figsize': [7.2, 5.0], 'margins': {'l': 0.06, 'r': 0.98, 't': 0.86, 'b': 0.13},
            'font': 'Arial', 'fs': 8.0, 'lw': 0.6, 'step': 1.3, 'max_mag': 4.0, 'clip': 1.0, 'signed': False,
            'headroom': 0.6, 'name_y': 0.3, 'color_names': False, 'norm': {'mode': 'max', 'ppm': None, 'width': 2.0},
            'struct_frac': 0.26, 'struct_gap': 0.45, 'seg_gap': 0.06, 'segments': [], 'xticks': {},
            'seg_headers': [], 'traces': [], 'assignments': [], 'peaks': [], 'structures': [],
            'title': '', 'panel': '', 'struct_panel': '', 'xlabel': 'auto', 'footnote': '', 'offsets': {}, 'styles': {},
            'integration': default_integration()}


# ------------------------------------------------------------------ reading
def read_jcamp(path):
    """##$KEY= value pairs of a Bruker parameter file (acqus, procs)."""
    text = open(path, encoding='latin-1').read()
    out = {}
    for m in re.finditer(r'##\$([A-Za-z0-9_]+)=\s*(<([^>]*)>|\([^)]*\)[^#]*|[^\n]*)', text):
        key, raw = m.group(1), m.group(2).strip()
        if m.group(3) is not None:
            out[key] = m.group(3)
            continue
        try:
            out[key] = float(raw) if any(ch in raw for ch in '.eE') else int(raw)
        except ValueError:
            out[key] = raw
    return out


def norm_solvent(s):
    s = (s or '').lower().replace('-', '').replace('_', '')
    for key, names in [('acetone', ['acetone', 'aceton']), ('dmso', ['dmso']), ('cdcl3', ['cdcl3', 'chloroform']),
                       ('c6d6', ['c6d6', 'benzene']), ('cd3od', ['meod', 'cd3od', 'methanol']), ('d2o', ['d2o']),
                       ('cd2cl2', ['cd2cl2']), ('thf', ['thf']), ('tol', ['tol'])]:
        if any(n in s for n in names):
            return key
    return s


def kind_of(nuc, pulprog):
    p = (pulprog or '').lower()
    if 'dept' in p:
        for angle in ('135', '90', '45'):
            if angle in p:
                return 'DEPT-' + angle
        return 'DEPT'
    if 'apt' in p or 'jmod' in p:
        return 'APT'
    return nuc or '?'


@functools.lru_cache(maxsize=96)
def read_bruker(pdata):
    """Real part of a processed 1D spectrum (…/expno/pdata/1) on TopSpin's own ppm axis."""
    procs = read_jcamp(os.path.join(pdata, 'procs'))
    acqus = read_jcamp(os.path.join(os.path.dirname(os.path.dirname(pdata)), 'acqus'))
    if os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(pdata)), 'acqu2s')):
        raise ValueError('2D data are not supported')
    order = '>' if int(procs.get('BYTORDP', 0)) == 1 else '<'
    dtype = order + ('f8' if int(procs.get('DTYPP', 0)) == 2 else 'i4')
    d = np.fromfile(os.path.join(pdata, '1r'), dtype=dtype).astype(float)
    d *= 2.0 ** int(procs.get('NC_proc', 0))
    n = d.size
    ppm = float(procs['OFFSET']) - np.arange(n) * float(procs['SW_p']) / float(procs['SF']) / n
    nuc = str(acqus.get('NUC1', '?'))
    pulprog = str(acqus.get('PULPROG', ''))
    return {'ppm': ppm, 'd': d, 'nuc': nuc, 'pulprog': pulprog, 'solvent': str(acqus.get('SOLVENT', '')),
            'sf': float(procs['SF']), 'kind': kind_of(nuc, pulprog)}


def scan(root, max_depth=4):
    """Every 1D experiment (folder with acqus and pdata/1/1r) below root."""
    items = []
    root = os.path.normpath(root)
    for dirpath, dirs, files in os.walk(root):
        if os.path.relpath(dirpath, root).count(os.sep) >= max_depth:
            dirs[:] = []
        if 'acqus' not in files or not os.path.isfile(os.path.join(dirpath, 'pdata', '1', '1r')):
            continue
        dirs[:] = []
        if os.path.isfile(os.path.join(dirpath, 'acqu2s')):
            continue
        try:
            par = read_jcamp(os.path.join(dirpath, 'acqus'))
        except OSError:  # e.g. a OneDrive file that is not downloaded
            par = None
        rel = os.path.relpath(dirpath, root).replace('\\', '/')
        sample, _, expno = rel.rpartition('/')
        title = ''
        try:
            lines = open(os.path.join(dirpath, 'pdata', '1', 'title'), encoding='latin-1').read().strip().splitlines()
            title = lines[0][:80] if lines else ''
        except OSError:
            pass
        items.append({'path': os.path.join(dirpath, 'pdata', '1'), 'sample': sample or os.path.basename(root),
                      'expno': expno or rel, 'nuc': str(par.get('NUC1', '?')) if par else '?',
                      'pulprog': str(par.get('PULPROG', '')) if par else '',
                      'kind': kind_of(par.get('NUC1'), par.get('PULPROG')) if par else 'offline',
                      'solvent': str(par.get('SOLVENT', '')) if par else '', 'title': title})
    items.sort(key=lambda r: (r['sample'].lower(), int(r['expno']) if str(r['expno']).isdigit() else 0))
    return items


# ------------------------------------------------------------------ referencing
def noise_of(d):
    """Robust noise level: 10th percentile of the standard deviations of 64 chunks."""
    return float(np.percentile([np.std(c) for c in np.array_split(d, 64)], 10))


def _quintet_center(ppm, d, target, sf, sigma):
    """Centre line of a 1:2:3:2:1 CD2H quintet within +-0.3 ppm of target.

    Both shoulders at +-J must be 0.4-0.97 of the centre and both at +-2J 0.1-0.75, symmetric within 35 / 50 %.
    A singlet next to the solvent (e.g. acetylacetone CH3 beside acetone-d5) fails this test.
    """
    j = 2.2 / sf
    rp, rd = ppm[::-1], d[::-1]
    w = np.where((ppm > target - 0.3) & (ppm < target + 0.3))[0]
    good = []
    for i in w[1:-1]:
        if not (d[i] >= d[i - 1] and d[i] >= d[i + 1] and d[i] > 15 * sigma):
            continue
        c = ppm[i]
        l1, r1 = np.interp(c + j, rp, rd) / d[i], np.interp(c - j, rp, rd) / d[i]
        l2, r2 = np.interp(c + 2 * j, rp, rd) / d[i], np.interp(c - 2 * j, rp, rd) / d[i]
        if 0.4 < l1 < 0.97 and 0.4 < r1 < 0.97 and 0.1 < l2 < 0.75 and 0.1 < r2 < 0.75 \
                and abs(l1 - r1) < 0.35 * max(l1, r1) and abs(l2 - r2) < 0.5 * max(l2, r2):
            good.append((d[i], c))
    if good:
        return max(good)[1]
    return float(ppm[w][np.argmax(d[w])]) if len(w) else None


def solvent_shift(sp):
    rule = SOLVENTS.get((sp['nuc'], norm_solvent(sp['solvent'])))
    if not rule:
        return None, None
    target, how = rule
    ppm, d = sp['ppm'], sp['d']
    if how == 'quintet':
        c = _quintet_center(ppm, d, target, sp['sf'], noise_of(d))
    else:
        span = 0.3 if sp['nuc'] == '1H' else 2.5
        w = (ppm > target - span) & (ppm < target + span)
        c = float(ppm[w][np.argmax(np.abs(d[w]))]) if w.any() else None
    if c is None:
        return None, None
    return float(target - c), f"{norm_solvent(sp['solvent'])} {sp['nuc']} {target} ppm"


def sibling_13c(pdata):
    """A 13C{1H} spectrum of the same sample (DEPT has no solvent carbon and borrows its referencing)."""
    sample_dir = os.path.dirname(os.path.dirname(os.path.dirname(pdata)))
    for e in sorted(os.listdir(sample_dir)):
        cand = os.path.join(sample_dir, e, 'pdata', '1')
        if not os.path.isfile(os.path.join(cand, '1r')):
            continue
        try:
            par = read_jcamp(os.path.join(sample_dir, e, 'acqus'))
        except OSError:
            continue
        if par.get('NUC1') == '13C' and kind_of('13C', par.get('PULPROG')) == '13C':
            return cand
    return None


def auto_reference(pdata):
    """(ppm shift to add, note). 1H/13C: solvent; DEPT/APT: from the 13C of the same sample; else TopSpin."""
    sp = read_bruker(pdata)
    if sp['kind'].startswith('DEPT') or sp['kind'] == 'APT':
        sib = sibling_13c(pdata)
        if sib:
            shift, note = solvent_shift(read_bruker(sib))
            if shift is not None:
                return shift, 'from 13C of the same sample (' + note + ')'
        return 0.0, 'TopSpin referencing (no 13C spectrum of this sample)'
    shift, note = solvent_shift(sp)
    if shift is None:
        return 0.0, 'TopSpin referencing (no solvent rule for %s / %s)' % (sp['nuc'], sp['solvent'] or '-')
    return shift, note


def auto_flip(pdata, shift):
    """True when a DEPT spectrum was phased upside down (chain-end CH3 near 14 ppm or the tallest CH points down)."""
    sp = read_bruker(pdata)
    ppm, d = sp['ppm'] + shift, sp['d']
    if sp['kind'] == 'DEPT-135':
        w = (ppm > 13) & (ppm < 15.5)
    elif sp['kind'] == 'DEPT-90':
        w = (ppm > -10) & (ppm < 230)
    else:
        return False
    if not w.any():
        return False
    v = d[w]
    return bool(v[np.argmax(np.abs(v))] < 0)


def default_range(sp, shift):
    """ppm window holding every signal above 12 sigma, with 5 % padding."""
    ppm, d = sp['ppm'] + shift, np.abs(sp['d'])
    idx = np.where(d > 12 * noise_of(sp['d']))[0]
    if len(idx) < 2:
        return [round(float(ppm.max()), 2), round(float(ppm.min()), 2)]
    hi, lo = ppm[idx].max(), ppm[idx].min()
    pad = 0.05 * (hi - lo) + 1e-3
    return [round(float(hi + pad), 2), round(float(lo - pad), 2)]


def nearest_peak(pdata, shift, flip, ppm0, window):
    sp = read_bruker(pdata)
    ppm = sp['ppm'] + shift
    d = sp['d'] * (-1 if flip else 1)
    w = (ppm > ppm0 - window) & (ppm < ppm0 + window)
    return float(ppm[w][np.argmax(np.abs(d[w]))]) if w.any() else ppm0


# ------------------------------------------------------------------ peak picking and integration
# Approach borrowed from NMRium / cheminfo (auto peaks and ranges): a robust noise level, a threshold that is both a
# multiple of the noise and a fraction of the tallest peak, ranges grown from each peak to the baseline and merged into
# multiplets, then a multiplicity guess from the number of lines and their spacing.

def spectrum(pdata, shift=0.0, flip=False):
    sp = read_bruker(pdata)
    return sp['ppm'] + shift, sp['d'] * (-1 if flip else 1), noise_of(sp['d']), sp['sf']


def pick_peaks(ppm, d, sigma, sf, thr=10.0, min_ratio=0.005, window=None):
    """Local maxima above max(thr x sigma, min_ratio x tallest) that stand out from both sides by 3 sigma."""
    hz_per_pt = abs(ppm[0] - ppm[1]) * sf
    w = max(2, int(round(1.5 / hz_per_pt)))  # ~1.5 Hz either side
    sel = np.ones(d.size, bool) if window is None else (ppm < max(window)) & (ppm > min(window))
    idx = np.where(sel)[0]
    if len(idx) < 2 * w + 3:
        return []
    y = d
    top = float(y[sel].max())
    level = max(thr * sigma, min_ratio * top)
    reach = max(w, int(round(30 / hz_per_pt)))  # look up to 30 Hz either side for the prominence
    out = []
    for i in idx[w:-w]:
        if y[i] < level or not (y[i] >= y[i - 1] and y[i] > y[i + 1]):
            continue
        # topographic prominence: walk each way until the signal rises above this point; the higher of the two
        # lowest points passed is the base. Noise ripples on the flank of a big peak have almost no prominence.
        lows = []
        for step in (-1, 1):
            j, low = i, y[i]
            while 0 < j < y.size - 1 and abs(j - i) < reach:
                j += step
                if y[j] > y[i]:
                    break
                low = min(low, y[j])
            lows.append(low)
        if y[i] - max(lows) < max(6 * sigma, 0.03 * y[i]):
            continue
        out.append({'ppm': float(ppm[i]), 'height': float(y[i]), 'snr': float(y[i] / sigma), 'i': int(i)})
    return out


def integrate(ppm, d, hi, lo, edge=4):
    """Area between hi and lo (ppm units x intensity) above a straight baseline through the window edges."""
    idx = np.where((ppm <= max(hi, lo)) & (ppm >= min(hi, lo)))[0]
    if len(idx) < 2 * edge + 2:
        return 0.0
    y = d[idx]
    left, right = float(np.mean(y[:edge])), float(np.mean(y[-edge:]))
    base = np.linspace(left, right, y.size)
    return float(np.sum(y - base) * abs(ppm[1] - ppm[0]))


def multiplicity(lines, sf):
    """s / d / t / q / quint / dd / m from the line heights and spacings (Hz) of one multiplet."""
    n = len(lines)
    if n == 0:
        return '', None
    if n == 1:
        return 's', None
    pos = sorted((l['ppm'] for l in lines), reverse=True)
    gaps = np.abs(np.diff(pos)) * sf
    j = float(np.median(gaps))
    h = [l['height'] for l in sorted(lines, key=lambda l: -l['ppm'])]
    even = np.all(np.abs(gaps - j) < 0.25 * j + 0.3)
    if n == 2:
        return 'd', j
    if even:
        pattern = {3: [1, 2, 1], 4: [1, 3, 3, 1], 5: [1, 4, 6, 4, 1]}.get(n)
        if pattern:
            ratio = np.array(h) / max(h)
            expect = np.array(pattern) / max(pattern)
            if np.all(np.abs(ratio - expect) < 0.35):
                return {3: 't', 4: 'q', 5: 'quint'}[n], j
    if n == 4 and abs(gaps[0] - gaps[2]) < 0.25 * gaps[0] + 0.3:
        return 'dd', float(gaps[0])
    return 'm', None


def auto_ranges(ppm, d, sigma, sf, thr=10.0, min_ratio=0.005, gap_hz=2.0, window=None, solvent_ppm=None):
    """Integration ranges: each picked peak grown to where the signal falls to 2 sigma, touching ranges merged."""
    peaks = pick_peaks(ppm, d, sigma, sf, thr, min_ratio, window)
    if not peaks:
        return []
    spans = []
    for p in peaks:
        i = p['i']
        floor = max(2 * sigma, 0.002 * p['height'])  # Lorentzian tails are long: stop at 0.2 % of the peak
        a = i
        while a > 0 and d[a - 1] > floor:
            a -= 1
        b = i
        while b < d.size - 1 and d[b + 1] > floor:
            b += 1
        spans.append([a, b, [p]])
    spans.sort(key=lambda s: s[0])
    gap_pts = gap_hz / (abs(ppm[0] - ppm[1]) * sf)
    merged = []
    for s in spans:
        if merged and s[0] <= merged[-1][1] + gap_pts:
            merged[-1][1] = max(merged[-1][1], s[1])
            merged[-1][2] += s[2]
        else:
            merged.append(s)
    # split a merged range at deep valleys: the signal between two line groups falls below split x the smaller group
    hz_per_pt = abs(ppm[0] - ppm[1]) * sf
    split, pieces = 0.08, []
    for a, b, lines in merged:
        lines = sorted({l['i']: l for l in lines}.values(), key=lambda l: l['i'])
        start, group = a, [lines[0]]
        for prev, nxt in zip(lines, lines[1:]):
            k = prev['i'] + int(np.argmin(d[prev['i']:nxt['i'] + 1]))
            group_top = max(l['height'] for l in group)
            if d[k] < split * min(group_top, nxt['height']) and (nxt['i'] - prev['i']) * hz_per_pt > 4:
                pieces.append([start, k, group])
                start, group = k, []
            group.append(nxt)
        pieces.append([start, b, group])
    merged = pieces
    out = []
    pad = max(1, int(round(1.0 / (abs(ppm[0] - ppm[1]) * sf))))  # 1 Hz either side
    merged.sort(key=lambda m: m[0])
    bounds = []
    for k, (a, b, lines) in enumerate(merged):
        extra = pad + min(int(round(10 / hz_per_pt)), (b - a) // 4)  # a little more for the tails, at most 10 Hz
        a, b = max(0, a - extra), min(d.size - 1, b + extra)
        if bounds:
            a = max(a, bounds[-1][1] + 1)  # never overlap the previous range (no area counted twice)
        if k + 1 < len(merged):
            b = min(b, (b + merged[k + 1][0]) // 2 if merged[k + 1][0] > merged[k][1] else merged[k + 1][0] - 1)
        bounds.append([a, max(a + 1, b), lines])
    for a, b, lines in bounds:
        hi, lo = float(ppm[a]), float(ppm[b])
        seen, uniq = set(), []
        for l in lines:
            if l['i'] not in seen:
                seen.add(l['i'])
                uniq.append(l)
        mult, j = multiplicity(uniq, sf)
        seg = d[a:b + 1]
        center = float(np.sum(ppm[a:b + 1] * np.clip(seg, 0, None)) / max(np.sum(np.clip(seg, 0, None)), 1e-30))
        out.append({'hi': round(hi, 4), 'lo': round(lo, 4), 'center': round(center, 4), 'mult': mult,
                    'J': None if j is None else round(j, 1), 'lines': len(uniq), 'area': integrate(ppm, d, hi, lo),
                    'solvent': bool(solvent_ppm is not None and lo - 0.02 <= solvent_ppm <= hi + 0.02)})
    out.sort(key=lambda r: -r['center'])
    return out


def relative_integrals(areas, ref_index, ref_value=1.0, mode='H', nuclei=None, ref_nuclei=1.0):
    """Scale areas so the reference range equals ref_value.

    mode 'H': values are numbers of nuclei (ref_value = nuclei of the reference signal).
    mode 'mmol': ref_value = mmol of the reference compound, ref_nuclei = its nuclei in the reference signal,
    nuclei[i] = nuclei behind range i; result = mmol of each species.
    """
    if ref_index is None or not (0 <= ref_index < len(areas)) or areas[ref_index] == 0:
        return [None] * len(areas)
    ref = areas[ref_index]
    if mode == 'mmol':
        nuclei = nuclei or [1.0] * len(areas)
        return [a / ref * ref_nuclei / max(n or 1.0, 1e-12) * ref_value for a, n in zip(areas, nuclei)]
    return [a / ref * ref_value for a in areas]


def solvent_position(nuc, solvent):
    rule = SOLVENTS.get((nuc, norm_solvent(solvent)))
    return rule[0] if rule else None


def new_trace(pdata):
    """Trace entry for a doc, with automatic referencing and DEPT phase check."""
    sp = read_bruker(pdata)
    shift, note = auto_reference(pdata)
    flip = auto_flip(pdata, shift)
    sample = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(pdata))))
    return {'id': os.urandom(3).hex(), 'path': pdata, 'label': sample, 'nuc': sp['nuc'], 'kind': sp['kind'],
            'solvent': sp['solvent'], 'shift': round(shift, 4), 'auto_shift': round(shift, 4), 'ref_note': note,
            'flip': flip, 'auto_flip': flip, 'scale': 1.0, 'dy': 0.0, 'color': 'black', 'visible': True,
            'range': default_range(sp, shift)}


def nucleus_label(nuc):
    m = re.match(r'(\d+)([A-Za-z]+)', nuc or '')
    return '$\\delta$($^{%s}$%s) (ppm)' % (m.group(1), m.group(2)) if m else '$\\delta$ (ppm)'


def peak_runs(y, w, thr, nn):
    """Index ranges of the peaks inside mask w (runs above 2.5 sigma that reach thr), widened to their base.

    Only peaks are coloured, never the noise between them, so wide windows stay clean.
    """
    idx = np.where(w)[0]
    if not len(idx):
        return []
    lo, hi = idx[0], idx[-1] + 1
    a = np.abs(y[lo:hi])
    on = a > 2.5 * nn
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if a[i:j].max() > thr:
                pad = max(2, (j - i) // 2)
                runs.append([max(lo + i - pad, lo), min(lo + j + pad, hi)])
            i = j
        else:
            i += 1
    merged = []
    for r in runs:
        if merged and r[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r)
    return merged


def parse_map(s):
    """'0:orange, 1:blue' -> {'0': 'orange', '1': 'blue'}"""
    if isinstance(s, dict):
        return {str(k): v for k, v in s.items()}
    out = {}
    for part in str(s or '').split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            out[k.strip()] = v.strip()
    return out


def break_segment(segments, k, a, b, min_frac=0.02):
    """Cut the ppm range a-b out of segment k (a broken axis). Pieces narrower than min_frac are dropped."""
    hi, lo = segments[k]
    top, bottom = max(a, b), min(a, b)
    span = abs(hi - lo)
    pieces = [[hi, round(top, 3)], [round(bottom, 3), lo]]
    pieces = [p for p in pieces if p[0] - p[1] > min_frac * span]
    return segments[:k] + pieces + segments[k + 1:]


# ------------------------------------------------------------------ structures (optional RDKit)
def rdkit_available():
    try:
        import rdkit  # noqa: F401
        return True
    except ImportError:
        return False


@functools.lru_cache(maxsize=64)
def structure_image(smiles, highlight_json, notes_json):
    from PIL import Image
    from rdkit import Chem
    from rdkit.Chem.Draw import rdMolDraw2D
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError('invalid SMILES: ' + smiles)
    for a in mol.GetAtoms():  # [*:1] / [*:2] are drawn as R / R'
        if a.GetAtomicNum() == 0:
            a.SetProp('atomLabel', "R'" if a.GetAtomMapNum() == 2 else 'R')
        a.SetAtomMapNum(0)
    for idx, txt in json.loads(notes_json).items():
        if int(idx) < mol.GetNumAtoms():
            mol.GetAtomWithIdx(int(idx)).SetProp('atomNote', txt)
    cols = {int(i): tuple(int(hexc(c)[k:k + 2], 16) / 255 for k in (1, 3, 5))
            for i, c in json.loads(highlight_json).items() if int(i) < mol.GetNumAtoms()}
    dr = rdMolDraw2D.MolDraw2DCairo(420, 200)
    o = dr.drawOptions()
    o.clearBackground = False
    o.useBWAtomPalette()
    o.fixedFontSize = 22
    o.highlightRadius = 0.32
    o.fixedBondLength = 42
    o.annotationFontScale = 0.9
    dr.DrawMolecule(mol, highlightAtoms=list(cols), highlightAtomColors=cols, highlightBonds=[])
    dr.FinishDrawing()
    im = Image.open(io.BytesIO(dr.GetDrawingText())).convert('RGBA')
    bb = im.getchannel('A').getbbox()
    if bb:
        im = im.crop((max(bb[0] - 8, 0), max(bb[1] - 8, 0), min(bb[2] + 8, im.width), min(bb[3] + 8, im.height)))
    return np.array(im)


# ------------------------------------------------------------------ figure
def pick_font(name):
    from matplotlib import font_manager
    try:
        font_manager.findfont(name, fallback_to_default=False)
        return name
    except ValueError:
        return 'DejaVu Sans'


def render(doc, fig):
    """Draw doc on fig (cleared first). Returns geometry for interaction and a list of label overlaps."""
    fig.clear()
    fs = float(doc.get('fs', 8))
    font = pick_font(doc.get('font', 'Arial'))
    W, H = doc.get('figsize', [7.2, 5.0])
    mg = doc.get('margins') or default_doc()['margins']
    offsets, styles = doc.get('offsets', {}), doc.get('styles', {})
    labels, issues = [], []

    def put(target, lid, x, y, s, transform=None, size=None, **kw):
        st = styles.get(lid, {})
        if st.get('hidden') or not str(s).strip():
            return None
        dx, dy = offsets.get(lid, [0, 0])
        base = transform if transform is not None else (target.transData if hasattr(target, 'transData') else fig.transFigure)
        t = target.text(x, y, s, transform=base + ScaledTranslation(dx / 72, dy / 72, fig.dpi_scale_trans),
                        fontsize=st.get('size', size or fs), fontfamily=font, **kw)
        t._nmr_id = lid
        labels.append(t)
        return t

    traces = [t for t in doc.get('traces', []) if t.get('visible', True)]
    structs = [s for s in doc.get('structures', []) if s.get('smiles')]
    if structs and not rdkit_available():
        issues.append({'ids': [], 'msg': 'structures need RDKit (pip install rdkit); not drawn'})
        structs = []
    sfrac = float(doc.get('struct_frac', 0.26)) if structs else 0.0
    segs = [list(map(float, s)) for s in (doc.get('segments') or [])]
    if not segs and traces:
        segs = [list(traces[0].get('range') or default_range(read_bruker(traces[0]['path']), traces[0].get('shift', 0)))]
    if not segs:
        segs = [[10.0, 0.0]]
    widths = [abs(a - b) for a, b in segs]
    gs = GridSpec(1, len(segs), left=mg['l'], right=mg['r'] - sfrac, top=mg['t'], bottom=mg['b'],
                  width_ratios=widths, wspace=float(doc.get('seg_gap', 0.06)), figure=fig)
    axes = [fig.add_subplot(gs[0, k]) for k in range(len(segs))]
    n = len(traces)
    step = float(doc.get('step', 1.3))
    max_mag = float(doc.get('max_mag', 4))
    clip = float(doc.get('clip', 1.0))
    signed = bool(doc.get('signed', False))
    lw = float(doc.get('lw', 0.6))
    norm = doc.get('norm') or {'mode': 'max'}
    top = n * step + 0.05
    first_nuc = None
    trace_lines = {}
    common_sc = 1.0
    if traces:
        sp0 = read_bruker(traces[0]['path'])
        p0 = sp0['ppm'] + float(traces[0].get('shift', 0))
        m = (p0 < segs[0][0]) & (p0 > segs[0][1])
        common_sc = float(np.abs(sp0['d'][m]).max()) if m.any() else 1.0

    for ti, tr in enumerate(traces):
        sp = read_bruker(tr['path'])
        first_nuc = first_nuc or sp['nuc']
        ppm = sp['ppm'] + float(tr.get('shift', 0))
        d = sp['d'] * (-1 if tr.get('flip') else 1)
        sig = noise_of(sp['d'])
        pos = n - 1 - ti  # the first trace in the list is drawn on top
        off = pos * step + float(tr.get('dy', 0))
        gain = float(tr.get('scale', 1))
        color = hexc(tr.get('color', 'black'))
        m0 = (ppm < segs[0][0]) & (ppm > segs[0][1])
        base0 = float(np.abs(d[m0]).max()) if m0.any() else 1.0
        ref_sc = None
        if norm.get('mode') == 'peak' and norm.get('ppm') not in (None, ''):
            wr = (ppm > float(norm['ppm']) - float(norm.get('width', 2))) & (ppm < float(norm['ppm']) + float(norm.get('width', 2)))
            if wr.any():
                ref_sc = float(np.abs(d[wr]).max())
        trace_lines[tr['id']] = {'pos': pos, 'off': off, 'lines': []}
        for k, (ax, (hi, lo)) in enumerate(zip(axes, segs)):
            m = (ppm < hi) & (ppm > lo)
            if not m.any():
                continue
            if ref_sc:
                sc = ref_sc
            elif norm.get('mode') == 'common':
                sc = common_sc
            else:  # each segment to its own maximum, never magnified beyond max_mag x the first segment
                sc = max(float(np.abs(d[m]).max()), base0 / max_mag) if k else float(np.abs(d[m]).max())
            sc = max(sc, 1e-12)
            y = d / sc * gain
            clipped = y[m].max() > clip
            y = np.clip(y, -clip if signed else -0.08, clip)
            nn = sig / sc * gain
            line, = ax.plot(ppm[m], y[m] + off, color=color, lw=lw, solid_joinstyle='round')
            line._nmr_trace = tr['id']
            trace_lines[tr['id']]['lines'].append(line)
            if clipped and doc.get('clip_marks', True):
                ax.text(ppm[m][np.argmax(y[m])], off + clip + 0.02, '//', fontsize=6, ha='center', va='bottom', color='#444')
            for a in doc.get('assignments', []):
                if not a.get('on', True):
                    continue
                alo, ahi = sorted([float(a['lo']), float(a['hi'])])
                w = (ppm > alo) & (ppm < ahi) & m
                thr = float(a.get('sigma', 6)) * nn
                if w.any() and np.abs(y[w]).max() > thr:
                    for i0, i1 in peak_runs(y, w, thr, nn):
                        c, = ax.plot(ppm[i0:i1], y[i0:i1] + off, color=hexc(a['color']), lw=lw * 1.6)
                        c._nmr_trace = tr['id']
            for p in doc.get('peaks', []):
                c0 = float(p['ppm'])
                if not lo < c0 < hi:
                    continue
                hw = float(p.get('search') or 0.008 * abs(hi - lo))
                w = np.where((ppm > c0 - hw) & (ppm < c0 + hw))[0]
                if len(w) < 3:
                    continue
                j = w[np.argmax(y[w])]
                side = max(np.interp(ppm[j] + 1.6 * hw, ppm[::-1], y[::-1]), np.interp(ppm[j] - 1.6 * hw, ppm[::-1], y[::-1]))
                if not (y[j] > float(p.get('sigma', 10)) * nn and y[j] > float(p.get('resolve', 1.3)) * max(side, 0)
                        and w[0] < j < w[-1]):
                    continue  # too weak or not a resolved maximum: no label
                ww = (ppm > ppm[j] - 1.15 * hw) & (ppm < ppm[j] + 1.15 * hw)
                c, = ax.plot(ppm[ww], y[ww] + off, color=hexc(p['color']), lw=lw * 1.7)
                c._nmr_trace = tr['id']
                put(ax, 'peak:%s:%s' % (p['id'], tr['id']), ppm[j], y[ww].max() + off + 0.04, p['label'],
                    color=hexc(p['color']), ha='center', va='bottom', fontweight='bold')
        draw_integrals(doc, tr, ppm, d, off, axes, segs, put, lw)
        hi0, lo0 = segs[0]
        put(axes[0], 'name:' + tr['id'], hi0 - 0.015 * abs(hi0 - lo0), off + float(doc.get('name_y', 0.3)),
            tr.get('label', ''), ha='left', va='bottom', color=color if doc.get('color_names') else 'black')

    for k, (ax, (hi, lo)) in enumerate(zip(axes, segs)):
        ax.set_xlim(hi, lo)
        ax.set_ylim(-clip * 1.02 if signed else -0.1, top + float(doc.get('headroom', 0.6)))
        ax.set_yticks([])
        for side in ('left', 'right', 'top'):
            ax.spines[side].set_visible(False)
        ax.tick_params(labelsize=fs, width=0.7, length=3)
        ticks = (doc.get('xticks') or {}).get(str(k))
        if ticks:
            ax.set_xticks([float(v) for v in str(ticks).replace(',', ' ').split()])
        else:
            ax.xaxis.set_major_locator(MaxNLocator(nbins=max(2, int(6 * widths[k] / sum(widths) + 1.5))))
        for t in ax.get_xticklabels():
            t.set_fontfamily(font)
    for ax in axes[1:]:  # break marks
        for x in (0, 1):
            ax.plot([x - 0.025, x + 0.025], [-0.02, 0.02], transform=ax.transAxes, color='black', lw=0.7, clip_on=False)

    for a in doc.get('assignments', []):
        if not a.get('on', True) or not a.get('header', True):
            continue
        alo, ahi = sorted([float(a['lo']), float(a['hi'])])
        for ax, (hi, lo) in zip(axes, segs):
            if lo < (alo + ahi) / 2 < hi:
                dec = 2 if abs(hi - lo) < 20 else 1
                txt = a.get('label', '') + (('\n%.*f–%.*f' % (dec, ahi, dec, alo)) if a.get('show_range', True) else '')
                put(ax, 'assign:' + a['id'], (alo + ahi) / 2, top, txt, color=hexc(a['color']), ha='center', va='bottom',
                    size=fs * 0.94, fontweight='bold', linespacing=1.0)
    for i, h in enumerate(doc.get('seg_headers', [])):
        k = int(h.get('segment', 0))
        if k < len(axes) and h.get('text'):
            put(axes[k], 'seghead:%d' % i, float(np.mean(segs[k])), top, h['text'], color=hexc(h.get('color')),
                ha='center', va='bottom', size=fs * 0.94, fontweight='bold', linespacing=1.0)

    xl = doc.get('xlabel') or 'auto'
    xl = nucleus_label(first_nuc) if xl == 'auto' else xl
    p0, p1 = axes[0].get_position(), axes[-1].get_position()
    put(fig, 'xlabel', (p0.x0 + p1.x1) / 2, mg['b'] - 0.075, xl, ha='center', va='top', size=fs * 1.12)
    put(fig, 'title', p0.x0, mg['t'] + 0.035, doc.get('title', ''), ha='left', va='bottom', size=fs * 1.06)
    put(fig, 'panel', max(p0.x0 - 0.035, 0.005), mg['t'] + 0.07, doc.get('panel', ''), ha='left', va='bottom',
        size=fs * 1.6, fontweight='bold')
    put(fig, 'footnote', 0.5, 0.012, doc.get('footnote', ''), ha='center', va='bottom', size=fs * 0.8, color='#555', wrap=True)

    if structs:
        x0 = mg['r'] - sfrac + 0.02
        gs2 = GridSpec(len(structs), 1, left=x0, right=mg['r'], top=mg['t'], bottom=mg['b'],
                       hspace=float(doc.get('struct_gap', 0.45)), figure=fig)
        images = []
        for r, s in enumerate(structs):
            try:
                images.append(structure_image(s['smiles'], json.dumps(parse_map(s.get('highlight', ''))),
                                              json.dumps(parse_map(s.get('notes', '')))))
            except Exception as error:  # noqa: BLE001 - a bad SMILES must not stop the figure
                images.append(None)
                issues.append({'ids': [], 'msg': 'structure %d: %s' % (r + 1, error)})
        IW = max([im.shape[1] for im in images if im is not None] or [1])  # one pixel scale for all structures
        IH = max([im.shape[0] for im in images if im is not None] or [1])
        for r, s in enumerate(structs):
            a = fig.add_subplot(gs2[r, 0])
            a.axis('off')
            im = images[r]
            if im is not None:
                a.imshow(im)
                a.set_xlim(-(IW - im.shape[1]) / 2, im.shape[1] + (IW - im.shape[1]) / 2)
                a.set_ylim(im.shape[0] + (IH - im.shape[0]) / 2, -(IH - im.shape[0]) / 2)
            put(a, 'stitle:%d' % r, 0.5, 1.0, s.get('title', ''), transform=a.transAxes, ha='center', va='bottom',
                size=fs * 0.94, color=hexc(s.get('title_color', 'black')))
        if doc.get('struct_panel'):
            put(fig, 'struct_panel', x0 - 0.01, mg['t'] + 0.03, doc['struct_panel'], ha='left', va='bottom',
                size=fs * 1.6, fontweight='bold')

    issues += check_overlaps(fig, labels, axes)
    return {'labels': labels, 'axes': axes, 'segments': segs, 'step': step, 'traces': trace_lines, 'issues': issues}


def default_integration():
    return {'ranges': [], 'ref': None, 'ref_value': 1.0, 'mode': 'H', 'ref_nuclei': 1.0, 'show_values': False,
            'show_curves': False, 'decimals': 2, 'traces': 'all'}


def trace_integrals(doc, tr):
    """(ranges, areas, scaled values) of the doc's integration ranges on one trace."""
    integ = doc.get('integration') or default_integration()
    ranges = integ.get('ranges', [])
    ppm, d, _, _ = spectrum(tr['path'], float(tr.get('shift', 0)), tr.get('flip'))
    areas = [integrate(ppm, d, r['hi'], r['lo']) for r in ranges]
    ids = [r['id'] for r in ranges]
    ref = ids.index(integ['ref']) if integ.get('ref') in ids else None
    values = relative_integrals(areas, ref, float(integ.get('ref_value', 1) or 1), integ.get('mode', 'H'),
                                [float(r.get('nuclei', 1) or 1) for r in ranges], float(integ.get('ref_nuclei', 1) or 1))
    return ranges, areas, values


def draw_integrals(doc, tr, ppm, d, off, axes, segs, put, lw):
    integ = doc.get('integration') or {}
    if not integ.get('ranges') or not (integ.get('show_values') or integ.get('show_curves')):
        return
    if integ.get('traces', 'all') not in ('all', tr['id']):
        return
    ranges, areas, values = trace_integrals(doc, tr)
    biggest = max([abs(a) for a in areas] or [1]) or 1
    dec = int(integ.get('decimals', 2))
    for r, area, value in zip(ranges, areas, values):
        for ax, (hi, lo) in zip(axes, segs):
            c = (r['hi'] + r['lo']) / 2
            if not lo < c < hi:
                continue
            if integ.get('show_curves'):
                w = (ppm <= r['hi']) & (ppm >= r['lo'])
                if w.sum() > 3:
                    y = d[w] - np.linspace(d[w][:4].mean(), d[w][-4:].mean(), w.sum())
                    cum = np.cumsum(y)  # rises from left (high ppm) to right, as in TopSpin
                    cum = cum / (cum.max() or 1) * 0.45 * abs(area) / biggest
                    ln, = ax.plot(ppm[w], off + 0.05 + cum, color='#555555', lw=lw * 0.9)
                    ln._nmr_trace = tr['id']
            if integ.get('show_values') and value is not None:
                put(ax, 'integ:%s:%s' % (r['id'], tr['id']), c, off - 0.04, f'{value:.{dec}f}', ha='center', va='top',
                    size=float(doc.get('fs', 8)) * 0.8, color='#333333')
            break


def check_overlaps(fig, labels, axes):
    """Labels touching each other, a tick label, a spectrum trace, or the figure edge."""
    if not hasattr(fig.canvas, 'get_renderer'):  # a bare Figure (exports): give it an Agg canvas to measure text
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    boxes = [(t, t.get_window_extent(r)) for t in labels]
    ticks = []
    for ax in axes:
        lo, hi = sorted(ax.get_xlim())
        ticks += [(t, t.get_window_extent(r)) for t in ax.get_xticklabels()
                  if t.get_visible() and t.get_text() and lo <= t.get_position()[0] <= hi]
    out, seen = [], set()

    def add(ids, msg):
        if msg not in seen:
            seen.add(msg)
            out.append({'ids': ids, 'msg': msg})

    def short(t):
        return t.get_text()[:20].replace('\n', ' ')
    for i, (t1, b1) in enumerate(boxes):
        for t2, b2 in boxes[i + 1:] + ticks:
            if b1.padded(0.5).overlaps(b2):
                add([t1._nmr_id] + ([t2._nmr_id] if hasattr(t2, '_nmr_id') else []), f'"{short(t1)}" overlaps "{short(t2)}"')
    for ax in axes:
        lines = [ln for ln in ax.get_lines() if hasattr(ln, '_nmr_trace')]
        xys = [ln.get_transform().transform(np.column_stack(ln.get_data())) for ln in lines]
        for t, b in boxes:
            bp = b.padded(-1)
            for xy in xys:
                if ((xy[:, 0] > bp.x0) & (xy[:, 0] < bp.x1) & (xy[:, 1] > bp.y0) & (xy[:, 1] < bp.y1)).any():
                    add([t._nmr_id], f'"{short(t)}" crosses a spectrum')
                    break
    W, H = fig.bbox.width, fig.bbox.height
    for t, b in boxes:
        if b.x0 < -1 or b.x1 > W + 1 or b.y0 < -1 or b.y1 > H + 1:
            add([t._nmr_id], f'"{short(t)}" is outside the figure')
    return out


# ------------------------------------------------------------------ synthetic data (self-test, demos)
def write_demo_bruker(folder, nuc='1H', solvent='CDCl3', peaks=((7.26, 1.0, 0.004),), sw=12.0, offset=11.0,
                      sf=500.13, n=16384, pulprog='zg30', noise=0.002, seed=0):
    """Write a minimal Bruker experiment (acqus, pdata/1/procs, pdata/1/1r) with Lorentzian peaks."""
    rng = np.random.default_rng(seed)
    ppm = offset - np.arange(n) * sw / n
    y = sum(h / (1 + ((ppm - c) / w) ** 2) for c, h, w in peaks) + rng.normal(0, noise, n)
    os.makedirs(os.path.join(folder, 'pdata', '1'), exist_ok=True)
    with open(os.path.join(folder, 'acqus'), 'w', encoding='latin-1') as f:
        f.write(f'##TITLE= demo\n##$NUC1= <{nuc}>\n##$PULPROG= <{pulprog}>\n##$SOLVENT= <{solvent}>\n##$SFO1= {sf}\n##END=\n')
    with open(os.path.join(folder, 'pdata', '1', 'procs'), 'w', encoding='latin-1') as f:
        f.write(f'##TITLE= demo\n##$SI= {n}\n##$OFFSET= {offset}\n##$SW_p= {sw * sf}\n##$SF= {sf}\n'
                f'##$BYTORDP= 0\n##$DTYPP= 0\n##$NC_proc= 0\n##END=\n')
    (np.round(y / np.abs(y).max() * 2e8)).astype('<i4').tofile(os.path.join(folder, 'pdata', '1', '1r'))
    read_bruker.cache_clear()
    return os.path.join(folder, 'pdata', '1')
