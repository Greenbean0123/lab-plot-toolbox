"""NMR: stacked Bruker spectra with automatic referencing, coloured assignments and draggable labels."""
import copy
import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.transforms import ScaledTranslation

from . import core
from ..widgets import ToolLayout, Segmented, Form, PreviewSwitch, section, hint, button_row, ask_string, figure_canvas, fit_figure
from ..i18n import T, get_language

KIND_ALL = '全部类型'
COLOR_NAMES = list(core.PALETTE)
NORM_TEXT = [('max', '每段按自身最大值'), ('common', '所有谱线共用一个比例'), ('peak', '按指定峰（如内标）')]
PRESETS = [  # name, SMILES, highlighted atoms, atom notes
    ('Terminal alkene', 'C=C[*:1]', '0:orange, 1:blue', ''),
    ('Internal alkene', '[*:1]C=C[*:2]', '1:pink, 2:pink', ''),
    ('Hacac, enol', 'CC(O)=CC(C)=O', '0:sky, 3:sky, 5:sky', '3:a, 0:c, 5:c'),
    ('Hacac, keto', 'CC(=O)CC(C)=O', '0:sky, 3:sky, 5:sky', '3:b, 0:d, 5:d'),
    ('R2Se2', '[*:1][Se][Se][*:1]', '1:blue, 2:blue', ''),
    ('R2Se', '[*:1][Se][*:1]', '1:sky', ''),
    ('Selenoester', '[*:2]C(=O)[Se][*:1]', '3:pink', ''),
]
MODES = [('move', '移动'), ('region', '上色'), ('peak', '字母'), ('integ', '积分'), ('ref', '定标'), ('break', '断轴')]
MODE_HINTS = {'move': '拖动标签或谱线；Ctrl+滚轮缩放谱线；双击标签改文字',
              'region': '在谱上横向拖动，添加上色区域',
              'peak': '点击一个峰，添加字母标注',
              'ref': '点击一个峰，输入它应有的化学位移',
              'integ': '在谱上横向拖动，添加积分区间',
              'break': '在谱上横向拖过不需要的区域，把它切掉（断轴）'}


