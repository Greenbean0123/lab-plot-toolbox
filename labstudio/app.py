"""Main window: sidebar navigation, shared appearance and status bar. Tools load on first use."""
import importlib
import os
import sys
import tkinter as tk
import traceback
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

from .paths import APP_VERSION, ASSETS, Preferences
from .theme import Theme
from .widgets import install_wheel_router, Tooltip
from .i18n import T, LANGUAGES, app_name, get_language, set_language

NAV_GLIDE_MS = 180  # accent bar glide (tool switches move it at once, before the page swap)
SIDEBAR_SLIDE_MS = 240  # sidebar collapse / expand

TOOLS = [
    # key, nav label, nav caption, module, class
    ('xrd', 'XRD', '堆叠图谱 · 标准卡片', 'labstudio.xrd.view', 'XRDView'),
    ('tem', 'TEM', '标尺 · 粒径分布', 'labstudio.tem.view', 'TEMView'),
    ('uvvis', 'UV-Vis', '吸收光谱 · 归一化', 'labstudio.uvvis.view', 'UVVisView'),
    ('nmr', 'NMR', '堆叠谱图 · 自动定标', 'labstudio.nmr.view', 'NMRView'),
]


class LabStudio(tk.Tk):
    def __init__(self, initial_tool=None):
        super().__init__()
        self.withdraw()
        self.prefs = Preferences()
        set_language(self.prefs.get('language', 'en'))
        self.title(app_name())
        self.tk.call('tk', 'appname', 'Lab Plot Toolbox')
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.dpi = d = max(1.0, self.winfo_fpixels('1i') / 96)
        w, h = min(round(1480 * d), sw - 80), min(round(940 * d), sh - 110)
        self.geometry(f'{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}')
        self.minsize(min(round(1000 * d), sw - 80), min(round(600 * d), sh - 110))
        self.icons = {}
        icon_path = ASSETS / 'app.png'
        if icon_path.is_file():
            self.icons['app'] = ImageTk.PhotoImage(Image.open(icon_path).resize((64, 64), Image.Resampling.LANCZOS))
            self.icons['brand'] = ImageTk.PhotoImage(Image.open(icon_path).resize((round(30 * d), round(30 * d)), Image.Resampling.LANCZOS))
            try:
                self.iconphoto(True, self.icons['app'])
            except tk.TclError:
                pass
        self.theme = Theme(self, self.prefs.get('theme', 'light'), self.prefs.get('scale', '100%'))
        self.theme.set_language(get_language())
        from .update.manager import UpdateManager
        self.updates = UpdateManager(self)
        self.views, self.nav_buttons = {}, {}
        self.current = None
        self.sidebar_expanded = bool(self.prefs.get('sidebar_expanded', True))
        self._status_job = None
        self.theme.apply()  # creates the icons used while building
        self.build()
        self.theme.apply()
        install_wheel_router(self)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind_all('<Control-o>', lambda e: self.shortcut('shortcut_open'))
        self.bind_all('<Control-s>', lambda e: self.shortcut('shortcut_save'))
        self.bind_all('<F5>', lambda e: self.shortcut('shortcut_refresh'))
        self.bind_all('<Control-comma>', self.open_settings)
        self.report_callback_exception = self.callback_error
        self.deiconify()
        self.after(1000, self.updates.start)
        self.after(30, lambda: self.show(initial_tool or self.prefs.get('tool', TOOLS[0][0])))

    # ---------- layout ----------
    def build(self):
        pad = self.theme.pad
        # The sidebar is an overlay placed above a same-width placeholder in the layout. Animating the overlay's width
        # only clips it; the rest of the window is laid out once, when the animation ends.
        self.sidebar_space = ttk.Frame(self, style='Canvas.TFrame', width=1)
        self.sidebar_space.pack(side='left', fill='y')
        self.sidebar_space.pack_propagate(False)
        self.sidebar = ttk.Frame(self, style='Sidebar.TFrame')
        self.sidebar_inner, self._sidebar_width, self._sidebar_job = None, 0, None
        self._swap_job = None
        self._deferred = {}  # hidden tool -> figure refit to run when it is shown again
        self.build_sidebar()
        self.theme.subscribe(lambda: self.move_indicator(animate=False))
        # Main area
        main = ttk.Frame(self, style='Canvas.TFrame')
        main.pack(side='left', fill='both', expand=True)
        self.main = main
        self.status_var = tk.StringVar(self, value=T('就绪'))
        status_row = ttk.Frame(main, style='Canvas.TFrame')
        status_row.pack(side='bottom', fill='x', padx=(0, pad(10)))
        ttk.Frame(main, style='Divider.TFrame', height=1).pack(side='bottom', fill='x', padx=pad(16))
        self.status_dot = ttk.Label(status_row, text='●', style='Dot.TLabel')
        self.status_dot.pack(side='left')
        self.status_label = ttk.Label(status_row, textvariable=self.status_var, style='Status.TLabel', anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True)
        # Settings live in the bottom-right corner: always reachable, out of the way.
        gear = ttk.Button(status_row, text=' ' + T('设置'), image=self.theme.icon('gear'), compound='left',
                          style='PreviewTool.TButton', command=self.open_settings)
        gear.pack(side='right', pady=pad(3))
        # Shown only when a newer signed release is available.
        self.update_button = ttk.Button(status_row, style='HeadAccent.TButton', command=lambda: self.updates.install())
        self.update_anchor = gear
        self.show_update_button(self.updates.release)
        ttk.Label(status_row, text=T('Ctrl+O 打开 · Ctrl+S 导出 · F5 刷新预览'), style='Status.TLabel').pack(side='right', padx=pad(0, 6))
        self.stage = ttk.Frame(main, style='Canvas.TFrame')
        self.stage.pack(fill='both', expand=True)
        self.stage.rowconfigure(0, weight=1)
        self.stage.columnconfigure(0, weight=1)
        self.sidebar.lift()

    def build_sidebar(self, animate=False):
        """Expanded: icon, name and caption. Collapsed: icons only, names shown as tooltips."""
        pad, expanded = self.theme.pad, self.sidebar_expanded
        # Each layout gets its own overlay window, so the old and new sidebars can slide over one another.
        old_overlay = self.sidebar if self.sidebar_inner is not None else None
        overlay = ttk.Frame(self, style='Sidebar.TFrame') if old_overlay is not None else self.sidebar
        inner = ttk.Frame(overlay, style='Sidebar.TFrame')
        self.nav_buttons = {}
        self._indicator_y, self._indicator_job = None, None
        top = ttk.Frame(inner, style='Sidebar.TFrame', padding=pad(20, 22, 12, 22) if expanded else pad(12, 18, 12, 18))
        top.pack(fill='x')
        toggle = ttk.Button(top, image=self.theme.icon('side.collapse' if expanded else 'side.expand'), style='SideToggle.TButton',
                            command=self.toggle_sidebar)
        Tooltip(toggle, T('收起侧栏') if expanded else T('展开侧栏'))
        if expanded:
            toggle.pack(side='right', anchor='n')
            if 'brand' in self.icons:
                ttk.Label(top, image=self.icons['brand'], style='Brand.TLabel').pack(side='left', padx=pad(0, 10))
            names = ttk.Frame(top, style='Sidebar.TFrame')
            names.pack(side='left')
            ttk.Label(names, text=app_name(), style='Brand.TLabel').pack(anchor='w')
            subtitle = 'Lab Plot Toolbox' if get_language() == 'zh' else 'TEM · XRD · UV-Vis · NMR'
            ttk.Label(names, text=f'{subtitle}  {APP_VERSION}', style='SidebarMuted.TLabel').pack(anchor='w')
            ttk.Label(inner, text=T('工具'), style='SidebarMuted.TLabel', padding=pad(24, 0, 0, 8)).pack(fill='x')
        else:
            if 'brand' in self.icons:
                ttk.Label(top, image=self.icons['brand'], style='Brand.TLabel').pack(pady=pad(0, 12))
            toggle.pack()
        icon_size = self.theme.px(26)
        for key, label, caption, _, _ in TOOLS:
            icon = ASSETS / f'{key}.png'
            if icon.is_file() and key not in self.icons:
                self.icons[key] = ImageTk.PhotoImage(Image.open(icon).convert('RGBA').resize((icon_size, icon_size), Image.Resampling.LANCZOS))
            if expanded:
                button = ttk.Button(inner, text=f'  {label}\n  {T(caption)}', image=self.icons.get(key), compound='left',
                                    style='Nav.TButton', command=lambda k=key: self.show(k))
            else:
                button = ttk.Button(inner, image=self.icons.get(key), style='NavIcon.TButton', command=lambda k=key: self.show(k))
                Tooltip(button, f'{label} · {T(caption)}')
            button.pack(fill='x', padx=pad(12) if expanded else pad(10), pady=pad(2))
            # Place the accent bar once buttons have their real size (and after any resize).
            button.bind('<Configure>', lambda e: self._indicator_job is None and self.move_indicator(animate=False), add='+')
            self.nav_buttons[key] = button
        # Accent bar that glides to the selected tool.
        self.nav_indicator = tk.Frame(inner, background=self.theme.c['accent'], width=self.theme.d(3), height=1)
        self.sidebar_inner = inner
        self.highlight_nav()
        self.update_idletasks()
        width = inner.winfo_reqwidth()
        start = self._sidebar_width or width
        inner.place(x=0, y=0, relheight=1, width=width)

        def settle():
            if old_overlay is not None and old_overlay.winfo_exists():
                self.cancel_timers_of([old_overlay])
                old_overlay.destroy()
            self.sidebar = overlay
            overlay.place(x=0, y=0, relheight=1, width=width)
            overlay.lift()
            # The one re-layout of the main area: done with painting suspended, then shown in a single pass.
            main = getattr(self, 'main', None)  # absent while the window is first built
            if main is None:
                self.sidebar_space.configure(width=width)
            else:
                self.freeze(main, True)
                try:
                    self.sidebar_space.configure(width=width)
                    self.update_idletasks()
                finally:
                    self.freeze(main, False)
            self._sidebar_width, self._sidebar_job = width, None
            self.after_idle(lambda: self.move_indicator(animate=False))
        if not animate or old_overlay is None or start == width:
            settle()
            return
        # Slide, never resize: moving a window is a cheap blit (~3 ms a frame), while resizing it re-renders all of
        # its contents (~50 ms a frame). Expanding slides the wide panel in over the icon rail; collapsing slides it
        # out and uncovers the rail, which is already in place underneath.
        expanding = width > start
        mover, span = (overlay, width) if expanding else (old_overlay, start)
        if expanding:
            overlay.place(x=-span, y=0, relheight=1, width=span)
            overlay.lift()
        else:
            overlay.place(x=0, y=0, relheight=1, width=width)
            overlay.lift()
            old_overlay.lift()
        import time
        began = time.perf_counter()

        def step():
            t = min(1.0, (time.perf_counter() - began) * 1000 / SIDEBAR_SLIDE_MS)
            eased = 1 - (1 - t) ** 3  # ease-out cubic
            x = -span * (1 - eased) if expanding else -span * eased
            mover.place(x=round(x), y=0, relheight=1, width=span)
            if t < 1:
                self._sidebar_job = self.after(8, step)
            else:
                settle()
        step()

    def highlight_nav(self, animate=True):
        expanded = self.sidebar_expanded
        for key, button in self.nav_buttons.items():
            active = key == self.current
            button.configure(style=('NavActive.TButton' if active else 'Nav.TButton') if expanded else
                             ('NavIconActive.TButton' if active else 'NavIcon.TButton'))
        self.move_indicator(animate=animate)

    def move_indicator(self, animate=True):
        indicator = getattr(self, 'nav_indicator', None)
        if indicator is None or not indicator.winfo_exists():
            return
        indicator.configure(background=self.theme.c['accent'])
        button = self.nav_buttons.get(self.current)
        if button is None or button.winfo_height() <= 1:
            indicator.place_forget()
            return
        height = round(button.winfo_height() * 0.46)
        x = button.winfo_x() + self.theme.d(2)
        target = button.winfo_y() + (button.winfo_height() - height) / 2
        if self._indicator_job is not None:
            self.after_cancel(self._indicator_job)
            self._indicator_job = None
        start = self._indicator_y
        if not animate or start is None or not indicator.winfo_ismapped():
            self._indicator_y = target
            indicator.place(x=x, y=target, height=height)
            indicator.lift()
            return
        import time
        began = time.perf_counter()

        def step():
            t = min(1.0, (time.perf_counter() - began) * 1000 / NAV_GLIDE_MS)
            self._indicator_y = start + (target - start) * (1 - (1 - t) ** 3)  # ease-out cubic
            if indicator.winfo_exists():
                indicator.place(x=x, y=self._indicator_y, height=height)
            self._indicator_job = self.after(10, step) if t < 1 else None
        step()

    def schedule_preload(self):
        """Build the other tools in the background after start-up, so the first switch to them is instant."""
        if getattr(self, '_preload_job', None) is None:
            self._preload_job = self.after(1200, self.preload_next)

    def preload_next(self):
        self._preload_job = None
        missing = [spec for spec in TOOLS if spec[0] not in self.views]
        if not missing or self._swap_job is not None or self._sidebar_job is not None:
            if missing:
                self._preload_job = self.after(600, self.preload_next)
            return
        key, _, _, module_name, class_name = missing[0]
        try:
            view = getattr(importlib.import_module(module_name), class_name)(self.stage, self)
        except Exception:
            traceback.print_exc()
            return
        self.views[key] = view
        # Lay it out behind the current view and leave it there: switching to it only raises it.
        current = self.views.get(self.current)
        view.grid(row=0, column=0, sticky='nsew')
        if current is not None:
            view.lower(current)
        self.update_idletasks()
        if len(missing) > 1:
            self._preload_job = self.after(400, self.preload_next)

    def toggle_sidebar(self):
        if self._sidebar_job is not None:
            return  # ignore clicks while animating
        self.sidebar_expanded = not self.sidebar_expanded
        self.prefs.set('sidebar_expanded', self.sidebar_expanded)
        self.build_sidebar(animate=True)

    def show_update_button(self, release):
        button = getattr(self, 'update_button', None)
        if button is None or not button.winfo_exists():
            return
        if release:
            button.configure(text=T('更新到 {version}').format(version=release['version']))
            button.pack(side='right', padx=self.theme.pad(0, 8), pady=self.theme.pad(3), before=self.update_anchor)
        else:
            button.pack_forget()

    def open_settings(self, _=None):
        window = getattr(self, 'settings_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_set()
            return
        from .settings import SettingsWindow
        self.settings_window = SettingsWindow(self)

    # ---------- navigation ----------
    def show(self, key):
        if key not in {t[0] for t in TOOLS}:
            key = TOOLS[0][0]
        if key == self.current:
            return
        first_time = key not in self.views
        # A switch between shown tools paints at once. The very first page (and a tool built on demand) paints
        # lazily instead: painting the whole new window synchronously would hold up its start-up work for seconds.
        switching = not first_time and self.views.get(self.current) is not None
        self.current = key
        self.highlight_nav(animate=False)
        if switching:
            self.flush_paint(self.sidebar)  # the highlight moves before the (slower) page swap
        self.prefs.set('tool', key)
        if key not in self.views:
            spec = next(t for t in TOOLS if t[0] == key)
            self.status(T('正在加载 {tool} 工具…').format(tool=spec[1]), 'busy', timeout=None)
            self.config(cursor='watch')
            self.update_idletasks()
            try:
                module = importlib.import_module(spec[3])
                self.views[key] = getattr(module, spec[4])(self.stage, self)
            except Exception as error:
                self.config(cursor='')
                traceback.print_exc()
                messagebox.showerror(app_name(), T('{tool} 工具加载失败：\n{error}').format(tool=spec[1], error=error))
                self.status(T('加载失败'), 'error')
                self.current = None
                return
            finally:
                self.config(cursor='')
            self.status(T('就绪'))
        view = self.views[key]
        self.title(f'{app_name()} · {T(view.TITLE)}')
        if self._swap_job is not None:
            self.after_cancel(self._swap_job)
            self._swap_job = None

        def swap():
            # Every tool stays mapped in the same grid cell and is brought to the front. Raising only repaints;
            # unmapping and re-mapping also re-creates and lays out every child window, about twice as slow.
            self._swap_job = None
            if not view.winfo_manager():
                view.grid(row=0, column=0, sticky='nsew')
            view.tkraise()
            if switching:
                self.flush_paint(self.stage)
            refit = self._deferred.pop(view, None)
            if refit is not None:
                refit()  # the window was resized while this tool was hidden
            if hasattr(view, 'on_show'):
                view.on_show()
        swap()  # at once: the highlight has already moved, and waiting only made the click feel slow
        if first_time:
            self.schedule_preload()

    @staticmethod
    def flush_paint(widget):
        """Paint pending changes now instead of when Windows gets round to it.

        Windows posts WM_PAINT at the lowest priority, so a newly shown page could stay blank for a few hundred
        ms. RDW_UPDATENOW delivers it at once; Tk turns it into Expose events, and the idle pass draws them.
        """
        import _tkinter
        widget.update_idletasks()  # geometry first, so the paint covers the final layout
        if sys.platform == 'win32':
            try:
                import ctypes
                # RDW_UPDATENOW | RDW_ALLCHILDREN
                ctypes.windll.user32.RedrawWindow(int(widget.winfo_id()), None, None, 0x0100 | 0x0080)
            except Exception:
                pass
        while widget.tk.dooneevent(_tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT):
            pass
        widget.update_idletasks()

    def defer_if_hidden(self, view, callback):
        """Tools behind the current one still get resize events. Their figure refit waits until they are shown.

        Returns True when the call was deferred (the caller should return).
        """
        current = self.views.get(self.current)
        if current is None or current is view:
            return False
        self._deferred[view] = callback
        return True

    @staticmethod
    def freeze(widget, frozen):
        """Windows: suspend painting of a widget tree, then repaint it in one pass (no piece-by-piece drawing)."""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            hwnd = int(widget.winfo_id())
            user32 = ctypes.windll.user32
            user32.SendMessageW(hwnd, 0x000B, 0 if frozen else 1, 0)  # WM_SETREDRAW
            if not frozen:
                # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
                user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
        except Exception:
            pass

    def shortcut(self, name):
        view = self.views.get(self.current)
        if view is not None and hasattr(view, name):
            getattr(view, name)()
            return 'break'

    # ---------- language ----------
    def change_language(self, lang):
        if lang == get_language() or lang not in LANGUAGES:
            return
        busy = any(getattr(v, 'files', None) or getattr(v, 'spectra', None) for v in self.views.values())
        busy = busy or (self.views.get('xrd') is not None and self.views['xrd'].layers and not self.views['xrd'].layers_from_startup())
        if busy and not messagebox.askyesno('Language / 语言', T('切换语言会重新载入界面，当前打开的文件和未保存的设置将被清空。继续吗？'), parent=self):
            return
        self.prefs.set('language', lang)
        set_language(lang)
        self.rebuild()

    def rebuild(self):
        """Recreate every widget so all text is drawn in the new language."""
        tool = self.current
        for view in self.views.values():
            if hasattr(view, 'close'):
                try:
                    view.close()
                except Exception:
                    traceback.print_exc()
        self.cancel_animations()
        if self._status_job is not None:
            self.after_cancel(self._status_job)
            self._status_job = None
        self.cancel_timers_of(self.winfo_children())
        for child in self.winfo_children():
            child.destroy()
        self.views, self.nav_buttons, self.current = {}, {}, None
        self.theme.listeners.clear()
        self.theme.set_language(get_language())
        self.build()
        self.theme.apply()
        self.title(app_name())
        self.show(tool or TOOLS[0][0])

    # ---------- appearance ----------
    def set_theme(self, mode):
        self.theme.set(mode=mode)
        self.prefs.set('theme', self.theme.mode)

    def set_scale(self, scale):
        self.theme.set(scale=scale)
        self.prefs.set('scale', self.theme.scale)

    # ---------- status ----------
    def status(self, text, kind='info', timeout=8000):
        """kind: info | ok | error | busy. Messages fade back to 'Ready' after timeout ms."""
        if self._status_job is not None:
            self.after_cancel(self._status_job)
            self._status_job = None
        self.status_var.set(text)
        style = {'ok': 'StatusOk.TLabel', 'error': 'StatusError.TLabel', 'busy': 'StatusBusy.TLabel'}.get(kind, 'Status.TLabel')
        self.status_label.configure(style=style)
        self.status_dot.configure(style={'ok': 'DotOk.TLabel', 'error': 'DotError.TLabel', 'busy': 'DotBusy.TLabel'}.get(kind, 'Dot.TLabel'))
        if timeout:
            self._status_job = self.after(timeout, lambda: self.status(T('就绪'), timeout=None))
        if kind == 'busy':
            self.update_idletasks()

    def callback_error(self, exc, value, tb):
        traceback.print_exception(exc, value, tb)
        messagebox.showerror(app_name(), T('发生错误：\n{error}').format(error=value))

    def cancel_timers_of(self, widgets):
        """Cancel Tk timers whose callbacks belong to widgets about to be destroyed.

        Destroying a widget deletes its Python callbacks but not Tcl's pending timers; a later callback can reuse
        the same command name, and the stale timer would then call that unrelated function.
        """
        names, stack = set(), list(widgets)
        while stack:
            widget = stack.pop()
            names.update(getattr(widget, '_tclCommands', None) or ())
            stack.extend(widget.winfo_children())
        if not names:
            return
        for job in self.tk.splitlist(self.tk.call('after', 'info')):
            try:
                script = self.tk.splitlist(self.tk.call('after', 'info', job))[0]
            except tk.TclError:
                continue
            if any(word in names for word in self.tk.splitlist(script)):
                self.tk.call('after', 'cancel', job)

    def cancel_animations(self):
        for name in ('_swap_job', '_sidebar_job', '_indicator_job', '_preload_job'):
            job = getattr(self, name, None)
            if job is not None:
                self.after_cancel(job)
                setattr(self, name, None)

    def close(self):
        self.cancel_animations()
        self.updates.stop()
        for view in self.views.values():
            if hasattr(view, 'close'):
                try:
                    view.close()
                except Exception:
                    traceback.print_exc()
        self.destroy()


def main():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    tool = sys.argv[1] if len(sys.argv) > 1 else None
    LabStudio(tool).mainloop()
