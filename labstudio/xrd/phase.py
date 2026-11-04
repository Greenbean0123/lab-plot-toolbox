"""Phase identification helpers for nanocrystal XRD.

- Scherrer broadening: turn a stick card into the profile a crystallite of a given size would give.
- Peak finding + Scherrer size from measured peak widths.
- Multi-phase matching: score every reference card against one sample pattern.

All angles are 2θ in degrees, wavelengths in Å, sizes in nm.
"""
import numpy as np
from ..numerics import find_peaks, savgol


def scherrer_fwhm(two_theta, size_nm, wavelength=1.54059, k=0.9):
    """FWHM in degrees 2θ for crystallite size L: β = Kλ / (L cos θ)."""
    theta = np.deg2rad(np.asarray(two_theta, dtype=float) / 2)
    beta = k * wavelength / (size_nm * 10 * np.cos(theta))  # radians; L in Å
    return np.rad2deg(beta)


def scherrer_size(two_theta, fwhm_deg, wavelength=1.54059, k=0.9, instrument_deg=0.0):
    """Crystallite size in nm from a measured FWHM, after removing instrument broadening (Gaussian, in quadrature)."""
    fwhm = np.asarray(fwhm_deg, dtype=float)
    corrected = np.sqrt(np.clip(fwhm ** 2 - instrument_deg ** 2, 0, None))
    theta = np.deg2rad(np.asarray(two_theta, dtype=float) / 2)
    with np.errstate(divide='ignore'):
        size_angstrom = k * wavelength / (np.deg2rad(corrected) * np.cos(theta))
    return np.where(corrected > 0, size_angstrom / 10, np.inf)


def broaden(peaks, size_nm, wavelength=1.54059, k=0.9, xmin=5.0, xmax=90.0, step=0.02, eta=0.5):
    """Pseudo-Voigt profile (η Lorentzian fraction) of stick peaks [[2θ, I], ...]; returns [[x, y]] normalized to 1."""
    if not np.isfinite(size_nm) or size_nm <= 0:
        raise ValueError('Crystallite size must be a positive number (nm)')
    peaks = np.asarray(peaks, dtype=float)
    widths = scherrer_fwhm(peaks[:, 0], size_nm, wavelength, k)
    if len(widths):
        step = min(step, max(0.002, float(widths.min()) / 6))  # at least ~6 samples across the narrowest peak
    x = np.arange(xmin, xmax + step / 2, step)
    y = np.zeros_like(x)
    for (center, intensity), w in zip(peaks, widths):
        if intensity <= 0 or center < xmin - 5 * w or center > xmax + 5 * w:
            continue
        dx = (x - center) / (w / 2)
        y += intensity * (eta / (1 + dx ** 2) + (1 - eta) * np.exp(-np.log(2) * dx ** 2))
    if y.max() <= 0:
        raise ValueError('No reference peaks inside the plotted 2θ range')
    return np.column_stack([x, y / y.max()])


