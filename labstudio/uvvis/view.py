"""UV-Vis absorbance spectra: import, normalize, compare and export."""
import csv
import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, colorchooser

from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator

from .core import MODES, read_spectrum, normalize
from ..widgets import ToolLayout, Segmented, Form, PlotToolbar, section, hint, button_row, ask_string, figure_canvas, fit_figure
from ..i18n import T

COLORS = ['#176b87', '#dc6546', '#4b9664', '#8a65b5', '#bd9532', '#3c5a99', '#c2457a', '#5f7d2b']
MODE_TEXT = [(MODES[0], '各曲线全局最大值 = 1'), (MODES[1], '指定波长区间内最大值 = 1'), (MODES[2], '原始吸光度（不归一化）')]


class UVVisView(ttk.Frame):
    TITLE = 'UV-Vis 光谱'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app, self.theme = app, app.theme
        self.spectra = []
        self.vars = {}
        self._pending = None
        layout = ToolLayout(self, self.theme, T('UV-Vis 吸收光谱'), T('导入多条光谱，统一归一化后叠加比较；导出图片或处理后的数据'))
        layout.action(T('导出数据…'), self.export_data)
        layout.action(T('导出图片…'), self.export_figure, accent=True)
        self.layout = layout
        pages = Segmented(layout.controls, self.theme)
        pages.pack(fill='both', expand=True)
        self.build_data(pages.add(T('数据')))
        self.build_figure(pages.add(T('图形')))
        self.build_preview(layout.preview)
        self.theme.subscribe(self.paint)
        self.paint()
        self.update_plot()

    # ---------- settings ----------
    def var(self, key, default='', redraw=True):
        v = tk.StringVar(self, value=default)
        self.vars[key] = v
        if redraw:
            v.trace_add('write', lambda *_: self.schedule())
        return v

    def build_data(self, page):
        body = section(page, T('光谱文件'), first=True)
        button_row(body, [(T('添加光谱…'), self.add_files), (T('移除'), self.remove)])
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        self.tree = ttk.Treeview(frame, columns=('show', 'export', 'name'), show=('tree', 'headings'), height=7, selectmode='browse')
        self.tree.column('#0', width=self.theme.px(38), minwidth=self.theme.px(38), stretch=False, anchor='center')
        for col, text, width, anchor in (('show', T('显示'), 50, 'center'), ('export', T('导出'), 58, 'center'), ('name', T('名称'), 200, 'w')):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=self.theme.px(width), minwidth=self.theme.px(width if col != 'name' else 80), stretch=col == 'name', anchor=anchor)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<ButtonRelease-1>', self.tree_click)
        self.tree.bind('<Double-1>', lambda e: self.rename() if self.tree.identify_column(e.x) == '#3' else None)
        button_row(body, [(T('重命名'), self.rename), (T('曲线颜色…'), self.pick_color)], pady=(8, 0))
        hint(body, T('点击「显示」「导出」两列切换开关；双击名称可重命名；左侧色块就是曲线颜色。'))
        body = section(page, T('归一化'))
        form = Form(body, self.theme, label_width=8)
        self.mode = self.var('mode', MODES[0])
        form.choice(T('方式'), self.mode, MODE_TEXT)
        self.range_row = form.pair(T('区间 (nm)'), self.var('norm_low', '600'), self.var('norm_high', '1300'))
        self.mode.trace_add('write', lambda *_: self.sync_range())
        self.sync_range()
        hint(body, T('每条曲线各自除以自己的最大值。区间模式只用区间内的最大值，但整条曲线都会被缩放。'))

    def sync_range(self):
        state = '!disabled' if self.mode.get() == MODES[1] else 'disabled'
        for child in self.range_row.winfo_children():
            if isinstance(child, ttk.Entry):
                child.state([state])

    def build_figure(self, page):
        body = section(page, T('标题与坐标轴'), first=True)
        form = Form(body, self.theme, label_width=10)
        form.entry(T('图标题'), self.var('title', ''))
        form.pair(T('X 轴 (nm)'), self.var('xmin'), self.var('xmax'))
        form.pair(T('Y 轴'), self.var('ymin'), self.var('ymax'))
        hint(body, T('坐标范围留空 = 自动。'))
        self.legend = tk.BooleanVar(self, value=True)
        self.grid_lines = tk.BooleanVar(self, value=True)
        form = Form(section(page, T('显示')), self.theme)
        form.check(T('图例'), self.legend, self.schedule)
        form.check(T('网格线'), self.grid_lines, self.schedule)
        body = section(page, T('导出尺寸'))
        form = Form(body, self.theme, label_width=10)
        form.pair(T('宽 × 高 (英寸)'), self.var('width', '9'), self.var('height', '5.5'), '×')
        form.entry(T('分辨率 (DPI)'), self.var('dpi', '300'))
        hint(body, T('预览按导出比例缩放显示；尺寸 1–40 英寸，DPI 72–1200。'))

    def build_preview(self, parent):
        top = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(16, 10, 16, 0))
        top.pack(fill='x')
        self.info = tk.StringVar(self, value='')
        info = ttk.Label(top, textvariable=self.info, style='Preview.TLabel', wraplength=400)
        info.pack(side='left', fill='x', expand=True)
        info.bind('<Configure>', lambda e: info.configure(wraplength=max(200, e.width)))
        self.figure = Figure(figsize=(9, 5.5), dpi=100, facecolor='white', layout='tight')
        self.ax = self.figure.add_subplot(111)
        self.stage = ttk.Frame(parent, style='Preview.TFrame')
        self.stage.pack(fill='both', expand=True, padx=self.theme.px(16), pady=self.theme.px(12))
        self.canvas = figure_canvas(self.figure, self.stage)
        self.toolbar = PlotToolbar(parent, self.theme, self.canvas)
        self.toolbar.pack(side='bottom', fill='x', padx=self.theme.px(14), pady=self.theme.pad(0, 10), before=self.stage)
        self.empty = ttk.Label(self.stage, text=T('点击「添加光谱…」导入 CSV / TXT 光谱文件'), style='PreviewEmpty.TLabel')
        self._fit_job = None
        self.stage.bind('<Configure>', self.schedule_fit)

    def paint(self):
        self.refresh_list()

    # ---------- list ----------
    def refresh_list(self, select=None):
        if select is None:
            chosen = self.tree.selection()
            select = int(chosen[0]) if chosen else None
        self.tree.delete(*self.tree.get_children())
        for i, curve in enumerate(self.spectra):
            tag = f'c{i}'
            self.tree.insert('', 'end', iid=str(i), tags=(tag,), image=self.theme.color_chip(curve['color'], faded=not curve['show']), values=(
                '●' if curve['show'] else '○', '●' if curve['export'] else '○', curve['label']))
            self.tree.tag_configure(tag, foreground=self.theme.c['text'] if curve['show'] else self.theme.c['faint'])
        if select is not None and 0 <= select < len(self.spectra):
            self.tree.selection_set(str(select))

    def selected(self):
        chosen = self.tree.selection()
        return int(chosen[0]) if chosen else None

    def tree_click(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row or column not in ('#1', '#2'):
            return
        curve = self.spectra[int(row)]
        key = 'show' if column == '#1' else 'export'
        curve[key] = not curve[key]
        self.refresh_list(int(row))
        self.schedule()

    def add_files(self):
        paths = filedialog.askopenfilenames(title=T('添加光谱'), filetypes=[(T('光谱文件'), '*.csv *.txt *.dat'), (T('所有文件'), '*.*')])
        self.load_paths(paths)

    def load_paths(self, paths):
        errors = []
        for path in paths:
            try:
                x, y = read_spectrum(path)
                self.spectra.append(dict(x=x, y=y, label=Path(path).stem, color=COLORS[len(self.spectra) % len(COLORS)],
                                         show=True, export=True, path=path))
            except Exception as exc:
                errors.append(f'{Path(path).name}: {exc}')
        if len(self.spectra) == 1 and not self.vars['title'].get():
            self.vars['title'].set(self.spectra[0]['label'])
        self.refresh_list(len(self.spectra) - 1 if self.spectra else None)
        self.update_plot()
        if errors:
            messagebox.showerror(T('导入失败'), '\n'.join(errors))
        elif paths:
            self.app.status(T('已导入 {n} 条光谱').format(n=len(paths)), 'ok')

    def remove(self):
        i = self.selected()
        if i is not None:
            del self.spectra[i]
            self.refresh_list(min(i, len(self.spectra) - 1) if self.spectra else None)
            self.update_plot()

    def rename(self):
        i = self.selected()
        if i is None:
            return
        name = ask_string(self, self.theme, T('重命名'), T('曲线名称（图例中显示）：'), self.spectra[i]['label'])
        if name:
            self.spectra[i]['label'] = name
            self.refresh_list(i)
            self.update_plot()

    def pick_color(self):
        i = self.selected()
        if i is None:
            self.app.status(T('请先在列表中选择一条光谱'), 'error')
            return
        color = colorchooser.askcolor(self.spectra[i]['color'], title=T('曲线颜色'), parent=self.winfo_toplevel())[1]
        if color:
            self.spectra[i]['color'] = color
            self.refresh_list(i)
            self.update_plot()
            if self.theme.contrast(color) < 1.6:
                self.app.status(T('这个颜色在白色背景的图上很难看清，建议选深一点的颜色。'), 'error', timeout=12000)

    # ---------- plotting (logic unchanged from UV-Vis Plotter) ----------
    def number(self, key):
        value = self.vars[key].get().strip()
        if not value:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Limits must be finite numbers.')
        return number

    def dimensions(self):
        width, height, dpi = (self.number(k) for k in ('width', 'height', 'dpi'))
        if width is None or height is None or dpi is None or not (1 <= width <= 40 and 1 <= height <= 40 and 72 <= dpi <= 1200):
            raise ValueError(T('图片尺寸需在 1–40 英寸之间，DPI 在 72–1200 之间。'))
        if width * height * dpi * dpi > 100_000_000:
            raise ValueError(T('导出图片超过 1 亿像素，请减小尺寸或 DPI。'))
        return width, height, dpi

    def prepare(self, purpose='show'):
        limits = [self.number(k) for k in ('xmin', 'xmax', 'ymin', 'ymax')]
        for lo, hi in (limits[:2], limits[2:]):
            if lo is not None and hi is not None and lo >= hi:
                raise ValueError(T('坐标轴最小值必须小于最大值。'))
        mode = self.mode.get()
        low, high = (self.number('norm_low'), self.number('norm_high')) if mode == MODES[1] else (None, None)
        processed = []
        for curve in self.spectra:
            if not curve.get(purpose, True):
                continue
            try:
                values, peak = normalize(curve['x'], curve['y'], mode, low, high)
            except ValueError as exc:
                raise ValueError(f"{curve['label']}: {exc}") from exc
            processed.append((curve, values, peak))
        return limits, processed

    def draw_on(self, figure, purpose):
        limits, processed = self.prepare(purpose)
        figure.clear()
        ax = figure.add_subplot(111)
        for curve, y, peak in processed:
            ax.plot(curve['x'], y, color=curve['color'], linewidth=1.8, label=curve['label'])
        ax.set_title(self.vars['title'].get(), fontsize=19, pad=20)
        ax.set_xlabel('Wavelength (nm)', fontsize=13, labelpad=10)
        ax.set_ylabel('Absorbance (a.u.)' if self.mode.get() == MODES[2] else 'Normalized absorbance (a.u.)', fontsize=13, labelpad=10)
        if self.grid_lines.get():
            ax.grid(True, color='#d6d6d6', linewidth=1)
        ax.set_axisbelow(True)
        ax.tick_params(labelsize=11)
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        for spine in ax.spines.values():
            spine.set_color('#b8b8b8')
        if processed:
            if self.legend.get():
                ax.legend(frameon=False, fontsize=9)
            ax.margins(x=0, y=.08)
        ax.set_xlim(left=limits[0], right=limits[1])
        ax.set_ylim(bottom=limits[2], top=limits[3])
        figure.get_layout_engine().set(pad=2)
        return processed

    def schedule(self, *_):
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(450, lambda: self.update_plot(quiet=True))

    def update_plot(self, quiet=False):
        self._pending = None
        try:
            w, h, dpi = self.dimensions()
            processed = self.draw_on(self.figure, 'show')
        except Exception as exc:
            if quiet:
                self.app.status(str(exc), 'error')
            else:
                messagebox.showerror(T('无法绘图'), str(exc))
            return None
        self.fit_preview()
        self.canvas.draw_idle()
        if self.spectra:
            self.empty.place_forget()
        else:
            self.empty.place(relx=.5, rely=.5, anchor='center')
        peaks = '  ·  '.join(T('{name} 最大值 {peak}').format(name=c['label'], peak=f'{p:.4g}') for c, y, p in processed if p is not None)
        self.info.set(T('{n} 条显示  ·  导出 {w} × {h} in @ {dpi} DPI').format(n=len(processed), w=f'{w:g}', h=f'{h:g}', dpi=f'{dpi:g}') + (f'  ·  {peaks}' if peaks else ''))
        return processed

    def schedule_fit(self, event=None):
        if self.app.defer_if_hidden(self, self.schedule_fit):
            return
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self.fit_preview)

    def fit_preview(self, event=None):
        self._fit_job = None
        try:
            width, height, _ = self.dimensions()
        except ValueError:
            return
        fit_figure(self.canvas, self.stage, width, height, theme=self.theme)

    # ---------- export ----------
    def export_figure(self):
        try:
            width, height, dpi = self.dimensions()
            figure = Figure(figsize=(width, height), dpi=100, facecolor='white', layout='tight')
            processed = self.draw_on(figure, 'export')
        except Exception as exc:
            messagebox.showerror(T('导出失败'), str(exc))
            return
        if not processed:
            messagebox.showinfo(T('导出'), T('没有勾选「导出」的光谱。'))
            return
        path = filedialog.asksaveasfilename(title=T('导出图片'), defaultextension='.png',
                                            filetypes=[('PNG', '*.png'), ('PDF', '*.pdf'), ('SVG', '*.svg')])
        if path:
            try:
                figure.savefig(path, dpi=dpi, facecolor='white')
                self.app.status(T('已保存图片：{path}').format(path=path), 'ok')
            except Exception as exc:
                messagebox.showerror(T('导出失败'), str(exc))

    def export_data(self):
        try:
            _, processed = self.prepare('export')
        except ValueError as exc:
            messagebox.showerror(T('导出失败'), str(exc))
            return
        if not processed:
            messagebox.showinfo(T('导出'), T('没有勾选「导出」的光谱。'))
            return
        path = filedialog.asksaveasfilename(title=T('导出数据'), defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['Spectrum', 'Wavelength (nm)', 'Raw absorbance', 'Plotted absorbance', 'Normalization divisor', 'Normalization mode'])
                    for curve, y, peak in processed:
                        writer.writerows((curve['label'], x, raw, value, peak if peak is not None else '', self.mode.get())
                                         for x, raw, value in zip(curve['x'], curve['y'], y))
                self.app.status(T('已保存数据：{path}').format(path=path), 'ok')
            except Exception as exc:
                messagebox.showerror(T('导出失败'), str(exc))

    # ---------- shell hooks ----------
    def shortcut_open(self):
        self.add_files()

    def shortcut_save(self):
        self.export_figure()

    def shortcut_refresh(self):
        self.update_plot()

    def close(self):
        for job in (self._pending, self._fit_job):
            if job is not None:
                self.after_cancel(job)
        self._pending = self._fit_job = None
