"""Choose export folder, filename, formats and (for batch) images."""
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog

from ..widgets import Dialog, hint
from . import tem_scalebar_distribution as core
from ..i18n import T


class ExportDialog(Dialog):
    def __init__(self, parent, theme, kind, directory, formats, default_name, files=None):
        title = T('批量导出 TEM 图') if files else (T('导出 TEM 图') if kind == 'tem' else T('导出粒径分布图'))
        super().__init__(parent, theme, title, ok_text=T('导出'))
        self.kind, self.default_name, self.files = kind, default_name, files
        b = self.body
        ttk.Label(b, text=T('保存位置'), style='Section.TLabel').pack(anchor='w')
        row = ttk.Frame(b)
        row.pack(fill='x', pady=(6, 14))
        self.folder = tk.StringVar(self, value=str(directory))
        ttk.Entry(row, textvariable=self.folder, width=52).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text=T('浏览…'), command=self.browse).pack(side='left', padx=(6, 0))
        ttk.Label(b, text=T('文件名前缀') if files else T('文件名'), style='Section.TLabel').pack(anchor='w')
        self.name = tk.StringVar(self)
        entry = ttk.Entry(b, textvariable=self.name)
        entry.pack(fill='x', pady=(6, 0))
        hint(b, T('留空则使用原文件名；填写前缀会生成 前缀_001、前缀_002 …') if files else
             T('留空则使用默认名称：{name}（扩展名可省略）').format(name=default_name), pady=(4, 14))
        ttk.Label(b, text=T('格式'), style='Section.TLabel').pack(anchor='w')
        options = ([('png', 'PNG'), ('tif', 'TIFF'), ('pdf', T('PDF（图表为矢量）')), ('svg', T('SVG（图表为矢量）'))] if kind == 'tem'
                   else [('png', 'PNG'), ('pdf', T('PDF（矢量）')), ('svg', T('SVG（矢量）'))])
        grid = ttk.Frame(b)
        grid.pack(fill='x', pady=(4, 8))
        self.format_vars = {}
        for i, (value, label) in enumerate(options):
            variable = tk.BooleanVar(self, value=value in formats)
            self.format_vars[value] = variable
            ttk.Checkbutton(grid, text=label, variable=variable).grid(row=i // 2, column=i % 2, sticky='w', padx=(0, 24))
        self.report = tk.BooleanVar(self, value=False)
        ttk.Checkbutton(b, text=T('同时保存参数 / 统计报告 (JSON)'), variable=self.report).pack(anchor='w', pady=(2, 0))
        if files:
            ttk.Label(b, text=T('要导出的图片（可按 Ctrl / Shift 多选）'), style='Section.TLabel').pack(anchor='w', pady=(14, 6))
            row = ttk.Frame(b)
            row.pack(fill='both', expand=True)
            self.selection = tk.Listbox(row, selectmode='extended', exportselection=False, height=min(8, len(files)), width=60)
            theme.paint_listbox(self.selection)
            bar = ttk.Scrollbar(row, orient='vertical', command=self.selection.yview)
            self.selection.configure(yscrollcommand=bar.set)
            bar.pack(side='right', fill='y')
            self.selection.pack(side='left', fill='both', expand=True)
            for file in files:
                self.selection.insert('end', Path(file).name)
            self.selection.selection_set(0, 'end')
        self.after(50, entry.focus_set)

    def browse(self):
        result = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if result:
            self.folder.set(result)

    def apply(self):
        extensions = {'.png', '.tif', '.tiff', '.pdf', '.svg'} if self.kind == 'tem' else {'.png', '.pdf', '.svg'}
        core.export_stem('.', self.name.get(), self.default_name, extensions)  # raises ValueError on bad names
        if not self.folder.get().strip():
            raise ValueError(T('请选择保存位置。'))
        if not any(v.get() for v in self.format_vars.values()):
            raise ValueError(T('请至少选择一种格式。'))
        if self.files and not self.selection.curselection():
            raise ValueError(T('请至少选择一张图片。'))
        self.result = dict(directory=Path(self.folder.get().strip()).expanduser(), name=self.name.get().strip(),
                           formats=[k for k, v in self.format_vars.items() if v.get()], report=self.report.get(),
                           files=[self.files[i] for i in self.selection.curselection()] if self.files else None)
