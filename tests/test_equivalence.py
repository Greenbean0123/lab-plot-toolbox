"""Check that the toolbox produces the same results as the original standalone programs.

Run:  .venv\\Scripts\\python.exe tests\\test_equivalence.py
Needs the original projects next to this folder (../TEM重绘制软件, ../XRD软件 4); skips those that are missing.
"""
import sys
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import numpy as np
import tifffile
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ORIGINAL_TEM = ROOT.parent / 'TEM重绘制软件' / 'outputs'
ORIGINAL_XRD = ROOT.parent / 'XRD软件 4' / 'src'
ORIGINAL_UV = ROOT.parent / 'XRD软件 4' / 'uvvis'
results = []


def check(name, condition, detail=''):
    results.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + (f'  ({detail})' if detail else ''))


def tem(tmp):
    from labstudio.tem import tem_scalebar_distribution as new
    sys.path.insert(0, str(ORIGINAL_TEM))
    import tem_scalebar_distribution as old
    rng = np.random.default_rng(3)
    image = tmp / 'tem.tif'
    array = np.clip(rng.normal(150, 20, (900, 1024)), 0, 255).astype(np.uint8)
    array[820:] = 0
    array[850:858, 600:751] = 255
    tifffile.imwrite(image, array)
    csv_path = tmp / 'd.csv'
    csv_path.write_text('diameter_nm\n' + '\n'.join(f'{v:.3f}' for v in rng.normal(40, 6, 80)), encoding='utf-8')
    argv = ['--nm-per-pixel', '0.4', '--erase-box', '0,820,1024,900', '--inset-roi', '100,100,260,220',
            '--inset-width', '300', '--inset-x', '650', '--inset-y', '30', '--bar-nm', '50']
    for label, extra in (('scale bar + inset', []), ('with histogram overlay', ['--overlay-distribution', '--csv', str(csv_path)])):
        outs = []
        for module in (old, new):
            args = module.create_parser().parse_args(argv + extra + ['--output-dir', str(tmp)])
            img, report = module.redraw(image, args, render_only=True)
            outs.append((np.asarray(img), report))
        same = outs[0][0].shape == outs[1][0].shape and np.array_equal(outs[0][0], outs[1][0])
        check(f'TEM redraw identical: {label}', same, f"bar {outs[1][1]['bar_pixels']} px")
    stats = []
    for module in (old, new):
        args = module.create_parser().parse_args(['--csv', str(csv_path), '--output-dir', str(tmp)])
        args.distribution_formats = ['png']
        stats.append(module.distribution(args))
    check('TEM size distribution statistics identical',
          all(stats[0][k] == stats[1][k] for k in ('mean_nm', 'std_nm', 'N')), f"mean {stats[1]['mean_nm']:.3f} nm")


def xrd(tmp):
    from labstudio.xrd import xrd_core as new
    sys.path.insert(0, str(ORIGINAL_XRD))
    import xrd_plotter as old
    demo = ROOT / 'demo-data' / 'xrd'
    samples = sorted(demo.glob('*.csv'))
    cards = sorted(list(demo.glob('*.xy')) + list(demo.glob('*.pdf')))
    settings = dict(xmin=10, xmax=90, title='t', color='#0000dd', spacing=1.35, dpi=150, width_cm=20, height_cm=None,
                    xlabel='2θ (°)', ylabel='Intensity(a.u.)', wavelength=1.54059)
    images = []
    for module in (old, new):
        s = [module.read_sample(p) for p in samples]
        c = [module.read_card(p, wavelength=1.54059, upper_angle=90) for p in cards]
        fig = module.make_figure(s, c, **settings)
        path = tmp / f'xrd_{module.__name__.split(".")[-1]}.png'
        fig.savefig(path, dpi=150)
        images.append(np.asarray(Image.open(path)))
    check('XRD figure pixels identical', images[0].shape == images[1].shape and np.array_equal(*images),
          f'{len(samples)} patterns, {len(cards)} cards')


def uvvis(tmp):
    from labstudio.uvvis import core as new
    sys.path.insert(0, str(ORIGINAL_UV))
    import uvvis_plotter as old
    x = np.arange(300, 900, 1.5)
    path = tmp / 'uv.csv'
    path.write_text('nm,abs\n' + '\n'.join(f'{a},{np.exp(-((a - 520) / 40) ** 2):.6f}' for a in x), encoding='utf-8')
    a, b = old.read_spectrum(path), new.read_spectrum(path)
    same = all(np.array_equal(p, q) for p, q in zip(a, b))
    for mode in new.MODES:
        same &= np.array_equal(old.normalize(*a, mode, 400, 600)[0], new.normalize(*b, mode, 400, 600)[0])
    check('UV-Vis reading and all normalization modes identical', same)


if __name__ == '__main__':
    try:
        import scipy  # noqa: F401  (the original programs import it)
    except ImportError:
        print('SKIP: the original programs need SciPy; run pip install -r requirements-dev.txt first.')
        sys.exit(0)
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        for name, test, needed in (('TEM', tem, ORIGINAL_TEM), ('XRD', xrd, ORIGINAL_XRD), ('UV-Vis', uvvis, ORIGINAL_UV)):
            if not needed.is_dir():
                print(f'SKIP {name}: original project not found at {needed}')
                continue
            try:
                test(tmp)
            except Exception as error:
                import traceback
                traceback.print_exc()
                check(f'{name} comparison ran', False, str(error))
    failed = [n for n, ok in results if not ok]
    print(f'\n{len(results) - len(failed)}/{len(results)} passed')
    sys.exit(1 if failed else 0)
