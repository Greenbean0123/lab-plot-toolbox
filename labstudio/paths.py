"""Where the toolbox keeps preferences and startup data (never inside the program folder)."""
import json
import os
import sys
from pathlib import Path

APP_NAME = '科研绘图工具箱'
APP_VERSION = '0.1.4'
PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / 'assets'
DEMO_DATA = PROJECT_DIR / 'demo-data'


def local_root():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path(os.environ.get('LOCALAPPDATA', Path.home()))


def data_directory():
    override = os.environ.get('LABSTUDIO_DATA_DIR')  # used by the self-test
    return Path(override) if override else local_root() / 'LabPlotStudio'


def legacy_xrd_directory():
    """Data folder of the standalone XRD Plotter, reused so existing startup data keeps loading."""
    return local_root() / 'XRD Plotter'


class Preferences:
    """Small JSON settings file; failures to save never interrupt work."""

    def __init__(self):
        self.path = data_directory() / 'preferences.json'
        try:
            self.values = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.values, dict):
                self.values = {}
        except (OSError, ValueError):
            self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass
