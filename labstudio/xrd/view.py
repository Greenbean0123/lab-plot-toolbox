"""XRD stacked patterns with reference cards. One layer list defines both content and plot order."""
import json
import os
import re
import subprocess
import sys
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, colorchooser

import numpy as np
from matplotlib.colors import to_hex

from .xrd_core import read_sample, read_card, make_figure, export_data, palette_colors
from .phase import broaden, sample_peaks, scherrer_size, match
from ..paths import DEMO_DATA, data_directory, legacy_xrd_directory
from ..widgets import (ToolLayout, Segmented, Form, PlotToolbar, Dialog, section, hint, button_row,
                       ask_string, figure_canvas, fit_figure)
from ..i18n import T

LABEL_DEFAULTS = {'font_size': 11, 'x_percent': 99.2, 'y_offset': 1.08, 'alignment': 'right'}
NUMERIC = ('xmin', 'xmax', 'spacing', 'width_cm', 'height_cm', 'wavelength',
           'frame_linewidth', 'pattern_linewidth', 'card_linewidth', 'reference_baseline_linewidth')


# ---------- startup data folder (same layout as XRD Plotter) ----------
def prepare_folder(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('patterns', 'references'):
        (folder / name).mkdir(exist_ok=True)
    readme = folder / 'README.txt'
    if not readme.exists():
        readme.write_text('XRD startup data\n\n'
                          'patterns/: sample CSV files (2theta, Yobs, Bkg columns).\n'
                          'references/: PDF, CIF, XY or reference CSV files.\n'
                          'Files load automatically when the XRD tool opens.\n', encoding='utf-8')
    return folder


def discover(folder):
    folder = Path(folder)

    def files(directory, extensions):
        if not directory.is_dir():
            return []
        return sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in extensions),
                      key=lambda p: p.name.casefold())
    samples = files(folder, ('.csv',)) + files(folder / 'patterns', ('.csv',))
    cards = files(folder, ('.pdf', '.cif', '.xy')) + files(folder / 'references', ('.pdf', '.cif', '.xy', '.csv'))
    return samples, cards


