"""Draw the three tool icons (labstudio/assets/xrd.png, tem.png, uvvis.png) in the app-icon style.

Rounded tile with a diagonal gradient and a white line drawing. Run after changing the design.
"""
import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
ASSETS = Path(__file__).resolve().parent.parent / 'labstudio' / 'assets'
WHITE = (255, 255, 255, 255)


def tile(top, bottom):
    """Rounded square with a top-left -> bottom-right gradient and a soft inner highlight."""
    grad = Image.new('RGBA', (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x * .4 + y * .6) / S
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
    icon = Image.new('RGBA', (S, S))
    icon.paste(grad, mask=mask)
    return icon, Image.new('RGBA', (S, S), (0, 0, 0, 0))


def finish(icon, layer):
    return Image.alpha_composite(icon, layer)


def line(draw, points, width, fill=WHITE):
    draw.line(points, fill=fill, width=width, joint='curve')
    r = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def xrd():
    icon, layer = tile((84, 92, 246), (156, 84, 232))
    g = ImageDraw.Draw(layer)
    # Measured pattern: a few sharp Bragg peaks on a flat background.
    peaks = [(.24, 300, 14), (.43, 150, 18), (.62, 220, 14), (.8, 110, 20)]
    points = []
    for i in range(241):
        u = i / 240
        x = 190 + u * (S - 380)
        y = 520 - sum(h * math.exp(-((u - c) * S / w) ** 2) for c, h, w in peaks)
        points.append((x, y))
    line(g, points, 34)
    # Reference card: sticks at the same positions, heights following the card intensities.
    base = 790
    g.line([(190, base), (S - 190, base)], fill=(255, 255, 255, 150), width=18)
    for c, h, _ in peaks:
        x = 190 + c * (S - 380)
        g.line([(x, base), (x, base - h * .55)], fill=WHITE, width=30)
        g.ellipse((x - 15, base - h * .55 - 15, x + 15, base - h * .55 + 15), fill=WHITE)
    return finish(icon, layer)


def tem():
    icon, layer = tile((16, 150, 160), (34, 96, 168))
    g = ImageDraw.Draw(layer)
    # Nanoparticles: filled discs with a brighter rim, like a bright-field micrograph inverted to white.
    for cx, cy, r, alpha in ((350, 380, 120, 255), (590, 330, 82, 235), (690, 540, 104, 255), (450, 610, 70, 215),
                             (280, 600, 46, 190), (760, 330, 40, 175)):
        g.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, round(alpha * .3)), outline=(255, 255, 255, alpha),
                  width=26)
    # Scale bar with end caps.
    y = 805
    g.rounded_rectangle((545, y - 22, 835, y + 22), radius=22, fill=WHITE)
    return finish(icon, layer)


def uvvis():
    icon, layer = tile((248, 136, 58), (226, 64, 128))
    g = ImageDraw.Draw(layer)
    # Visible-spectrum strip along the bottom.
    left, right, top, bottom = 200, S - 200, 770, 830
    strip = Image.new('RGBA', (right - left, bottom - top))
    sp = strip.load()
    for x in range(strip.width):
        hue = .78 * (1 - x / strip.width)  # violet -> red
        r, gg, b = colorsys.hsv_to_rgb(hue, .75, 1)
        for y in range(strip.height):
            sp[x, y] = (round(r * 255), round(gg * 255), round(b * 255), 255)
    mask = Image.new('L', strip.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, strip.width - 1, strip.height - 1), radius=strip.height // 2, fill=255)
    layer.paste(strip, (left, top), mask)
    g.rounded_rectangle((left - 6, top - 6, right + 6, bottom + 6), radius=(bottom - top) // 2 + 6, outline=WHITE, width=12)
    # Absorbance curve: a band with a rising UV edge.
    points = []
    for i in range(241):
        u = i / 240
        x = 200 + u * (S - 400)
        y = 650 - 330 * math.exp(-((u - .45) / .16) ** 2) - 120 * math.exp(-u / .08)
        points.append((x, y))
    line(g, points, 36)
    return finish(icon, layer)


for name, make in (('xrd', xrd), ('tem', tem), ('uvvis', uvvis)):
    make().resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / f'{name}.png')
print('icons written to', ASSETS)
