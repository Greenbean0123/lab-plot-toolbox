"""Checks for XRD phase identification and Scherrer broadening.

Run:  .venv\\Scripts\\python.exe tests\\test_phase.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from labstudio.xrd.phase import scherrer_fwhm, scherrer_size, broaden, sample_peaks, match  # noqa: E402

results = []


def check(name, condition, detail=''):
    results.append(bool(condition))
    print(('PASS ' if condition else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


# 1. Scherrer equation: textbook value and round trip.
fwhm = float(scherrer_fwhm(30.0, 10.0))  # Cu Kα1, K = 0.9, L = 10 nm
check('Scherrer FWHM at 2θ=30°, 10 nm', abs(fwhm - 0.8225) < 0.002, f'{fwhm:.4f}°')
check('Scherrer size round trip', abs(float(scherrer_size(30.0, fwhm)) - 10) < 1e-9)
check('Instrument broadening removed in quadrature', abs(float(scherrer_size(30.0, np.hypot(fwhm, 0.1), instrument_deg=0.1)) - 10) < 1e-6)


def card(lines, label):
    peaks = np.array(lines, dtype=float)
    return dict(label=label, peaks=peaks, kind='sticks',
                records=[{'2theta': t, 'intensity': i, 'h': 1, 'k': n, 'l': 0} for n, (t, i) in enumerate(lines)])


# 2. A simulated 8 nm nanocrystal pattern of phase A, with noise.
A = card([[28.4, 100], [33.0, 35], [47.4, 60], [56.3, 30], [69.2, 12]], 'Phase A')
B = card([[31.0, 100], [44.9, 70], [53.2, 40], [65.0, 25]], 'Phase B')
C = card([[28.4, 60], [37.6, 100], [50.1, 45], [61.8, 30]], 'Phase C (shares one line)')
profile = broaden(A['peaks'], 8.0, xmin=10, xmax=80, step=0.02)
rng = np.random.default_rng(1)
y = profile[:, 1] + rng.normal(0, 0.01, len(profile))
peaks = sample_peaks(profile[:, 0], y / y.max(), 10, 80, prominence=0.05)
check('Peaks found in simulated pattern', len(peaks) == 5, ', '.join(f"{p['two_theta']:.2f}" for p in peaks))
sizes = scherrer_size([p['two_theta'] for p in peaks], [p['fwhm'] for p in peaks])
median = float(np.median(sizes))
check('Recovered crystallite size ≈ 8 nm', abs(median - 8) < 0.8, f'{median:.2f} nm')

# 3. Ranking: the true phase first, with every peak assigned to it.
ranked, assignments = match(peaks, [B, C, A], 10, 80, tolerance=0.3)
check('True phase ranked first', ranked[0]['label'] == 'Phase A', ' > '.join(f"{r['label']} {r['score']:.2f}" for r in ranked))
check('True phase score high, decoys low', ranked[0]['score'] > 0.9 and ranked[-1]['score'] < 0.3)
check('Every sample peak assigned to Phase A', all(a['matches'] and a['matches'][0]['label'] == 'Phase A' for a in assignments))

# 4. A two-phase mixture leaves no unexplained peaks only when both phases are considered.
mix = broaden(np.vstack([A['peaks'], B['peaks'] * [1, 0.6]]), 12.0, xmin=10, xmax=80)
peaks = sample_peaks(mix[:, 0], mix[:, 1], 10, 80, prominence=0.04)
ranked, assignments = match(peaks, [A, B, C], 10, 80, tolerance=0.3)
top_two = {r['label'] for r in ranked[:2]}
check('Mixture: both phases ranked on top', top_two == {'Phase A', 'Phase B'}, ' > '.join(r['label'] for r in ranked))
check('Mixture: no unassigned peaks', all(a['matches'] for a in assignments))

# 5. Profile (XY) cards are matched through their own peaks.
xy = dict(label='XY card', kind='profile', peaks=broaden(A['peaks'], 20, xmin=10, xmax=80), records=[])
ranked, _ = match(sample_peaks(profile[:, 0], profile[:, 1], 10, 80), [xy, B], 10, 80)
check('XY profile card matches', ranked[0]['label'] == 'XY card' and ranked[0]['score'] > 0.8, f"{ranked[0]['score']:.2f}")

# 6. Very large crystallites: the simulated profile must keep very narrow peaks (measured directly on the curve).
big = broaden(A['peaks'], 500.0, xmin=10, xmax=80)
x, y = big[:, 0], big[:, 1]
near = (x > 27.9) & (x < 28.9)
xs, ys = x[near], y[near]
top = int(np.argmax(ys))
half = ys[top] / 2
left = np.interp(half, ys[:top + 1], xs[:top + 1])
right = np.interp(half, ys[top:][::-1], xs[top:][::-1])
expected = float(scherrer_fwhm(28.4, 500.0))
check('500 nm crystallites: narrow peak keeps its Scherrer width', abs((right - left) - expected) / expected < 0.1,
      f'{right - left:.4f}° vs {expected:.4f}°')
check('500 nm crystallites: peak top at the card position', abs(xs[top] - 28.4) < expected / 2, f'{xs[top]:.4f}°')

print(f'\n{sum(results)}/{len(results)} passed')
sys.exit(0 if all(results) else 1)
