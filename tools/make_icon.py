"""Draw the application icon (assets/app.png, app.ico and app.icns). Run once after changing the design."""
from pathlib import Path
import math
from PIL import Image, ImageDraw

S = 1024
assets = Path(__file__).resolve().parent.parent / 'labstudio' / 'assets'
gradient = Image.new('RGBA', (S, S))
px = gradient.load()
top, bottom = (66, 108, 245), (32, 182, 196)
for y in range(S):
    for x in range(S):
        t = (x * .35 + y * .65) / S
        px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
mask = Image.new('L', (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
icon = Image.new('RGBA', (S, S))
icon.paste(gradient, mask=mask)
layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))  # translucent strokes blend instead of cutting holes
draw = ImageDraw.Draw(layer)
# Three stacked spectra, the top one with a sharp peak.
for row, (base, alpha, peaks) in enumerate([(330, 255, [(.42, 210, 26)]), (520, 215, [(.55, 120, 40), (.3, 60, 30)]), (710, 175, [(.68, 90, 46)])]):
    points = []
    for i in range(121):
        u = i / 120
        x = 175 + u * (S - 350)
        y = base - sum(h * math.exp(-((u - c) * S / w) ** 2) for c, h, w in peaks) - 8 * math.sin(u * 19)
        points.append((x, y))
    draw.line(points, fill=(255, 255, 255, alpha), width=34, joint='curve')
    for p in (points[0], points[-1]):
        draw.ellipse((p[0] - 17, p[1] - 17, p[0] + 17, p[1] + 17), fill=(255, 255, 255, alpha))
full = Image.alpha_composite(icon, layer)
full.save(assets / 'app.icns')  # macOS bundle icon, from the full 1024 px drawing
icon = full.resize((256, 256), Image.Resampling.LANCZOS)
icon.save(assets / 'app.png')
icon.save(assets / 'app.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print('icon written to', assets)
