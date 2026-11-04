"""Small numpy replacements for the few SciPy functions this program used.

SciPy adds ~145 MB to an installation; these follow SciPy's algorithms so results stay the same
(checked against SciPy in tests/test_numerics.py when SciPy is installed).
"""
import math

import numpy as np

_SQRT_2PI = math.sqrt(2 * math.pi)


# ---------- scipy.stats.norm ----------
def normal_fit(data):
    """Maximum-likelihood Gaussian fit (same as scipy.stats.norm.fit): mean and std with ddof=0."""
    data = np.asarray(data, dtype=float)
    mu = data.mean()
    return mu, np.sqrt(((data - mu) ** 2).mean())


def normal_pdf(x, mu, sigma):
    z = (np.asarray(x, dtype=float) - mu) / sigma
    return np.exp(-z * z / 2.0) / _SQRT_2PI / sigma


# ---------- scipy.ndimage.label + find_objects ----------
def component_boxes(mask):
    """Bounding boxes (row slice, column slice) of 4-connected True regions, via row runs and union-find."""
    mask = np.asarray(mask, dtype=bool)
    runs, parent, previous = [], [], []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r, row in enumerate(mask):
        edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        current, j = [], 0
        for s, e in zip(starts, ends):
            index = len(runs)
            runs.append((r, int(s), int(e)))
            parent.append(index)
            current.append(index)
            while j < len(previous) and runs[previous[j]][2] <= s:
                j += 1
            k = j
            while k < len(previous) and runs[previous[k]][1] < e:  # shares at least one column
                a, b = find(index), find(previous[k])
                if a != b:
                    parent[max(a, b)] = min(a, b)
                k += 1
        previous = current
    boxes = {}
    for index, (r, s, e) in enumerate(runs):
        root = find(index)
        r0, r1, c0, c1 = boxes.get(root, (r, r + 1, s, e))
        boxes[root] = (min(r0, r), max(r1, r + 1), min(c0, s), max(c1, e))
    return [(slice(r0, r1), slice(c0, c1)) for r0, r1, c0, c1 in boxes.values()]


# ---------- scipy.signal.savgol_filter (mode='interp') ----------
def savgol(y, window, order=2):
    y = np.asarray(y, dtype=float)
    half = window // 2
    offsets = np.arange(-half, half + 1)
    coeffs = np.linalg.pinv(np.vander(offsets, order + 1, increasing=True))[0]
    out = np.convolve(y, coeffs[::-1], mode='same')
    # Edges: fit one polynomial to the first / last window and evaluate it (SciPy's 'interp' mode).
    for segment, positions, target in ((y[:window], np.arange(half), slice(0, half)),
                                       (y[-window:], np.arange(window - half, window), slice(len(y) - half, len(y)))):
        fit = np.polyfit(np.arange(window), segment, order)
        out[target] = np.polyval(fit, positions)
    return out


# ---------- scipy.signal.find_peaks / peak_prominences / peak_widths ----------
def local_maxima(y):
    """Indices of local maxima; a flat top gives its middle sample (as SciPy)."""
    peaks, i, n = [], 1, len(y)
    while i < n - 1:
        if y[i - 1] < y[i]:
            j = i
            while j + 1 < n - 1 and y[j + 1] == y[i]:
                j += 1
            if y[j + 1] < y[i]:
                peaks.append((i + j) // 2)
            i = j
        i += 1
    return np.asarray(peaks, dtype=int)


def prominences(y, peaks):
    """Prominence and the left / right base index of each peak (SciPy's algorithm, no window)."""
    result, left_bases, right_bases = [], [], []
    for p in peaks:
        i, left_min, left_base = p, y[p], p
        while i >= 0 and y[i] <= y[p]:
            if y[i] < left_min:
                left_min, left_base = y[i], i
            i -= 1
        i, right_min, right_base = p, y[p], p
        while i < len(y) and y[i] <= y[p]:
            if y[i] < right_min:
                right_min, right_base = y[i], i
            i += 1
        result.append(y[p] - max(left_min, right_min))
        left_bases.append(left_base)
        right_bases.append(right_base)
    return np.asarray(result, float), np.asarray(left_bases, int), np.asarray(right_bases, int)


def widths(y, peaks, prom, left_bases, right_bases, rel_height=0.5):
    """Width at prom*rel_height below each peak, with interpolated crossing positions (SciPy's peak_widths)."""
    out, lefts, rights = [], [], []
    for p, pr, lb, rb in zip(peaks, prom, left_bases, right_bases):
        height = y[p] - pr * rel_height
        i = p
        while lb < i and height < y[i]:
            i -= 1
        left = float(i)
        if y[i] < height:
            left += (height - y[i]) / (y[i + 1] - y[i])
        i = p
        while i < rb and height < y[i]:
            i += 1
        right = float(i)
        if y[i] < height:
            right -= (height - y[i]) / (y[i - 1] - y[i])
        out.append(right - left)
        lefts.append(left)
        rights.append(right)
    return np.asarray(out), np.asarray(lefts), np.asarray(rights)


def find_peaks(y, prominence=0.0, width=0.0):
    """Peaks with at least this prominence and half-prominence width (in samples).

    Returns indices, prominences, widths, left and right interpolated half-height positions.
    """
    y = np.asarray(y, dtype=float)
    peaks = local_maxima(y)
    if not len(peaks):
        empty = np.array([])
        return peaks, empty, empty, empty, empty
    prom, lb, rb = prominences(y, peaks)
    keep = prom >= prominence
    peaks, prom, lb, rb = peaks[keep], prom[keep], lb[keep], rb[keep]
    w, left, right = widths(y, peaks, prom, lb, rb)
    keep = w >= width
    return peaks[keep], prom[keep], w[keep], left[keep], right[keep]
