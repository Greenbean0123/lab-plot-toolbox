"""Open the app, run a scripted scenario and save a window screenshot (for visual checks).

python tools/snapshot.py <tool> <out.png> [dark] [scenario]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import ImageGrab
from labstudio.app import LabStudio
import labstudio.app as appmod

tool, out = sys.argv[1], sys.argv[2]
dark = 'dark' in sys.argv[3:]
scenario = next((a for a in sys.argv[3:] if a != 'dark'), None)
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
app = LabStudio(tool)
app.geometry(f'+20+20')
app.theme.set(mode='dark' if dark else 'light')
app.update()
app.show(tool)
for _ in range(10):
    app.update()
    time.sleep(.05)
view = app.views[tool]
if scenario:
    import importlib
    importlib.import_module('scenarios').run(scenario, app, view)
for _ in range(30):
    app.update()
    time.sleep(.05)
x, y = app.winfo_rootx(), app.winfo_rooty()
shot = ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height()), all_screens=True)
if shot.width > 1800:
    shot = shot.resize((1800, round(shot.height * 1800 / shot.width)))
shot.save(out)
print('saved', out, app.status_var.get())
app.close()