def sample_peaks(x, y, xmin, xmax, prominence=0.05):
    """Peaks of a normalized, background-subtracted pattern with their FWHM in degrees."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = (x >= xmin) & (x <= xmax)
    x, y = x[keep], y[keep]
    if len(x) < 5:
        return []
    # Smooth over ~0.25° and ignore features narrower than ~0.08°, so counting noise is not taken for peaks.
    step = float(np.median(np.diff(x))) if len(x) > 1 else 0.02
    window = max(5, int(round(0.25 / step)) | 1)
    if window < len(y):
        smooth = savgol(y, window, 2)
        noise = 1.4826 * float(np.median(np.abs(y - smooth - np.median(y - smooth))))
        y = smooth
    else:
        noise = 0.0
    index, prominences, _, left, right = find_peaks(y, prominence=max(prominence, 6 * noise), width=max(1.0, 0.08 / step))
    if not len(index):
        return []
    positions = np.arange(len(x))
    result = []
    for i, l, r, prom in zip(index, left, right, prominences):
        fwhm = float(np.interp(r, positions, x) - np.interp(l, positions, x))
        result.append(dict(two_theta=float(x[i]), height=float(y[i]), prominence=float(prom), fwhm=fwhm))
    return result


def card_lines(card, xmin, xmax, min_relative=5.0):
    """Stick lines of a card inside the range, intensity relative to the card maximum (%), with hkl when known."""
    peaks = np.asarray(card['peaks'], dtype=float)
    if card.get('kind') == 'profile':  # measured / simulated curve (XY): use its peaks as lines
        top = peaks[:, 1].max()
        found = sample_peaks(peaks[:, 0], peaks[:, 1] / top, xmin, xmax, prominence=0.03) if top > 0 else []
        return [dict(two_theta=p['two_theta'], relative=100 * p['height'], hkl='')
                for p in found if 100 * p['height'] >= min_relative]
    records = card.get('records') or []
    top = peaks[:, 1].max() if len(peaks) else 0
    lines = []
    for i, (angle, intensity) in enumerate(peaks):
        relative = 100 * intensity / top if top > 0 else 0
        if xmin <= angle <= xmax and relative >= min_relative:
            rec = records[i] if i < len(records) and isinstance(records[i], dict) else {}
            hkl = (rec.get('h'), rec.get('k'), rec.get('l'))
            lines.append(dict(two_theta=float(angle), relative=float(relative),
                              hkl='({}{}{})'.format(*hkl) if None not in hkl else ''))
    return lines


def match(peaks, cards, xmin, xmax, tolerance=0.3, min_relative=5.0):
    """Score each card against the sample peaks.

    found:     intensity-weighted share of the card's lines that have a sample peak within ±tolerance.
    explained: height-weighted share of the sample peaks that this card accounts for.
    score:     geometric mean of the two (1 = every line found and every sample peak explained).
    Returns (results sorted by score, per-peak assignments).
    """
    results = []
    total_height = sum(p['height'] for p in peaks) or 1
    assignments = [dict(peak, matches=[]) for peak in peaks]
    for card_index, card in enumerate(cards):
        lines = card_lines(card, xmin, xmax, min_relative)
        if not lines:
            results.append(dict(index=card_index, label=card['label'], found=0.0, explained=0.0, score=0.0,
                                lines=0, lines_found=0, shift=None))
            continue
        weight_found, offsets, explained_height, n_found = 0.0, [], 0.0, 0
        for line in lines:
            nearest = min(peaks, key=lambda p: abs(p['two_theta'] - line['two_theta']), default=None)
            if nearest is not None and abs(nearest['two_theta'] - line['two_theta']) <= tolerance:
                weight_found += line['relative']
                offsets.append(nearest['two_theta'] - line['two_theta'])
                n_found += 1
        for peak, assignment in zip(peaks, assignments):
            nearest = min(lines, key=lambda l: abs(l['two_theta'] - peak['two_theta']))
            delta = peak['two_theta'] - nearest['two_theta']
            if abs(delta) <= tolerance:
                explained_height += peak['height']
                assignment['matches'].append(dict(card=card_index, label=card['label'], hkl=nearest['hkl'],
                                                  delta=delta, relative=nearest['relative']))
        found = weight_found / sum(l['relative'] for l in lines)
        explained = explained_height / total_height
        results.append(dict(index=card_index, label=card['label'], found=found, explained=explained,
                            score=float(np.sqrt(found * explained)), lines=len(lines), lines_found=n_found,
                            shift=float(np.mean(offsets)) if offsets else None))
    results.sort(key=lambda r: r['score'], reverse=True)
    rank = {r['index']: i for i, r in enumerate(results)}
    for assignment in assignments:
        # Best-ranked phase first, then the closest line.
        assignment['matches'].sort(key=lambda m: (rank[m['card']], abs(m['delta'])))
    return results, assignments
