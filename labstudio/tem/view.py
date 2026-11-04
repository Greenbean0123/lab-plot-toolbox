"""TEM images: recalibrate and redraw scale bars, add an inset, plot particle-size distributions."""
import copy
import csv
import json
import tempfile
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import numpy as np
import tifffile
from PIL import Image, ImageTk

from . import tem_scalebar_distribution as core
from .export_dialog import ExportDialog
from ..widgets import ToolLayout, Segmented, PreviewSwitch, Form, section, hint, button_row
from ..i18n import T

YES_NO = [('Yes', '是'), ('No', '否')]
CORNERS = [('bottom-right', '右下'), ('bottom-left', '左下'), ('top-right', '右上'), ('top-left', '左上')]
WHITE_BLACK = [('white', '白色'), ('black', '黑色')]
DIST_KEYS = ('column', 'bins', 'bin_width', 'y_mode', 'dpi')


class TEMView(ttk.Frame):
    TITLE = 'TEM 标尺与粒径'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app, self.theme = app, app.theme
        self.temp = tempfile.TemporaryDirectory(prefix='labstudio_tem_')
        self.files, self.settings, self.vars = [], {}, {}
        self.current = None
        self.loading = False
        self.pending = None
        self.preview_image = None
        self.report = None
        self.view = 'tem'
        self.photo = None
        self.transform = (1, 0, 0)
        self.drag = None
        self.csv_path = None
        self.preview_zoom = None  # None = fit to window; otherwise screen pixels per image pixel.
        self.preview_pan = (0, 0)
        self.render_pending = None
        self.render_key = None
        self.image_item = None
        self.viewport_context = None
        self.display_source = None
        self.vars['match_original'] = tk.StringVar(self, value='true')
        layout = ToolLayout(self, self.theme, T('TEM 标尺与粒径分布'), T('重新校准并绘制清晰标尺，添加局部放大图，绘制粒径直方图与高斯拟合'))
        layout.action(T('批量导出…'), self.export_all)
        layout.action(T('导出粒径分布…'), self.export_distribution)
        layout.action(T('导出 TEM 图…'), self.export_tem, accent=True)
        self.pages = Segmented(layout.controls, self.theme)
        self.pages.pack(fill='both', expand=True)
        self.build_data(self.pages.add(T('数据校准')))
        self.build_bar(self.pages.add(T('新标尺')))
        self.build_size(self.pages.add(T('粒径分布')))
        self.build_inset(self.pages.add(T('局部放大')))
        self.build_preview(layout.preview)
        self.theme.subscribe(self.paint)
        self.paint()

    # ---------- settings widgets ----------
    def v(self, key, default=''):
        var = tk.StringVar(self, value=str(default))
        self.vars[key] = var
        var.trace_add('write', lambda *_: self.changed(key))
        return var

    def build_data(self, page):
        body = section(page, T('图片'), first=True)
        button_row(body, [(T('添加 TIFF…'), self.add_files), (T('移除'), self.remove_file), (T('示例'), self.sample)])
        holder = ttk.Frame(body, style='ListField.TFrame', padding=self.theme.d(6))
        holder.pack(fill='x', pady=(self.theme.d(2), 0))
        self.listbox = tk.Listbox(holder, height=6, exportselection=False)
        self.listbox.pack(fill='x')
        self.listbox.bind('<<ListboxSelect>>', self.select_file)
        hint(body, T('每张图片单独保存校准与标尺设置；原始文件不会被修改。'))
        body = section(page, T('原标尺校准'))
        form = Form(body, self.theme)
        form.choice(T('校准方式'), self.v('cal_mode', 'Auto measure bar'),
                    [('Auto measure bar', T('自动识别原标尺')), ('Manual bar width', T('手动输入标尺像素')), ('nm per pixel', T('直接输入 nm/像素'))])
        form.entry(T('原标尺数值'), self.v('original_bar_nm'))
        form.combo(T('原标尺单位'), self.v('original_unit', 'nm'), ['nm', 'µm'])
        form.entry(T('原标尺宽度 (px)'), self.v('original_bar_px'))
        form.entry(T('nm / 像素'), self.v('nm_per_pixel'))
        button_row(body, [(T('重新识别原标尺'), self.redetect_scale), (T('两点测量原标尺'), lambda: self.tool('measure'))], pady=(8, 0))
        hint(body, T('自动模式读取原图中的 nm/µm 文字并测量标尺长度；TIFF 内的物理元数据优先。请核对识别出的数值。'))
        body = section(page, T('擦除原标注'))
        form = Form(body, self.theme)
        form.entry(T('擦除框'), self.v('erase_box'))
        form.choice(T('填充背景'), self.v('background', 'Auto'), [('Auto', T('自动')), ('Black', T('黑色')), ('White', T('白色'))])
        form.choice(T('去除底部黑条'), self.v('remove_footer', 'Yes'), YES_NO)
        form.choice(T('原标注位置'), self.v('original_corner', 'bottom-right'), CORNERS)
        ttk.Button(body, text=T('在图上框选擦除区域'), command=lambda: self.tool('erase')).pack(fill='x', pady=(8, 0))
        hint(body, T('擦除框格式 x0,y0,x1,y1（原图像素）。留空时：先尝试识别独立的底部信息栏，否则擦除角落 420 × 130 px 区域。其他排版请手动框选。'))
        body = section(page, T('项目'))
        button_row(body, [(T('保存项目…'), self.save_project), (T('打开项目…'), self.open_project)])
        hint(body, T('项目文件 (.json) 记录所有图片路径与设置，兼容原 TEM Graph tool 的项目文件。'))

    def build_bar(self, page):
        body = section(page, T('新标尺'), first=True)
        form = Form(body, self.theme)
        form.entry(T('长度 (nm)'), self.v('bar_nm', ''))
        form.entry(T('自动目标宽度 (px)'), self.v('target_width', '500'))
        form.choice(T('颜色'), self.v('color', 'white'), WHITE_BLACK)
        form.entry(T('线粗 (px)'), self.v('thickness', '36'))
        form.entry(T('字号 (px)'), self.v('font_size', '128'))
        form.choice(T('文字位置'), self.v('label_position', 'above'), [('above', T('线上方')), ('below', T('线下方'))])
        form.entry(T('文字与线间距 (px)'), self.v('gap', '12'))
        hint(body, T('长度留空 = 与原标尺相同。修改长度时文字会同步更新，保证标定正确。尺寸均以原图像素为单位。'))
        body = section(page, T('位置'))
        form = Form(body, self.theme)
        form.choice(T('角落预设'), self.v('corner', 'bottom-right'), CORNERS)
        form.entry(T('边距 (px)'), self.v('margin', '110'))
        form.entry(T('自定义 X (px)'), self.v('bar_x'))
        form.entry(T('自定义 Y (px)'), self.v('bar_y'))
        ttk.Button(body, text=T('恢复角落预设位置'), command=self.reset_position).pack(fill='x', pady=(8, 0))
        hint(body, T('也可以直接在右侧预览中拖动标尺。'))

    def build_size(self, page):
        body = section(page, T('直径数据'), first=True)
        ttk.Button(body, text=T('打开直径 CSV…'), command=self.open_csv).pack(fill='x')
        self.csv_label = ttk.Label(body, text=T('尚未选择 CSV'), style='Muted.TLabel')
        self.csv_label.pack(anchor='w', pady=(6, 4))
        form = Form(body, self.theme)
        self.column_widget = form.combo(T('直径列 (nm)'), self.v('column', 'diameter_nm'), ['diameter_nm'])
        form.entry(T('分箱数'), self.v('bins', '12'))
        form.entry(T('分箱宽度 (nm)'), self.v('bin_width'))
        form.choice(T('Y 轴'), self.v('y_mode', 'count'), [('count', T('计数')), ('frequency', T('频率')), ('density', T('概率密度'))])
        form.entry(T('导出 DPI (≥300)'), self.v('dpi', '300'))
        self.pdf = tk.BooleanVar(self, value=True)
        form.check(T('导出时同时生成 PDF'), self.pdf)
        ttk.Button(body, text=T('预览粒径分布'), command=self.refresh_distribution).pack(fill='x', pady=(8, 0))
        hint(body, T('高斯最大似然拟合；标准差 ddof=0。本工具不做颗粒分割，需要提供已测量的直径数据。'))
        body = section(page, T('叠加到 TEM 图上'))
        form = Form(body, self.theme)
        form.choice(T('叠加直方图'), self.v('overlay_enabled', 'No'), YES_NO)
        form.entry(T('图宽 (图像 px)'), self.v('overlay_width', '800'))
        form.pair(T('位置 X, Y'), self.v('overlay_x', ''), self.v('overlay_y', ''), ',')
        form.choice(T('背景'), self.v('overlay_background', 'plot only'),
                    [('plot only', T('仅绘图区白底')), ('white', T('整体白底')), ('transparent', T('透明'))])
        form.color(T('柱颜色'), self.v('overlay_bar_color', '#ff5555'), T('直方图颜色'))
        form.entry(T('柱不透明度 (0–1)'), self.v('overlay_bar_alpha', '.65'))
        form.color(T('曲线颜色'), self.v('overlay_curve_color', '#203bff'), T('高斯曲线颜色'))
        form.entry(T('曲线不透明度'), self.v('overlay_curve_alpha', '1'))
        form.entry(T('背景不透明度'), self.v('overlay_background_alpha', '1'))
        form.entry(T('轴标题字号 (pt)'), self.v('overlay_label_size', '10'))
        form.entry(T('刻度字号 (pt)'), self.v('overlay_tick_size', '10'))
        form.entry(T('统计文字字号 (pt)'), self.v('overlay_stats_size', '10'))
        hint(body, T('使用上方选择的 CSV。在预览中拖动图表移动、拖动右下角蓝色手柄缩放。'
                   '导出 SVG / PDF 时图表保持矢量，TEM 图像仍为位图。位置留空 = 自动。'))

    def build_inset(self, page):
        body = section(page, T('局部放大图'), first=True)
        ttk.Button(body, text=T('在图上框选放大区域'), style='Accent.TButton', command=lambda: self.tool('inset')).pack(fill='x', pady=(0, 8))
        form = Form(body, self.theme)
        form.choice(T('显示放大图'), self.v('inset_enabled', 'No'), YES_NO)
        form.entry(T('源区域'), self.v('inset_roi'))
        form.entry(T('放大图宽度 (px)'), self.v('inset_width', '500'))
        form.pair(T('位置 X, Y'), self.v('inset_x'), self.v('inset_y'), ',')
        hint(body, T('源区域格式 x0,y0,x1,y1。框选区域后，在预览中拖动放大图移动，拖动右下角黄色手柄缩放（保持比例）。尺寸均为原图像素。'))
        body = section(page, T('边框与连接线'))
        form = Form(body, self.theme)
        form.choice(T('边框 / 标尺颜色'), self.v('inset_color', 'white'), WHITE_BLACK)
        form.entry(T('边框粗细 (px)'), self.v('inset_border', '4'))
        form.choice(T('连接线'), self.v('inset_connector', 'Yes'), YES_NO)
        form.entry(T('连接线粗细 (px)'), self.v('inset_connector_width', '4'))
        form.choice(T('连接线颜色'), self.v('inset_connector_color', 'white'), WHITE_BLACK)
        body = section(page, T('放大图标尺'))
        form = Form(body, self.theme)
        form.choice(T('显示标尺'), self.v('inset_show_scalebar', 'Yes'), YES_NO)
        form.entry(T('标尺长度 (nm)'), self.v('inset_bar_nm'))
        hint(body, T('长度留空 = 自动选择；放大倍数变化时标尺会自动换算。'))

    def build_preview(self, parent):
        px = self.theme.px
        bar = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(14, 10, 14, 6))
        bar.pack(fill='x')
        zoom = ttk.Frame(bar, style='Preview.TFrame')
        zoom.pack(side='right')
        self.switch = PreviewSwitch(bar, [('tem', T('TEM 预览')), ('original', T('原图')), ('distribution', T('粒径分布'))], self.switch_view,
                                    self.theme)
        self.switch.pack(side='left')
        ttk.Button(zoom, image=self.theme.icon('minus'), style='PreviewTool.TButton', command=lambda: self.zoom_preview(0.8)).pack(side='left')
        self.zoom_hint = tk.StringVar(self, value=T('适应'))
        ttk.Label(zoom, textvariable=self.zoom_hint, width=9, anchor='center', style='Preview.TLabel').pack(side='left')
        ttk.Button(zoom, image=self.theme.icon('plus'), style='PreviewTool.TButton', command=lambda: self.zoom_preview(1.25)).pack(side='left')
        ttk.Button(zoom, text=' ' + T('适应窗口'), image=self.theme.icon('fit'), compound='left', style='PreviewTool.TButton',
                   command=self.fit_preview).pack(side='left', padx=(self.theme.d(6), 0))
        self.file_hint = tk.StringVar(self, value='')
        ttk.Label(parent, textvariable=self.file_hint, style='Preview.TLabel', padding=self.theme.pad(16, 0, 16, 6)).pack(fill='x')
        bottom = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(16, 6, 16, 10))
        bottom.pack(side='bottom', fill='x')
        self.info = tk.StringVar(self, value='')
        info = ttk.Label(bottom, textvariable=self.info, style='Preview.TLabel', wraplength=600, justify='left')
        info.pack(fill='x')
        info.bind('<Configure>', lambda e: info.configure(wraplength=max(200, e.width)))
        self.progress_row = ttk.Frame(bottom, style='Preview.TFrame')
        self.overlay_progress = tk.DoubleVar(self, value=0)
        self.overlay_progress_text = tk.StringVar(self, value='')
        ttk.Label(self.progress_row, textvariable=self.overlay_progress_text, style='Preview.TLabel').pack(side='left', padx=(0, 10))
        ttk.Progressbar(self.progress_row, variable=self.overlay_progress, maximum=100).pack(side='left', fill='x', expand=True)
        self.canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0, cursor='hand2')
        self.canvas.pack(fill='both', expand=True, padx=px(14), pady=(0, px(4)))
        self.canvas.wheel_handler = self.wheel_zoom
        self.canvas.bind('<Configure>', self.schedule_preview)
        for button in ('2', '3'):
            self.canvas.bind(f'<ButtonPress-{button}>', self.pan_start)
            self.canvas.bind(f'<B{button}-Motion>', self.pan_motion)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.mode = tk.StringVar(self, value='move')
        self.switch.set('tem')

    def paint(self):
        self.theme.paint_listbox(self.listbox)
        self.canvas.configure(background=self.theme.c['image_bg'])
        self.render_key = None
        self.draw_preview()

    def say(self, text, kind='info'):
        self.app.status(text, kind)

    # ---------- state ----------
    def image_state(self):
        return {k: v.get() for k, v in self.vars.items() if k not in DIST_KEYS}

    @staticmethod
    def default_image_settings():
        return dict(cal_mode='Auto measure bar', original_bar_nm='', original_unit='nm', match_original='true', original_bar_px='', nm_per_pixel='',
                    erase_box='', background='Auto', remove_footer='Yes', original_corner='bottom-right', bar_nm='', target_width='500',
                    color='white', thickness='36', font_size='128', corner='bottom-right', margin='110',
                    label_position='above', gap='12', bar_x='', bar_y='',
                    inset_enabled='No', inset_roi='', inset_width='500', inset_x='', inset_y='',
                    inset_color='white', inset_border='4', inset_connector='Yes', inset_bar_nm='',
                    inset_connector_width='4', inset_connector_color='white', inset_show_scalebar='Yes',
                    overlay_enabled='No', overlay_width='800', overlay_x='', overlay_y='', overlay_background='plot only',
                    overlay_bar_color='#ff5555', overlay_curve_color='#203bff', overlay_bar_alpha='.65', overlay_curve_alpha='1', overlay_background_alpha='1',
                    overlay_label_size='10', overlay_tick_size='10', overlay_stats_size='10')

    def store(self):
        if self.current:
            self.settings[str(self.current)] = self.image_state()

    def changed(self, key=None, *_):
        if self.loading:
            return
        if key == 'bar_nm':
            self.vars['match_original'].set('true' if not self.vars['bar_nm'].get().strip() else 'false')
        elif key in ('original_unit', 'original_bar_nm') and self.vars['match_original'].get() == 'true':
            self.loading = True
            self.vars['bar_nm'].set('')
            self.loading = False
        self.store()
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(450, self.auto_refresh)

    def auto_refresh(self):
        self.pending = None
        if self.view == 'distribution':
            self.refresh_distribution(quiet=True)
        elif self.current and self.mode.get() == 'move':
            self.refresh_tem(quiet=True)

    # ---------- files ----------
    def add_files(self):
        names = filedialog.askopenfilenames(title=T('添加 TEM 图片'), filetypes=[(T('TIFF 图片'), '*.tif *.tiff')])
        self.load_files(names)

    def load_files(self, names):
        for name in names:
            path = Path(name).resolve()
            if path not in self.files:
                self.files.append(path)
                self.settings.setdefault(str(path), self.default_image_settings())
                self.listbox.insert('end', path.name)
        if names:
            self.listbox.selection_clear(0, 'end')
            self.listbox.selection_set(len(self.files) - 1)
            self.select_file()

    def select_file(self, _=None):
        indices = self.listbox.curselection()
        if not indices:
            return
        self.store()
        self.current = self.files[indices[0]]
        self.loading = True
        for key, value in dict(self.default_image_settings(), **self.settings.get(str(self.current), {})).items():
            if key in self.vars:
                self.vars[key].set(value)
        self.loading = False
        self.mode.set('move')
        self.refresh_tem(quiet=True)

    def remove_file(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        removed = self.files.pop(sel[0])
        self.settings.pop(str(removed), None)
        self.listbox.delete(sel[0])
        self.current = None
        if self.files:
            self.listbox.selection_set(0)
            self.select_file()
        else:
            self.preview_image = None
            self.report = None
            self.canvas.delete('all')
            self.image_item = None
            self.render_key = None
            self.draw_preview()

    def sample(self):
        # Synthetic demonstration only; never bundle research data.
        folder = Path(self.temp.name)
        image = folder / 'synthetic_demo.tif'
        csv_file = folder / 'synthetic_diameters.csv'
        rng = np.random.default_rng(21)
        array = np.clip(rng.normal(175, 15, (1091, 1024)), 0, 255).astype(np.uint8)
        yy, xx = np.mgrid[0:1024, 0:1024]
        for cx, cy, r in rng.uniform([60, 60, 22], [964, 964, 46], (38, 3)):  # a few dark "particles"
            array[:1024][(xx - cx) ** 2 + (yy - cy) ** 2 < r * r] = np.uint8(rng.uniform(55, 95))
        array[1024:] = 0
        array[1050:1058, 500:751] = 255
        tifffile.imwrite(image, array)
        values = np.clip(rng.normal(65, 12, 103), 1, None)
        csv_file.write_text('diameter_nm\n' + '\n'.join(f'{v:.4f}' for v in values) + '\n', encoding='utf-8')
        self.load_files([str(image)])
        self.vars['original_bar_nm'].set('200')
        self.vars['erase_box'].set('0,1024,1024,1091')
        self.set_csv(csv_file)
        self.vars['column'].set('diameter_nm')
        self.refresh_tem(quiet=True)
        self.say(T('已载入合成示例图片与直径数据（非实验数据，仅存于临时文件夹）'), 'ok')

    # ---------- core arguments (unchanged logic) ----------
    def get_args(self, state=None, out=None):
        a = core.create_parser().parse_args([])
        s = state or self.image_state()
        for key in ('original_bar_nm', 'original_bar_px', 'nm_per_pixel', 'bar_nm', 'target_width'):
            value = s.get(key, '').strip()
            setattr(a, key, core.positive(value) if value else None)
        a.target_width = a.target_width or 500
        a.match_original_bar = s.get('match_original', 'true') == 'true' or a.bar_nm is None
        if a.match_original_bar:
            a.bar_nm = None
        a.ocr_default_unit = 'um' if s.get('original_unit') == 'µm' else 'nm'
        if a.original_bar_nm is not None and a.ocr_default_unit == 'um':
            a.original_bar_nm *= 1000
        mode = s['cal_mode']
        a.measure_original_bar = mode == 'Auto measure bar'
        if mode == 'nm per pixel':
            a.original_bar_nm = a.original_bar_px = None
        else:
            a.nm_per_pixel = None
        if mode == 'Auto measure bar':
            a.original_bar_px = None
        for key in ('thickness', 'font_size', 'margin', 'gap'):
            value = int(s[key])
            if value < (1 if key in ('thickness', 'font_size') else 0):
                raise ValueError(f'{key} is too small')
            setattr(a, key, value)
        for key in ('bar_x', 'bar_y'):
            setattr(a, key, int(s[key]) if s.get(key, '').strip() else None)
        for key in ('color', 'corner', 'original_corner', 'label_position'):
            setattr(a, key, s[key])
        a.background = {'Auto': None, 'Black': 0, 'White': 255}[s['background']]
        a.keep_original_footer = s.get('remove_footer', 'Yes') == 'No'
        a.erase_box = core.rectangle(s['erase_box']) if s['erase_box'].strip() else None
        a.inset_roi = core.rectangle(s['inset_roi']) if s.get('inset_enabled') == 'Yes' and s.get('inset_roi', '').strip() else None
        a.inset_width = int(s.get('inset_width', '500'))
        a.inset_x = int(s['inset_x']) if s.get('inset_x', '').strip() else None
        a.inset_y = int(s['inset_y']) if s.get('inset_y', '').strip() else None
        a.inset_color = s.get('inset_color', 'white')
        a.inset_border = int(s.get('inset_border', '4'))
        a.inset_no_connector = s.get('inset_connector', 'Yes') == 'No'
        a.inset_connector_width = int(s.get('inset_connector_width', '4'))
        a.inset_connector_color = s.get('inset_connector_color', 'white')
        a.inset_bar_nm = core.positive(s['inset_bar_nm']) if s.get('inset_bar_nm', '').strip() else None
        a.inset_no_scalebar = s.get('inset_show_scalebar', 'Yes') == 'No'
        a.overlay_distribution = s.get('overlay_enabled', 'No') == 'Yes'
        a.overlay_width = int(s.get('overlay_width', '800'))
        a.overlay_x = int(s['overlay_x']) if s.get('overlay_x', '').strip() else None
        a.overlay_y = int(s['overlay_y']) if s.get('overlay_y', '').strip() else None
        a.overlay_background = s.get('overlay_background', 'plot only')
        self.chart_style(a, s)
        if a.overlay_distribution:
            a.progress = self.update_overlay_progress
            dist = self.dist_args(out or self.temp.name)
            for key in ('csv', 'column', 'bins', 'bin_width', 'y_mode'):
                setattr(a, key, getattr(dist, key))
        a.output_dir = Path(out or self.temp.name)
        a.protected_inputs = list(self.files)
        a.output_dir.mkdir(parents=True, exist_ok=True)
        return a

    def dist_args(self, out):
        if not self.csv_path:
            raise ValueError(T('请先打开直径 CSV'))
        a = core.create_parser().parse_args([])
        a.csv = self.csv_path
        a.column = self.vars['column'].get()
        a.bins = int(self.vars['bins'].get())
        a.bin_width = core.positive(self.vars['bin_width'].get()) if self.vars['bin_width'].get().strip() else None
        a.y_mode = self.vars['y_mode'].get()
        a.dpi = int(self.vars['dpi'].get())
        a.pdf = self.pdf.get()
        a.output_dir = Path(out)
        self.chart_style(a, self.image_state())
        if a.bins < 1 or a.dpi < 300:
            raise ValueError(T('分箱数需为正数，DPI 至少 300'))
        return a

    @staticmethod
    def chart_style(args, state):
        from matplotlib.colors import is_color_like
        for key, default in (('overlay_bar_color', '#ff5555'), ('overlay_curve_color', '#203bff')):
            value = state.get(key, default)
            if not is_color_like(value):
                raise ValueError(T('直方图 / 曲线颜色无效'))
            setattr(args, key, value)
        for key in ('overlay_label_size', 'overlay_tick_size', 'overlay_stats_size'):
            value = float(state.get(key, '10'))
            if not 0 < value <= 100:
                raise ValueError(T('叠加图字号需在 0–100 pt 之间'))
            setattr(args, key, value)
        for key, default in (('overlay_bar_alpha', '.65'), ('overlay_curve_alpha', '1'), ('overlay_background_alpha', '1')):
            value = float(state.get(key, default))
            if not 0 <= value <= 1:
                raise ValueError(T('不透明度需在 0–1 之间'))
            setattr(args, key, value)

    def fail(self, e, quiet=False):
        if 0 < self.overlay_progress.get() < 100:
            self.overlay_progress_text.set(T('叠加图已停止'))
        self.say(str(e), 'error')
        if not quiet:
            messagebox.showerror(T('请检查设置'), str(e))

    # ---------- previews ----------
    def switch_view(self, view):
        {'tem': self.refresh_tem, 'original': self.show_original, 'distribution': self.refresh_distribution}[view]()

    def refresh_tem(self, quiet=False):
        if not self.current:
            if not quiet:
                self.say(T('请先添加 TIFF 图片'), 'error')
            return
        self.view = 'tem'
        self.mode.set('move')
        try:
            if self.vars['cal_mode'].get() == 'Auto measure bar' and not self.vars['original_bar_nm'].get().strip():
                self.app.status(T('正在识别原标尺文字并测量长度…'), 'busy', timeout=None)
            self.preview_image, self.report = core.redraw(self.current, self.get_args(), render_only=True)
            r = self.report
            self.loading = True
            try:
                if r.get('original_bar_nm') is not None:
                    factor = 1000 if self.vars['original_unit'].get() == 'µm' else 1
                    self.vars['original_bar_nm'].set(f"{r['original_bar_nm'] / factor:g}")
                if r.get('measured_original_bar_pixels') is not None:
                    self.vars['original_bar_px'].set(str(r['measured_original_bar_pixels']))
                self.vars['nm_per_pixel'].set(f"{r['nm_per_pixel']:.12g}")
                if self.vars['match_original'].get() == 'true' or not self.vars['bar_nm'].get().strip():
                    self.vars['bar_nm'].set(f"{r['bar_nm']:g}")
            finally:
                self.loading = False
            self.store()
            original = f"{r.get('original_bar_nm') or T('元数据')} nm / {r['measured_original_bar_pixels'] or T('手动或元数据')} px"
            self.info.set(T('新标尺 {bar} nm = {px} px   ·   {scale} nm/px   ·   原标尺 {original}').format(bar=f"{r['bar_nm']:g}", px=r['bar_pixels'], scale=f"{r['nm_per_pixel']:.8g}", original=original))
            if 'selected unit' in (r.get('recognized_scale_text') or ''):
                self.say(T('识别到数字但单位不清晰，暂按所选单位 ') + self.vars['original_unit'].get() + T(' 处理，请核对。'), 'error')
            else:
                self.say(T('预览已更新 · 原文件未改动'), 'ok')
            self.draw_preview()
            if self.report.get('distribution_overlay'):
                self.update_overlay_progress(100, T('叠加图预览完成'))
        except Exception as e:
            self.report = None
            try:
                self.show_original()
            except Exception:
                pass
            self.fail(e, quiet)

    def redetect_scale(self):
        if not self.current:
            return
        self.loading = True
        try:
            self.vars['cal_mode'].set('Auto measure bar')
            if self.vars['match_original'].get() == 'true':
                self.vars['bar_nm'].set('')
            for key in ('original_bar_nm', 'original_bar_px', 'nm_per_pixel'):
                self.vars[key].set('')
        finally:
            self.loading = False
        self.refresh_tem(quiet=False)

    def show_original(self):
        if not self.current:
            return
        self.view = 'original'
        with Image.open(self.current) as im:
            self.preview_image = im.copy()
        self.info.set(T('原图 · 可在「数据校准」页用两点测量原标尺，或框选擦除区域。'))
        self.draw_preview()

    def refresh_distribution(self, quiet=False):
        try:
            args = self.dist_args(self.temp.name)
            args.pdf = False  # Preview only needs PNG; PDF is written on export.
            args.distribution_formats = ['png']
            r = core.distribution(args)
            with Image.open(Path(self.temp.name) / 'size_distribution.png') as im:
                self.preview_image = im.copy()
            self.view = 'distribution'
            self.info.set(T('平均值 ± 标准差 = {mean} ± {std} nm   ·   N = {n}   ·   高斯 MLE (ddof=0)').format(mean=f"{r['mean_nm']:.4f}", std=f"{r['std_nm']:.4f}", n=r['N']))
            self.say(T('粒径分布预览已更新'), 'ok')
            self.draw_preview()
        except Exception as e:
            self.fail(e, quiet)

    def draw_preview(self, quick=False):
        self.switch.set(self.view)
        if self.view == 'distribution':
            self.file_hint.set(T('直径数据：') + (self.csv_path.name if self.csv_path else T('无')))
        else:
            text = T('当前图片：') + (self.current.name if self.current else T('无'))
            if self.view == 'tem' and self.report and self.report.get('distribution_overlay'):
                text += T('   ·   叠加图数据：') + Path(self.report['distribution_overlay']['csv']).name
            self.file_hint.set(text)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.preview_image is None:
            self.canvas.delete('all')
            self.image_item = None
            if cw > 2:
                self.canvas.create_text(cw / 2, ch / 2, fill=self.theme.c['faint'], font=self.theme.fonts['h2'], justify='center',
                                        text=T('添加 TIFF 图片开始\n或点击「示例」体验全部功能'))
            return
        w, h = self.preview_image.size
        if cw < 2 or ch < 2:
            return
        context = (str(self.current) if self.view != 'distribution' else str(self.csv_path), self.view, w, h)
        if context != self.viewport_context:
            self.preview_zoom, self.preview_pan = None, (0, 0)
            self.viewport_context = context
        scale = self.preview_zoom or min(max(cw - 24, 1) / w, max(ch - 24, 1) / h)
        ox, oy = (cw - w * scale) / 2 + self.preview_pan[0], (ch - h * scale) / 2 + self.preview_pan[1]
        self.transform = (scale, ox, oy)
        self.zoom_hint.set((T('适应 ') if self.preview_zoom is None else '') + f'{scale * 100:.0f}%')
        key = (id(self.preview_image), cw, ch, scale, ox, oy, self.view, self.theme.mode)
        if key == self.render_key and not quick:
            return
        # Render only the visible viewport so zooming never allocates a giant image.
        if self.display_source is not self.preview_image:
            self.display_source = self.preview_image
            self.display_rgb = self.preview_image.convert('RGB')
            self.display_thumbnail = self.display_rgb.copy()
            self.display_thumbnail.thumbnail((768, 768), Image.Resampling.BILINEAR)
        source, sampling = self.display_rgb, 1
        if quick:
            source = self.display_thumbnail
            sampling = source.width / self.preview_image.width
        viewport = source.transform((cw, ch), Image.Transform.AFFINE,
                                    (sampling / scale, 0, -ox * sampling / scale, 0, sampling / scale, -oy * sampling / scale),
                                    resample=Image.Resampling.BILINEAR if quick else Image.Resampling.BICUBIC,
                                    fillcolor=self.theme.c['image_bg'])
        photo = ImageTk.PhotoImage(viewport)
        if self.image_item is None:
            self.canvas.delete('all')
            self.image_item = self.canvas.create_image(0, 0, anchor='nw', image=photo)
        else:
            self.canvas.itemconfigure(self.image_item, image=photo)
        self.photo = photo
        self.render_key = None if quick else key
        self.canvas.delete('inset_handle')
        if self.view == 'tem' and self.report and self.report.get('distribution_overlay'):
            x0, y0, x1, y1 = self.report['distribution_overlay']['box']
            self.handle(ox + x1 * scale, oy + y1 * scale, '#62c9ee')
        if self.view == 'tem' and self.report and self.report.get('inset'):
            x0, y0, x1, y1 = self.report['inset']['box']
            self.handle(ox + x1 * scale, oy + y1 * scale, '#ffca3a')

    def handle(self, x, y, color):
        s = self.theme.px(10)
        self.canvas.create_rectangle(x - s, y - s, x, y, fill=color, outline='#202833', tags='inset_handle')

    def schedule_preview(self, _=None):
        if self.render_pending is not None:
            self.after_cancel(self.render_pending)
        self.draw_preview(quick=True)
        self.render_pending = self.after(180, self.flush_preview)

    def flush_preview(self):
        self.render_pending = None
        self.draw_preview()

    def fit_preview(self):
        self.preview_zoom, self.preview_pan = None, (0, 0)
        self.draw_preview()

    def zoom_preview(self, factor, anchor=None):
        if self.preview_image is None:
            return
        scale, ox, oy = self.transform
        new = max(0.02, min(8.0, scale * factor))
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        ax, ay = anchor if anchor else (cw / 2, ch / 2)
        # Keep the image point under the anchor fixed.
        nx, ny = ax - (ax - ox) * new / scale, ay - (ay - oy) * new / scale
        w, h = self.preview_image.size
        self.preview_zoom = new
        self.preview_pan = (nx - (cw - w * new) / 2, ny - (ch - h * new) / 2)
        self.draw_preview()

    def wheel_zoom(self, event):
        delta = 1 if getattr(event, 'num', None) == 4 else -1 if getattr(event, 'num', None) == 5 else event.delta
        self.zoom_preview(1.15 if delta > 0 else 1 / 1.15, (event.x_root - self.canvas.winfo_rootx(), event.y_root - self.canvas.winfo_rooty()))
        return 'break'

    def pan_start(self, event):
        self.pan_anchor = (event.x, event.y, self.preview_pan)

    def pan_motion(self, event):
        if self.preview_image is None or not hasattr(self, 'pan_anchor'):
            return
        x, y, offset = self.pan_anchor
        if self.preview_zoom is None:
            self.preview_zoom = self.transform[0]
        self.preview_pan = (offset[0] + event.x - x, offset[1] + event.y - y)
        self.draw_preview(quick=True)
        self.schedule_render()

    def schedule_render(self):
        if self.render_pending is not None:
            self.after_cancel(self.render_pending)
        self.render_pending = self.after(120, self.flush_preview)

    # ---------- canvas interaction (unchanged logic) ----------
    def point(self, event):
        scale, ox, oy = self.transform
        w, h = self.preview_image.size
        return (max(0, min(w - 1, round((event.x - ox) / scale))), max(0, min(h - 1, round((event.y - oy) / scale))))

    def tool(self, mode):
        if not self.current:
            messagebox.showinfo(T('添加图片'), T('请先添加 TIFF 图片。'))
            return
        if mode == 'inset':
            self.refresh_tem()
        else:
            self.show_original()
        self.mode.set(mode)
        self.app.status({'inset': T('在预览中拖出一个矩形，作为局部放大的源区域。'),
                         'measure': T('在原图上从原标尺一端拖到另一端（端点中心到中心）。'),
                         'erase': T('拖出一个矩形，完整覆盖原标尺及其文字。')}[mode], 'busy', timeout=None)

    def press(self, event):
        if self.preview_image is None or self.view == 'distribution':
            return
        point = self.point(event)
        if self.mode.get() in ('measure', 'erase', 'inset'):
            self.drag = (point, (event.x, event.y))
        elif self.view == 'tem' and self.report:
            for key, move, resize in (('distribution_overlay', 'move_chart', 'resize_chart'), ('inset', 'move_inset', 'resize_inset')):
                box = self.report.get(key)
                if box:
                    x0, y0, x1, y1 = box['box']
                    if x0 <= point[0] < x1 and y0 <= point[1] < y1:
                        tolerance = self.theme.px(16) / self.transform[0]
                        self.mode.set(resize if x1 - point[0] < tolerance and y1 - point[1] < tolerance else move)
                        self.drag = (point, (x0, y0))
                        return
            x0, y0, x1, y1 = self.report['annotation_box']
            if x0 - 15 <= point[0] <= x1 + 15 and y0 - 15 <= point[1] <= y1 + 15:
                self.drag = (point, (x0, y0))

    def motion(self, event):
        if self.drag is None:
            return
        self.canvas.delete('selection')
        if self.mode.get() in ('measure', 'erase', 'inset'):
            x, y = self.drag[1]
            if self.mode.get() == 'measure':
                self.canvas.create_line(x, y, event.x, event.y, fill='#ffca3a', width=3, tags='selection')
            else:
                self.canvas.create_rectangle(x, y, event.x, event.y, outline='#ffca3a', width=2, tags='selection')
        else:
            point = self.point(event)
            x = self.drag[1][0] + point[0] - self.drag[0][0]
            y = self.drag[1][1] + point[1] - self.drag[0][1]
            scale, ox, oy = self.transform
            box = (self.report['distribution_overlay']['box'] if self.mode.get() in ('move_chart', 'resize_chart') else
                   self.report['inset']['box'] if self.mode.get() in ('move_inset', 'resize_inset') else self.report['annotation_box'])
            if self.mode.get() in ('resize_inset', 'resize_chart'):
                x, y = box[:2]
                width = max(80, point[0] - x)
                box = [x, y, x + width, y + round(width * (box[3] - box[1]) / (box[2] - box[0]))]
            self.canvas.create_rectangle(ox + x * scale, oy + y * scale, ox + (x + box[2] - box[0]) * scale,
                                         oy + (y + box[3] - box[1]) * scale, outline='#ffca3a', width=2, tags='selection')

    def release(self, event):
        if self.drag is None:
            return
        point = self.point(event)
        start, other = self.drag
        self.drag = None
        self.canvas.delete('selection')
        self.loading = True
        try:
            mode = self.mode.get()
            if mode == 'measure':
                distance = ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** .5
                if distance < 2:
                    raise ValueError(T('测量距离太短，请沿原标尺拖动'))
                self.vars['original_bar_px'].set(f'{distance:.4f}')
                self.vars['cal_mode'].set('Manual bar width')
            elif mode in ('erase', 'inset'):
                x0, x1 = sorted([start[0], point[0]])
                y0, y1 = sorted([start[1], point[1]])
                if x1 == x0 or y1 == y0:
                    raise ValueError(T('请拖出一个有面积的矩形'))
                key = 'inset_roi' if mode == 'inset' else 'erase_box'
                self.vars[key].set(f'{x0},{y0},{x1 + 1},{y1 + 1}')
                if key == 'inset_roi':
                    self.vars['inset_enabled'].set('Yes')
                    w, h = self.preview_image.size
                    ratio = (y1 - y0 + 1) / (x1 - x0 + 1)
                    width = min(500, w - 40, int((h - 40) / ratio))
                    self.vars['inset_width'].set(str(width))
                    self.vars['inset_x'].set(str(max(0, w - width - 20)))
                    self.vars['inset_y'].set('20')
            elif mode in ('move_chart', 'resize_chart'):
                box = self.report['distribution_overlay']['box']
                w, h = self.preview_image.size
                width, height = box[2] - box[0], box[3] - box[1]
                x, y = other
                if mode == 'resize_chart':
                    width = max(160, min(point[0] - x, w - x, int((h - y) / .75)))
                    self.vars['overlay_width'].set(str(width))
                else:
                    x = max(0, min(w - width, x + point[0] - start[0]))
                    y = max(0, min(h - height, y + point[1] - start[1]))
                self.vars['overlay_x'].set(str(x))
                self.vars['overlay_y'].set(str(y))
            elif mode in ('move_inset', 'resize_inset'):
                box = self.report['inset']['box']
                w, h = self.preview_image.size
                width, height = box[2] - box[0], box[3] - box[1]
                x, y = other
                if mode == 'resize_inset':
                    ratio = height / width
                    width = max(80, min(point[0] - x, w - x, int((h - y) / ratio)))
                    self.vars['inset_width'].set(str(width))
                else:
                    x = max(0, min(w - width, x + point[0] - start[0]))
                    y = max(0, min(h - height, y + point[1] - start[1]))
                self.vars['inset_x'].set(str(x))
                self.vars['inset_y'].set(str(y))
            else:
                x = other[0] + point[0] - start[0]
                y = other[1] + point[1] - start[1]
                box = self.report['annotation_box']
                w, h = self.preview_image.size
                self.vars['bar_x'].set(str(max(0, min(w - (box[2] - box[0]), x))))
                self.vars['bar_y'].set(str(max(0, min(h - (box[3] - box[1]), y))))
        except Exception as e:
            self.fail(e)
        finally:
            self.loading = False
        self.mode.set('move')
        self.store()
        self.refresh_tem(quiet=True)

    def reset_position(self):
        self.vars['bar_x'].set('')
        self.vars['bar_y'].set('')
        self.refresh_tem(quiet=True)

    # ---------- distribution data ----------
    def open_csv(self):
        name = filedialog.askopenfilename(title=T('打开直径 CSV'), filetypes=[('CSV', '*.csv')])
        if name:
            try:
                self.set_csv(Path(name))
                self.refresh_distribution(quiet=True)
            except Exception as e:
                self.fail(e)

    def set_csv(self, path):
        with path.open(encoding='utf-8-sig', newline='') as f:
            fields = csv.DictReader(f).fieldnames or []
        columns = [c for c in fields if c.strip()]
        if not columns:
            raise ValueError(T('CSV 中没有带名称的列'))
        self.csv_path = path
        self.csv_label.config(text=path.name)
        self.column_widget.configure(values=columns)
        if self.vars['column'].get() not in columns:
            self.vars['column'].set('diameter_nm' if 'diameter_nm' in columns else columns[0])

    def update_overlay_progress(self, value, message):
        self.overlay_progress.set(value)
        self.overlay_progress_text.set(T('叠加图 {value}%').format(value=f'{value:.0f}'))
        if value < 100:
            if not self.progress_row.winfo_ismapped():
                self.progress_row.pack(fill='x', pady=(6, 0))
        else:
            self.after(900, lambda: self.progress_row.winfo_exists() and self.progress_row.pack_forget())
        self.update_idletasks()

    # ---------- export (unchanged logic, new dialog) ----------
    def choose_export(self, kind, batch=False):
        source = self.current if kind == 'tem' else self.csv_path
        directory = getattr(self, 'last_export_directory', None) or (source.parent if source else Path.home())
        formats = getattr(self, 'tem_export_formats', ['png', 'tif']) if kind == 'tem' else ['png'] + (['pdf'] if self.pdf.get() else [])
        default = self.current.stem + '_scalebar' if kind == 'tem' and self.current else 'size_distribution'
        options = ExportDialog(self, self.theme, kind, directory, formats, default, files=list(self.files) if batch else None).show()
        if options is not None:
            self.last_export_directory = options['directory']
            if kind == 'tem':
                self.tem_export_formats = options['formats']
            else:
                self.pdf.set('pdf' in options['formats'])
        return options

    def done(self, files):
        self.say(T('已导出 ') + ', '.join(Path(f).name for f in files), 'ok')
        messagebox.showinfo(T('导出完成'), T('已保存：\n') + '\n'.join(files))

    def export_tem(self):
        if not self.current:
            messagebox.showinfo(T('添加图片'), T('请先添加 TIFF 图片。'))
            return
        options = self.choose_export('tem')
        if options is None:
            return
        try:
            args = self.get_args(out=options['directory'])
            args.tem_name, args.tem_formats = options['name'], options['formats']
            r = core.redraw(self.current, args)
            if options['report']:
                Path(r['output_stem'] + '_report.json').write_text(json.dumps(r, indent=2), encoding='utf-8')
            self.done(r['output_files'])
        except Exception as e:
            self.fail(e)

    def export_distribution(self):
        if not self.csv_path:
            messagebox.showinfo(T('打开 CSV'), T('请先在「粒径分布」页打开直径 CSV。'))
            return
        options = self.choose_export('distribution')
        if options is None:
            return
        try:
            options['directory'].mkdir(parents=True, exist_ok=True)
            args = self.dist_args(options['directory'])
            args.distribution_name, args.distribution_formats = options['name'], options['formats']
            r = core.distribution(args)
            if options['report']:
                Path(r['output_stem'] + '_report.json').write_text(json.dumps(r, indent=2), encoding='utf-8')
            self.done(r['output_files'])
        except Exception as e:
            self.fail(e)

    def export_all(self):
        self.store()
        if not self.files:
            messagebox.showinfo(T('添加图片'), T('请先添加 TIFF 图片。'))
            return
        options = self.choose_export('tem', batch=True)
        if options is None:
            return
        try:
            files = [Path(path).resolve() for path in options['files']]
            counts = Counter(p.stem.casefold() for p in files)
            prefix = core.export_stem('.', options['name'], 'batch', {'.png', '.tif', '.tiff', '.pdf', '.svg'}).name if options['name'] else None
            jobs = []  # Validate every image before writing any output.
            for index, path in enumerate(files, 1):
                args = self.get_args(self.settings[str(path)], options['directory'])
                args.tem_formats = options['formats']
                args.tem_name = f'{prefix}_{index:03}' if prefix else f'{path.stem}_scalebar_{index:03}' if counts[path.stem.casefold()] > 1 else None
                jobs.append((path, args))
            targets = []
            for path, args in jobs:
                _, report = core.redraw(path, copy.copy(args), render_only=True)
                targets.extend(str(Path(p).resolve()).casefold() for p in report['output_files'])
            if len(targets) != len(set(targets)):
                raise ValueError(T('输出文件名重复，请换一个批量前缀。'))
            reports = [core.redraw(path, args) for path, args in jobs]
            if options['report']:
                report_path = options['directory'] / ((prefix + '_report.json') if prefix else 'tem_export_report.json')
                report_path.write_text(json.dumps(reports, indent=2), encoding='utf-8')
            self.say(T('已导出 {n} 张 TEM 图到 {folder}').format(n=len(reports), folder=options['directory']), 'ok')
            messagebox.showinfo(T('导出完成'), T('{n} 张 TEM 图已导出到：\n{folder}').format(n=len(reports), folder=options['directory']))
        except Exception as e:
            self.fail(e)

    # ---------- projects (same JSON format as TEM Graph tool) ----------
    def save_project(self):
        self.store()
        name = filedialog.asksaveasfilename(title=T('保存项目'), defaultextension='.json', filetypes=[(T('TEM 项目'), '*.json')])
        if name:
            try:
                obj = {'version': 1, 'images': self.settings, 'csv': str(self.csv_path) if self.csv_path else None,
                       'distribution': {k: self.vars[k].get() for k in DIST_KEYS}}
                Path(name).write_text(json.dumps(obj, indent=2), encoding='utf-8')
                self.say(T('项目已保存'), 'ok')
            except Exception as e:
                self.fail(e)

    def open_project(self):
        name = filedialog.askopenfilename(title=T('打开项目'), filetypes=[(T('TEM 项目'), '*.json')])
        if not name:
            return
        try:
            obj = json.loads(Path(name).read_text(encoding='utf-8-sig'))
            missing = [p for p in obj['images'] if not Path(p).exists()]
            if missing:
                raise ValueError(T('找不到项目中的图片：') + missing[0])
            self.current = None
            self.files = []
            self.listbox.delete(0, 'end')
            self.settings = obj['images']
            self.load_files(list(self.settings))
            if obj.get('csv'):
                self.set_csv(Path(obj['csv']))
            for k, v in obj.get('distribution', {}).items():
                if k in self.vars:
                    self.vars[k].set(v)
            self.say(T('项目已打开'), 'ok')
        except Exception as e:
            self.fail(e)

    # ---------- shell hooks ----------
    def shortcut_open(self):
        self.add_files()

    def shortcut_save(self):
        self.export_distribution() if self.view == 'distribution' else self.export_tem()

    def shortcut_refresh(self):
        self.switch_view(self.view)

    def close(self):
        for job in (self.pending, self.render_pending):
            if job:
                self.after_cancel(job)
        self.temp.cleanup()
