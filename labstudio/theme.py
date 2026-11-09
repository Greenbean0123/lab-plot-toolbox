"""One visual language for every tool: colors, fonts, and image-based ttk styles (rounded, light and dark).

Plain ttk draws square, flat widgets. Here every button, field, card, checkbox and scrollbar thumb is an
anti-aliased image element generated with Pillow at the screen's DPI. On a theme change the same images are
repainted in place, so styles never need to be recreated.
"""
import sys
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFilter, ImageTk

# Warm neutral greys with near-black ink. Blue marks selection and focus only; the main action is an ink-coloured
# button. Light sidebar in the same family as the window, with the active tool on a raised white pill.
PALETTES = {
    'light': dict(
        canvas='#f5f5f3', surface='#ffffff', subtle='#f0f0ee', hover='#e9e9e6', field='#f2f2f0',
        border='#e5e5e2', strong_border='#d4d4cf', text='#1b1b18', muted='#6c6c66', faint='#a3a39a',
        accent='#2c73de', accent_hover='#2462c2', accent_soft='#ebf2fc', accent_text='#ffffff',
        primary='#1b1b18', primary_hover='#3a3a36', primary_text='#ffffff',
        image_bg='#1b1b18', sidebar='#efefed', sidebar_hover='#e6e6e3', sidebar_active='#ffffff',
        sidebar_text='#4a4a45', sidebar_title='#1b1b18', sidebar_muted='#8c8c85',
        danger='#c4442a', success='#2b7649', warning='#b07400', shadow=(27, 27, 24, 26),
    ),
    'dark': dict(
        canvas='#1b1b1a', surface='#222220', subtle='#282826', hover='#323230', field='#2c2c2a',
        border='#2f2f2c', strong_border='#42423e', text='#ececea', muted='#9b9b94', faint='#6a6a64',
        accent='#5a92e5', accent_hover='#78a7ec', accent_soft='#1d2a3d', accent_text='#ffffff',
        primary='#ececea', primary_hover='#ffffff', primary_text='#1b1b18',
        image_bg='#121211', sidebar='#161615', sidebar_hover='#222220', sidebar_active='#2c2c2a',
        sidebar_text='#c2c2bc', sidebar_title='#ececea', sidebar_muted='#6a6a64',
        danger='#ef6e55', success='#4fb37a', warning='#d9a23a', shadow=(0, 0, 0, 90),
    ),
}
for _palette in PALETTES.values():
    _palette['preview'] = _palette['canvas']  # previews sit directly on the canvas

SCALES = ('90%', '100%', '110%', '125%', '150%')
SS = 4  # supersampling for anti-aliased shapes


def ui_family(root, language='zh'):
    families = set(tkfont.families(root))
    latin = ('Segoe UI',) if sys.platform == 'win32' else ('SF Pro Text', 'Helvetica Neue') if sys.platform == 'darwin' else ()
    for name in (latin if language == 'en' else ()) + (('Microsoft YaHei UI', 'Microsoft YaHei') if sys.platform == 'win32' else
                 ('PingFang SC', 'Hiragino Sans GB') if sys.platform == 'darwin' else
                 ('Noto Sans CJK SC', 'WenQuanYi Micro Hei')):
        if name in families:
            return name
    return tkfont.nametofont('TkDefaultFont', root).actual('family')


