"""Settings window, opened from the gear in the status bar (or Ctrl+,). Changes apply immediately."""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from .i18n import T, LANGUAGES, get_language, app_name
from .paths import APP_VERSION, data_directory
from .theme import SCALES
from .widgets import section, hint, Form


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.theme = app.theme  # section() looks up .theme on the toplevel
        self.title(T('设置'))
        self.transient(app)
        self.resizable(False, False)
        d = self.theme.d
        outer = ttk.Frame(self, style='Canvas.TFrame', padding=(d(20), d(18), d(20), d(16)))
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=T('设置'), style='Title.TLabel').pack(anchor='w', pady=(0, d(12)))

        body = section(outer, T('外观'), first=True)
        form = Form(body, self.theme, label_width=10)
        track = ttk.Frame(form, style='CardTrack.TFrame', padding=d(3))
        self.mode_buttons = {}
        for i, (mode, label) in enumerate((('light', T('浅色')), ('dark', T('深色')))):
            button = ttk.Button(track, text=label, style='Seg.TButton', command=lambda m=mode: self.set_mode(m))
            button.grid(row=0, column=i, sticky='nsew')
            track.columnconfigure(i, weight=1, uniform='mode')
            self.mode_buttons[mode] = button
        form.widget(T('主题'), track)
        self.scale = tk.StringVar(self, value=self.theme.scale)
        box = form.combo(T('界面大小'), self.scale, SCALES)
        box.bind('<<ComboboxSelected>>', lambda e: self.app.set_scale(self.scale.get()))

        body = section(outer, T('语言'))
        form = Form(body, self.theme, label_width=10)
        self.language = tk.StringVar(self, value=LANGUAGES[get_language()])
        box = form.combo('Language / 语言', self.language, LANGUAGES.values())
        box.bind('<<ComboboxSelected>>', lambda e: self.change_language())
        hint(body, T('切换语言会重新载入界面。'))

        body = section(outer, T('数据存储'))
        path = ttk.Label(body, text=str(data_directory()), style='Muted.TLabel', wraplength=d(360), justify='left')
        path.pack(fill='x')
        ttk.Button(body, text=T('打开文件夹'), command=self.open_data).pack(anchor='w', pady=(d(8), 0))
        hint(body, T('偏好设置和启动数据保存在这里，不会写入程序文件夹。'))

        body = section(outer, T('关于'))
        ttk.Label(body, text=f'{app_name()}  ·  {T("版本 {version}").format(version=APP_VERSION)}', style='Section.TLabel').pack(anchor='w')
        hint(body, T('整合 TEM Graph tool、XRD Plotter 和 UV-Vis Plotter（Greenbean0123），MIT 许可证。'))

        bottom = ttk.Frame(outer, style='Canvas.TFrame')
        bottom.pack(fill='x', pady=(d(6), 0))
        ttk.Button(bottom, text=T('完成'), style='HeadAccent.TButton', command=self.destroy).pack(side='right')
        self.bind('<Escape>', lambda e: self.destroy())
        self.theme.subscribe(self.paint)
        self.paint()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + max(40, (app.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f'+{max(0, x)}+{max(0, y)}')
        self.deiconify()
        self.focus_set()

    def paint(self):
        if not self.winfo_exists():
            return
        self.configure(background=self.theme.c['canvas'])
        for mode, button in self.mode_buttons.items():
            button.configure(style='SegActive.TButton' if mode == self.theme.mode else 'Seg.TButton')

    def set_mode(self, mode):
        self.app.set_theme(mode)

    def change_language(self):
        lang = next(k for k, v in LANGUAGES.items() if v == self.language.get())
        if lang != get_language():
            self.destroy()
            self.app.change_language(lang)

    def open_data(self):
        folder = data_directory()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(folder))
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])
