"""Histogram panels: raster preview, true vector chart over a raster TEM in PDF/SVG."""
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
from .tem_scalebar_distribution import distribution_figure, progress


def chart_args(args):
    if not getattr(args, 'csv', None):
        raise ValueError('Open a diameter CSV before enabling the TEM distribution overlay')
    return SimpleNamespace(csv=Path(args.csv), column=args.column, bins=args.bins,
                           bin_width=args.bin_width, y_mode=args.y_mode)


def panel_figure(args, width):
    fig, report = distribution_figure(args)
    try:
        fig.set_dpi(width / 6.4)
        ax = fig.axes[0]
        for patch in ax.patches:
            patch.set_facecolor(getattr(args, 'overlay_bar_color', '#ff5555'))
            patch.set_edgecolor(getattr(args, 'overlay_bar_color', '#ff5555'))
            patch.set_alpha(getattr(args, 'overlay_bar_alpha', .65))
        ax.lines[0].set_color(getattr(args, 'overlay_curve_color', '#203bff'))
        ax.lines[0].set_alpha(getattr(args, 'overlay_curve_alpha', 1))
        ax.get_legend().remove()
        ax.texts[0].set_text(f"Mean = {report['mean_nm']:.2f} nm\nStd = {report['std_nm']:.2f} nm\nN = {report['N']}")
        ax.xaxis.label.set_fontsize(getattr(args, 'overlay_label_size', 10))
        ax.yaxis.label.set_fontsize(getattr(args, 'overlay_label_size', 10))
        ax.tick_params(labelsize=getattr(args, 'overlay_tick_size', 10))
        ax.texts[0].set_fontsize(getattr(args, 'overlay_stats_size', 10))
        background = getattr(args, 'overlay_background', 'plot only')
        alpha = getattr(args, 'overlay_background_alpha', 1)
        fig.patch.set_alpha(alpha if background == 'white' else 0)
        ax.patch.set_alpha(alpha if background == 'plot only' else 0)
        ax.texts[0].get_bbox_patch().set_alpha(0)
        if background == 'plot only':
            ax.tick_params(colors='white')
            ax.xaxis.label.set_color('white')
            ax.yaxis.label.set_color('white')
        ax.spines[['top', 'right']].set_visible(True)
        return fig, report
    except Exception:
        plt.close(fig)
        raise


@lru_cache(maxsize=8)
def cached_panel(path, modified, size, column, bins, bin_width, mode, width, background, style):
    args = SimpleNamespace(csv=Path(path), column=column, bins=bins, bin_width=bin_width,
                           y_mode=mode, overlay_background=background)
    for key, value in style:
        setattr(args, key, value)
    fig, report = panel_figure(args, width)
    try:
        fig.canvas.draw()
        panel = Image.fromarray(np.asarray(fig.canvas.buffer_rgba()).copy())
        return panel, report
    finally:
        plt.close(fig)


def compose(image, args, avoid_box=None):
    params = chart_args(args)
    width = int(args.overlay_width)
    if width < 160:
        raise ValueError('Distribution panel width must be at least 160 px')
    w, h = image.size
    # Clamp to the image so a panel remains usable on smaller TIFFs.
    width = min(width, w, int(h / .75))
    if width < 160:
        raise ValueError('TEM image is too small for a distribution panel')
    stat = params.csv.stat()
    style = tuple((key, getattr(args, key, default)) for key, default in (
        ('overlay_bar_color', '#ff5555'), ('overlay_curve_color', '#203bff'),
        ('overlay_bar_alpha', .65), ('overlay_curve_alpha', 1), ('overlay_background_alpha', 1),
        ('overlay_label_size', 10), ('overlay_tick_size', 10), ('overlay_stats_size', 10)))
    for key, value in style:
        if key.endswith('_alpha') and not 0 <= value <= 1:
            raise ValueError('Opacity must be between 0 and 1')
        if key.endswith('_size') and not 0 < value <= 100:
            raise ValueError('Overlay font sizes must be between 0 and 100 pt')
    progress(args, 40, 'Reading CSV, fitting Gaussian and rendering chart (or using cache)')
    panel, stats = cached_panel(str(params.csv.resolve()), stat.st_mtime_ns, stat.st_size,
        params.column, params.bins, params.bin_width, params.y_mode, width, args.overlay_background, style)
    pw, ph = panel.size
    progress(args, 75, 'Compositing distribution onto TEM')
    x = max(0, min(w-pw, int(args.overlay_x) if args.overlay_x is not None else w-pw-20))
    y = max(0, min(h-ph, int(args.overlay_y) if args.overlay_y is not None else h-ph-20))
    if args.overlay_y is None and avoid_box:
        ax0, ay0, ax1, ay1 = avoid_box
        if x < ax1 and x+pw > ax0 and y < ay1 and y+ph > ay0:
            y = max(0, ay0-ph-20)
    output = image.convert('RGB')
    output.paste(panel, (x, y), panel)
    return output, dict(stats, csv=str(params.csv.resolve()), box=[x, y, x+pw, y+ph],
                        background=args.overlay_background, style=dict(style))


def save_vector(base, args, overlay, stem, formats):
    w, h = base.size
    if overlay:
        width = overlay['box'][2] - overlay['box'][0]
        params = chart_args(args)
        params.overlay_background = args.overlay_background
        for key, value in overlay['style'].items():
            setattr(params, key, value)
        fig, _ = panel_figure(params, width)
        try:
            fig.canvas.draw()
            ax = fig.axes[0]
            px, py, aw, ah = ax.get_position().bounds
            fig.set_layout_engine(None)
            dpi = fig.dpi
            fig.set_size_inches(w/dpi, h/dpi)
            x0, y0, x1, y1 = overlay['box']
            ax.set_position([(x0+px*(x1-x0))/w, (h-y1+py*(y1-y0))/h,
                             aw*(x1-x0)/w, ah*(y1-y0)/h])
            # The panel's white background must include its labels and margins.
            if args.overlay_background == 'white':
                from matplotlib.patches import Rectangle
                fig.add_artist(Rectangle((x0/w, (h-y1)/h), (x1-x0)/w, (y1-y0)/h,
                                         transform=fig.transFigure, facecolor='white', alpha=getattr(args, 'overlay_background_alpha', 1), edgecolor='none', zorder=0))
                fig.patch.set_alpha(0)
            ax.set_zorder(1)
            _write(fig, base, stem, formats)
        finally:
            plt.close(fig)
    else:
        fig = plt.figure(figsize=(w/100, h/100), dpi=100)
        try:
            _write(fig, base, stem, formats)
        finally:
            plt.close(fig)


def _write(fig, base, stem, formats):
    ax = fig.add_axes([0, 0, 1, 1], zorder=-1)
    ax.imshow(np.asarray(base), cmap='gray', vmin=0, vmax=255, interpolation='none', aspect='auto')
    ax.set_axis_off()
    for extension in ('pdf', 'svg'):
        if extension in formats:
            fig.savefig(str(stem)+'.'+extension, dpi=fig.dpi)