def rgba(color, alpha=255):
    if isinstance(color, tuple):
        return color
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def rounded(size, radius, fill=None, outline=None, width=1, shadow=None, inset=0, accent_bar=None):
    """Anti-aliased rounded rectangle on a transparent canvas.

    shadow: (rgba, blur_px, offset_y) drawn under the shape; inset leaves room for it around the shape.
    accent_bar: (color, width_px) vertical bar at the left edge (selected navigation item).
    """
    w, h = size
    im = Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))
    box = (inset * SS, inset * SS, (w - inset) * SS - 1, (h - inset) * SS - 1)
    if shadow:
        color, blur, dy = shadow
        layer = Image.new('RGBA', im.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((box[0], box[1] + dy * SS, box[2], box[3] + dy * SS), radius * SS, fill=rgba(color))
        im = Image.alpha_composite(im, layer.filter(ImageFilter.GaussianBlur(blur * SS)))
    draw = ImageDraw.Draw(im)
    if fill or outline:
        draw.rounded_rectangle(box, radius * SS, fill=rgba(fill) if fill else None,
                               outline=rgba(outline) if outline else None, width=round(width * SS) if outline else 0)
    if accent_bar:
        color, bar = accent_bar
        draw.rounded_rectangle((box[0], box[1] + radius * SS, box[0] + bar * SS, box[3] - radius * SS), bar * SS // 2, fill=rgba(color))
    return im.resize((w, h), Image.Resampling.LANCZOS)


class Theme:
    """Owns named fonts, styles and generated images. Views subscribe to repaint their plain tk widgets."""

    def __init__(self, root, mode='light', scale='100%'):
        self.root = root
        self.mode = mode if mode in PALETTES else 'light'
        self.scale = scale if scale in SCALES else '100%'
        self.listeners = []
        # Fonts are in points and follow the screen DPI by themselves; pixel sizes need this factor.
        self.dpi = max(1.0, root.winfo_fpixels('1i') / 96)
        self.family = ui_family(root)
        base = 13 if sys.platform == 'darwin' else 10
        self.base_sizes = dict(body=base, small=base - 1, bold=base, section=base + 1, h1=base + 6,
                               h2=base + 2, brand=base + 2, nav=base, mono=base, caps=base - 2)
        self.fonts = {}
        for name, size in self.base_sizes.items():
            weight = 'bold' if name in ('bold', 'section', 'h1', 'h2', 'brand', 'caps') else 'normal'
            fam = ('Consolas' if sys.platform == 'win32' else 'Menlo') if name == 'mono' else self.family
            self.fonts[name] = tkfont.Font(root, family=fam, size=size, weight=weight)
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.images = {}
        self.elements_ready = False

    @property
    def c(self):
        return PALETTES[self.mode]

    @property
    def factor(self):
        return int(self.scale.rstrip('%')) / 100

    def px(self, value):
        """Pixels for a size designed at 96 DPI and 100% UI scale."""
        return max(1, round(value * self.factor * self.dpi))

    def pad(self, *values):
        return tuple(round(v * self.dpi) for v in values)

    def d(self, value):
        """DPI-only pixels (image elements keep their size when the UI scale changes)."""
        return max(1, round(value * self.dpi))

    def subscribe(self, callback):
        self.listeners.append(callback)

    def set(self, mode=None, scale=None):
        if mode in PALETTES:
            self.mode = mode
        if scale in SCALES:
            self.scale = scale
        self.apply()

    def set_language(self, language):
        """English uses a Latin UI font; Chinese uses a CJK font."""
        self.family = ui_family(self.root, language)
        for name, font in self.fonts.items():
            if name != 'mono':
                font.configure(family=self.family)

    # ---------- generated images ----------
    def image(self, name, pil):
        """Create or repaint (same size) a named PhotoImage."""
        if name in self.images and self.images[name].width() == pil.width and self.images[name].height() == pil.height:
            self.images[name].paste(pil)
        else:
            self.images[name] = ImageTk.PhotoImage(pil, master=self.root)
        return self.images[name]

    def _shapes(self):
        """name -> PIL image for every image element, in the current palette."""
        c, d = self.c, self.d
        r = d(8)
        # ttk tiles the stretchable centre of an image element. A tiny centre means thousands of alpha-blended
        # draws per card (a page took ~45 s to appear); a large centre keeps it to a handful.
        n = 2 * r + 200
        clear = None
        shapes = {}

        def box(fill=None, outline=None, width=1, radius=r, size=n, **kw):
            return rounded((size, size), radius, fill, outline, d(width) if outline else 1, **kw)
        # Buttons: (normal, hover, pressed, disabled). Borderless filled shapes; only header buttons, which sit on the
        # grey canvas, keep a hairline so they stay visible there.
        raised = dict(shadow=(c['shadow'], 1.2, 0.5), inset=1)
        variants = {
            'Btn': (box(c['field']), box(c['hover']), box(c['strong_border']), box(c['subtle'])),
            'HeadBtn': (box(c['surface'], c['border']), box(c['subtle'], c['border']), box(c['hover'], c['border']),
                        box(c['canvas'], c['border'])),
            'Accent': (box(c['primary']), box(c['primary_hover']), box(c['primary_hover']), box(c['strong_border'])),
            'Ghost': (box(clear), box(c['hover']), box(c['strong_border']), box(clear)),
            'Seg': (box(clear), box(c['hover']), box(c['hover']), box(clear)),
            'SegActive': (box(c['surface'], **raised),) * 4,
            'Nav': (box(clear), box(c['sidebar_hover']), box(c['sidebar_hover']), box(clear)),
            'NavActive': (box(c['sidebar_active'], **raised),) * 4,
            'SidebarBtn': (box(c['sidebar_hover']), box(c['hover']), box(c['hover']), box(c['sidebar_hover'])),
        }
        for key, images in variants.items():
            for state, im in zip(('normal', 'hover', 'pressed', 'disabled'), images):
                shapes[f'{key}.{state}'] = im
        # Fields
        shapes['Field.normal'] = box(c['field'])
        shapes['Field.hover'] = box(c['hover'])
        shapes['Field.focus'] = box(c['field'], c['accent'], width=1.5)
        shapes['Field.disabled'] = box(c['subtle'])
        shapes['SideField.normal'] = box(c['sidebar_hover'])
        shapes['SideField.hover'] = box(c['hover'])
        # Containers: flat panels with a hairline edge, no drop shadow.
        cr, sh = d(12), d(4)
        shapes['Card'] = rounded((2 * (cr + sh) + 400,) * 2, cr, c['surface'], c['border'], d(1), inset=sh)
        shapes['Track'] = box(c['field'], radius=d(9), size=2 * d(9) + 200)
        shapes['ListField'] = box(c['surface'], c['border'])
        # Checkbox / radio indicators
        s = d(16)
        shapes['Check.off'] = rounded((s, s), d(4), c['field'], c['strong_border'], d(1.2))
        shapes['Check.hover'] = rounded((s, s), d(4), c['field'], c['accent'], d(1.2))
        on = rounded((s, s), d(4), c['accent'])
        tick = ImageDraw.Draw(on)
        tick.line([(s * .26, s * .52), (s * .43, s * .69), (s * .75, s * .33)], fill=rgba(c['accent_text']), width=max(2, d(2)), joint='curve')
        shapes['Check.on'] = on
        shapes['Check.disabled'] = rounded((s, s), d(4), c['subtle'], c['border'], d(1))
        # Scrollbar thumbs
        t = d(6)
        shapes['Thumb.normal'] = rounded((t + 2, 2 * t + 200), t // 2, c['strong_border'])
        shapes['Thumb.hover'] = rounded((t + 2, 2 * t + 200), t // 2, c['faint'])
        # Combobox chevron
        a = d(16)
        chevron = Image.new('RGBA', (a * SS, a * SS), (0, 0, 0, 0))
        ImageDraw.Draw(chevron).line([(a * SS * .3, a * SS * .42), (a * SS * .5, a * SS * .62), (a * SS * .7, a * SS * .42)],
                                     fill=rgba(c['muted']), width=round(1.6 * self.dpi * SS), joint='curve')
        shapes['Chevron'] = chevron.resize((a, a), Image.Resampling.LANCZOS)
        side = chevron.copy()
        side.putdata([(*rgba(c['sidebar_text'])[:3], p[3]) for p in side.getdata()])
        shapes['SideChevron'] = side.resize((a, a), Image.Resampling.LANCZOS)
        # Variants that sit on a different background need their own copy (see flattening below).
        for new, old in (('HeadAccent', 'Accent'), ('CanvasGhost', 'Ghost')):
            for state in ('normal', 'hover', 'pressed', 'disabled'):
                shapes[f'{new}.{state}'] = shapes[f'{old}.{state}']
        shapes['CardTrack'] = shapes['Track']
        shapes['PageThumb.normal'], shapes['PageThumb.hover'] = shapes['Thumb.normal'], shapes['Thumb.hover']
        # Pre-composite every shape onto the colour it always sits on. Opaque images are drawn with a plain blit;
        # images with transparency are blended in software by Tk on every repaint, which made switching pages stutter.
        backgrounds = {'Btn': 'surface', 'HeadBtn': 'canvas', 'Accent': 'surface', 'HeadAccent': 'canvas',
                       'Ghost': 'surface', 'CanvasGhost': 'canvas', 'Seg': 'subtle', 'SegActive': 'subtle',
                       'Nav': 'sidebar', 'NavActive': 'sidebar', 'SidebarBtn': 'sidebar', 'Field': 'surface',
                       'SideField': 'sidebar', 'Card': 'canvas', 'Track': 'canvas', 'CardTrack': 'surface',
                       'ListField': 'surface', 'Check': 'surface', 'Thumb': 'surface', 'PageThumb': 'canvas',
                       'Chevron': 'field', 'SideChevron': 'sidebar_hover'}
        return {name: self.flat(im, backgrounds[name.split('.')[0]]) for name, im in shapes.items()}

    def flat(self, image, background):
        """Composite an RGBA image onto a palette colour, giving an opaque RGB image."""
        base = Image.new('RGBA', image.size, rgba(self.c[background]))
        return Image.alpha_composite(base, image).convert('RGB')

    def _create_elements(self, img):
        s = self.style
        r = self.d(8)
        cr, sh = self.d(12), self.d(4)

        def element(name, prefix, border, sticky='nsew', states=('disabled', 'pressed', 'hover')):
            mapping = {'hover': 'active !disabled', 'pressed': 'pressed !disabled', 'disabled': 'disabled', 'focus': 'focus',
                       'on': 'selected', 'off': '!selected'}
            spec = [(mapping[st], img[f'{prefix}.{st}']) for st in states]
            s.element_create(name, 'image', img[f'{prefix}.normal'] if f'{prefix}.normal' in img else img[prefix], *spec,
                             border=border, padding=self.d(1), sticky=sticky, width=2 * border + 2, height=2 * border + 2)
        for key in ('Btn', 'HeadBtn', 'Accent', 'HeadAccent', 'Ghost', 'CanvasGhost', 'Seg', 'SegActive', 'Nav', 'NavActive',
                    'SidebarBtn'):
            element(f'{key}.bg', key, r + 2)
        s.element_create('Field.bg', 'image', img['Field.normal'], ('disabled', img['Field.disabled']),
                         ('focus', img['Field.focus']), ('active', img['Field.hover']), border=r + 2, padding=self.d(2), sticky='nsew',
                         width=2 * r + 6, height=2 * r + 6)
        s.element_create('SideField.bg', 'image', img['SideField.normal'], ('active', img['SideField.hover']), border=r + 2,
                         padding=self.d(2), sticky='nsew', width=2 * r + 6, height=2 * r + 6)
        s.element_create('Card.bg', 'image', img['Card'], border=cr + sh + 2, padding=sh + self.d(2), sticky='nsew',
                         width=2 * (cr + sh) + 6, height=2 * (cr + sh) + 6)
        s.element_create('Track.bg', 'image', img['Track'], border=self.d(9) + 2, padding=self.d(1), sticky='nsew',
                         width=2 * self.d(9) + 6, height=2 * self.d(9) + 6)
        s.element_create('CardTrack.bg', 'image', img['CardTrack'], border=self.d(9) + 2, padding=self.d(1), sticky='nsew',
                         width=2 * self.d(9) + 6, height=2 * self.d(9) + 6)
        s.element_create('ListField.bg', 'image', img['ListField'], border=r + 2, padding=self.d(3), sticky='nsew',
                         width=2 * r + 6, height=2 * r + 6)
        s.element_create('Check.indicator', 'image', img['Check.off'], ('disabled', img['Check.disabled']),
                         ('selected', img['Check.on']), ('active', img['Check.hover']), sticky='')
        s.element_create('Thumb.v', 'image', img['Thumb.normal'], ('active', img['Thumb.hover']), ('pressed', img['Thumb.hover']),
                         border=(0, self.d(6) // 2 + 1), sticky='ns', width=self.d(6) + 2, height=self.d(6) + 6)
        s.element_create('PageThumb.v', 'image', img['PageThumb.normal'], ('active', img['PageThumb.hover']),
                         ('pressed', img['PageThumb.hover']), border=(0, self.d(6) // 2 + 1), sticky='ns',
                         width=self.d(6) + 2, height=self.d(6) + 6)
        s.element_create('Chevron.downarrow', 'image', img['Chevron'], sticky='e')
        s.element_create('SideChevron.downarrow', 'image', img['SideChevron'], sticky='e')

        def button_layout(element_name):
            return [(element_name, {'sticky': 'nsew', 'children': [
                ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})]
        styles = {'TButton': 'Btn', 'Head.TButton': 'HeadBtn', 'Accent.TButton': 'Accent', 'HeadAccent.TButton': 'HeadAccent',
                  'Ghost.TButton': 'Ghost', 'PreviewTool.TButton': 'CanvasGhost', 'Seg.TButton': 'Seg', 'SegActive.TButton': 'SegActive',
                  'PreviewSeg.TButton': 'Seg', 'PreviewSegActive.TButton': 'SegActive', 'Nav.TButton': 'Nav',
                  'NavActive.TButton': 'NavActive', 'SidebarSmall.TButton': 'SidebarBtn', 'NavIcon.TButton': 'Nav',
                  'NavIconActive.TButton': 'NavActive', 'SideToggle.TButton': 'Nav'}
        for style, key in styles.items():
            s.layout(style, button_layout(f'{key}.bg'))
        s.layout('Link.TButton', [('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})])
        s.layout('TEntry', [('Field.bg', {'sticky': 'nsew', 'children': [
            ('Entry.padding', {'sticky': 'nsew', 'children': [('Entry.textarea', {'sticky': 'nsew'})]})]})])
        for prefix, field, arrow in (('', 'Field.bg', 'Chevron.downarrow'), ('Sidebar.', 'SideField.bg', 'SideChevron.downarrow')):
            s.layout(f'{prefix}TCombobox', [(field, {'sticky': 'nsew', 'children': [
                (arrow, {'side': 'right', 'sticky': 'ns'}),
                ('Combobox.padding', {'expand': '1', 'sticky': 'nsew', 'children': [('Combobox.textarea', {'sticky': 'nsew'})]})]})])
        s.layout('TSpinbox', [('Field.bg', {'sticky': 'nsew', 'children': [
            ('null', {'side': 'right', 'sticky': '', 'children': [
                ('Spinbox.uparrow', {'side': 'top', 'sticky': 'e'}), ('Spinbox.downarrow', {'side': 'bottom', 'sticky': 'e'})]}),
            ('Spinbox.padding', {'sticky': 'nsew', 'children': [('Spinbox.textarea', {'sticky': 'nsew'})]})]})])
        for kind in ('Checkbutton', 'Radiobutton'):
            s.layout(f'T{kind}', [(f'{kind}.padding', {'sticky': 'nsew', 'children': [
                ('Check.indicator', {'side': 'left', 'sticky': ''}),
                (f'{kind}.label', {'side': 'left', 'sticky': 'nsew'})]})])
        for orient, sticky in (('Vertical', 'ns'), ('Horizontal', 'ew')):
            for prefix, thumb in (('', 'Thumb.v'), ('Page.', 'PageThumb.v')):
                thumb = thumb if orient == 'Vertical' else f'{orient}.Scrollbar.thumb'
                s.layout(f'{prefix}{orient}.TScrollbar', [(f'{orient}.Scrollbar.trough', {'sticky': sticky, 'children': [
                    (thumb, {'expand': '1', 'sticky': 'nsew'})]})])
        for name, element_name in (('Card.TFrame', 'Card.bg'), ('Track.TFrame', 'Track.bg'), ('CardTrack.TFrame', 'CardTrack.bg'),
                                   ('ListField.TFrame', 'ListField.bg')):
            s.layout(name, [(element_name, {'sticky': 'nsew'})])
        s.layout('Treeview.Item', [('Treeitem.padding', {'sticky': 'nsew', 'children': [
            ('Treeitem.image', {'side': 'left', 'sticky': ''}),
            ('Treeitem.focus', {'side': 'left', 'sticky': '', 'children': [('Treeitem.text', {'side': 'left', 'sticky': ''})]})]})])
        s.layout('Treeview', [('ListField.bg', {'sticky': 'nsew', 'children': [
            ('Treeview.padding', {'sticky': 'nsew', 'children': [('Treeview.treearea', {'sticky': 'nsew'})]})]})])

    def apply(self):
        c, s, px, d = self.c, self.style, self.px, self.d
        for name, size in self.base_sizes.items():
            self.fonts[name].configure(size=max(7, round(size * self.factor)))
        f = self.fonts
        shapes = self._shapes()
        images = {name: self.image(name, im) for name, im in shapes.items()}
        if not self.elements_ready:
            self._create_elements(images)
            self.elements_ready = True
        flat = dict(bordercolor=c['surface'], lightcolor=c['surface'], darkcolor=c['surface'])
        s.configure('.', background=c['surface'], foreground=c['text'], font=f['body'], troughcolor=c['surface'],
                    focuscolor=c['accent'], selectbackground=c['accent_soft'], selectforeground=c['text'],
                    insertcolor=c['text'], **flat)
        # Containers
        s.configure('TFrame', background=c['surface'])
        for name, color in (('Canvas', 'canvas'), ('Preview', 'canvas'), ('Subtle', 'subtle'), ('Sidebar', 'sidebar'),
                            ('Divider', 'border'), ('SidebarDivider', 'sidebar_active')):
            s.configure(f'{name}.TFrame', background=c[color])
        s.configure('Card.TFrame', background=c['canvas'])
        s.configure('Track.TFrame', background=c['canvas'])
        s.configure('CardTrack.TFrame', background=c['surface'])
        s.configure('ListField.TFrame', background=c['surface'])
        # Labels
        s.configure('TLabel', background=c['surface'], foreground=c['text'])
        s.configure('Title.TLabel', background=c['canvas'], foreground=c['text'], font=f['h1'])
        s.configure('Sub.TLabel', background=c['canvas'], foreground=c['muted'])
        s.configure('H2.TLabel', font=f['h2'])
        s.configure('Section.TLabel', font=f['section'], foreground=c['text'])
        s.configure('Muted.TLabel', foreground=c['muted'])
        s.configure('Hint.TLabel', foreground=c['muted'], font=f['small'])
        s.configure('Subtle.TLabel', background=c['subtle'], foreground=c['muted'], font=f['small'])
        s.configure('Preview.TLabel', background=c['canvas'], foreground=c['muted'], font=f['small'])
        s.configure('PreviewEmpty.TLabel', background=c['canvas'], foreground=c['faint'], font=f['h2'])
        s.configure('Brand.TLabel', background=c['sidebar'], foreground=c['sidebar_title'], font=f['brand'])
        s.configure('SidebarMuted.TLabel', background=c['sidebar'], foreground=c['sidebar_muted'], font=f['caps'])
        s.configure('Status.TLabel', background=c['canvas'], foreground=c['muted'], font=f['small'], padding=(px(6), px(6)))
        for kind, color in (('Dot', c['faint']), ('DotOk', c['success']), ('DotError', c['danger']), ('DotBusy', c['accent'])):
            s.configure(f'{kind}.TLabel', background=c['canvas'], foreground=color, font=f['small'], padding=(px(16), px(6), 0, px(6)))
        for kind in ('ok', 'error', 'busy'):
            s.configure(f'Status{kind.title()}.TLabel', background=c['canvas'], foreground=c['text'], font=f['small'], padding=(px(6), px(6)))
        # Buttons: the image is the body; 'background' is the colour around its rounded corners.
        base = dict(relief='flat', focusthickness=0, anchor='center', padding=(px(14), px(6)))
        for style, bg, fg in (('TButton', 'surface', 'text'), ('Head.TButton', 'canvas', 'text'), ('Accent.TButton', 'surface', 'primary_text'),
                              ('HeadAccent.TButton', 'canvas', 'primary_text'), ('Ghost.TButton', 'surface', 'muted'),
                              ('PreviewTool.TButton', 'canvas', 'muted'), ('Seg.TButton', 'subtle', 'muted'),
                              ('SegActive.TButton', 'subtle', 'text'), ('PreviewSeg.TButton', 'subtle', 'muted'),
                              ('PreviewSegActive.TButton', 'subtle', 'text')):
            s.configure(style, background=c[bg], foreground=c[fg], **base)
            s.map(style, foreground=[('disabled', c['faint'])] + ([('active', c['text'])] if fg == 'muted' else []),
                  background=[('active', c[bg])])
        for style in ('Accent.TButton', 'HeadAccent.TButton', 'SegActive.TButton', 'PreviewSegActive.TButton'):
            s.configure(style, font=f['bold'])
        for style in ('Seg.TButton', 'SegActive.TButton', 'PreviewSeg.TButton', 'PreviewSegActive.TButton', 'PreviewTool.TButton', 'Ghost.TButton'):
            s.configure(style, padding=(px(10), px(5)))
        s.configure('Link.TButton', background=c['surface'], foreground=c['accent'], padding=(px(2), px(2)), relief='flat')
        s.map('Link.TButton', background=[('active', c['surface'])], foreground=[('active', c['accent_hover'])])
        for style, fg in (('Nav.TButton', c['sidebar_text']), ('NavActive.TButton', c['sidebar_title'])):
            s.configure(style, background=c['sidebar'], foreground=fg, anchor='w', padding=(px(16), px(10)), font=f['nav'], relief='flat')
            s.map(style, background=[('active', c['sidebar'])], foreground=[('active', c['sidebar_title'])])
        for style, fg in (('NavIcon.TButton', c['sidebar_text']), ('NavIconActive.TButton', c['sidebar_title'])):
            s.configure(style, background=c['sidebar'], foreground=fg, anchor='center', padding=(px(13), px(11)), relief='flat')
            s.map(style, background=[('active', c['sidebar'])])
        s.configure('SideToggle.TButton', background=c['sidebar'], anchor='center', padding=(px(8), px(8)), relief='flat')
        s.map('SideToggle.TButton', background=[('active', c['sidebar'])])
        s.configure('Tooltip.TLabel', background=c['text'], foreground=c['canvas'], font=f['small'], padding=(px(9), px(5)))
        s.configure('SidebarSmall.TButton', background=c['sidebar'], foreground=c['sidebar_text'], padding=(px(10), px(6)), font=f['small'])
        s.map('SidebarSmall.TButton', background=[('active', c['sidebar'])], foreground=[('active', c['sidebar_title'])])
        # Fields
        for name in ('TEntry', 'TCombobox', 'TSpinbox'):
            s.configure(name, background=c['surface'], fieldbackground=c['field'], foreground=c['text'], insertcolor=c['text'],
                        padding=(px(10), px(6)), insertwidth=max(1, d(1)))
            s.map(name, foreground=[('disabled', c['faint']), ('readonly', c['text'])], background=[('readonly', c['surface'])],
                  fieldbackground=[('readonly', c['field'])],
                  selectbackground=[('readonly', c['field']), ('!disabled', c['accent_soft'])],
                  selectforeground=[('!disabled', c['text'])])
        s.configure('TSpinbox', arrowcolor=c['muted'], arrowsize=px(9), bordercolor=c['field'], lightcolor=c['field'], darkcolor=c['field'])
        s.map('TSpinbox', background=[('active', c['field']), ('!disabled', c['field'])])
        s.configure('Sidebar.TCombobox', background=c['sidebar'], foreground=c['sidebar_text'], fieldbackground=c['sidebar_hover'],
                    padding=(px(9), px(4)))
        s.map('Sidebar.TCombobox', foreground=[('readonly', c['sidebar_text'])], fieldbackground=[('readonly', c['sidebar_hover'])],
              background=[('readonly', c['sidebar'])], selectbackground=[('readonly', c['sidebar_hover'])],
              selectforeground=[('readonly', c['sidebar_text'])])
        self.root.option_add('*TCombobox*Listbox.background', c['surface'])
        self.root.option_add('*TCombobox*Listbox.foreground', c['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', c['accent_soft'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', c['text'])
        self.root.option_add('*TCombobox*Listbox.font', f['body'])
        self.root.option_add('*TCombobox*Listbox.borderWidth', 0)
        self.root.option_add('*TCombobox*Listbox.relief', 'flat')
        for name in ('TCheckbutton', 'TRadiobutton'):
            s.configure(name, background=c['surface'], foreground=c['text'], padding=(0, px(4)), indicatormargin=0)
            s.map(name, background=[('active', c['surface'])])
        # Lists
        s.configure('Treeview', background=c['surface'], fieldbackground=c['surface'], foreground=c['text'],
                    rowheight=px(30), borderwidth=0, relief='flat', font=f['body'], padding=d(2))
        s.map('Treeview', background=[('selected', c['accent_soft'])], foreground=[('selected', c['text'])])
        s.configure('Treeview.Heading', background=c['surface'], foreground=c['faint'], relief='flat', font=f['caps'],
                    padding=(px(8), px(7)), bordercolor=c['surface'], lightcolor=c['surface'], darkcolor=c['surface'])
        s.map('Treeview.Heading', background=[('active', c['subtle'])])
        # Scrollbars, separators, progress
        for prefix, trough in (('', 'surface'), ('Page.', 'canvas')):
            s.configure(f'{prefix}Vertical.TScrollbar', troughcolor=c[trough], background=c[trough], bordercolor=c[trough],
                        lightcolor=c[trough], darkcolor=c[trough], gripcount=0, relief='flat', width=d(8))
            s.configure(f'{prefix}Horizontal.TScrollbar', troughcolor=c[trough], background=c['strong_border'], bordercolor=c[trough],
                        lightcolor=c['strong_border'], darkcolor=c['strong_border'], gripcount=0, arrowsize=d(8))
        s.configure('TSeparator', background=c['border'])
        s.configure('Horizontal.TProgressbar', troughcolor=c['border'], background=c['accent'], bordercolor=c['border'],
                    lightcolor=c['accent'], darkcolor=c['accent'], thickness=px(4))
        self.root.configure(background=c['canvas'])
        self.paint_icons()
        for callback in list(self.listeners):
            try:
                callback()
            except tk.TclError:
                pass

    # ---------- small line icons ----------
    def paint_icons(self):
        size = self.d(16)
        for name in ('home', 'pan', 'zoom', 'minus', 'plus', 'fit', 'gear'):
            self.image(f'icon.{name}', self.draw_icon(name, size, self.c['muted']))
        for name in ('collapse', 'expand'):
            self.image(f'icon.side.{name}', self.draw_icon(name, self.d(18), self.c['sidebar_text']))

    def icon(self, name):
        return self.images[f'icon.{name}']

    @staticmethod
    def draw_icon(name, size, color):
        s = size * SS
        im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        g = ImageDraw.Draw(im)
        w = max(SS, round(s * 0.09))
        col = rgba(color)
        if name == 'home':
            g.line([(s * .15, s * .5), (s * .5, s * .18), (s * .85, s * .5)], fill=col, width=w, joint='curve')
            g.line([(s * .27, s * .42), (s * .27, s * .82), (s * .73, s * .82), (s * .73, s * .42)], fill=col, width=w, joint='curve')
        elif name == 'pan':
            for (x0, y0, x1, y1) in ((.5, .12, .5, .88), (.12, .5, .88, .5)):
                g.line([(s * x0, s * y0), (s * x1, s * y1)], fill=col, width=w)
            for tip, a, b in (((.5, .1), (.38, .24), (.62, .24)), ((.5, .9), (.38, .76), (.62, .76)),
                              ((.1, .5), (.24, .38), (.24, .62)), ((.9, .5), (.76, .38), (.76, .62))):
                g.line([(s * a[0], s * a[1]), (s * tip[0], s * tip[1]), (s * b[0], s * b[1])], fill=col, width=w, joint='curve')
        elif name == 'zoom':
            g.ellipse((s * .14, s * .14, s * .64, s * .64), outline=col, width=w)
            g.line([(s * .58, s * .58), (s * .86, s * .86)], fill=col, width=round(w * 1.3))
        elif name in ('minus', 'plus'):
            g.line([(s * .22, s * .5), (s * .78, s * .5)], fill=col, width=w)
            if name == 'plus':
                g.line([(s * .5, s * .22), (s * .5, s * .78)], fill=col, width=w)
        elif name == 'gear':
            import math
            points = []
            for i in range(16):  # 8 teeth
                angle = math.pi * 2 * (i + .5) / 16
                radius = s * (.46 if (i // 1) % 2 == 0 else .34)
                for da in (-.13, .13):
                    points.append((s / 2 + radius * math.cos(angle + da), s / 2 + radius * math.sin(angle + da)))
            g.polygon(points, outline=col, width=w)
            g.ellipse((s * .37, s * .37, s * .63, s * .63), outline=col, width=w)
        elif name in ('collapse', 'expand'):
            sign = -1 if name == 'collapse' else 1
            for dx in (-.12, .12):
                cx = s * (.5 + dx)
                g.line([(cx - sign * s * .1, s * .3), (cx + sign * s * .1, s * .5), (cx - sign * s * .1, s * .7)],
                       fill=col, width=w, joint='curve')
        elif name == 'fit':
            for (ax, ay), (dx, dy) in (((.15, .15), (1, 1)), ((.85, .15), (-1, 1)), ((.15, .85), (1, -1)), ((.85, .85), (-1, -1))):
                g.line([(s * ax, s * (ay + dy * .25)), (s * ax, s * ay), (s * (ax + dx * .25), s * ay)], fill=col, width=w, joint='curve')
        return im.resize((size, size), Image.Resampling.LANCZOS)

    def paper_shadow(self, w, h):
        """Paper edge for a figure of w×h pixels: a 1 px hairline just outside it, no drop shadow.

        The returned image is larger by 2*margin and is placed behind the figure.
        """
        m = 1
        image = Image.new('RGB', (w + 2 * m, h + 2 * m), rgba(self.c['strong_border' if self.mode == 'light' else 'border'])[:3])
        return image, m

    def contrast(self, color, background='#ffffff'):
        """WCAG contrast ratio between two colors (1 = identical, 21 = black on white)."""
        def luminance(value):
            channels = [v / 65535 for v in self.root.winfo_rgb(value)]
            channels = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
        try:
            a, b = luminance(color), luminance(background)
        except tk.TclError:
            return 21.0
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    def color_chip(self, color, faded=False):
        """Curve-color chip for lists, with a contrasting ring so any color stands out on the list background."""
        try:
            rgb = tuple(v // 257 for v in self.root.winfo_rgb(color))
        except tk.TclError:
            rgb = (128, 128, 128)
        name = f'chip.{rgb}.{faded}.{self.mode}'
        if name not in self.images:
            w, h, ring = self.d(24), self.d(14), max(1, round(1.5 * self.dpi))
            halo = (255, 255, 255, 235) if self.mode == 'dark' else rgba(self.c['strong_border'])
            alpha = 90 if faded else 255
            chip = rounded((w, h), self.d(4), (*halo[:3], round(halo[3] * alpha / 255)))
            inner = rounded((w - 2 * ring, h - 2 * ring), max(1, self.d(4) - ring), (*rgb, alpha))
            chip.alpha_composite(inner, (ring, ring))
            pad = Image.new('RGBA', (w + self.d(6), h), (0, 0, 0, 0))
            pad.alpha_composite(chip, (self.d(6), 0))
            self.images[name] = ImageTk.PhotoImage(pad, master=self.root)
        return self.images[name]

    # ---------- plain tk widgets ----------
    def paint_listbox(self, widget):
        c = self.c
        widget.configure(background=c['surface'], foreground=c['text'], selectbackground=c['accent_soft'],
                         selectforeground=c['text'], highlightthickness=0, borderwidth=0,
                         activestyle='none', font=self.fonts['body'])

    def readable(self, color):
        """Display version of a data color with enough contrast on the list background (data is unchanged)."""
        try:
            r, g, b = (v / 65535 for v in self.root.winfo_rgb(color))
            br, bg, bb = (v / 65535 for v in self.root.winfo_rgb(self.c['surface']))
        except tk.TclError:
            return self.c['text']

        def luminance(r, g, b):
            f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
        target = 1.0 if self.mode == 'dark' else 0.0
        mixed = [r, g, b]
        for step in range(11):
            t = step / 10
            mixed = [v + (target - v) * t for v in (r, g, b)]
            a, b2 = luminance(*mixed), luminance(br, bg, bb)
            if (max(a, b2) + 0.05) / (min(a, b2) + 0.05) >= 4.0:
                break
        return '#%02x%02x%02x' % tuple(round(v * 255) for v in mixed)
