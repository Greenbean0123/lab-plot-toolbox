"""UV-Vis spectrum reading and normalization (copied unchanged from UV-Vis Plotter)."""
import csv
import math
from pathlib import Path

import numpy as np

MODES = ('Global maximum', 'Maximum in wavelength range', 'Raw absorbance')


def read_spectrum(path):
    text = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            content = text.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError('Cannot decode this file.')
    lines = content.splitlines()
    marker = next((i for i, s in enumerate(lines) if s.strip().upper().startswith('XYDATA')), None)
    rows = []
    started = False
    for number, line in enumerate(lines, 1):
        if marker is not None and number <= marker + 1:
            continue
        if not line.strip():
            continue
        delimiter = '\t' if '\t' in line else ';' if ';' in line else ','
        cells = next(csv.reader([line], delimiter=delimiter))
        if len(cells) < 2:
            cells = line.split()
        try:
            pair = [float(cells[0]), float(cells[1])]
        except (ValueError, IndexError):
            if started:
                if marker is not None:
                    break
                raise ValueError(f'Invalid numeric data on line {number}.')
            continue
        if not all(math.isfinite(v) for v in pair):
            raise ValueError(f'Non-finite data on line {number}.')
        rows.append(pair)
        started = True
    if len(rows) < 2:
        raise ValueError('At least two wavelength / absorbance rows are required.')
    data = np.asarray(rows)
    data = data[np.argsort(data[:, 0], kind='stable')]
    return data[:, 0], data[:, 1]


def normalize(x, y, mode, low=None, high=None):
    if mode == 'Raw absorbance':
        return y.copy(), None
    selected = y
    if mode == 'Maximum in wavelength range':
        if low is None or high is None or not np.isfinite([low, high]).all() or low >= high:
            raise ValueError('Normalization range must have a finite minimum below its maximum.')
        selected = y[(x >= low) & (x <= high)]
        if not len(selected):
            raise ValueError('No data points inside the normalization range.')
    peak = float(selected.max())
    if peak <= 0:
        raise ValueError('Normalization requires a positive maximum absorbance.')
    return y / peak, peak
