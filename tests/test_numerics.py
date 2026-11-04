"""Compare labstudio.numerics with SciPy on random inputs. Needs SciPy (pip install -r requirements-dev.txt).

Run:  .venv\\Scripts\\python.exe tests\\test_numerics.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from labstudio import numerics as nx  # noqa: E402

try:
    from scipy import ndimage, signal, stats
except ImportError:
    print('SKIP: SciPy is not installed (it is only needed for this comparison test).')
    sys.exit(0)

rng = np.random.default_rng(7)
failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(('PASS ' if ok else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


# Gaussian fit and pdf
worst = 0.0
for _ in range(200):
    data = rng.normal(rng.uniform(5, 80), rng.uniform(1, 20), rng.integers(2, 500))
    a, b = stats.norm.fit(data), nx.normal_fit(data)
    x = np.linspace(data.min() - 10, data.max() + 10, 300)
    worst = max(worst, abs(a[0] - b[0]), abs(a[1] - b[1]), np.max(np.abs(stats.norm.pdf(x, *a) - nx.normal_pdf(x, *b))))
check('normal fit / pdf match SciPy', worst < 1e-12, f'max difference {worst:.1e}')

# Connected components (4-connectivity) bounding boxes
same = True
for _ in range(300):
    mask = rng.random((rng.integers(1, 60), rng.integers(1, 90))) > rng.uniform(.3, .8)
    labels, _ = ndimage.label(mask)
    expected = sorted((s[0].start, s[0].stop, s[1].start, s[1].stop) for s in ndimage.find_objects(labels) if s)
    got = sorted((s[0].start, s[0].stop, s[1].start, s[1].stop) for s in nx.component_boxes(mask))
    same &= expected == got
check('component bounding boxes match ndimage.label + find_objects', same, '300 random masks')

# Savitzky-Golay smoothing
worst = 0.0
for _ in range(200):
    y = rng.normal(size=rng.integers(30, 800)).cumsum()
    window = int(rng.integers(2, 12)) * 2 + 1
    if window >= len(y):
        continue
    worst = max(worst, np.max(np.abs(signal.savgol_filter(y, window, 2) - nx.savgol(y, window, 2))))
check('Savitzky-Golay matches savgol_filter', worst < 1e-8, f'max difference {worst:.1e}')

# Peak finding, prominence and half-prominence widths
same, worst = True, 0.0
for _ in range(300):
    x = np.linspace(0, 1, rng.integers(50, 1500))
    y = sum(rng.uniform(.1, 1) * np.exp(-((x - rng.uniform()) / rng.uniform(.002, .05)) ** 2) for _ in range(rng.integers(1, 8)))
    y = y + rng.normal(0, rng.uniform(0, .03), len(x))
    if rng.random() < .3:
        y = np.round(y, 2)  # plateaus
    prom, width = rng.uniform(0, .2), rng.uniform(0, 6)
    idx, props = signal.find_peaks(y, prominence=prom, width=width)
    got = nx.find_peaks(y, prom, width)
    if not np.array_equal(idx, got[0]):
        same = False
        continue
    if len(idx):
        w, _, left, right = signal.peak_widths(y, idx, rel_height=0.5)
        worst = max(worst, np.max(np.abs(props['prominences'] - got[1])), np.max(np.abs(w - got[2])),
                    np.max(np.abs(left - got[3])), np.max(np.abs(right - got[4])))
check('find_peaks indices identical to SciPy', same, '300 random noisy / plateau signals')
check('prominences and widths match SciPy', worst < 1e-9, f'max difference {worst:.1e}')
sys.exit(1 if failures else 0)