class NMRView(ttk.Frame):
    TITLE = 'NMR 谱图'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app, self.theme = app, app.theme
        self.doc = core.default_doc()
        self.items, self.meta = [], None
        self.history, self.future = [], []
        self._saved = json.dumps(self.doc)
        self._pending = self._fit_job = None
        self._loading = False
        self.mode, self.drag, self.selected_label = 'move', None, None
        layout = ToolLayout(self, self.theme, T('NMR 谱图'),
                            T('读取 Bruker 数据并自动定标（1H、13C、DEPT、77Se 等）；在预览中拖动标签和谱线，导出出版级叠图'))
        layout.action(T('打开配置…'), self.load_doc)
        layout.action(T('保存配置…'), self.save_doc)
        layout.action(T('导出图片…'), self.export_figure, accent=True)
        self.layout = layout
        pages = self.pages = Segmented(layout.controls, self.theme)
        pages.pack(fill='both', expand=True)
        self.build_data(pages.add(T('数据')))
        self.build_marks(pages.add(T('标注')))
        self.build_integration(pages.add(T('积分')))
        self.build_figure(pages.add(T('图形')))
        self.build_preview(layout.preview)
        self.theme.subscribe(self.paint)
        folder = self.app.prefs.get('nmr_folder', '')
        if folder and os.path.isdir(folder):
            self.folder.set(folder)
            self.after(300, self.scan)
        self.update_plot()

    # ================================================================== data page
    def build_data(self, page):
        body = section(page, T('数据文件夹'), first=True)
        self.folder = tk.StringVar(self, value='')
        ttk.Entry(body, textvariable=self.folder).pack(fill='x')
        button_row(body, [(T('浏览…'), self.browse), (T('扫描'), self.scan)], pady=(8, 0))
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(self.theme.d(8), self.theme.d(4)))
        self.kind = tk.StringVar(self, value=T(KIND_ALL))
        self.kind_box = ttk.Combobox(row, textvariable=self.kind, state='readonly', width=11)
        self.kind_box.pack(side='left')
        self.kind_box.bind('<<ComboboxSelected>>', lambda e: self.fill_items())
        self.search = tk.StringVar(self, value='')
        self.search.trace_add('write', lambda *_: self.fill_items())
        ttk.Entry(row, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=(self.theme.d(6), 0))
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        self.items_tree = ttk.Treeview(frame, columns=('kind', 'expno', 'solvent'), show=('tree', 'headings'), height=9,
                                       selectmode='extended')
        self.items_tree.heading('#0', text=T('样品'), anchor='w')
        self.items_tree.column('#0', width=self.theme.px(150), stretch=True)
        for col, text, width in (('kind', T('类型'), 70), ('expno', '#', 36), ('solvent', T('溶剂'), 66)):
            self.items_tree.heading(col, text=text, anchor='w')
            self.items_tree.column(col, width=self.theme.px(width), stretch=False)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=scroll.set)
        self.items_tree.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        self.items_tree.bind('<Double-1>', lambda e: self.add_selected())
        button_row(body, [(T('加入图中'), self.add_selected)], pady=(8, 0))
        self.scan_info = hint(body, T('选择包含 Bruker 实验文件夹的目录（如 …/nmr），点击「扫描」。双击实验加入图中。'))

        body = section(page, T('图中的谱线'))
        frame = ttk.Frame(body)
        frame.pack(fill='x')
        self.tree = ttk.Treeview(frame, columns=('show', 'name', 'kind'), show=('tree', 'headings'), height=6, selectmode='browse')
        self.tree.column('#0', width=self.theme.px(38), stretch=False, anchor='center')
        for col, text, width, stretch in (('show', T('显示'), 46, False), ('name', T('名称'), 150, True), ('kind', T('类型'), 70, False)):
            self.tree.heading(col, text=text, anchor='w')
            self.tree.column(col, width=self.theme.px(width), stretch=stretch)
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.load_trace_form())
        self.tree.bind('<ButtonRelease-1>', self.tree_click)
        self.tree.bind('<Double-1>', lambda e: self.rename_trace() if self.tree.identify_column(e.x) == '#2' else None)
        button_row(body, [(T('上移'), lambda: self.move_trace(-1)), (T('下移'), lambda: self.move_trace(1)),
                          (T('移除'), self.remove_trace)], pady=(8, 0))
        form = Form(body, self.theme, label_width=10)
        self.tv = {k: tk.StringVar(self) for k in ('shift', 'scale', 'dy', 'color')}
        self.tflip = tk.BooleanVar(self, value=False)
        box = ttk.Frame(form)
        box.columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=self.tv['shift'], width=8).grid(row=0, column=0, sticky='ew')
        ttk.Button(box, text=T('自动'), command=self.auto_shift).grid(row=0, column=1, padx=(self.theme.d(6), 0))
        form.widget(T('ppm 平移'), box)
        form.entry(T('纵向缩放'), self.tv['scale'])
        form.entry(T('上下偏移'), self.tv['dy'])
        form.widget(T('颜色'), ttk.Combobox(form, textvariable=self.tv['color'], values=COLOR_NAMES, width=10))
        form.check(T('翻转相位'), self.tflip, self.trace_form_changed)
        for v in self.tv.values():
            v.trace_add('write', lambda *_: self.trace_form_changed())
        self.ref_note = hint(body, '')

    def browse(self):
        folder = filedialog.askdirectory(title=T('选择 NMR 数据文件夹'), initialdir=self.folder.get() or None)
        if folder:
            self.folder.set(folder)
            self.scan()

    def scan(self):
        folder = self.folder.get().strip()
        if not os.path.isdir(folder):
            self.app.status(T('文件夹不存在'), 'error')
            return
        self.app.status(T('正在扫描…'), 'busy', timeout=None)
        self.config(cursor='watch')
        self.update_idletasks()
        try:
            self.items = core.scan(folder)
        finally:
            self.config(cursor='')
        self.app.prefs.set('nmr_folder', folder)
        kinds = sorted({i['kind'] for i in self.items}, key=lambda k: (k == 'offline', k))
        self.kind_box.configure(values=[T(KIND_ALL)] + [T('云端') if k == 'offline' else k for k in kinds])
        offline = sum(i['kind'] == 'offline' for i in self.items)
        msg = T('找到 {n} 个一维实验').format(n=len(self.items) - offline)
        if offline:
            msg += ' · ' + T('{n} 个在云端未下载（OneDrive：右键文件夹 → 始终保留在此设备上）').format(n=offline)
        self.scan_info.configure(text=msg)
        self.fill_items()
        self.app.status(msg, 'ok')

    def fill_items(self):
        tree = self.items_tree
        tree.delete(*tree.get_children())
        kind, q = self.kind.get(), self.search.get().strip().lower()
        kind = KIND_ALL if kind == T(KIND_ALL) else ('offline' if kind == T('云端') else kind)
        groups = {}
        for i, it in enumerate(self.items):
            if (kind == KIND_ALL and it['kind'] != 'offline') or it['kind'] == kind:
                if not q or q in (it['sample'] + ' ' + it['title']).lower():
                    groups.setdefault(it['sample'], []).append(i)
        open_all = bool(q) or len(groups) < 12
        for sample, idx in groups.items():
            parent = tree.insert('', 'end', text=sample, open=open_all, values=('', '', ''))
            for i in idx:
                it = self.items[i]
                tree.insert(parent, 'end', iid=f'i{i}', text=it['title'] or it['pulprog'],
                            values=(T('云端') if it['kind'] == 'offline' else it['kind'], it['expno'], it['solvent']))

    def add_selected(self):
        chosen = [s for s in self.items_tree.selection() if s.startswith('i')]
        if not chosen:  # a sample row: add all of its spectra of the shown type
            for s in self.items_tree.selection():
                chosen += [c for c in self.items_tree.get_children(s)]
        added, errors = 0, []
        for s in chosen:
            it = self.items[int(s[1:])]
            if it['kind'] == 'offline':
                errors.append(f"{it['sample']}/{it['expno']}: " + T('文件在云端，尚未下载'))
                continue
            try:
                self.add_trace(it['path'])
                added += 1
            except Exception as exc:
                errors.append(f"{it['sample']}/{it['expno']}: {exc}")
        if errors:
            messagebox.showerror(T('部分谱图无法读取'), '\n'.join(errors[:12]))
        if added:
            self.app.status(T('已加入 {n} 条谱线').format(n=added), 'ok')

    def add_trace(self, path):
        tr = core.new_trace(path)
        nucs = {t['nuc'] for t in self.doc['traces']}
        if nucs and tr['nuc'] not in nucs:
            self.app.status(T('注意：不同 nucleus 的谱不能共用 ppm 轴'), 'error', timeout=12000)
        self.doc['traces'].append(tr)
        if not self.doc['segments']:
            self.doc['segments'] = [tr['range']]
        if tr['kind'] == 'DEPT-135' and not self.doc['signed']:
            self.doc['signed'], self.doc['step'] = True, max(self.doc['step'], 2.1)
            self.sync_figure_form()
        self.commit()
        self.refresh_traces(len(self.doc['traces']) - 1)

    # ---------- trace list ----------
    def refresh_traces(self, select=None):
        if select is None:
            sel = self.tree.selection()
            select = self.tree.index(sel[0]) if sel else None
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self.doc['traces']):
            self.tree.insert('', 'end', iid=str(i), image=self.theme.color_chip(core.hexc(t['color']), faded=not t['visible']),
                             values=('●' if t['visible'] else '○', t['label'], t['kind']))
        if select is not None and 0 <= select < len(self.doc['traces']):
            self.tree.selection_set(str(select))
        self.load_trace_form()
        self.refresh_integration()

    def current_trace(self):
        sel = self.tree.selection()
        return self.doc['traces'][int(sel[0])] if sel and int(sel[0]) < len(self.doc['traces']) else None

    def load_trace_form(self):
        t = self.current_trace()
        self._loading = True
        try:
            for k in ('shift', 'scale', 'dy'):
                self.tv[k].set('' if t is None else f'{t[k]:g}')
            self.tv['color'].set('' if t is None else t['color'])
            self.tflip.set(bool(t and t['flip']))
            self.ref_note.configure(text='' if t is None else T('定标：') + t['ref_note'] +
                                    (('  ·  ' + T('已自动翻转相位')) if t['auto_flip'] else ''))
        finally:
            self._loading = False

    def trace_form_changed(self):
        t = self.current_trace()
        if self._loading or t is None:
            return
        try:
            for k in ('shift', 'scale', 'dy'):
                if self.tv[k].get().strip():
                    t[k] = float(self.tv[k].get())
        except ValueError:
            return
        if self.tv['color'].get().strip():
            t['color'] = self.tv['color'].get().strip()
        t['flip'] = bool(self.tflip.get())
        self.commit()
        i = self.doc['traces'].index(t)
        self.tree.item(str(i), image=self.theme.color_chip(core.hexc(t['color']), faded=not t['visible']))

    def tree_click(self, event):
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == '#1':
            t = self.doc['traces'][int(row)]
            t['visible'] = not t['visible']
            self.refresh_traces(int(row))
            self.commit()

    def move_trace(self, delta):
        t = self.current_trace()
        if t is None:
            return
        tr = self.doc['traces']
        i = tr.index(t)
        j = i + delta
        if 0 <= j < len(tr):
            tr[i], tr[j] = tr[j], tr[i]
            self.refresh_traces(j)
            self.commit()

    def remove_trace(self):
        t = self.current_trace()
        if t is not None:
            i = self.doc['traces'].index(t)
            del self.doc['traces'][i]
            self.refresh_traces(min(i, len(self.doc['traces']) - 1) if self.doc['traces'] else None)
            self.commit()

    def rename_trace(self):
        t = self.current_trace()
        if t is None:
            return
        name = ask_string(self, self.theme, T('重命名'), T('谱线名称（图中显示）：'), t['label'])
        if name:
            t['label'] = name
            self.refresh_traces()
            self.commit()

    def auto_shift(self):
        t = self.current_trace()
        if t is not None:
            t['shift'] = t['auto_shift']
            self.load_trace_form()
            self.commit()

    # ================================================================== marks page
    def build_marks(self, page):
        body = section(page, T('上色区域'), first=True)
        self.atree = self.small_tree(body, (('label', T('标签'), 110), ('range', T('范围 (ppm)'), 110), ('color', T('颜色'), 70)))
        self.atree.bind('<<TreeviewSelect>>', lambda e: self.load_mark_form('a'))
        button_row(body, [(T('添加'), self.add_assignment), (T('删除'), lambda: self.delete_mark('a'))], pady=(8, 0))
        form = Form(body, self.theme, label_width=10)
        self.av = {k: tk.StringVar(self) for k in ('label', 'hi', 'lo', 'color', 'sigma')}
        self.ab = {k: tk.BooleanVar(self, value=True) for k in ('header', 'show_range')}
        form.entry(T('标签'), self.av['label'])
        form.pair(T('范围 (ppm)'), self.av['hi'], self.av['lo'])
        form.widget(T('颜色'), ttk.Combobox(form, textvariable=self.av['color'], values=COLOR_NAMES, width=10))
        form.entry(T('阈值 (σ)'), self.av['sigma'])
        form.check(T('顶部显示标签'), self.ab['header'], lambda: self.mark_form_changed('a'))
        form.check(T('标签下显示范围'), self.ab['show_range'], lambda: self.mark_form_changed('a'))
        for v in self.av.values():
            v.trace_add('write', lambda *_: self.mark_form_changed('a'))
        hint(body, T('只有超过 σ × noise 的峰会被上色。也可以在预览上方选「上色区域」，直接在谱上拖动添加。下标写法：CH$_2$。'))

        body = section(page, T('字母峰'))
        self.ptree = self.small_tree(body, (('label', T('字母'), 60), ('ppm', 'ppm', 90), ('color', T('颜色'), 70)))
        self.ptree.bind('<<TreeviewSelect>>', lambda e: self.load_mark_form('p'))
        button_row(body, [(T('添加'), self.add_peak), (T('删除'), lambda: self.delete_mark('p'))], pady=(8, 0))
        form = Form(body, self.theme, label_width=10)
        self.pv = {k: tk.StringVar(self) for k in ('label', 'ppm', 'color', 'sigma')}
        form.entry(T('字母'), self.pv['label'])
        form.entry('ppm', self.pv['ppm'])
        form.widget(T('颜色'), ttk.Combobox(form, textvariable=self.pv['color'], values=COLOR_NAMES, width=10))
        form.entry(T('阈值 (σ)'), self.pv['sigma'])
        for v in self.pv.values():
            v.trace_add('write', lambda *_: self.mark_form_changed('p'))
        hint(body, T('峰太弱或没有分辨开时不显示字母，避免误标。'))

        body = section(page, T('结构式'))
        if not core.rdkit_available():
            hint(body, T('结构式需要 RDKit（pip install rdkit）。安装后重新打开工具即可使用。'), pady=(0, 0))
            self.stree = None
            return
        self.stree = self.small_tree(body, (('title', T('标题'), 140), ('smiles', 'SMILES', 140)))
        self.stree.bind('<<TreeviewSelect>>', lambda e: self.load_mark_form('s'))
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(self.theme.d(8), 0))
        self.preset = tk.StringVar(self, value=PRESETS[0][0])
        ttk.Combobox(row, textvariable=self.preset, values=[p[0] for p in PRESETS], state='readonly', width=16).pack(side='left')
        ttk.Button(row, text=T('添加'), command=self.add_structure).pack(side='left', padx=(self.theme.d(6), 0))
        ttk.Button(row, text=T('删除'), command=lambda: self.delete_mark('s')).pack(side='left', padx=(self.theme.d(6), 0))
        form = Form(body, self.theme, label_width=10)
        self.sv = {k: tk.StringVar(self) for k in ('title', 'smiles', 'highlight', 'notes')}
        form.entry(T('标题'), self.sv['title'])
        form.entry('SMILES', self.sv['smiles'])
        form.entry(T('高亮原子'), self.sv['highlight'])
        form.entry(T('原子注释'), self.sv['notes'])
        for v in self.sv.values():
            v.trace_add('write', lambda *_: self.mark_form_changed('s'))
        hint(body, T("原子序号 = SMILES 中原子的顺序（从 0 开始）；[*:1]、[*:2] 画成 R、R'。高亮写法：0:orange, 1:blue。"))

    def small_tree(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='x')
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', height=4, selectmode='browse')
        for col, text, width in columns:
            tree.heading(col, text=text, anchor='w')
            tree.column(col, width=self.theme.px(width), stretch=True)
        tree.pack(fill='x')
        return tree

    def mark_list(self, which):
        return {'a': self.doc['assignments'], 'p': self.doc['peaks'], 's': self.doc['structures']}[which]

    def mark_tree(self, which):
        return {'a': self.atree, 'p': self.ptree, 's': self.stree}[which]

    def refresh_marks(self, which, select=None):
        tree = self.mark_tree(which)
        if tree is None:
            return
        if select is None:
            sel = tree.selection()
            select = int(sel[0]) if sel else None
        tree.delete(*tree.get_children())
        for i, m in enumerate(self.mark_list(which)):
            if which == 'a':
                values = (m['label'], f"{m['hi']:g} – {m['lo']:g}", m['color'])
            elif which == 'p':
                values = (m['label'], f"{m['ppm']:g}", m['color'])
            else:
                values = (m.get('title', ''), m['smiles'])
            tree.insert('', 'end', iid=str(i), values=values)
        if select is not None and 0 <= select < len(self.mark_list(which)):
            tree.selection_set(str(select))
        self.load_mark_form(which)

    def current_mark(self, which):
        tree = self.mark_tree(which)
        sel = tree.selection() if tree is not None else ()
        lst = self.mark_list(which)
        return lst[int(sel[0])] if sel and int(sel[0]) < len(lst) else None

    def load_mark_form(self, which):
        m = self.current_mark(which)
        self._loading = True
        try:
            if which == 'a':
                for k in self.av:
                    self.av[k].set('' if m is None else (f'{m[k]:g}' if isinstance(m[k], float) else str(m[k])))
                for k in self.ab:
                    self.ab[k].set(True if m is None else m.get(k, True) is not False)
            elif which == 'p':
                for k in self.pv:
                    self.pv[k].set('' if m is None else (f'{m[k]:g}' if isinstance(m[k], float) else str(m[k])))
            elif self.stree is not None:
                for k in self.sv:
                    self.sv[k].set('' if m is None else str(m.get(k, '')))
        finally:
            self._loading = False

    def mark_form_changed(self, which):
        m = self.current_mark(which)
        if self._loading or m is None:
            return
        try:
            if which == 'a':
                m.update(label=self.av['label'].get(), hi=float(self.av['hi'].get()), lo=float(self.av['lo'].get()),
                         color=self.av['color'].get().strip() or 'blue', sigma=float(self.av['sigma'].get() or 6),
                         header=bool(self.ab['header'].get()), show_range=bool(self.ab['show_range'].get()))
            elif which == 'p':
                m.update(label=self.pv['label'].get(), ppm=float(self.pv['ppm'].get()),
                         color=self.pv['color'].get().strip() or 'sky', sigma=float(self.pv['sigma'].get() or 10))
            else:
                m.update({k: v.get() for k, v in self.sv.items()})
        except ValueError:
            return
        tree, i = self.mark_tree(which), self.mark_list(which).index(m)
        self.refresh_row(which, tree, i, m)
        self.commit()

    def refresh_row(self, which, tree, i, m):
        if which == 'a':
            tree.item(str(i), values=(m['label'], f"{m['hi']:g} – {m['lo']:g}", m['color']))
        elif which == 'p':
            tree.item(str(i), values=(m['label'], f"{m['ppm']:g}", m['color']))
        else:
            tree.item(str(i), values=(m.get('title', ''), m['smiles']))

    def next_color(self):
        used = {a['color'] for a in self.doc['assignments']}
        return next((c for c in core.MARK_ORDER if c not in used), 'grey')

    def next_letter(self):
        used = {p['label'] for p in self.doc['peaks']}
        return next((c for c in 'abcdefghijklmnopqrstuvwxyz' if c not in used), '*')

    def add_assignment(self, hi=None, lo=None):
        if hi is None:
            s = self.doc['segments'][0] if self.doc['segments'] else [6, 5]
            c, w = (s[0] + s[1]) / 2, abs(s[0] - s[1]) * 0.04
            hi, lo = round(c + w, 2), round(c - w, 2)
        self.doc['assignments'].append({'id': os.urandom(3).hex(), 'label': 'new', 'hi': hi, 'lo': lo,
                                        'color': self.next_color(), 'sigma': 6.0, 'header': True, 'show_range': True})
        self.refresh_marks('a', len(self.doc['assignments']) - 1)
        self.commit()

    def add_peak(self, ppm=None):
        if ppm is None:
            s = self.doc['segments'][0] if self.doc['segments'] else [6, 5]
            ppm = round((s[0] + s[1]) / 2, 2)
        self.doc['peaks'].append({'id': os.urandom(3).hex(), 'label': self.next_letter(), 'ppm': ppm, 'color': 'sky',
                                  'sigma': 10.0, 'resolve': 1.3})
        self.refresh_marks('p', len(self.doc['peaks']) - 1)
        self.commit()

    def add_structure(self):
        p = next(p for p in PRESETS if p[0] == self.preset.get())
        self.doc['structures'].append({'title': p[0], 'smiles': p[1], 'highlight': p[2], 'notes': p[3],
                                       'title_color': 'sky' if p[0].startswith('Hacac') else 'black'})
        self.refresh_marks('s', len(self.doc['structures']) - 1)
        self.commit()

    def delete_mark(self, which):
        m = self.current_mark(which)
        if m is not None:
            lst = self.mark_list(which)
            i = lst.index(m)
            del lst[i]
            self.refresh_marks(which, min(i, len(lst) - 1) if lst else None)
            self.commit()

    # ================================================================== integration page
    def integ(self):
        integ = self.doc.setdefault('integration', core.default_integration())
        for k, v in core.default_integration().items():
            integ.setdefault(k, v)
        return integ

    def build_integration(self, page):
        body = section(page, T('自动找峰与积分区间'), first=True)
        form = Form(body, self.theme, label_width=11)
        self.int_trace = tk.StringVar(self, value='')
        self.int_trace_box = ttk.Combobox(form, textvariable=self.int_trace, state='readonly', width=10)
        self.int_trace_box.bind('<<ComboboxSelected>>', lambda e: self.refresh_integration())
        form.widget(T('谱线'), self.int_trace_box)
        self.pick = {'thr': tk.StringVar(self, value='10'), 'ratio': tk.StringVar(self, value='0.5'),
                     'hi': tk.StringVar(self, value=''), 'lo': tk.StringVar(self, value='')}
        form.entry(T('阈值 (σ)'), self.pick['thr'])
        form.entry(T('最低相对高度 (%)'), self.pick['ratio'])
        form.pair(T('范围 (ppm)'), self.pick['hi'], self.pick['lo'])
        button_row(body, [(T('自动积分区间'), self.auto_integrate), (T('找峰'), self.find_peaks)], pady=(8, 0))
        hint(body, T('范围留空 = 图中显示的分段。阈值同时要求超过 σ × noise 和最高峰的一定比例；相邻的峰合并成一个多重峰。'
                     '也可以在预览上方选「积分」，直接在谱上拖动添加区间。'))

        body = section(page, T('积分区间'))
        self.itree = self.small_tree(body, (('range', T('范围 (ppm)'), 110), ('mult', T('多重度'), 60),
                                            ('area', T('积分'), 70), ('value', T('相对值'), 70)))
        self.itree.configure(height=7, selectmode='extended')
        self.itree.bind('<<TreeviewSelect>>', lambda e: self.load_range_form())
        button_row(body, [(T('设为参考'), self.set_ref_range), (T('合并'), self.merge_ranges),
                          (T('删除'), self.delete_ranges), (T('清空'), self.clear_ranges)], pady=(8, 0))
        form = Form(body, self.theme, label_width=11)
        self.rv = {'hi': tk.StringVar(self), 'lo': tk.StringVar(self), 'nuclei': tk.StringVar(self)}
        form.pair(T('范围 (ppm)'), self.rv['hi'], self.rv['lo'])
        form.entry(T('该信号的核数'), self.rv['nuclei'])
        for v in self.rv.values():
            v.trace_add('write', lambda *_: self.range_form_changed())
        body = section(page, T('参考与显示'))
        form = Form(body, self.theme, label_width=11)
        self.int_mode = tk.StringVar(self, value='H')
        self.int_mode.trace_add('write', lambda *_: self.integ_settings_changed())
        form.choice(T('换算方式'), self.int_mode, [('H', '核数（参考信号 = 给定 H 数）'), ('mmol', '物质的量（内标 mmol）')])
        self.iv = {k: tk.StringVar(self) for k in ('ref_value', 'ref_nuclei', 'decimals')}
        form.entry(T('参考值'), self.iv['ref_value'])
        form.entry(T('内标信号核数'), self.iv['ref_nuclei'])
        form.entry(T('小数位'), self.iv['decimals'])
        self.ib = {k: tk.BooleanVar(self, value=False) for k in ('show_values', 'show_curves')}
        form.check(T('在图上显示积分值'), self.ib['show_values'], self.integ_settings_changed)
        form.check(T('显示积分曲线'), self.ib['show_curves'], self.integ_settings_changed)
        self.int_on = tk.StringVar(self, value=T('所有谱线'))
        self.int_on_box = ttk.Combobox(form, textvariable=self.int_on, state='readonly', width=10)
        self.int_on_box.bind('<<ComboboxSelected>>', lambda e: self.integ_settings_changed())
        form.widget(T('显示在'), self.int_on_box)
        for v in self.iv.values():
            v.trace_add('write', lambda *_: self.integ_settings_changed())
        button_row(body, [(T('导出积分表…'), self.export_integrals)], pady=(8, 0))
        hint(body, T('核数模式：参考区间的值 = 参考值（例如 1 H），其它区间按面积换算。物质的量模式：参考值 = 内标 mmol，'
                     '每个区间填该信号对应的核数，结果是 mmol。积分表包含图中所有谱线。'))

        body = section(page, T('峰列表'))
        self.pktree = self.small_tree(body, (('ppm', 'ppm', 80), ('snr', 'S/N', 70), ('height', T('相对高度'), 80)))
        self.pktree.configure(height=6, selectmode='extended')
        button_row(body, [(T('转为字母峰'), self.peaks_to_letters), (T('转为上色区域'), self.peaks_to_windows)], pady=(8, 0))
        self.peak_list = []
        self.sync_integration_form()

    def int_current(self):
        name = self.int_trace.get()
        for i, t in enumerate(self.doc['traces']):
            if f"{i + 1}. {t['label']}" == name:
                return t
        return self.doc['traces'][0] if self.doc['traces'] else None

    def sync_integration_form(self):
        integ = self.integ()
        self._loading = True
        try:
            self.int_mode.set(integ['mode'])
            self.iv['ref_value'].set(f"{integ['ref_value']:g}")
            self.iv['ref_nuclei'].set(f"{integ['ref_nuclei']:g}")
            self.iv['decimals'].set(str(integ['decimals']))
            for k in self.ib:
                self.ib[k].set(bool(integ[k]))
        finally:
            self._loading = False

    def refresh_integration(self):
        if not hasattr(self, 'itree'):
            return
        names = [f"{i + 1}. {t['label']}" for i, t in enumerate(self.doc['traces'])]
        self.int_trace_box.configure(values=names)
        if self.int_trace.get() not in names:
            self.int_trace.set(names[0] if names else '')
        self.int_on_box.configure(values=[T('所有谱线')] + names)
        integ = self.integ()
        on = integ.get('traces', 'all')
        self.int_on.set(next((f"{i + 1}. {t['label']}" for i, t in enumerate(self.doc['traces']) if t['id'] == on), T('所有谱线')))
        self.sync_integration_form()
        sel = set(self.itree.selection())
        self.itree.delete(*self.itree.get_children())
        tr = self.int_current()
        areas = values = None
        if tr is not None and integ['ranges']:
            try:
                _, areas, values = core.trace_integrals(self.doc, tr)
            except Exception:
                areas = values = None
        dec = int(integ.get('decimals', 2))
        for i, r in enumerate(integ['ranges']):
            ref = ' ★' if r['id'] == integ.get('ref') else ''
            mult = (r.get('mult') or '') + (f" {r['J']:g} Hz" if r.get('J') else '') + (' ⚠' if r.get('solvent') else '')
            self.itree.insert('', 'end', iid=str(i), values=(f"{r['hi']:.3f} – {r['lo']:.3f}{ref}", mult,
                              '' if areas is None else f'{areas[i]:.4g}',
                              '' if values is None or values[i] is None else f'{values[i]:.{dec}f}'))
        keep = [s for s in sel if self.itree.exists(s)]
        if keep:
            self.itree.selection_set(keep)

    def window_for_picking(self):
        try:
            hi, lo = float(self.pick['hi'].get()), float(self.pick['lo'].get())
            return [(max(hi, lo), min(hi, lo))]
        except ValueError:
            return [tuple(s) for s in (self.doc['segments'] or [])] or [None]

    def picking_input(self):
        tr = self.int_current()
        if tr is None:
            messagebox.showinfo(T('积分'), T('图中还没有谱线。'))
            return None
        try:
            thr, ratio = float(self.pick['thr'].get() or 10), float(self.pick['ratio'].get() or 0.5) / 100
        except ValueError:
            messagebox.showerror(T('积分'), T('请输入数字。'))
            return None
        ppm, d, sig, sf = core.spectrum(tr['path'], tr['shift'], tr['flip'])
        return tr, ppm, d, sig, sf, thr, ratio

    def auto_integrate(self):
        got = self.picking_input()
        if got is None:
            return
        tr, ppm, d, sig, sf, thr, ratio = got
        solvent = core.solvent_position(tr['nuc'], tr['solvent'])
        found = []
        for win in self.window_for_picking():
            found += core.auto_ranges(ppm, d, sig, sf, thr, ratio, window=win, solvent_ppm=solvent)
        integ = self.integ()
        integ['ranges'] = [dict(r, id=os.urandom(3).hex(), nuclei=1.0) for r in found]
        main = [r for r in integ['ranges'] if not r['solvent']]
        integ['ref'] = max(main, key=lambda r: r['area'])['id'] if main else None
        self.refresh_integration()
        self.commit()
        solv = sum(r['solvent'] for r in found)
        self.app.status(T('找到 {n} 个积分区间').format(n=len(found)) + (('  ·  ' + T('{n} 个含溶剂峰（⚠）').format(n=solv)) if solv else ''), 'ok')

    def add_range(self, hi, lo):
        tr = self.int_current()
        mult, j = '', None
        if tr is not None:
            ppm, d, sig, sf = core.spectrum(tr['path'], tr['shift'], tr['flip'])
            mult, j = core.multiplicity(core.pick_peaks(ppm, d, sig, sf, window=(hi, lo)), sf)
        integ = self.integ()
        integ['ranges'].append({'id': os.urandom(3).hex(), 'hi': hi, 'lo': lo, 'mult': mult, 'J': j and round(j, 1),
                                'nuclei': 1.0, 'solvent': False})
        integ['ranges'].sort(key=lambda r: -(r['hi'] + r['lo']))
        if integ.get('ref') is None:
            integ['ref'] = integ['ranges'][0]['id']
        self.refresh_integration()
        self.commit()
        self.app.status(T('已添加积分区间 {hi}–{lo} ppm').format(hi=f'{hi:.3f}', lo=f'{lo:.3f}'), 'ok')

    def selected_ranges(self):
        rs = self.integ()['ranges']
        return [rs[int(s)] for s in self.itree.selection() if int(s) < len(rs)]

    def load_range_form(self):
        sel = self.selected_ranges()
        self._loading = True
        try:
            r = sel[0] if len(sel) == 1 else None
            for k in self.rv:
                self.rv[k].set('' if r is None else f'{r[k]:g}')
        finally:
            self._loading = False

    def range_form_changed(self):
        sel = self.selected_ranges()
        if self._loading or len(sel) != 1:
            return
        try:
            hi, lo, n = float(self.rv['hi'].get()), float(self.rv['lo'].get()), float(self.rv['nuclei'].get() or 1)
        except ValueError:
            return
        sel[0].update(hi=max(hi, lo), lo=min(hi, lo), nuclei=n)
        self.commit()
        self.after_idle(self.refresh_integration)

    def set_ref_range(self):
        sel = self.selected_ranges()
        if sel:
            self.integ()['ref'] = sel[0]['id']
            self.refresh_integration()
            self.commit()

    def merge_ranges(self):
        sel = self.selected_ranges()
        if len(sel) < 2:
            self.app.status(T('选择两个或更多区间再合并'), 'error')
            return
        integ = self.integ()
        keep = sel[0]
        keep.update(hi=max(r['hi'] for r in sel), lo=min(r['lo'] for r in sel), mult='m', J=None,
                    solvent=any(r.get('solvent') for r in sel))
        ids = {r['id'] for r in sel[1:]}
        if integ.get('ref') in ids:
            integ['ref'] = keep['id']
        integ['ranges'] = [r for r in integ['ranges'] if r['id'] not in ids]
        self.refresh_integration()
        self.commit()

    def delete_ranges(self):
        ids = {r['id'] for r in self.selected_ranges()}
        integ = self.integ()
        integ['ranges'] = [r for r in integ['ranges'] if r['id'] not in ids]
        if integ.get('ref') in ids:
            integ['ref'] = integ['ranges'][0]['id'] if integ['ranges'] else None
        self.refresh_integration()
        self.commit()

    def clear_ranges(self):
        integ = self.integ()
        integ['ranges'], integ['ref'] = [], None
        self.refresh_integration()
        self.commit()

    def integ_settings_changed(self):
        if self._loading:
            return
        integ = self.integ()
        try:
            integ['ref_value'] = float(self.iv['ref_value'].get() or 1)
            integ['ref_nuclei'] = float(self.iv['ref_nuclei'].get() or 1)
            integ['decimals'] = max(0, min(6, int(self.iv['decimals'].get() or 2)))
        except ValueError:
            return
        integ['mode'] = self.int_mode.get()
        for k in self.ib:
            integ[k] = bool(self.ib[k].get())
        on = self.int_on.get()
        integ['traces'] = next((t['id'] for i, t in enumerate(self.doc['traces']) if f"{i + 1}. {t['label']}" == on), 'all')
        self.commit()
        self.after_idle(self.refresh_integration)

    def export_integrals(self, path=None):
        integ = self.integ()
        if not integ['ranges'] or not self.doc['traces']:
            messagebox.showinfo(T('积分'), T('还没有积分区间。'))
            return None
        if path is None:
            path = filedialog.asksaveasfilename(title=T('导出积分表'), defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return None
        import csv
        unit = 'mmol' if integ['mode'] == 'mmol' else 'nuclei'
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['Trace', 'Spectrum folder', 'Range high (ppm)', 'Range low (ppm)', 'Centre (ppm)', 'Multiplicity',
                        'J (Hz)', 'Nuclei', 'Reference', 'Area', f'Value ({unit})', 'Contains solvent'])
            for tr in self.doc['traces']:
                ranges, areas, values = core.trace_integrals(self.doc, tr)
                for r, a, v in zip(ranges, areas, values):
                    w.writerow([tr['label'], tr['path'], r['hi'], r['lo'], r.get('center', (r['hi'] + r['lo']) / 2),
                                r.get('mult', ''), r.get('J') or '', r.get('nuclei', 1), 'yes' if r['id'] == integ.get('ref') else '',
                                f'{a:.6g}', '' if v is None else f'{v:.6g}', 'yes' if r.get('solvent') else ''])
        self.app.status(T('已保存积分表：{path}').format(path=path), 'ok')
        return path

    def find_peaks(self):
        got = self.picking_input()
        if got is None:
            return
        tr, ppm, d, sig, sf, thr, ratio = got
        peaks = []
        for win in self.window_for_picking():
            peaks += core.pick_peaks(ppm, d, sig, sf, thr, ratio, window=win)
        peaks.sort(key=lambda p: -p['ppm'])
        self.peak_list = peaks
        top = max([p['height'] for p in peaks] or [1])
        self.pktree.delete(*self.pktree.get_children())
        for i, p in enumerate(peaks):
            self.pktree.insert('', 'end', iid=str(i), values=(f"{p['ppm']:.4f}", f"{p['snr']:.0f}", f"{p['height'] / top * 100:.1f} %"))
        self.app.status(T('找到 {n} 个峰').format(n=len(peaks)), 'ok')

    def chosen_peaks(self):
        return [self.peak_list[int(s)] for s in self.pktree.selection() if int(s) < len(self.peak_list)]

    def peaks_to_letters(self):
        for p in self.chosen_peaks():
            self.doc['peaks'].append({'id': os.urandom(3).hex(), 'label': self.next_letter(), 'ppm': round(p['ppm'], 4),
                                      'color': 'sky', 'sigma': 10.0, 'resolve': 1.3})
        self.refresh_marks('p')
        self.commit()

    def peaks_to_windows(self):
        span = abs(self.doc['segments'][0][0] - self.doc['segments'][0][1]) if self.doc['segments'] else 10
        for p in self.chosen_peaks():
            w = max(0.004 * span, 0.01)
            self.doc['assignments'].append({'id': os.urandom(3).hex(), 'label': f"{p['ppm']:.2f}", 'hi': round(p['ppm'] + w, 4),
                                            'lo': round(p['ppm'] - w, 4), 'color': self.next_color(), 'sigma': 6.0,
                                            'header': True, 'show_range': False})
        self.refresh_marks('a')
        self.commit()

    # ================================================================== figure page
    def build_figure(self, page):
        self.fv = {}

        def v(key, value=''):
            var = tk.StringVar(self, value=value)
            var.trace_add('write', lambda *_: self.figure_form_changed())
            self.fv[key] = var
            return var
        body = section(page, T('x 轴分段'), first=True)
        form = Form(body, self.theme, label_width=10)
        box = ttk.Frame(form)
        box.columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=v('segments'), width=10).grid(row=0, column=0, sticky='ew')
        ttk.Button(box, text=T('自动'), command=self.auto_segments).grid(row=0, column=1, padx=(self.theme.d(6), 0))
        form.widget(T('范围 (ppm)'), box)
        form.entry(T('刻度（可空）'), v('xticks'))
        form.entry(T('分段标题'), v('seg_headers'))
        hint(body, T('多段用逗号分开，例如 6.3–4.75, 3.76–3.58。也可以在预览上方选「断轴」，直接在谱上拖过要切掉的区域。'
                     '刻度和分段标题按段用 | 分开，例如 6.0 5.5 5.0 | 3.7 3.6。'))
        body = section(page, T('谱线'))
        form = Form(body, self.theme, label_width=11)
        form.entry(T('谱线间距'), v('step'))
        form.entry(T('线宽 (pt)'), v('lw'))
        form.entry(T('削顶高度'), v('clip'))
        form.entry(T('分段最大放大'), v('max_mag'))
        form.entry(T('名称高度'), v('name_y'))
        form.entry(T('顶部留白'), v('headroom'))
        self.norm = tk.StringVar(self, value='max')
        self.norm.trace_add('write', lambda *_: self.figure_form_changed())
        form.choice(T('归一化'), self.norm, NORM_TEXT)
        form.pair(T('参考峰 ppm ± 宽'), v('norm_ppm'), v('norm_width'), '±')
        self.signed = tk.BooleanVar(self, value=False)
        self.color_names = tk.BooleanVar(self, value=False)
        form.check(T('保留负峰（DEPT-135）'), self.signed, self.figure_form_changed)
        form.check(T('谱线名称用谱线颜色'), self.color_names, self.figure_form_changed)
        body = section(page, T('文字'))
        form = Form(body, self.theme, label_width=10)
        form.entry(T('图标题'), v('title'))
        form.pair(T('分图字母'), v('panel'), v('struct_panel'), '·')
        form.entry(T('x 轴标题'), v('xlabel'))
        form.entry(T('脚注'), v('footnote'))
        form.widget(T('字体'), ttk.Combobox(form, textvariable=v('font'), values=['Arial', 'Helvetica', 'Calibri', 'Times New Roman',
                                                                                    'DejaVu Sans'], width=10))
        form.entry(T('字号 (pt)'), v('fs'))
        hint(body, T('x 轴标题填 auto 按 nucleus 自动生成；上下标用 matplotlib 写法，例如 $^{13}$C、R$_2$Se$_2$。'))
        button_row(body, [(T('重置标签位置'), self.reset_offsets), (T('恢复隐藏的标签'), self.unhide)], pady=(8, 0))
        body = section(page, T('导出尺寸'))
        form = Form(body, self.theme, label_width=11)
        form.pair(T('宽 × 高 (英寸)'), v('w'), v('h'), '×')
        form.pair(T('左 / 右边界'), v('ml'), v('mr'), '/')
        form.pair(T('下 / 上边界'), v('mb'), v('mt'), '/')
        form.entry(T('分辨率 (DPI)'), v('dpi', '600'))
        if core.rdkit_available():
            form.entry(T('结构栏宽度'), v('struct_frac'))
        hint(body, T('单栏约 3.3 英寸，双栏约 7 英寸。边界是占整张图的比例（0–1）。'))
        self.sync_figure_form()

    def sync_figure_form(self):
        d, fv = self.doc, self.fv
        self._loading = True
        try:
            fv['segments'].set(', '.join(f'{a:g}–{b:g}' for a, b in d['segments']))
            fv['xticks'].set(' | '.join(d['xticks'].get(str(k), '') for k in range(len(d['segments']))).strip(' |'))
            heads = {h['segment']: h['text'] for h in d['seg_headers']}
            fv['seg_headers'].set(' | '.join(heads.get(k, '').replace('\n', ' / ') for k in range(len(d['segments']))).strip(' |'))
            for k in ('step', 'lw', 'clip', 'max_mag', 'name_y', 'headroom', 'fs', 'struct_frac'):
                if k in fv:
                    fv[k].set(f'{d[k]:g}')
            for k in ('title', 'panel', 'struct_panel', 'xlabel', 'footnote', 'font'):
                fv[k].set(d[k])
            fv['norm_ppm'].set('' if d['norm'].get('ppm') is None else f"{d['norm']['ppm']:g}")
            fv['norm_width'].set(f"{d['norm'].get('width', 2):g}")
            self.norm.set(d['norm'].get('mode', 'max'))
            fv['w'].set(f"{d['figsize'][0]:g}")
            fv['h'].set(f"{d['figsize'][1]:g}")
            for k, key in (('ml', 'l'), ('mr', 'r'), ('mb', 'b'), ('mt', 't')):
                fv[k].set(f"{d['margins'][key]:g}")
            self.signed.set(d['signed'])
            self.color_names.set(d['color_names'])
        finally:
            self._loading = False

    def figure_form_changed(self):
        if self._loading:
            return
        d, fv = self.doc, self.fv
        try:
            segs = []
            for part in fv['segments'].get().replace('—', '–').split(','):
                part = part.strip()
                if not part:
                    continue
                a, b = [float(x) for x in part.replace('–', ' ').replace(' -', ' ').replace(' to ', ' ').split()[:2]]
                segs.append([max(a, b), min(a, b)])
            nums = {k: float(fv[k].get()) for k in ('step', 'lw', 'clip', 'max_mag', 'name_y', 'headroom', 'fs', 'w', 'h',
                                                   'ml', 'mr', 'mb', 'mt') if fv[k].get().strip()}
            if 'struct_frac' in fv and fv['struct_frac'].get().strip():
                nums['struct_frac'] = float(fv['struct_frac'].get())
            norm_ppm = float(fv['norm_ppm'].get()) if fv['norm_ppm'].get().strip() else None
            norm_width = float(fv['norm_width'].get() or 2)
        except ValueError:
            return
        if segs:
            d['segments'] = segs
        d['xticks'] = {str(k): s.strip() for k, s in enumerate(fv['xticks'].get().split('|')) if s.strip()}
        d['seg_headers'] = [{'segment': k, 'text': s.strip().replace(' / ', '\n'), 'color': 'sky'}
                            for k, s in enumerate(fv['seg_headers'].get().split('|')) if s.strip()]
        for k in ('step', 'lw', 'clip', 'max_mag', 'name_y', 'headroom', 'fs', 'struct_frac'):
            if k in nums:
                d[k] = nums[k]
        for k in ('title', 'panel', 'struct_panel', 'xlabel', 'footnote', 'font'):
            d[k] = fv[k].get()
        d['norm'] = {'mode': self.norm.get(), 'ppm': norm_ppm, 'width': norm_width}
        if 'w' in nums and 'h' in nums:
            d['figsize'] = [nums['w'], nums['h']]
        for k, key in (('ml', 'l'), ('mr', 'r'), ('mb', 'b'), ('mt', 't')):
            if k in nums:
                d['margins'][key] = nums[k]
        d['signed'], d['color_names'] = bool(self.signed.get()), bool(self.color_names.get())
        self.commit()

    def auto_segments(self):
        t = next((t for t in self.doc['traces'] if t['visible']), None)
        if t is not None:
            self.doc['segments'] = [core.default_range(core.read_bruker(t['path']), t['shift'])]
            self.doc['xticks'], self.doc['seg_headers'] = {}, []
            self.sync_figure_form()
            self.commit()

    def reset_offsets(self):
        self.doc['offsets'] = {}
        self.commit()

    def unhide(self):
        for s in self.doc['styles'].values():
            s.pop('hidden', None)
        self.commit()

    # ================================================================== preview
    def build_preview(self, parent):
        top = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(16, 10, 16, 0))
        top.pack(fill='x')
        short = {'ref': 'Ref.', 'break': 'Break', 'integ': 'Integral'}  # six buttons must fit beside the preview
        self.switch = PreviewSwitch(top, [(k, short.get(k, T(t)) if get_language() == 'en' else T(t)) for k, t in MODES],
                                    self.set_mode, self.theme)
        for button in self.switch.buttons.values():  # fit each button to its text, not ttk's default width
            button.configure(width=-max(4, len(button.cget('text')) + 1))
        self.switch.pack(side='left')
        self.switch.set('move')
        ttk.Button(top, text='↶', width=3, style='PreviewTool.TButton', command=self.undo).pack(side='left', padx=(self.theme.d(10), 0))
        ttk.Button(top, text='↷', width=3, style='PreviewTool.TButton', command=self.redo).pack(side='left')
        info_row = ttk.Frame(parent, style='Preview.TFrame', padding=self.theme.pad(16, 6, 16, 0))
        info_row.pack(fill='x')
        self.mode_hint = tk.StringVar(self, value=T(MODE_HINTS['move']))
        ttk.Label(info_row, textvariable=self.mode_hint, style='Preview.TLabel').pack(anchor='w')
        self.info = tk.StringVar(self, value='')
        info = ttk.Label(info_row, textvariable=self.info, style='Preview.TLabel', wraplength=400)
        info.pack(side='left', fill='x', expand=True)
        info.bind('<Configure>', lambda e: info.configure(wraplength=max(200, e.width)))
        self.figure = Figure(figsize=(7.2, 5.0), dpi=100, facecolor='white')
        self.stage = ttk.Frame(parent, style='Preview.TFrame')
        self.stage.pack(fill='both', expand=True, padx=self.theme.px(16), pady=self.theme.px(12))
        self.canvas = figure_canvas(self.figure, self.stage)
        self.empty = ttk.Label(self.stage, text=T('选择数据文件夹并扫描，然后双击实验加入图中'), style='PreviewEmpty.TLabel')
        self.stage.bind('<Configure>', self.schedule_fit)
        for name, handler in (('button_press_event', self.on_press), ('motion_notify_event', self.on_motion),
                              ('button_release_event', self.on_release), ('scroll_event', self.on_scroll)):
            self.canvas.mpl_connect(name, handler)
        widget = self.canvas.get_tk_widget()
        for key, delta in (('Left', (-1, 0)), ('Right', (1, 0)), ('Up', (0, 1)), ('Down', (0, -1))):
            widget.bind(f'<{key}>', lambda e, dl=delta: self.nudge(dl, 1))
            widget.bind(f'<Shift-{key}>', lambda e, dl=delta: self.nudge(dl, 5))
        widget.bind('<Delete>', lambda e: self.hide_selected())
        widget.bind('<Control-z>', lambda e: self.undo())
        widget.bind('<Control-y>', lambda e: self.redo())
        widget.bind('<Escape>', lambda e: self.select_label(None))

    def paint(self):
        self.refresh_traces()

    def set_mode(self, mode):
        self.mode = mode
        self.switch.set(mode)
        self.mode_hint.set(T(MODE_HINTS[mode]))
        self.canvas.get_tk_widget().configure(cursor='fleur' if mode == 'move' else 'crosshair')

    # ---------- history ----------
    def commit(self):
        snap = json.dumps(self.doc)
        if snap != self._saved:
            self.history.append(self._saved)
            del self.history[:-100]
            self.future.clear()
            self._saved = snap
        self.schedule()

    def restore(self, snap):
        self.doc = json.loads(snap)
        self._saved = snap
        self.refresh_traces()
        for which in ('a', 'p', 's'):
            self.refresh_marks(which)
        self.sync_figure_form()
        self.refresh_integration()
        self.update_plot()

    def undo(self):
        if self.history:
            self.future.append(self._saved)
            self.restore(self.history.pop())

    def redo(self):
        if self.future:
            self.history.append(self._saved)
            self.restore(self.future.pop())

    # ---------- drawing ----------
    def schedule(self, *_):
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(250, lambda: self.update_plot(quiet=True))

    def update_plot(self, quiet=False):
        self._pending = None
        if not self.doc['traces']:
            self.meta = None
            self.figure.clear()
            self.canvas.draw_idle()
            self.empty.place(relx=.5, rely=.5, anchor='center')
            self.info.set('')
            return None
        self.empty.place_forget()
        self.fit_preview(draw=False)
        try:
            self.meta = core.render(self.doc, self.figure)
        except Exception as exc:
            if quiet:
                self.app.status(T('预览未更新：{error}').format(error=exc), 'error')
            else:
                messagebox.showerror(T('无法绘图'), str(exc))
            return None
        self.decorate()
        w, h = self.doc['figsize']
        issues = self.meta['issues']
        text = T('{n} 条谱线 · 导出 {w} × {h} in').format(n=len(self.meta['traces']), w=f'{w:g}', h=f'{h:g}')
        if issues:
            text += '  ·  ⚠ ' + T('{n} 处重叠：').format(n=len(issues)) + '; '.join(i['msg'] for i in issues[:3])
        else:
            text += '  ·  ✓ ' + T('无重叠')
        self.info.set(text)
        return self.meta

    def decorate(self):
        """Preview-only marks: red boxes around overlapping labels, accent box around the selected one."""
        bad = {i for issue in self.meta['issues'] for i in issue['ids']}
        renderer = self.canvas.get_renderer()
        inv = self.figure.transFigure.inverted()
        for t in self.meta['labels']:
            color = '#D55E00' if t._nmr_id in bad else ('#0072B2' if t._nmr_id == self.selected_label else None)
            if color:
                b = t.get_window_extent(renderer).padded(2)
                (x0, y0), (x1, y1) = inv.transform([(b.x0, b.y0), (b.x1, b.y1)])
                self.figure.add_artist(Rectangle((x0, y0), x1 - x0, y1 - y0, transform=self.figure.transFigure, fill=False,
                                                 ec=color, lw=1.0, ls='--' if t._nmr_id in bad else '-'))
        self.canvas.draw_idle()

    def schedule_fit(self, event=None):
        if self.app.defer_if_hidden(self, self.schedule_fit):
            return
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self.refit)

    def refit(self):
        self._fit_job = None
        if self.doc['traces']:
            self.update_plot(quiet=True)  # text placement depends on the preview size

    def fit_preview(self, draw=True):
        w, h = self.doc['figsize']
        if w > 0 and h > 0:
            fit_figure(self.canvas, self.stage, w, h, theme=self.theme)

    # ---------- mouse ----------
    def spectrum_axis(self, event):
        if self.meta is None or event.inaxes is None:
            return None
        return self.meta['axes'].index(event.inaxes) if event.inaxes in self.meta['axes'] else None

    def label_at(self, event):
        if self.meta is None:
            return None
        for t in reversed(self.meta['labels']):
            if t.contains(event)[0]:
                return t
        return None

    def trace_at(self, ydata):
        """Trace whose baseline is the highest one at or below the pointer."""
        best = None
        for tid, info in self.meta['traces'].items():
            if info['off'] <= ydata + 0.15 and (best is None or info['off'] > best[1]['off']):
                best = (tid, info)
        if best is None and self.meta['traces']:
            best = min(self.meta['traces'].items(), key=lambda kv: kv[1]['off'])
        return best

    def on_press(self, event):
        if self.meta is None or event.button != 1:
            return
        self.canvas.get_tk_widget().focus_set()
        if self.mode == 'move':
            t = self.label_at(event)
            if t is not None:
                if event.dblclick:
                    self.edit_label(t._nmr_id)
                    return
                self.select_label(t._nmr_id, redraw=False)
                self.drag = {'kind': 'label', 'artist': t, 'x': event.x, 'y': event.y,
                             'offset': list(self.doc['offsets'].get(t._nmr_id, [0, 0])), 'base': t.get_transform()}
                return
            k = self.spectrum_axis(event)
            if k is None or event.ydata is None:
                self.select_label(None)
                return
            found = self.trace_at(event.ydata)
            if found:
                tid, info = found
                self.drag = {'kind': 'trace', 'id': tid, 'y': event.ydata, 'ax': event.inaxes,
                             'lines': [(ln, ln.get_ydata().copy()) for ax in self.meta['axes'] for ln in ax.get_lines()
                                       if getattr(ln, '_nmr_trace', None) == tid]}
            return
        k = self.spectrum_axis(event)
        if k is None or event.xdata is None:
            return
        if self.mode in ('region', 'break', 'integ'):
            span = event.inaxes.axvspan(event.xdata, event.xdata, color={'break': '#D55E00', 'integ': '#555555'}.get(self.mode, '#0072B2'),
                                        alpha=.15, lw=0)
            self.drag = {'kind': self.mode, 'k': k, 'x0': event.xdata, 'span': span}
        elif self.mode == 'peak':
            self.add_peak(round(event.xdata, 3))
            self.app.status(T('已添加字母峰 {label} @ {ppm} ppm').format(label=self.doc['peaks'][-1]['label'], ppm=f'{event.xdata:.3f}'), 'ok')
        elif self.mode == 'ref':
            self.set_reference(k, event.xdata, event.ydata)

    def on_motion(self, event):
        d = self.drag
        if d is None:
            return
        if d['kind'] == 'label' and event.x is not None:
            dx = (event.x - d['x']) * 72 / self.figure.dpi
            dy = (event.y - d['y']) * 72 / self.figure.dpi
            t = d['artist']
            # the stored transform already contains the old offset; add only the change
            t.set_transform(d['base'] + ScaledTranslation(dx / 72, dy / 72, self.figure.dpi_scale_trans))
            d['moved'] = (dx, dy)
            self.canvas.draw_idle()
        elif d['kind'] == 'trace' and event.inaxes is not None and event.ydata is not None:
            delta = event.ydata - d['y'] if event.inaxes is d['ax'] else d.get('delta', 0)
            d['delta'] = delta
            for ln, y0 in d['lines']:
                ln.set_ydata(y0 + delta)
            self.canvas.draw_idle()
        elif d['kind'] in ('region', 'break', 'integ') and event.inaxes is self.meta['axes'][d['k']] and event.xdata is not None:
            x0, x1 = sorted([d['x0'], event.xdata])
            span = d['span']
            if hasattr(span, 'set_width'):  # matplotlib >= 3.9 returns a Rectangle
                span.set_x(x0)
                span.set_width(x1 - x0)
            else:
                span.set_xy([[x0, 0], [x0, 1], [x1, 1], [x1, 0], [x0, 0]])
            d['x1'] = event.xdata
            self.canvas.draw_idle()

    def on_release(self, event):
        d, self.drag = self.drag, None
        if d is None:
            return
        if d['kind'] == 'label':
            if 'moved' in d and abs(d['moved'][0]) + abs(d['moved'][1]) > 0.3:
                ox, oy = d['offset']
                self.doc['offsets'][d['artist']._nmr_id] = [round(ox + d['moved'][0], 1), round(oy + d['moved'][1], 1)]
                self.commit()
            else:
                self.update_plot(quiet=True)  # show the selection box
        elif d['kind'] == 'trace':
            if abs(d.get('delta', 0)) > 1e-3:
                tr = next(t for t in self.doc['traces'] if t['id'] == d['id'])
                tr['dy'] = round(tr['dy'] + d['delta'], 3)
                self.load_trace_form()
                self.commit()
        elif d['kind'] in ('region', 'break', 'integ'):
            x1 = d.get('x1')
            if x1 is None or abs(x1 - d['x0']) < 1e-6:
                d['span'].remove()
                self.canvas.draw_idle()
                return
            hi, lo = max(d['x0'], x1), min(d['x0'], x1)
            span = abs(self.doc['segments'][d['k']][0] - self.doc['segments'][d['k']][1]) if self.doc['segments'] else 10
            dec = 3 if span < 20 else 1
            if d['kind'] == 'integ':
                self.add_range(round(hi, 4), round(lo, 4))
            elif d['kind'] == 'region':
                self.add_assignment(round(hi, dec), round(lo, dec))
                self.app.status(T('已添加上色区域，在「标注」页修改名称和颜色'), 'ok')
            else:
                segs = self.doc['segments'] or [list(self.meta['segments'][0])]
                self.doc['segments'] = core.break_segment(segs, d['k'], hi, lo)
                self.doc['xticks'], self.doc['seg_headers'] = {}, []
                self.sync_figure_form()
                self.commit()
                self.app.status(T('已切掉 {hi}–{lo} ppm').format(hi=f'{hi:.{dec}f}', lo=f'{lo:.{dec}f}'), 'ok')

    def on_scroll(self, event):
        gui = getattr(event, 'guiEvent', None)
        ctrl = bool(gui is not None and getattr(gui, 'state', 0) & 0x4) or (event.key or '').startswith('control')
        if not ctrl or self.spectrum_axis(event) is None or event.ydata is None:
            return
        found = self.trace_at(event.ydata)
        if found:
            tr = next(t for t in self.doc['traces'] if t['id'] == found[0])
            tr['scale'] = round(min(50, max(0.05, tr['scale'] * (1.12 if event.button == 'up' else 1 / 1.12))), 3)
            self.load_trace_form()
            self.app.status(T('{name} 纵向缩放 {scale}×').format(name=tr['label'], scale=f"{tr['scale']:.2f}"))
            self.commit()

    # ---------- labels ----------
    def select_label(self, lid, redraw=True):
        self.selected_label = lid
        if redraw:
            self.update_plot(quiet=True)

    def nudge(self, delta, step):
        lid = self.selected_label
        if lid:
            o = self.doc['offsets'].get(lid, [0, 0])
            self.doc['offsets'][lid] = [o[0] + delta[0] * step, o[1] + delta[1] * step]
            self.commit()
        return 'break'

    def hide_selected(self):
        lid = self.selected_label
        if lid:
            self.doc['styles'].setdefault(lid, {})['hidden'] = True
            self.selected_label = None
            self.app.status(T('已隐藏标签，可在「图形」页恢复'), 'ok')
            self.commit()

    def edit_label(self, lid):
        """Double-click on a label: edit the text it comes from."""
        kind, _, rest = lid.partition(':')
        d = self.doc
        if kind == 'name':
            target = next((t for t in d['traces'] if t['id'] == rest), None)
            key = 'label'
        elif kind == 'assign':
            target, key = next((a for a in d['assignments'] if a['id'] == rest), None), 'label'
        elif kind == 'peak':
            target, key = next((p for p in d['peaks'] if p['id'] == rest.split(':')[0]), None), 'label'
        elif kind == 'stitle':
            target, key = d['structures'][int(rest)], 'title'
        elif kind in ('title', 'panel', 'struct_panel', 'xlabel', 'footnote'):
            target, key = d, kind
        else:
            return
        if target is None:
            return
        text = ask_string(self, self.theme, T('编辑标签'), T('标签文字：'), str(target.get(key, '')))
        if text is not None:
            target[key] = text
            self.refresh_traces()
            for which in ('a', 'p', 's'):
                self.refresh_marks(which)
            self.sync_figure_form()
            self.commit()

    # ---------- reference ----------
    def set_reference(self, k, x, y):
        found = self.trace_at(y)
        if not found:
            return
        tr = next(t for t in self.doc['traces'] if t['id'] == found[0])
        hi, lo = self.meta['segments'][k]
        peak = core.nearest_peak(tr['path'], tr['shift'], tr['flip'], x, abs(hi - lo) * 0.01)
        value = ask_string(self, self.theme, T('定标'), T('{name}：选中的峰现在在 {ppm} ppm。输入它应有的化学位移：').format(
            name=tr['label'], ppm=f'{peak:.4f}'), f'{peak:.3f}')
        if not value:
            return
        try:
            delta = float(value) - peak
        except ValueError:
            messagebox.showerror(T('定标'), T('请输入数字。'))
            return
        same = [t for t in self.doc['traces'] if t is not tr and t['nuc'] == tr['nuc']
                and os.path.dirname(os.path.dirname(os.path.dirname(t['path']))) == os.path.dirname(os.path.dirname(os.path.dirname(tr['path'])))]
        group = [tr] + (same if same and messagebox.askyesno(T('定标'), T('同一样品还有 {n} 条同核谱线，一起平移吗？').format(n=len(same))) else [])
        for t in group:
            t['shift'] = round(t['shift'] + delta, 4)
            t['ref_note'] = T('手动：{a} → {b} ppm').format(a=f'{peak:.3f}', b=f'{float(value):.3f}')
        self.load_trace_form()
        self.commit()
        self.app.status(T('{name} 平移 {delta} ppm').format(name=tr['label'], delta=f'{delta:+.3f}'), 'ok')

    # ================================================================== files
    def save_doc(self):
        path = filedialog.asksaveasfilename(title=T('保存配置'), defaultextension='.nmrfig.json',
                                            filetypes=[(T('NMR 图配置'), '*.json')])
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.doc, f, ensure_ascii=False, indent=1)
            self.app.status(T('已保存配置：{path}').format(path=path), 'ok')

    def load_doc(self):
        path = filedialog.askopenfilename(title=T('打开配置'), filetypes=[(T('NMR 图配置'), '*.json'), (T('所有文件'), '*.*')])
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                doc = core.default_doc() | json.load(f)
            for t in doc['traces']:
                core.read_bruker(t['path'])  # fail now, with a clear message, if a spectrum moved
        except Exception as exc:
            messagebox.showerror(T('打开失败'), str(exc))
            return
        self.history.append(self._saved)
        self.restore(json.dumps(doc))
        self.app.status(T('已打开配置：{path}').format(path=path), 'ok')

    def export_figure(self, path=None, dpi=None):
        if not self.doc['traces']:
            messagebox.showinfo(T('导出'), T('图中还没有谱线。'))
            return None
        if path is None:
            path = filedialog.asksaveasfilename(title=T('导出图片'), defaultextension='.png', initialfile=self.doc.get('name', 'nmr_figure'),
                                                filetypes=[('PNG', '*.png'), ('PDF', '*.pdf'), ('SVG', '*.svg'), ('TIFF', '*.tif')])
        if not path:
            return None
        try:
            dpi = dpi or float(self.fv['dpi'].get() or 600)
            w, h = self.doc['figsize']
            figure = Figure(figsize=(w, h), dpi=100, facecolor='white')
            meta = core.render(copy.deepcopy(self.doc), figure)
            figure.savefig(path, dpi=dpi, facecolor='white')
        except Exception as exc:
            messagebox.showerror(T('导出失败'), str(exc))
            return None
        msg = T('已保存图片：{path}').format(path=path)
        if meta['issues']:
            msg += '  ·  ⚠ ' + T('{n} 处重叠：').format(n=len(meta['issues'])) + meta['issues'][0]['msg']
        self.app.status(msg, 'error' if meta['issues'] else 'ok', timeout=12000)
        return meta

    # ---------- shell hooks ----------
    def shortcut_open(self):
        self.browse()

    def shortcut_save(self):
        self.export_figure()

    def shortcut_refresh(self):
        self.update_plot()

    def close(self):
        for job in (self._pending, self._fit_job):
            if job is not None:
                self.after_cancel(job)
        self._pending = self._fit_job = None
