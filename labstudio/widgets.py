"""Reusable building blocks shared by all three tools.

Visual structure: views sit on a soft 'canvas' colour; settings are grouped in white rounded cards
(section()); previews float on the canvas, figures on a shadowed sheet of paper (fit_figure()).
"""
import math
import sys
import tkinter as tk
from tkinter import ttk, colorchooser

from PIL import ImageTk

from .i18n import T


class ScrollPanel(ttk.Frame):
    """Vertical scrolling container. Mouse-wheel routing is installed once by the shell."""

    def __init__(self, parent, theme, padding=(14, 12, 10, 22)):
        super().__init__(parent, style='Canvas.TFrame')
        self.theme = theme
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview, style='Page.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas, padding=theme.pad(*padding), style='Canvas.TFrame')
        self.window = self.canvas.create_window(0, 0, anchor='nw', window=self.body)
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self._remainder = 0.0
        theme.subscribe(self.paint)
        self.paint()

    def _on_scroll(self, first, last):
        # Hide the scrollbar entirely when everything fits.
        if float(first) <= 0 and float(last) >= 1:
            self.bar.pack_forget()
        elif not self.bar.winfo_ismapped():
            self.bar.pack(side='right', fill='y', before=self.canvas, padx=(0, self.theme.d(3)))
        self.bar.set(first, last)

    def paint(self):
        self.canvas.configure(background=self.theme.c['canvas'])

    def wheel(self, event):
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(event, 'num', None) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            self._remainder -= event.delta / (1 if sys.platform == 'darwin' else 120)
            steps = math.trunc(self._remainder)
            self._remainder -= steps
        if steps:
            self.canvas.yview_scroll(steps * 2, 'units')

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)


def install_wheel_router(root):
    """Scroll whichever ScrollPanel is under the pointer; never let the wheel change a dropdown value."""
    def route(event):
        widget = root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        if isinstance(widget, (tk.Listbox, tk.Text)) or widget.winfo_class() == 'Treeview':
            return  # These scroll themselves.
        while widget is not None:
            if isinstance(widget, ScrollPanel):
                widget.wheel(event)
                return 'break'
            handler = getattr(widget, 'wheel_handler', None)
            if handler is not None:
                return handler(event)
            widget = getattr(widget, 'master', None)

    def route_and_stop(event):
        route(event)
        return 'break'

    for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
        root.bind_all(sequence, route)
        for cls in ('TCombobox', 'TSpinbox'):
            root.bind_class(cls, sequence, route_and_stop)