def default_startup_folder(prefs):
    chosen = prefs.get('xrd_startup_folder')
    if chosen:
        return Path(chosen)
    legacy = legacy_xrd_directory()
    try:  # Folder chosen in the standalone XRD Plotter.
        return Path(json.loads((legacy / 'startup-data.json').read_text(encoding='utf-8'))['folder'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if (legacy / 'defaultpattern').is_dir():
        return legacy / 'defaultpattern'
    return data_directory() / 'xrd-startup'


class LabelDialog(Dialog):
    def __init__(self, parent, theme, layer):
        super().__init__(parent, theme, T('标签样式'))
        current = dict(LABEL_DEFAULTS, **layer['style'])
        ttk.Label(self.body, text=layer['label'], style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        form = Form(self.body, theme, label_width=14)
        self.fields = {k: tk.StringVar(self, value=str(current[k])) for k in LABEL_DEFAULTS}
        form.entry(T('字号 (pt)'), self.fields['font_size'])
        form.entry(T('水平位置 (% 范围)'), self.fields['x_percent'])
        form.entry(T('垂直偏移'), self.fields['y_offset'])
        form.choice(T('对齐'), self.fields['alignment'], [('left', T('左对齐')), ('center', T('居中')), ('right', T('右对齐'))])
        hint(self.body, T('水平位置：0 = 左边缘，100 = 右边缘。\n垂直偏移以归一化强度为单位，1 = 峰高。'), pady=(10, 0))
        ttk.Button(self.extra_buttons, text=T('恢复默认'), command=self.reset).pack(side='left')

    def reset(self):
        for key, value in LABEL_DEFAULTS.items():
            self.fields[key].set(str(value))

    def apply(self):
        style = {k: (v.get() if k == 'alignment' else float(v.get())) for k, v in self.fields.items()}
        if (not all(np.isfinite(style[k]) for k in ('font_size', 'x_percent', 'y_offset'))
                or not 1 <= style['font_size'] <= 200 or not 0 <= style['x_percent'] <= 100):
            raise ValueError(T('字号需在 1–200 pt，水平位置在 0–100%，垂直偏移需为有限数。'))
        self.result = {} if style == LABEL_DEFAULTS else style


class XRDView(ttk.Frame):
    TITLE = 'XRD 图谱'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app, self.theme = app, app.theme
        self.prefs = app.prefs
        self.layers = []  # top → bottom plot order
        self.palette = list(self.prefs.get('xrd_palette', ['#0000dd', '#d1453b', '#1d9a5b', '#8a4fd0']))
        self.palette_index = 0
        self.vars = {}
        self.rendered = {}
        self.canvas = None
        self._pending = None
        self._drag = None
        self.loaded_defaults = False
        self.startup_ids = set()
        self.startup_folder = default_startup_folder(self.prefs)
        layout = ToolLayout(self, self.theme, T('XRD 堆叠图谱'), T('样品图谱与标准卡片 (PDF / CIF / XY / CSV) 上下堆叠对照'))
        layout.action(T('导出数据…'), self.export_processed)
        layout.action(T('导出图片…'), self.export_image, accent=True)
        layout.action(T('刷新预览'), lambda: self.draw())
        self.pages = Segmented(layout.controls, self.theme)
        self.pages.pack(fill='both', expand=True)
        self.build_layers(self.pages.add(T('图层')))
        self.build_phase(self.pages.add(T('相分析')))
        self.build_layout(self.pages.add(T('版式')))
        self.build_style(self.pages.add(T('颜色线宽')))
        self.build_startup(self.pages.add(T('启动数据')))
        self.build_preview(layout.preview)
        self.theme.subscribe(self.paint)

    # ---------- page: layers ----------
    def build_layers(self, page):
        body = section(page, T('图层（从上到下即出图顺序）'), first=True)
        button_row(body, [(T('添加样品图谱…'), lambda: self.add('sample')), (T('添加标准卡片…'), lambda: self.add('card'))])
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        px = self.theme.px
        self.tree = ttk.Treeview(frame, columns=('vis', 'kind', 'name'), show=('tree', 'headings'), height=10, selectmode='browse')
        self.tree.column('#0', width=px(38), minwidth=px(38), stretch=False, anchor='center')
        for col, text, width, anchor, stretch in (('vis', T('显示'), 44, 'center', False), ('kind', T('类型'), 52, 'center', False),
                                                  ('name', T('名称'), 200, 'w', True)):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=px(width), minwidth=px(width if not stretch else 80), anchor=anchor, stretch=stretch)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<ButtonPress-1>', self.drag_start)
        self.tree.bind('<B1-Motion>', self.drag_motion)
        self.tree.bind('<ButtonRelease-1>', self.drag_end)
        self.tree.bind('<Double-1>', lambda e: self.rename() if self.tree.identify_column(e.x) == '#3' else None)
        self.tree.bind('<Delete>', lambda e: self.remove())
        self.tree.bind('<Alt-Up>', lambda e: self.move(-1))
        self.tree.bind('<Alt-Down>', lambda e: self.move(1))
        button_row(body, [(T('上移'), lambda: self.move(-1)), (T('下移'), lambda: self.move(1)), (T('删除'), self.remove)], pady=(8, 0))
        button_row(body, [(T('重命名'), self.rename), (T('标签样式…'), self.edit_label), (T('曲线颜色…'), self.pick_color)], pady=(6, 0))
        button_row(body, [(T('全部显示'), lambda: self.set_all_visible(True)), (T('全部隐藏'), lambda: self.set_all_visible(False))], pady=(6, 0))
        hint(body, T('拖动行可调整顺序；点击「显示」列切换显隐，隐藏的图层不会导出；双击名称可重命名。'
                   '样品图谱 CSV 需含 2theta、Yobs、Bkg 列，按 (obs − bkg) / max 归一化。'))

    # ---------- page: layout ----------
    def var(self, key, default):
        v = tk.StringVar(self, value=default)
        self.vars[key] = v
        v.trace_add('write', lambda *_: self.schedule())
        return v

    def build_layout(self, page):
        body = section(page, T('坐标范围'), first=True)
        form = Form(body, self.theme, label_width=12)
        form.pair(T('2θ 范围 (°)'), self.var('xmin', '10'), self.var('xmax', '90'))
        form.entry(T('堆叠间距'), self.var('spacing', '1.35'))
        form.entry(T('X 射线波长 (Å)'), self.var('wavelength', '1.54059'))
        hint(body, T('堆叠间距 ≥ 1.05。波长用于由 CIF 计算峰位和谢乐公式（默认 Cu Kα1）。'))
        body = section(page, T('图片尺寸'))
        form = Form(body, self.theme, label_width=12)
        form.entry(T('宽度 (cm)'), self.var('width_cm', '34.29'))
        form.entry(T('高度 (cm)'), self.var('height_cm', ''))
        form.entry(T('PNG 分辨率 (DPI)'), self.var('dpi', '400'))
        hint(body, T('高度留空 = 按图层数自动计算。预览按导出比例缩放，与窗口大小无关。'))
        body = section(page, T('文字'))
        form = Form(body, self.theme, label_width=12)
        form.entry(T('X 轴标题'), self.var('xlabel', '2θ (°)'))
        form.entry(T('Y 轴标题'), self.var('ylabel', 'Intensity(a.u.)'))
        form.entry(T('图标题'), self.var('title', ''))

    # ---------- page: colors & lines ----------
    def build_style(self, page):
        body = section(page, T('默认曲线颜色'), first=True)
        form = Form(body, self.theme, label_width=10)
        form.color(T('全局颜色'), self.var('color', '#0000dd'), T('全局曲线颜色'))
        ttk.Button(body, text=T('所有图谱改用全局颜色'), command=self.use_global_color).pack(fill='x', pady=(6, 0))
        hint(body, T('未单独设色的样品图谱使用全局颜色；参考卡片固定为黑色。'))
        body = section(page, T('自定义调色板'))
        self.palette_canvas = tk.Canvas(body, height=self.theme.px(34), highlightthickness=0, cursor='hand2')
        self.palette_canvas.pack(fill='x', pady=(0, 6))
        self.palette_canvas.bind('<Configure>', lambda e: self.draw_palette())
        self.palette_canvas.bind('<Button-1>', self.palette_click)
        self.palette_canvas.bind('<Double-1>', lambda e: self.palette_edit())
        button_row(body, [(T('添加'), self.palette_add), (T('修改'), self.palette_edit), (T('删除'), self.palette_remove),
                          ('◀', lambda: self.palette_move(-1)), ('▶', lambda: self.palette_move(1))])
        ttk.Button(body, text=T('按顺序应用到所有样品图谱'), style='Accent.TButton', command=self.apply_palette).pack(fill='x', pady=(4, 0))
        hint(body, T('单击色块选中，双击修改。颜色按图层顺序循环分配给样品图谱。'))
        body = section(page, T('线宽 (pt)'))
        form = Form(body, self.theme, label_width=12)
        for key, caption, default in (('pattern_linewidth', T('样品曲线'), '0.85'), ('card_linewidth', T('卡片峰线 / 曲线'), '0.85'),
                                      ('reference_baseline_linewidth', T('卡片基线'), '0.45'), ('frame_linewidth', T('坐标框'), '1.0')):
            form.spin(caption, self.var(key, default), 0, 10, 0.05)
        hint(body, T('0 表示隐藏对应线条。对预览和所有导出都生效。'))

    # ---------- page: startup data ----------
    def build_startup(self, page):
        body = section(page, T('启动时自动加载'), first=True)
        self.folder_text = tk.StringVar(self, value=str(self.startup_folder))
        label = ttk.Label(body, textvariable=self.folder_text, style='Muted.TLabel', wraplength=300, justify='left')
        label.pack(fill='x')
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(120, e.width - 4)))
        button_row(body, [(T('打开文件夹'), self.open_folder), (T('更换文件夹…'), self.choose_folder)], pady=(10, 0))
        ttk.Button(body, text=T('重新加载此文件夹'), command=self.reload_defaults).pack(fill='x', pady=(6, 0))
        hint(body, T('patterns 子文件夹放样品 CSV，references 子文件夹放 PDF / CIF / XY / CSV 卡片；'
                   '直接放在文件夹根目录也可以。首次打开 XRD 工具时自动加载。'))
        body = section(page, T('示例'))
        ttk.Button(body, text=T('加载示例数据（合成数据）'), command=self.load_demo).pack(fill='x')

    # ---------- preview ----------
    def build_preview(self, parent):
        top = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(16, 10, 16, 0))
        top.pack(fill='x')
        self.info = tk.StringVar(self, value='')
        ttk.Label(top, textvariable=self.info, style='Preview.TLabel').pack(side='left')
        self.toolbar_holder = ttk.Frame(parent, style='Preview.TFrame')
        self.toolbar_holder.pack(side='bottom', fill='x', padx=self.theme.px(14), pady=self.theme.pad(0, 10))
        self.stage = ttk.Frame(parent, style='Preview.TFrame')
        self.stage.pack(fill='both', expand=True, padx=self.theme.px(16), pady=self.theme.px(12))
        self._fit_job = None
        self.stage.bind('<Configure>', lambda e: self.schedule_fit())
        self.empty = ttk.Label(self.stage, text=T('添加样品图谱或标准卡片后在此预览'), style='PreviewEmpty.TLabel')
        self.empty.place(relx=.5, rely=.5, anchor='center')

    def paint(self):
        self.palette_canvas.configure(background=self.theme.c['surface'])
        self.draw_palette()
        self.refresh_tree()

    def on_show(self):
        if not self.loaded_defaults:
            self.loaded_defaults = True
            self.paint()
            self.after(50, self.load_defaults)

    # ---------- layer list ----------
    def refresh_tree(self, select=None):
        if select is None:
            chosen = self.tree.selection()
            select = chosen[0] if chosen else None
        self.tree.delete(*self.tree.get_children())
        c = self.theme.c
        for layer in self.layers:
            iid = layer['id']
            if layer['kind'] == 'sample':
                color = layer['color'] or self.vars['color'].get()
            else:
                color = c['text']
            try:
                self.winfo_rgb(color)
            except tk.TclError:
                color = c['text']
            # The chip shows the true curve color (cards plot in black) on a contrasting ring; text stays readable.
            chip = self.theme.color_chip(color if layer['kind'] == 'sample' else '#000000', faded=not layer['visible'])
            self.tree.insert('', 'end', iid=iid, tags=(iid,), image=chip, values=(
                '●' if layer['visible'] else '○', T('图谱') if layer['kind'] == 'sample' else T('卡片'), layer['label']))
            self.tree.tag_configure(iid, foreground=c['text'] if layer['visible'] else c['faint'])
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.see(select)
        self.refresh_sample_box()

    def selected(self):
        chosen = self.tree.selection()
        if not chosen:
            return None
        return next((i for i, layer in enumerate(self.layers) if layer['id'] == chosen[0]), None)

    def new_layer(self, kind, path, label):
        return dict(id=uuid.uuid4().hex, kind=kind, path=str(path), label=label, color=None, style={}, visible=True)

    def add(self, kind):
        types = [(T('XRD 图谱 CSV'), '*.csv')] if kind == 'sample' else [(T('标准卡片'), '*.pdf *.csv *.cif *.xy')]
        paths = filedialog.askopenfilenames(title=T('添加样品图谱') if kind == 'sample' else T('添加标准卡片'),
                                            initialdir=str(self.startup_folder) if self.startup_folder.is_dir() else None,
                                            filetypes=types)
        self.add_paths(kind, paths)

    def add_paths(self, kind, paths):
        errors, added = [], None
        self.app.status(T('正在读取文件…'), 'busy', timeout=None)
        for path in paths:
            try:
                if kind == 'card':
                    label = read_card(path)['label']
                else:
                    read_sample(path)
                    label = Path(path).stem
                layer = self.new_layer(kind, path, label)
                # New patterns go above the reference cards; new cards go to the bottom.
                if kind == 'sample':
                    index = next((i for i, l in enumerate(self.layers) if l['kind'] == 'card'), len(self.layers))
                    self.layers.insert(index, layer)
                else:
                    self.layers.append(layer)
                added = layer['id']
            except Exception as e:
                errors.append(f'{Path(path).name}: {e}')
        self.refresh_tree(added)
        if errors:
            self.app.status(T('部分文件无法读取'), 'error')
            messagebox.showerror(T('添加文件'), '\n'.join(errors))
        elif paths:
            self.app.status(T('已添加 {n} 个文件').format(n=len(paths)), 'ok')
        else:
            self.app.status(T('就绪'), timeout=None)
        if paths:
            self.draw(quiet=True)

    def remove(self):
        i = self.selected()
        if i is None:
            return
        self.layers.pop(i)
        self.refresh_tree(self.layers[min(i, len(self.layers) - 1)]['id'] if self.layers else None)
        self.draw(quiet=True)

    def move(self, step):
        i = self.selected()
        if i is None or not 0 <= i + step < len(self.layers):
            return 'break'
        self.layers[i], self.layers[i + step] = self.layers[i + step], self.layers[i]
        self.refresh_tree(self.layers[i + step]['id'])
        self.draw(quiet=True)
        return 'break'

    def rename(self):
        i = self.selected()
        if i is None:
            return
        name = ask_string(self, self.theme, T('重命名'), T('图中显示的标签：'), self.layers[i]['label'])
        if name:
            self.layers[i]['label'] = name
            self.refresh_tree()
            self.draw(quiet=True)

    def set_all_visible(self, value):
        for layer in self.layers:
            layer['visible'] = value
        self.refresh_tree()
        self.draw(quiet=True)

    def edit_label(self):
        i = self.selected()
        if i is None:
            self.app.status(T('请先在图层列表中选择一项'), 'error')
            return
        result = LabelDialog(self, self.theme, self.layers[i]).show()
        if result is not None:
            self.layers[i]['style'] = result
            self.draw(quiet=True)

    def pick_color(self):
        i = self.selected()
        if i is None:
            self.app.status(T('请先在图层列表中选择一个样品图谱'), 'error')
            return
        layer = self.layers[i]
        if layer['kind'] != 'sample':
            self.app.status(T('参考卡片固定为黑色，只能修改样品图谱的颜色'), 'error')
            return
        try:
            initial = to_hex(layer['color'] or self.vars['color'].get())
        except ValueError:
            initial = '#0000dd'
        chosen = colorchooser.askcolor(initial, title=T('{name} 的颜色').format(name=layer['label']), parent=self.winfo_toplevel())[1]
        if chosen:
            layer['color'] = chosen
            self.refresh_tree()
            self.draw(quiet=True)
            if self.theme.contrast(chosen) < 1.6:
                self.app.status(T('这个颜色在白色背景的图上很难看清，建议选深一点的颜色。'), 'error', timeout=12000)

    def use_global_color(self):
        for layer in self.layers:
            layer['color'] = None
        self.refresh_tree()
        self.draw(quiet=True)

    # Drag rows to reorder; a click on the visibility column toggles instead.
    def drag_start(self, event):
        row = self.tree.identify_row(event.y)
        self._drag = None
        if not row:
            return
        if self.tree.identify_column(event.x) == '#1':
            layer = next(l for l in self.layers if l['id'] == row)
            layer['visible'] = not layer['visible']
            self.refresh_tree(row)
            self.draw(quiet=True)
            return 'break'
        self._drag = row

    def drag_motion(self, event):
        if not self._drag:
            return
        target = self.tree.identify_row(event.y)
        if target and target != self._drag:
            self.tree.move(self._drag, '', self.tree.index(target))
            self.tree.configure(cursor='fleur')

    def drag_end(self, _=None):
        row, self._drag = self._drag, None
        self.tree.configure(cursor='')
        if not row:
            return
        order = list(self.tree.get_children())
        if order != [l['id'] for l in self.layers]:
            rank = {iid: i for i, iid in enumerate(order)}
            self.layers.sort(key=lambda l: rank[l['id']])
            self.draw(quiet=True)

    # ---------- palette ----------
    def draw_palette(self):
        canvas = self.palette_canvas
        canvas.delete('all')
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        c = self.theme.c
        if not self.palette:
            canvas.create_text(width / 2, height / 2, text=T('调色板为空，点击添加'), fill=c['muted'], font=self.theme.fonts['small'])
            return
        n = len(self.palette)
        gap = self.theme.px(4)
        w = (width - gap * (n - 1)) / n
        for i, color in enumerate(self.palette):
            x0 = i * (w + gap)
            selected = i == self.palette_index
            canvas.create_rectangle(x0 + 1, 3, x0 + w - 1, height - 3, fill=color,
                                    outline=c['text'] if selected else c['border'], width=2 if selected else 1)

    def palette_click(self, event):
        if not self.palette:
            self.palette_add()
            return
        n = len(self.palette)
        self.palette_index = min(n - 1, max(0, int(event.x / max(1, self.palette_canvas.winfo_width()) * n)))
        self.draw_palette()

    def palette_changed(self):
        self.palette_index = min(self.palette_index, len(self.palette) - 1) if self.palette else 0
        self.prefs.set('xrd_palette', self.palette)
        self.draw_palette()

    def palette_add(self):
        initial = self.palette[self.palette_index] if self.palette else '#0000dd'
        color = colorchooser.askcolor(initial, title=T('添加调色板颜色'), parent=self.winfo_toplevel())[1]
        if color:
            position = self.palette_index + 1 if self.palette else 0
            self.palette.insert(position, color)
            self.palette_index = position
            self.palette_changed()

    def palette_edit(self):
        if not self.palette:
            return
        color = colorchooser.askcolor(self.palette[self.palette_index], title=T('修改调色板颜色'), parent=self.winfo_toplevel())[1]
        if color:
            self.palette[self.palette_index] = color
            self.palette_changed()

    def palette_remove(self):
        if self.palette:
            self.palette.pop(self.palette_index)
            self.palette_changed()

    def palette_move(self, step):
        i, j = self.palette_index, self.palette_index + step
        if self.palette and 0 <= j < len(self.palette):
            self.palette[i], self.palette[j] = self.palette[j], self.palette[i]
            self.palette_index = j
            self.palette_changed()

    def apply_palette(self):
        samples = [l for l in self.layers if l['kind'] == 'sample']
        if not samples:
            self.app.status(T('还没有样品图谱'), 'error')
            return
        try:
            colors = palette_colors(self.palette, len(samples))
        except ValueError as e:
            messagebox.showerror(T('调色板'), str(e))
            return
        for layer, color in zip(samples, colors):
            layer['color'] = color
        self.refresh_tree()
        self.draw(quiet=True)

    # ---------- startup data ----------
    def open_folder(self):
        try:
            folder = prepare_folder(self.startup_folder)
            if sys.platform == 'win32':
                os.startfile(str(folder))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])
        except OSError as error:
            messagebox.showerror(T('启动数据'), str(error))

    def choose_folder(self):
        selected = filedialog.askdirectory(title=T('选择启动数据文件夹'), initialdir=str(self.startup_folder))
        if not selected:
            return
        self.startup_folder = Path(selected).resolve()
        self.prefs.set('xrd_startup_folder', str(self.startup_folder))
        self.folder_text.set(str(self.startup_folder))
        self.reload_defaults(ask=False)

    def reload_defaults(self, ask=True):
        if ask and self.layers and not messagebox.askyesno(T('重新加载'), T('用启动文件夹的内容替换当前图层列表？')):
            return
        self.layers = []
        self.load_defaults()
        if not self.layers:
            self.refresh_tree()
            self.draw(quiet=True)

    def load_defaults(self, folder=None, shorten_labels=True):
        folder = Path(folder or self.startup_folder)
        samples, cards = discover(folder)
        if not samples and not cards:
            self.app.status(T('启动文件夹中没有数据 · 可在「启动数据」页加载示例'), timeout=10000)
            return
        self.app.status(T('正在加载 {n} 个文件…').format(n=len(samples) + len(cards)), 'busy', timeout=None)
        errors = []
        for kind, paths in (('sample', samples), ('card', cards)):
            for path in paths:
                try:
                    if kind == 'sample':
                        read_sample(path)
                        match = re.search(r'\d+C', path.stem) if shorten_labels else None
                        label = match.group() if match else path.stem
                    else:
                        label = read_card(path)['label']
                    self.layers.append(self.new_layer(kind, path, label))
                except Exception as error:
                    errors.append(f'{path.name}: {error}')
        if folder == Path(self.startup_folder):
            self.startup_ids = {l['id'] for l in self.layers}
        self.refresh_tree()
        self.draw(quiet=True)
        if errors:
            messagebox.showwarning(T('启动数据'), T('以下文件无法读取：\n') + '\n'.join(errors))
        self.app.status(T('已加载 {folder} 中的 {n} 个文件').format(folder=folder.name, n=len(self.layers)), 'ok')

    def load_demo(self):
        if self.layers and not messagebox.askyesno(T('示例数据'), T('用示例数据替换当前图层列表？')):
            return
        self.layers = []
        self.load_defaults(DEMO_DATA / 'xrd', shorten_labels=False)

    def layers_from_startup(self):
        """True when the list only holds what the startup folder loads again automatically."""
        return {l['id'] for l in self.layers} == self.startup_ids

    # ---------- page: phase identification ----------
    def build_phase(self, page):
        body = section(page, T('纳米晶展宽（谢乐公式）'), first=True)
        form = Form(body, self.theme, label_width=12)
        self.broaden_on = tk.BooleanVar(self, value=False)
        form.check(T('把卡片竖线画成纳米晶峰形'), self.broaden_on, self.schedule)
        self.phase_vars = {}
        for key, caption, default in (('size', T('晶粒尺寸 (nm)'), '10'), ('k', T('形状因子 K'), '0.9')):
            var = tk.StringVar(self, value=default)
            var.trace_add('write', lambda *a: self.schedule() if self.broaden_on.get() else None)
            self.phase_vars[key] = var
            form.entry(caption, var)
        hint(body, T('按 β = Kλ / (L·cosθ) 把每条卡片峰展宽成伪 Voigt 峰形，可直接与纳米晶的宽峰对比。'
                     '波长取「版式」页的 X 射线波长；XY 曲线卡片不变。'))
        body = section(page, T('物相匹配'))
        form = Form(body, self.theme, label_width=12)
        self.match_sample = tk.StringVar(self)
        self.sample_box = form.combo(T('样品图谱'), self.match_sample, [])
        for key, caption, default in (('tolerance', T('峰位容差 ± (°)'), '0.3'), ('prominence', T('寻峰阈值 (0–1)'), '0.05'),
                                      ('instrument', T('仪器展宽 (°)'), '0')):
            self.phase_vars[key] = tk.StringVar(self, value=default)
            form.entry(caption, self.phase_vars[key])
        self.mark_peaks = tk.BooleanVar(self, value=True)
        form.check(T('在预览中标出峰的归属（不进入导出图）'), self.mark_peaks, lambda: self.draw(quiet=True))
        ttk.Button(body, text=T('匹配所有显示的卡片'), style='Accent.TButton', command=self.run_match).pack(fill='x', pady=(8, 0))
        hint(body, T('用样品图谱中找到的峰，与每张显示中的卡片比对：「命中」= 卡片强峰在样品中被找到的比例（按强度加权），'
                     '「解释」= 样品峰被该卡片解释的比例（按峰高加权），匹配度为二者的几何平均。'))
        body = section(page, T('匹配结果'))
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        px = self.theme.px
        self.phase_tree = ttk.Treeview(frame, columns=('phase', 'score', 'found', 'explained', 'shift'), show='headings',
                                       height=4, selectmode='none')
        for col, text, width, stretch in (('phase', T('物相'), 140, True), ('score', T('匹配度'), 56, False),
                                          ('found', T('命中'), 56, False), ('explained', T('解释'), 74, False),
                                          ('shift', T('偏移 (°)'), 64, False)):
            self.phase_tree.heading(col, text=text, anchor='w' if col == 'phase' else 'center')
            self.phase_tree.column(col, width=px(width), minwidth=px(min(width, 50)), stretch=stretch,
                                   anchor='w' if col == 'phase' else 'center')
        self.phase_tree.pack(fill='x')
        self.match_summary = tk.StringVar(self, value=T('尚未匹配'))
        summary = ttk.Label(body, textvariable=self.match_summary, style='Muted.TLabel', justify='left', wraplength=300)
        summary.pack(fill='x', pady=(8, 8))
        summary.bind('<Configure>', lambda e: summary.configure(wraplength=max(120, e.width - 4)))
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        self.peak_tree = ttk.Treeview(frame, columns=('tt', 'fwhm', 'size', 'phase'), show='headings', height=8, selectmode='none')
        for col, text, width, stretch in (('tt', '2θ (°)', 58, False), ('fwhm', 'FWHM (°)', 66, False),
                                          ('size', T('晶粒 (nm)'), 64, False), ('phase', T('归属'), 150, True)):
            self.peak_tree.heading(col, text=text, anchor='w' if col == 'phase' else 'center')
            self.peak_tree.column(col, width=px(width), minwidth=px(min(width, 50)), stretch=stretch,
                                  anchor='w' if col == 'phase' else 'center')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.peak_tree.yview)
        self.peak_tree.configure(yscrollcommand=scroll.set)
        self.peak_tree.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        ttk.Button(body, text=T('导出匹配结果 (CSV)…'), command=self.export_match).pack(fill='x', pady=(8, 0))
        self.match = None

    def phase_number(self, key, low=None, positive=False):
        value = float(self.phase_vars[key].get())
        if not np.isfinite(value) or (positive and value <= 0) or (low is not None and value < low):
            raise ValueError(T('请检查相分析参数：{name}').format(name=self.phase_vars[key].get() or key))
        return value

    def sample_choices(self):
        samples = [l for l in self.layers if l['kind'] == 'sample']
        return {f'{i}. {l["label"]}': l for i, l in enumerate(samples, 1)}

    def refresh_sample_box(self):
        if not hasattr(self, 'sample_box'):
            return
        choices = self.sample_choices()
        self.sample_box.configure(values=list(choices))
        if self.match_sample.get() not in choices:
            self.match_sample.set(next(iter(choices), ''))

    def run_match(self):
        layer = self.sample_choices().get(self.match_sample.get())
        if layer is None:
            self.app.status(T('请先添加样品图谱'), 'error')
            return
        try:
            settings = self.settings()
            tolerance = self.phase_number('tolerance', positive=True)
            prominence = self.phase_number('prominence', positive=True)
            instrument = self.phase_number('instrument', low=0)
            k = self.phase_number('k', positive=True)
            card_layers = [l for l in self.layers if l['kind'] == 'card' and l['visible']]
            if not card_layers:
                raise ValueError(T('请先添加并显示标准卡片'))
            sample = read_sample(layer['path'])
            peaks = sample_peaks(sample['x'], sample['normalized'], settings['xmin'], settings['xmax'], prominence)
            if not peaks:
                raise ValueError(T('在当前 2θ 范围内没有找到峰，请降低寻峰阈值'))
            cards = []
            for card_layer in card_layers:
                card = read_card(card_layer['path'], wavelength=settings['wavelength'], upper_angle=settings['xmax'])
                card['label'] = card_layer['label']
                cards.append(card)
            results, assignments = match(peaks, cards, settings['xmin'], settings['xmax'], tolerance)
            sizes = scherrer_size([a['two_theta'] for a in assignments], [a['fwhm'] for a in assignments],
                                  settings['wavelength'], k, instrument)
        except Exception as e:
            messagebox.showerror(T('物相匹配'), str(e))
            return
        self.match = dict(layer=layer['id'], sample=layer['label'], results=results, assignments=assignments,
                          sizes=[float(v) for v in sizes], tolerance=tolerance, prominence=prominence,
                          instrument=instrument, k=k, wavelength=settings['wavelength'])
        self.show_match()
        self.draw(quiet=True)
        best = results[0]
        self.app.status(T('最佳匹配：{phase}（匹配度 {score}）').format(phase=best['label'], score=f"{best['score']:.0%}"), 'ok')

    def show_match(self):
        m = self.match
        self.phase_tree.delete(*self.phase_tree.get_children())
        for r in m['results']:
            self.phase_tree.insert('', 'end', values=(
                r['label'], f"{r['score']:.0%}", f"{r['lines_found']}/{r['lines']}", f"{r['explained']:.0%}",
                '' if r['shift'] is None else f"{r['shift']:+.2f}"))
        self.peak_tree.delete(*self.peak_tree.get_children())
        c = self.theme.c
        self.peak_tree.tag_configure('none', foreground=c['danger'])
        for a, size in zip(m['assignments'], m['sizes']):
            best = a['matches'][0] if a['matches'] else None
            text = f"{best['label']} {best['hkl']}".strip() if best else T('未归属')
            self.peak_tree.insert('', 'end', tags=() if best else ('none',), values=(
                f"{a['two_theta']:.2f}", f"{a['fwhm']:.3f}", f'{size:.1f}' if np.isfinite(size) else '—', text))
        finite = [v for v in m['sizes'] if np.isfinite(v)]
        unassigned = [f"{a['two_theta']:.2f}°" for a in m['assignments'] if not a['matches']]
        lines = [T('{sample}：找到 {n} 个峰。').format(sample=m['sample'], n=len(m['assignments']))]
        if finite:
            lines.append(T('谢乐晶粒尺寸中位数 {size} nm（{n} 个峰，K = {k}）。').format(
                size=f'{np.median(finite):.1f}', n=len(finite), k=f"{m['k']:g}"))
        lines.append(T('未归属的峰：{peaks}').format(peaks=', '.join(unassigned)) if unassigned else T('所有峰都已归属。'))
        self.match_summary.set(' '.join(lines))

    def mark_match(self, fig, settings):
        m = self.match
        order = settings.get('display_order', [])
        if not m or not self.mark_peaks.get() or m['layer'] not in order or not fig.axes:
            return
        ax = fig.axes[0]
        base = (len(order) - 1 - order.index(m['layer'])) * settings['spacing']
        for a in m['assignments']:
            if not settings['xmin'] <= a['two_theta'] <= settings['xmax']:
                continue
            color = '#1d9a5b' if a['matches'] else '#d1453b'
            y = base + a['height'] + 0.06
            ax.plot([a['two_theta']], [y], marker='v', color=color, markersize=5, zorder=6)
            label = a['matches'][0]['hkl'] if a['matches'] else '?'
            if label:
                ax.text(a['two_theta'], y + 0.05, label, color=color, fontsize=7, ha='center', va='bottom', zorder=6)

    def export_match(self):
        if not self.match:
            self.app.status(T('请先运行物相匹配'), 'error')
            return
        path = filedialog.asksaveasfilename(title=T('导出匹配结果 (CSV)…'), defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        import csv
        m = self.match
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                w = csv.writer(stream)
                w.writerow(['sample', m['sample']])
                w.writerow(['tolerance_deg', m['tolerance'], 'prominence', m['prominence'], 'instrument_fwhm_deg', m['instrument'],
                            'scherrer_K', m['k'], 'wavelength_A', m['wavelength']])
                w.writerow([])
                w.writerow(['phase', 'score', 'card_lines_found', 'card_lines', 'intensity_weighted_found', 'sample_explained', 'mean_shift_deg'])
                for r in m['results']:
                    w.writerow([r['label'], f"{r['score']:.4f}", r['lines_found'], r['lines'], f"{r['found']:.4f}",
                                f"{r['explained']:.4f}", '' if r['shift'] is None else f"{r['shift']:.4f}"])
                w.writerow([])
                w.writerow(['two_theta_deg', 'height_norm', 'fwhm_deg', 'scherrer_size_nm', 'assigned_phase', 'hkl', 'delta_deg', 'other_matches'])
                for a, size in zip(m['assignments'], m['sizes']):
                    best = a['matches'][0] if a['matches'] else {}
                    others = '; '.join(f"{x['label']} {x['hkl']}".strip() for x in a['matches'][1:])
                    w.writerow([f"{a['two_theta']:.4f}", f"{a['height']:.4f}", f"{a['fwhm']:.4f}",
                                f'{size:.2f}' if np.isfinite(size) else '', best.get('label', ''), best.get('hkl', ''),
                                f"{best['delta']:.4f}" if best else '', others])
            self.app.status(T('已保存数据：{path}').format(path=path), 'ok')
        except Exception as e:
            messagebox.showerror(T('导出失败'), str(e))

    def broadening(self):
        """Scherrer settings when broadening is on, else None."""
        if not getattr(self, 'broaden_on', None) or not self.broaden_on.get():
            return None
        return dict(size_nm=self.phase_number('size', positive=True), k=self.phase_number('k', positive=True))

    # ---------- drawing ----------
    def settings(self):
        settings = {}
        for key, var in self.vars.items():
            value = var.get()
            if key == 'height_cm' and not value.strip():
                settings[key] = None
            elif key == 'dpi':
                settings[key] = int(value)
            elif key in NUMERIC:
                settings[key] = float(value)
            else:
                settings[key] = value
        return settings

    def collect(self, settings):
        samples, cards = [], []
        scherrer = self.broadening()
        for layer in self.layers:
            if not layer['visible']:
                continue
            if layer['kind'] == 'card':
                row = read_card(layer['path'], wavelength=settings['wavelength'], upper_angle=settings['xmax'])
                if scherrer and row.get('kind') != 'profile':
                    row['peaks'] = broaden(row['peaks'], scherrer['size_nm'], settings['wavelength'], scherrer['k'],
                                           settings['xmin'], settings['xmax'])
                    row['kind'] = 'profile'
                    row['scherrer_broadening'] = dict(scherrer, wavelength=settings['wavelength'], profile='pseudo-Voigt eta=0.5')
            else:
                row = read_sample(layer['path'])
                row['color'] = layer['color'] or settings['color']
            row['label'] = layer['label']
            row['label_style'] = dict(layer['style'])
            row['order_id'] = layer['id']
            (samples if layer['kind'] == 'sample' else cards).append(row)
        settings['display_order'] = [l['id'] for l in self.layers if l['visible']]
        return samples, cards

    def schedule(self):
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(600, lambda: self.draw(quiet=True))

    def draw(self, quiet=False):
        self._pending = None
        if hasattr(self, 'tree'):
            self.refresh_tree()
        try:
            settings = self.settings()
            samples, cards = self.collect(settings)
            fig = make_figure(samples, cards, **settings)
            self.mark_match(fig, settings)
        except Exception as e:
            if quiet:
                self.app.status(T('预览未更新：{error}').format(error=e), 'error', timeout=12000)
            else:
                messagebox.showerror('XRD', str(e))
            return False
        if self.canvas is not None:
            self.canvas.get_tk_widget().destroy()
            for child in self.toolbar_holder.winfo_children():
                child.destroy()
        self.canvas = figure_canvas(fig, self.stage)
        PlotToolbar(self.toolbar_holder, self.theme, self.canvas).pack(fill='x')
        inches = tuple(float(v) for v in fig.get_size_inches())
        self.rendered = dict(samples=samples, cards=cards, settings=settings, inches=inches)
        self.fit()
        if samples or cards:
            self.empty.place_forget()
        else:
            self.empty.place(relx=.5, rely=.5, anchor='center')
        self.info.set(T('{samples} 条图谱 · {cards} 张卡片  ·  导出尺寸 {w} × {h} cm  ·  PNG {dpi} DPI').format(
            samples=len(samples), cards=len(cards), w=f'{inches[0] * 2.54:.2f}', h=f'{inches[1] * 2.54:.2f}', dpi=settings['dpi']))
        return True

    def schedule_fit(self):
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self.fit)

    def fit(self):
        self._fit_job = None
        if self.canvas is not None and self.rendered:
            fit_figure(self.canvas, self.stage, *self.rendered['inches'], theme=self.theme)

    # ---------- export ----------
    def export_image(self):
        if not self.draw():
            return
        path = filedialog.asksaveasfilename(title=T('导出图片'), defaultextension='.png',
                                            filetypes=[(T('PNG 图片'), '*.png'), (T('PDF 矢量图'), '*.pdf'), (T('SVG 矢量图'), '*.svg')])
        if not path:
            return
        fmt = Path(path).suffix.lower().lstrip('.') or 'png'
        if fmt not in ('png', 'pdf', 'svg'):
            messagebox.showerror(T('导出'), T('请使用 .png、.pdf 或 .svg 扩展名。'))
            return
        try:
            r = self.rendered
            fig = make_figure(r['samples'], r['cards'], **r['settings'])
            fig.savefig(path, format=fmt, dpi=r['settings']['dpi'])
            self.app.status(T('已导出 {fmt}：{path}').format(fmt=fmt.upper(), path=path), 'ok')
        except Exception as e:
            messagebox.showerror(T('导出'), str(e))

    def export_processed(self):
        if not self.draw():
            return
        destination = filedialog.askdirectory(title=T('选择保存处理数据的位置'))
        if not destination:
            return
        name = ask_string(self, self.theme, T('数据文件夹'), T('新建文件夹名称：'), 'xrd_processed_data')
        if not name:
            return
        if name in ('.', '..') or '/' in name or '\\' in name:
            messagebox.showerror(T('导出'), T('文件夹名称不能包含路径分隔符。'))
            return
        data_dir = Path(destination) / name
        if data_dir.exists() and not messagebox.askyesno(T('覆盖'), T('该文件夹已存在，覆盖同名数据文件？')):
            return
        try:
            r = self.rendered
            export_data(data_dir, r['samples'], r['cards'], r['settings'])
            self.app.status(T('处理数据已保存到 {folder}').format(folder=data_dir), 'ok')
        except Exception as e:
            messagebox.showerror(T('导出'), str(e))

    # ---------- shell hooks ----------
    def shortcut_open(self):
        self.add('sample')

    def shortcut_save(self):
        self.export_image()

    def shortcut_refresh(self):
        self.draw()

    def close(self):
        for job in (self._pending, self._fit_job):
            if job is not None:
                self.after_cancel(job)
        self._pending = self._fit_job = None
