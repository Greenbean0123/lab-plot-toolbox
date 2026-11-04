"""XRD stacked-pattern reading, plotting and data export (copied from XRD Plotter)."""
from pathlib import Path
import sys
import csv
import json
import re

import numpy as np
import pdfplumber
import matplotlib
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator
from .card_formats import read_cif, read_xy, card_title, auto_formula_title
from .card_cache import load_card



def palette_colors(colors, count):
    """Repeat a user-defined ordered color list across sample patterns."""
    from matplotlib.colors import to_hex
    if count < 1:
        return []
    if not colors:
        raise ValueError('Add at least one color to the custom palette')
    validated = [to_hex(color) for color in colors]
    return [validated[i % len(validated)] for i in range(count)]


def read_sample(path):
    """Read ragged refinement CSV: only theta, observation and background are required."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = [s.strip().lstrip('#').lower().replace(' ', '') for s in next(reader)]
        def column(names):
            for name in names:
                if name in header:
                    return header.index(name)
            raise ValueError(f'{Path(path).name}: missing column {names}')
        ix = column(['twotheta', '2theta', 'two_theta'])
        iy = column(['yobs', 'obs', 'observed', 'intensity'])
        ib = column(['bkg', 'background', 'bg'])
        rows = []
        for line, row in enumerate(reader, 2):
            if not row or not any(s.strip() for s in row):
                continue
            try:
                rows.append([float(row[i]) for i in (ix, iy, ib)])
            except (ValueError, IndexError) as e:
                raise ValueError(f'{Path(path).name}: invalid data on line {line}') from e
    a = np.asarray(rows, dtype=float)
    if a.ndim != 2 or len(a) < 2 or not np.isfinite(a).all():
        raise ValueError(f'{path}: need at least two finite data rows')
    a = a[np.argsort(a[:, 0], kind='stable')]
    corrected = a[:, 1] - a[:, 2]
    maximum = float(corrected.max())
    if maximum <= 0:
        raise ValueError(f'{path}: max(obs - background) must be positive')
    return dict(path=str(path), label=Path(path).stem, x=a[:, 0], obs=a[:, 1],
                background=a[:, 2], corrected=corrected, normalized=corrected / maximum,
                normalization_max=maximum)


def read_card(path, wavelength=1.54059, upper_angle=100):
    return load_card(path, wavelength, upper_angle, _parse_card)


def _parse_card(path, wavelength=1.54059, upper_angle=100):
    path = Path(path)
    if path.suffix.lower() == '.cif':
        return read_cif(path, wavelength, upper_angle)
    if path.suffix.lower() == '.xy':
        return read_xy(path)
    if path.suffix.lower() not in ('.csv', '.pdf'):
        raise ValueError('Supported card formats: PDF, CSV, CIF and XY')
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        peaks = [[float(r['2theta']), float(r['intensity'])] for r in rows]
        return dict(path=str(path), label=auto_formula_title(path.stem), peaks=np.asarray(peaks), records=rows)
    records = {}
    texts = []
    # Each line may contain two independent seven-column records.
    pattern = re.compile(r'(?<![\d.])(?P<n>\d+)\s+(?P<t>\d+\.\d+)\s+(?P<d>\d+\.\d+)\s+(?P<i>\d+\.\d+)\s+(?P<h>-?\d+)\s+(?P<k>-?\d+)\s+(?P<l>-?\d+)(?![\d.])')
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            texts.append(text)
            in_table = False
            for line in text.splitlines():
                if 'No.' in line and '2Theta' in line and 'Intensity' in line:
                    in_table = True
                    continue
                if in_table and line.startswith('Note:'):
                    in_table = False
                if in_table:
                    for match in pattern.finditer(line):
                        r = match.groupdict()
                        n = int(r['n'])
                        record = dict(no=n, **{'2theta': float(r['t'])}, d=float(r['d']),
                                      intensity=float(r['i']), h=int(r['h']), k=int(r['k']), l=int(r['l']))
                        if n in records and records[n] != record:
                            raise ValueError(f'{path.name}: conflicting peak row {n}')
                        records[n] = record
    if not records or sorted(records) != list(range(1, max(records) + 1)):
        raise ValueError(f'{path.name}: cannot read complete peak table; use a CSV with 2theta,intensity columns')
    text = '\n'.join(texts)
    name = re.search(r'^Name:\s*(.*?)\s+I/Ic', text, re.M)
    formula = re.search(r'^Formula:\s*(.*)$', text, re.M)
    number = re.search(r'PDF Card No\.:\s*(\S+)', text)
    wavelength = re.search(r'calculated with wavelength\s*=\s*([\d.]+)', text)
    rows = [records[n] for n in sorted(records)]
    label = card_title(name.group(1) if name else path.stem,
                       formula.group(1) if formula else '', number.group(1) if number else '')
    return dict(path=str(path), label=label, records=rows,
                card_number=number.group(1) if number else '',
                wavelength=float(wavelength.group(1)) if wavelength else None,
                peaks=np.array([[r['2theta'], r['intensity']] for r in rows]))


def make_figure(samples, cards, xmin=10, xmax=90, title='', color='#0000dd', spacing=1.35, dpi=400, width_cm=34.29, height_cm=None, xlabel='2θ (°)', ylabel='Intensity(a.u.)', wavelength=1.54059, display_order=None, frame_linewidth=1.0, pattern_linewidth=0.85, card_linewidth=0.85, reference_baseline_linewidth=0.45):
    for name, value in [('Frame', frame_linewidth), ('Pattern', pattern_linewidth),
                        ('Reference', card_linewidth), ('Reference baseline', reference_baseline_linewidth)]:
        if not np.isfinite(value) or value < 0:
            raise ValueError(f'{name} line width must be a finite, non-negative number (pt)')
    if not isinstance(dpi, (int, float)) or not np.isfinite(dpi) or dpi <= 0 or int(dpi) != dpi:
        raise ValueError('DPI must be a positive whole number')
    if not samples and not cards:
        fig = Figure(figsize=(width_cm / 2.54, height_cm / 2.54 if height_cm else 4.5), facecolor='white')
        fig.text(0.5, 0.5, 'No visible patterns', ha='center', va='center', color='#64748b', fontsize=14)
        return fig
    if xmax <= xmin or spacing < 1.05:
        raise ValueError('Require xmax > xmin and spacing >= 1.05')
    matplotlib.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['DejaVu Sans', 'Arial'],
                               'font.size': 12, 'svg.fonttype': 'none', 'pdf.fonttype': 42})
    total = len(samples) + len(cards)
    if not np.isfinite(width_cm) or width_cm <= 0:
        raise ValueError('Image width must be positive (cm)')
    if height_cm is not None and (not np.isfinite(height_cm) or height_cm <= 0):
        raise ValueError('Image height must be positive (cm), or leave it blank for automatic height')
    height_inches = height_cm / 2.54 if height_cm is not None else max(4.5, total * 1.12)
    fig = Figure(figsize=(width_cm / 2.54, height_inches), facecolor='white')
    left = 0.08 if ylabel else 0.055
    ax = fig.add_axes([left, 0.14, 0.98 - left, 0.81])
    top_to_bottom = samples + cards
    if display_order is not None:
        ranks = {identifier: index for index, identifier in enumerate(display_order)}
        top_to_bottom = sorted(top_to_bottom, key=lambda row: ranks.get(row.get('order_id'), len(ranks)))
    rows = list(reversed(top_to_bottom))
    bottoms = []
    for i, row in enumerate(rows):
        base = i * spacing
        if 'normalized' in row:
            mask = (row['x'] >= xmin) & (row['x'] <= xmax)
            if not mask.any():
                raise ValueError(f"{row['label']}: no data in selected range")
            ax.plot(row['x'][mask], row['normalized'][mask] + base, color=row.get('color') or color, lw=pattern_linewidth)
            bottoms.append(base + min(0, row['normalized'][mask].min()))
        else:
            peaks = row['peaks']
            if len(peaks) == 0 or not np.isfinite(peaks).all() or peaks[:, 1].max() <= 0 or (row.get('kind') != 'profile' and (peaks[:, 1] < 0).any()):
                raise ValueError(f"{row['label']}: invalid card peaks")
            mask = (peaks[:, 0] >= xmin) & (peaks[:, 0] <= xmax)
            # Coincident lines remain separate, preserving the supplied table intensities.
            ax.hlines(base, xmin, xmax, color='#777777', lw=reference_baseline_linewidth)
            if row.get('kind') == 'profile':
                ax.plot(peaks[mask, 0], base + peaks[mask, 1] / peaks[:, 1].max(), color='black', lw=card_linewidth)
                bottoms.append(base + min(0, peaks[mask, 1].min() / peaks[:, 1].max()) if mask.any() else base)
            else:
                ax.vlines(peaks[mask, 0], base, base + peaks[mask, 1] / peaks[:, 1].max(), color='black', lw=card_linewidth)
                bottoms.append(base)
        label_style = row.get('label_style', {})
        ax.text(xmin + (xmax - xmin) * label_style.get('x_percent', 99.2) / 100,
                base + label_style.get('y_offset', 1.08),
                auto_formula_title(row['label']) if 'normalized' not in row else row['label'],
                ha=label_style.get('alignment', 'right'), va='bottom',
                fontsize=label_style.get('font_size', 11),
                bbox=dict(facecolor='white', edgecolor='none', pad=0.8))
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(min(bottoms) - 0.06, (total - 1) * spacing + 1.45)
    ax.set_xlabel(xlabel, fontsize=14)
    ax.set_ylabel(ylabel, fontsize=14)
    ax.set_yticks([])
    ax.xaxis.set_major_locator(MultipleLocator(5))
    ax.xaxis.set_minor_locator(MultipleLocator(1))
    ax.tick_params(axis='x', which='major', direction='out', length=7, width=1)
    ax.tick_params(axis='x', which='minor', direction='out', length=4, width=0.8)
    for spine in ax.spines.values():
        spine.set_linewidth(frame_linewidth)
    if title:
        fig.suptitle(title, fontsize=14, y=0.99)
    return fig


def export(fig, base, samples, cards, settings):
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    for ext in ('png', 'pdf', 'svg'):
        fig.savefig(str(base) + '.' + ext, dpi=settings.get('dpi', 400))
    export_data(base.parent / (base.name + '_data'), samples, cards, settings)


def export_data(data_dir, samples, cards, settings):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(samples, 1):
        np.savetxt(data_dir / f'sample_{i:02d}_processed.csv',
                   np.column_stack([s[k] for k in ['x', 'obs', 'background', 'corrected', 'normalized']]),
                   delimiter=',', header='2theta,obs,background,obs_minus_background,normalized', comments='')
    for i, card in enumerate(cards, 1):
        with (data_dir / f'card_{i:02d}_peaks.csv').open('w', newline='', encoding='utf-8-sig') as f:
            fields = list(card['records'][0])
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(card['records'])
    metadata = dict(settings=settings, formula='(obs - background) / max(obs - background)',
                    normalization_scope='entire scan, before display range filtering', negative_values='preserved',
                    samples=[{k: s[k] for k in ['path', 'label', 'normalization_max', 'color', 'label_style'] if k in s} for s in samples],
                    cards=[{k: v for k, v in c.items() if k not in ['peaks', 'records']} for c in cards])
    (data_dir / 'processing.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

