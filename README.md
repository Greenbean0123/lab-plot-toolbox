# Lab Plot Studio · 科研绘图工具箱

TEM scale bars and particle-size distributions, stacked XRD patterns with phase identification, and UV-Vis spectra — in one
desktop app for Windows and macOS. English and Chinese interface, light and dark theme.

把三个独立程序（[TEM-graph-tool](https://github.com/Greenbean0123/TEM-graph-tool)、
[xrd-plotter](https://github.com/Greenbean0123/xrd-plotter)、[uvvis-plotter](https://github.com/Greenbean0123/uvvis-plotter)）
合并成一个桌面应用。

## 下载 / Download

到 [Releases](https://github.com/Greenbean0123/lab-plot-toolbox/releases) 下载：

| 系统 | 文件 |
|---|---|
| Windows 10 / 11 | `LabPlotStudio-<版本>-Windows-x64.exe`（单文件）或 `…-Windows-x64.zip`（文件夹版，启动更快） |
| macOS（M1–M4 芯片） | `LabPlotStudio-<版本>-macOS-arm64.dmg` |
| macOS（Intel） | `LabPlotStudio-<版本>-macOS-x86_64.dmg` |

- macOS 版没有经过 Apple 公证：第一次打开时右键点 App → **打开** → **打开**（或在「系统设置 → 隐私与安全性」里点 **仍要打开**）。
- Windows 可能提示未知发布者：点 **更多信息** → **仍要运行**。

每个安装包都在构建机器上启动过一次，并通过了覆盖三个工具的自检（`--self-test`）。

## 功能

| 工具 | 功能 |
|---|---|
| **XRD** | 样品图谱与标准卡片（PDF / CIF / XY / CSV）堆叠对照；图层拖动排序与显隐；标签样式、调色板、线宽；导出图片和处理数据；启动文件夹自动加载 |
| **XRD 相分析** | 多相匹配并给出 (hkl) 归属；谢乐公式晶粒尺寸；把卡片画成指定晶粒尺寸的纳米晶峰形 |
| **TEM** | 自动识别原标尺并重新标定；可拖动的新标尺；局部放大图；直径直方图 + 高斯拟合，并可叠加到 TEM 图上；批量导出；项目文件 |
| **UV-Vis** | 多条光谱导入；三种归一化方式；导出图片和数据 |

计算部分沿用原项目代码，`tests/test_equivalence.py` 会与原程序逐像素对比输出。

## 从源码运行

Windows 上双击 **`启动.bat`**（首次运行会建立 `.venv` 并安装依赖）。也可以手动：

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py          # xrd | tem | uvvis 可作为参数
```

需要 Python 3.9+（含 Tkinter）。运行依赖约 150 MB，不需要 SciPy。

## 打包

```sh
pip install -r requirements-build.txt
python packaging/build.py            # Windows: 单个 exe；macOS: .app → .zip + .dmg
python packaging/build.py --onedir   # Windows: 文件夹版 zip
```

推送到 `main` 时，GitHub Actions 会在 Windows、macOS（Apple 芯片）和 macOS（Intel）上测试、打包并自检；
推送 `v*` 标签（与 `labstudio/paths.py` 里的版本号一致）时自动发布 Release。

## 测试

```sh
python tests/test_gui.py        # 界面操作与导出
python tests/test_phase.py      # 相分析与谢乐公式
python tests/test_i18n.py       # 每条界面文字都有英文翻译
pip install -r requirements-dev.txt
python tests/test_numerics.py   # 与 SciPy 对比
python tests/test_equivalence.py  # 与原程序对比（需要原项目在同级目录）
```

## 界面语言与设置

默认英文。右下角齿轮（或 `Ctrl+,`）打开设置：主题、界面大小、语言。偏好保存在
`%LOCALAPPDATA%\LabPlotStudio`（macOS：`~/Library/Application Support/LabPlotStudio`），不写入程序文件夹。

快捷键：`Ctrl+O` 打开 · `Ctrl+S` 导出 · `F5` 刷新预览 · TEM 预览里滚轮缩放、右键拖动平移

MIT License.