class Segmented(ttk.Frame):
    """Tab switcher drawn on a canvas: a white pill glides to the selected tab.

    Pages are stacked in one grid cell and only raised on switch, so a switch never re-lays out or
    redraws page content; the only thing animated is the pill (one canvas item).
    """
    DURATION = 160  # ms; the page swap follows the animation

    def __init__(self, parent, theme, on_change=None):
        super().__init__(parent, style='Canvas.TFrame')
        self.theme, self.on_change = theme, on_change
        self.titles, self.pages, self.items = [], [], []
        self.current, self.hover = None, None
        self.pill_x, self._anim, self._images = 0.0, None, {}
        d = theme.d
        self.bar = tk.Canvas(self, highlightthickness=0, borderwidth=0, height=self._height(), cursor='hand2')
        self.bar.pack(fill='x', padx=d(14), pady=(d(4), d(6)))
        self.holder = ttk.Frame(self, style='Canvas.TFrame')
        self.holder.pack(fill='both', expand=True)
        self.holder.rowconfigure(0, weight=1)
        self.holder.columnconfigure(0, weight=1)
        self.bar.bind('<Configure>', lambda e: self.redraw())
        self.bar.bind('<Button-1>', self.click)
        self.bar.bind('<Motion>', lambda e: self.set_hover(self.index_at(e.x)))
        self.bar.bind('<Leave>', lambda e: self.set_hover(None))
        theme.subscribe(self.redraw)

    # ---------- geometry ----------
    def _height(self):
        return self.theme.fonts['bold'].metrics('linespace') + 2 * self.theme.d(4) + self.theme.px(12)

    def _segment(self):
        return max(1, self.bar.winfo_width()) / max(1, len(self.titles))

    def _target(self, index):
        return index * self._segment() + self.theme.d(4)

    def index_at(self, x):
        if not self.titles:
            return None
        return max(0, min(len(self.titles) - 1, int(x // self._segment())))

    # ---------- drawing ----------
    def redraw(self):
        """Rebuild images for the current size and theme (resize, theme or scale change)."""
        if not self.bar.winfo_exists():
            return
        from .theme import rounded
        c, d = self.theme.c, self.theme.d
        height = self._height()
        if int(self.bar.cget('height')) != height:
            self.bar.configure(height=height)
        width = self.bar.winfo_width()
        self.bar.configure(background=c['canvas'])
        self.bar.delete('all')
        self.items = []
        if width < 10 or not self.titles:
            return
        inset = d(4)
        self._images['track'] = ImageTk.PhotoImage(self.theme.flat(rounded((width, height), d(9), c['field']), 'canvas'),
                                                   master=self.bar)
        seg = self._segment()
        pill = rounded((max(4, round(seg - 2 * inset)) + 2, height - 2 * inset + 2), d(7), c['surface'],
                       shadow=(c['shadow'], 1.2, 0.5), inset=1)
        self._images['pill'] = ImageTk.PhotoImage(self.theme.flat(pill, 'field'), master=self.bar)
        self.bar.create_image(0, 0, anchor='nw', image=self._images['track'])
        if self._anim is None and self.current is not None:
            self.pill_x = self._target(self.current)
        self.pill = self.bar.create_image(self.pill_x - 1, inset - 1, anchor='nw', image=self._images['pill'])
        for i, title in enumerate(self.titles):
            self.items.append(self.bar.create_text((i + .5) * seg, height / 2, text=title))
        self.restyle()

    def restyle(self):
        c, f = self.theme.c, self.theme.fonts
        for i, item in enumerate(self.items):
            active = i == self.current
            color = c['text'] if active or i == self.hover else c['muted']
            self.bar.itemconfigure(item, fill=color, font=f['bold'] if active else f['body'])

    def set_hover(self, index):
        if index != self.hover:
            self.hover = index
            self.restyle()

    # ---------- behaviour ----------
    def add(self, title):
        index = len(self.pages)
        page = ScrollPanel(self.holder, self.theme)
        page.grid(row=0, column=0, sticky='nsew')  # all pages share one cell; the current one is raised
        self.pages.append(page)
        self.titles.append(title)
        if self.current is None:
            self.select(0, animate=False)
        else:
            self.pages[self.current].tkraise()
        self.redraw()
        return page.body

    def click(self, event):
        index = self.index_at(event.x)
        if index is not None:
            self.select(index)

    def select(self, index, animate=True):
        """Switch at once. The pill jumps rather than slides: showing a page blocks while it paints, so a slide
        either stalls half-way or delays the page; an immediate response feels smoother than either."""
        if self.current == index:
            return
        self.current = index
        self.restyle()
        if self._anim is not None:
            self.bar.after_cancel(self._anim)
            self._anim = None
        if self.items:
            self.pill_x = self._target(index)
            self.bar.coords(self.pill, self.pill_x - 1, self.theme.d(4) - 1)
            self.bar.update_idletasks()  # the tab bar answers the click before the page swap
        self.show_page(index)

    def show_page(self, index):
        from .app import LabStudio
        page = self.pages[index]
        if not page.winfo_manager():
            page.grid()
        page.tkraise()  # raising only repaints; re-mapping a page also re-creates its windows (about 2x slower)
        LabStudio.flush_paint(self.holder)
        if self.on_change:
            self.on_change(index)


class PreviewSwitch(ttk.Frame):
    """Compact pill switch for preview modes."""

    def __init__(self, parent, options, command, theme=None):
        super().__init__(parent, style='Track.TFrame', padding=theme.d(3) if theme else 3)
        self.command = command
        self.buttons = {}
        for value, text in options:
            b = ttk.Button(self, text=text, style='PreviewSeg.TButton', command=lambda v=value: self.command(v))
            b.pack(side='left')
            self.buttons[value] = b

    def set(self, value):
        for key, button in self.buttons.items():
            button.configure(style='PreviewSegActive.TButton' if key == value else 'PreviewSeg.TButton')


def section(parent, title, hint=None, first=False):
    """A white rounded card with a heading; returns the frame for its contents."""
    d = _theme_of(parent).d if _theme_of(parent) else (lambda v: v)
    card = ttk.Frame(parent, style='Card.TFrame', padding=(d(18), d(14), d(18), d(18)))
    card.pack(fill='x', pady=(0 if first else d(4), d(8)))
    ttk.Label(card, text=title, style='Section.TLabel').pack(anchor='w', pady=(0, d(8)))
    if hint:
        globals()['hint'](card, hint, pady=(0, d(8)))
    body = ttk.Frame(card)
    body.pack(fill='x')
    return body


def _theme_of(widget):
    top = widget.winfo_toplevel()
    return getattr(top, 'theme', None)


def hint(parent, text, pady=(8, 0)):
    label = ttk.Label(parent, text=text, style='Hint.TLabel', justify='left', wraplength=200)
    label.pack(fill='x', pady=pady)
    label.bind('<Configure>', lambda e: label.configure(wraplength=max(120, e.width - 4)))
    return label


class Form(ttk.Frame):
    """Two-column label / control grid with consistent spacing."""

    def __init__(self, parent, theme, label_width=13):
        super().__init__(parent)
        self.theme = theme
        self.row = 0
        self.label_width = label_width
        self.columnconfigure(1, weight=1)
        self.pack(fill='x')
        self.gap = theme.d(4)

    def _label(self, text):
        ttk.Label(self, text=text, width=-self.label_width, style='Muted.TLabel').grid(
            row=self.row, column=0, sticky='w', padx=(0, self.theme.d(12)), pady=self.gap)

    def _place(self, widget):
        widget.grid(row=self.row, column=1, sticky='ew', pady=self.gap)
        self.row += 1
        return widget

    def entry(self, text, var, width=10):
        self._label(text)
        return self._place(ttk.Entry(self, textvariable=var, width=width))

    def spin(self, text, var, low, high, step):
        self._label(text)
        return self._place(ttk.Spinbox(self, textvariable=var, from_=low, to=high, increment=step, width=10))

    def combo(self, text, var, values):
        self._label(text)
        return self._place(ttk.Combobox(self, textvariable=var, values=list(values), state='readonly', width=10))

    def choice(self, text, var, options):
        """Readonly dropdown that shows friendly text but stores the internal value in var."""
        self._label(text)
        options = [(value, T(label)) for value, label in options]
        to_text = dict(options)
        to_value = {label: value for value, label in options}
        shown = tk.StringVar(self, value=to_text.get(var.get(), var.get()))
        box = ttk.Combobox(self, textvariable=shown, values=[label for _, label in options], state='readonly', width=10)
        box.bind('<<ComboboxSelected>>', lambda e: var.set(to_value.get(shown.get(), shown.get())))
        var.trace_add('write', lambda *_: shown.set(to_text.get(var.get(), var.get())))
        box._shown = shown
        return self._place(box)

    def pair(self, text, var_a, var_b, separator='–'):
        self._label(text)
        box = ttk.Frame(self)
        box.columnconfigure(0, weight=1)
        box.columnconfigure(2, weight=1)
        ttk.Entry(box, textvariable=var_a, width=6).grid(row=0, column=0, sticky='ew')
        ttk.Label(box, text=separator, style='Muted.TLabel').grid(row=0, column=1, padx=self.theme.d(6))
        ttk.Entry(box, textvariable=var_b, width=6).grid(row=0, column=2, sticky='ew')
        return self._place(box)

    def color(self, text, var, title=None):
        self._label(text)
        box = ttk.Frame(self)
        box.columnconfigure(1, weight=1)
        swatch = ColorSwatch(box, self.theme, var, title or text)
        swatch.grid(row=0, column=0, padx=(0, self.theme.d(8)))
        ttk.Entry(box, textvariable=var, width=9).grid(row=0, column=1, sticky='ew')
        return self._place(box)

    def check(self, text, var, command=None):
        widget = ttk.Checkbutton(self, text='  ' + text, variable=var, command=command)
        widget.grid(row=self.row, column=0, columnspan=2, sticky='w', pady=self.gap)
        self.row += 1
        return widget

    def widget(self, text, widget):
        if text:
            self._label(text)
            return self._place(widget)
        widget.grid(row=self.row, column=0, columnspan=2, sticky='ew', pady=self.gap)
        self.row += 1
        return widget


class ColorSwatch(tk.Label):
    """Clickable rounded color chip bound to a StringVar."""

    def __init__(self, parent, theme, var, title='Color', size=26):
        super().__init__(parent, borderwidth=0, highlightthickness=0, cursor='hand2')
        self.theme, self.var, self.title, self.size = theme, var, title, theme.d(size)
        self.photo = None
        self.bind('<Button-1>', self.pick)
        var.trace_add('write', lambda *_: self.paint())
        theme.subscribe(self.paint)
        self.paint()

    def paint(self):
        if not self.winfo_exists():
            return
        from .theme import rounded
        c, s = self.theme.c, self.size
        try:
            self.winfo_rgb(self.var.get())
            color = self.var.get() if self.var.get().startswith('#') else '#%02x%02x%02x' % tuple(
                v // 257 for v in self.winfo_rgb(self.var.get()))
        except tk.TclError:
            color = None
        image = rounded((s, s), self.theme.d(6), color or c['subtle'], c['strong_border'] if color else c['danger'], self.theme.d(1))
        self.photo = ImageTk.PhotoImage(image, master=self)
        self.configure(image=self.photo, background=c['surface'])

    def pick(self, _=None):
        try:
            initial = self.var.get()
            self.winfo_rgb(initial)
        except tk.TclError:
            initial = '#000000'
        chosen = colorchooser.askcolor(initial, title=self.title, parent=self.winfo_toplevel())[1]
        if chosen:
            self.var.set(chosen)


class Dialog(tk.Toplevel):
    """Themed modal dialog. Subclasses fill self.body and implement apply() -> bool."""

    def __init__(self, parent, theme, title, ok_text=None):
        super().__init__(parent.winfo_toplevel())
        self.withdraw()
        self.theme = theme
        self.result = None
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.configure(background=theme.c['surface'])
        self.resizable(False, False)
        outer = ttk.Frame(self, padding=theme.pad(26, 22, 26, 20))
        outer.pack(fill='both', expand=True)
        self.body = ttk.Frame(outer)
        self.body.pack(fill='both', expand=True)
        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=(theme.d(22), 0))
        ttk.Button(buttons, text=ok_text or T('确定'), style='Accent.TButton', command=self.ok).pack(side='right')
        ttk.Button(buttons, text=T('取消'), command=self.cancel).pack(side='right', padx=theme.d(8))
        self.extra_buttons = buttons
        self.bind('<Return>', lambda e: self.ok())
        self.bind('<Escape>', lambda e: self.cancel())
        self.protocol('WM_DELETE_WINDOW', self.cancel)

    def show(self):
        self.update_idletasks()
        master = self.master
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + max(40, (master.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f'+{max(0, x)}+{max(0, y)}')
        self.deiconify()
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result

    def ok(self):
        try:
            if self.apply() is False:
                return
        except ValueError as error:
            from tkinter import messagebox
            messagebox.showerror(self.title(), str(error), parent=self)
            return
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def apply(self):
        return True


class AskString(Dialog):
    def __init__(self, parent, theme, title, prompt, initial=''):
        super().__init__(parent, theme, title)
        ttk.Label(self.body, text=prompt).pack(anchor='w', pady=(0, theme.d(8)))
        self.var = tk.StringVar(self, value=initial)
        entry = ttk.Entry(self.body, textvariable=self.var, width=42)
        entry.pack(fill='x')
        entry.select_range(0, 'end')
        self.after(50, entry.focus_set)

    def apply(self):
        value = self.var.get().strip()
        if not value:
            return False
        self.result = value


def ask_string(parent, theme, title, prompt, initial=''):
    return AskString(parent, theme, title, prompt, initial).show()


class ToolLayout:
    """Header (title + actions) above a resizable split: settings cards on the left, preview on the right."""

    def __init__(self, view, theme, title, subtitle, controls_width=390):
        self.theme = theme
        view.configure(style='Canvas.TFrame')
        header = ttk.Frame(view, padding=theme.pad(28, 22, 24, 12), style='Canvas.TFrame')
        header.pack(fill='x')
        # Actions are packed first so they keep their full width; the subtitle wraps instead.
        self.actions = ttk.Frame(header, style='Canvas.TFrame')
        self.actions.pack(side='right', anchor='center', padx=theme.pad(16, 0))
        titles = ttk.Frame(header, style='Canvas.TFrame')
        titles.pack(side='left', fill='x', expand=True)
        ttk.Label(titles, text=title, style='Title.TLabel').pack(anchor='w')
        sub = ttk.Label(titles, text=subtitle, style='Sub.TLabel', wraplength=300, justify='left')
        sub.pack(anchor='w', fill='x', pady=(theme.d(2), 0))
        sub.bind('<Configure>', lambda e: sub.configure(wraplength=max(150, e.width)))
        # The divider between settings and preview: a visible line that brightens under the pointer; while dragging
        # an accent guide line follows the mouse and the panes are resized once, on release.
        self.split = tk.PanedWindow(view, orient='horizontal', sashwidth=theme.d(5), sashrelief='flat',
                                    opaqueresize=False, borderwidth=0, showhandle=False, sashcursor='sb_h_double_arrow',
                                    proxyrelief='flat', proxyborderwidth=0)
        self._sash_hover = False
        self.split.bind('<Motion>', self._hover_sash, add='+')
        self.split.bind('<Leave>', lambda e: self._set_sash_hover(False), add='+')
        self.split.pack(fill='both', expand=True, padx=(0, theme.d(16)), pady=(0, theme.d(8)))
        self.controls = ttk.Frame(self.split, style='Canvas.TFrame')
        self.preview = ttk.Frame(self.split, style='Preview.TFrame')
        self.split.add(self.controls, minsize=theme.px(300), width=theme.px(controls_width), stretch='never')
        self.split.add(self.preview, minsize=theme.px(320), stretch='always')
        theme.subscribe(self.paint)
        self.paint()

    def paint(self):
        c = self.theme.c
        self.split.configure(background=c['faint'] if self._sash_hover else c['strong_border'], proxybackground=c['accent'])

    def _hover_sash(self, event):
        # The panes cover everything but the sash, so motion over the split itself is motion over the sash.
        self._set_sash_hover(bool(self.split.identify(event.x, event.y)))

    def _set_sash_hover(self, value):
        if value != self._sash_hover:
            self._sash_hover = value
            self.paint()

    def action(self, text, command, accent=False):
        button = ttk.Button(self.actions, text=text, command=command, style='HeadAccent.TButton' if accent else 'Head.TButton')
        button.pack(side='right', padx=(self.theme.d(8), 0))
        return button


def button_row(parent, buttons, pady=(0, 6), equal=True):
    """Buttons of equal width in one row. buttons: [(text, command), ...]"""
    theme = _theme_of(parent)
    gap = theme.d(8) if theme else 6
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=tuple(theme.d(v) for v in pady) if theme else pady)
    for i, (text, command) in enumerate(buttons):
        ttk.Button(row, text=text, command=command).grid(row=0, column=i, sticky='ew', padx=(0 if i == 0 else gap, 0))
        row.columnconfigure(i, weight=1, uniform='buttons' if equal else None)
    return row


class PlotToolbar(ttk.Frame):
    """Themed replacement for matplotlib's toolbar: reset, pan, zoom and cursor readout."""

    def __init__(self, parent, theme, canvas):
        super().__init__(parent, style='Preview.TFrame')
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        message = tk.StringVar(self, value='')

        class Hidden(NavigationToolbar2Tk):
            def set_message(self, text):
                message.set(text.replace('\n', '  '))

        self.nav = Hidden(canvas, parent, pack_toolbar=False)  # never packed; provides pan/zoom logic
        self.buttons = {}
        for key, text, command in (('home', T('复位视图'), self.home), ('pan', T('平移'), lambda: self.mode('pan')),
                                   ('zoom', T('框选放大'), lambda: self.mode('zoom'))):
            button = ttk.Button(self, text=' ' + text, image=theme.icon(key), compound='left', style='PreviewTool.TButton', command=command)
            button.pack(side='left', padx=(0, theme.d(4)))
            self.buttons[key] = button
        ttk.Label(self, textvariable=message, style='Preview.TLabel').pack(side='right')

    def home(self):
        self.nav.home()

    def mode(self, name):
        getattr(self.nav, name)()
        active = str(self.nav.mode).lower()
        for key in ('pan', 'zoom'):
            self.buttons[key].configure(style='PreviewSegActive.TButton' if key in active else 'PreviewTool.TButton')


def figure_canvas(figure, master):
    """Matplotlib canvas where one figure pixel is one screen pixel; fit_figure sizes it."""
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    canvas = FigureCanvasTkAgg(figure, master=master)
    widget = canvas.get_tk_widget()
    widget.unbind('<Map>')  # Skip matplotlib's high-DPI enlargement so the preview matches the export.
    widget.configure(highlightthickness=0, borderwidth=0)
    return canvas


def fit_figure(canvas, stage, width_in, height_in, theme=None):
    """Show the whole figure at its export proportions, as large as the stage allows, on a soft shadow."""
    margin = theme.d(24) if theme else 0
    aw, ah = stage.winfo_width() - 2 * margin, stage.winfo_height() - 2 * margin
    if aw < 10 or ah < 10 or width_in <= 0 or height_in <= 0:
        return None
    dpi = min(aw / width_in, ah / height_in)
    w, h = max(1, round(width_in * dpi)), max(1, round(height_in * dpi))
    figure = canvas.figure
    figure.set_dpi(dpi)
    figure.set_size_inches(width_in, height_in, forward=False)
    widget = canvas.get_tk_widget()
    if theme is not None:
        shadow = getattr(stage, '_shadow', None)
        if shadow is None or not shadow.winfo_exists():
            shadow = stage._shadow = tk.Label(stage, borderwidth=0, highlightthickness=0)
            stage._shadow_key = None
        key = (w, h, theme.mode)
        if stage._shadow_key != key:
            image, m = theme.paper_shadow(w, h)
            shadow._photo = ImageTk.PhotoImage(image, master=stage)
            shadow.configure(image=shadow._photo, background=theme.c['canvas'])
            stage._shadow_key = key
        shadow.place(relx=.5, rely=.5, anchor='center')
        shadow.lower(widget)
    widget.place(relx=.5, rely=.5, anchor='center', width=w, height=h)
    canvas.draw_idle()
    return dpi


class Tooltip:
    """Small label shown beside a widget while the pointer is over it."""

    def __init__(self, widget, text, side='right'):
        self.widget, self.text, self.side, self.window, self.job = widget, text, side, None, None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')

    def schedule(self, _=None):
        self.cancel()
        self.job = self.widget.after(350, self.show)

    def cancel(self):
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None

    def show(self):
        self.job = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        label = ttk.Label(self.window, text=self.text, style='Tooltip.TLabel')
        label.pack()
        self.window.update_idletasks()
        w = self.widget
        if self.side == 'right':
            x = w.winfo_rootx() + w.winfo_width() + 8
            y = w.winfo_rooty() + (w.winfo_height() - label.winfo_reqheight()) // 2
        else:
            x = w.winfo_rootx() + (w.winfo_width() - label.winfo_reqwidth()) // 2
            y = w.winfo_rooty() - label.winfo_reqheight() - 6
        self.window.wm_geometry(f'+{x}+{y}')

    def hide(self, _=None):
        self.cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None
