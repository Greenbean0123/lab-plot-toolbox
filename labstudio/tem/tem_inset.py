"""Calibrated TEM insets sampled from the clean source, before annotations."""
from PIL import Image, ImageDraw, ImageFont


def compose(image, source, nm_per_pixel, roi, width=500, position=None,
            color='white', border=4, connector=True, bar_nm=None,
            connector_width=4, connector_color='white', show_scalebar=True):
    w, h = image.size
    x0, y0, x1, y1 = map(int, roi)
    if not (0 <= x0 < x1 <= source.width and 0 <= y0 < y1 <= source.height):
        raise ValueError('Inset source rectangle must be inside the cleaned image')
    width = int(width)
    height = round(width * (y1-y0)/(x1-x0))
    border = int(border)
    if width < 80 or height < 80 or width > w or height > h or not 1 <= border <= 20:
        raise ValueError('Inset must fit the image, be at least 80 px on each side, and border 1–20 px')
    left, top = position or (max(0, w-width-30), 30)
    left, top = int(left), int(top)
    if not (0 <= left <= w-width and 0 <= top <= h-height):
        raise ValueError('Inset position outside image; reduce width or change X/Y')
    zoom = width/(x1-x0)
    scale = nm_per_pixel/zoom
    length = bar_nm
    if length is None:
        target = width*.28*scale
        decade = 10**__import__('math').floor(__import__('math').log10(target))
        length = min([v*decade for v in (.1, .2, .5, 1, 2, 5, 10)], key=lambda v: abs(v-target))
    pixels = round(float(length)/scale)
    font = ImageFont.load_default(size=max(12, round(width*.045)))
    label = f'{length:g} nm'
    tile = source.crop(roi).resize((width, height), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(tile)
    tb = d.textbbox((0, 0), label, font=font)
    tw, th = tb[2]-tb[0], tb[3]-tb[1]
    margin = max(10, round(width*.04))
    thickness = max(3, round(width*.01))
    if show_scalebar and (pixels < 1 or max(pixels, tw)+2*margin > width or th+thickness+3*margin > height):
        raise ValueError('Inset scale bar does not fit; reduce inset bar length or enlarge inset')
    white = 255 if image.mode == 'L' else (255,)*3
    black = 0 if image.mode == 'L' else (0,)*3
    ink = white if color == 'white' else black
    bx, by = width-margin-pixels, height-margin-thickness
    tx = width-margin-tw
    if show_scalebar:
        d.rectangle((bx, by, bx+pixels-1, by+thickness-1), fill=ink)
        d.text((tx-tb[0], by-th-margin-tb[1]), label, font=font, fill=ink)
    out = image.copy()
    d = ImageDraw.Draw(out)
    if connector:
        connector_width = int(connector_width)
        if not 1 <= connector_width <= 30:
            raise ValueError('Connector thickness must be 1–30 px')
        line_ink = white if connector_color == 'white' else black
        dx, dy = left+width/2-(x0+x1)/2, top+height/2-(y0+y1)/2
        if abs(dx) >= abs(dy):
            sx, ex = (x1-1, left) if dx >= 0 else (x0, left+width-1)
            lines = [(sx, y0, ex, top), (sx, y1-1, ex, top+height-1)]
        else:
            sy, ey = (y1-1, top) if dy >= 0 else (y0, top+height-1)
            lines = [(x0, sy, left, ey), (x1-1, sy, left+width-1, ey)]
        for line in lines:
            d.line(line, fill=line_ink, width=connector_width)
    d.rectangle((x0, y0, x1-1, y1-1), outline=ink, width=border)
    out.paste(tile, (left, top))
    d.rectangle((left, top, left+width-1, top+height-1), outline=ink, width=border)
    return out, dict(source_box=list(roi), box=[left, top, left+width, top+height],
                     magnification=zoom, nm_per_pixel=scale, bar_nm=length,
                     show_scalebar=show_scalebar,
                     bar_pixels=pixels if show_scalebar else None,
                     pixel_rounding_error=abs(pixels-length/scale) if show_scalebar else None)
