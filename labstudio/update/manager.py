"""Update checks and installation for the main window. Worker threads never touch Tk widgets."""
import json
import time
import tkinter as tk
import webbrowser
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox

from ..i18n import T, app_name
from ..paths import APP_VERSION
from . import updater


class UpdateManager:
    """Owns update state for the app; the status bar button and the Settings window only display it."""

    def __init__(self, app):
        self.app = app
        self.events = Queue()
        self.status = tk.StringVar(app, value=T('当前版本 {version}').format(version=APP_VERSION))
        self.release = None
        self.busy = False
        self.manual = False
        self.installer = None
        self._poll_job = None

    # ---------- lifecycle ----------
    def start(self):
        updater.mark_ready()  # a freshly installed version confirms it started correctly
        try:
            from .cleanup import schedule
            schedule()
        except Exception:
            pass
        self._poll_job = self.app.after(100, self.poll)
        import os
        if updater.preferences().get('automatic', True) and not os.environ.get('LABSTUDIO_NO_UPDATE_CHECK'):
            self.app.after(4000, lambda: self.check(manual=False))

    def stop(self):
        if self._poll_job is not None:
            try:
                self.app.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    @property
    def automatic(self):
        return updater.preferences().get('automatic', True)

    def set_automatic(self, value):
        updater.save_preferences(value)

    # ---------- actions ----------
    def work(self, kind, function, *args):
        def run():
            try:
                self.events.put((kind, function(*args)))
            except Exception as error:
                self.events.put(('error', str(error)))
        Thread(target=run, daemon=True, name=f'labstudio-update-{kind}').start()

    def check(self, manual=True):
        if self.busy:
            return
        self.busy, self.manual = True, manual
        self.status.set(T('正在检查更新…'))
        self.work('checked', updater.check, APP_VERSION)

    def install(self):
        """Ask, then download and install the available release."""
        release = self.release
        if release is None or self.busy:
            return
        try:
            updater.installed_target()
        except ValueError as reason:
            question = T('版本 {version} 可以更新，但当前这份程序不能自动替换自己：\n{reason}\n\n要打开下载页面吗？').format(
                version=release['version'], reason=reason)
            if messagebox.askyesno(T('更新'), question, parent=self.app):
                webbrowser.open(updater.bundled_config().get('releases_page', ''))
            return
        notes = release['notes'].strip()[:2500]
        question = T('版本 {version} 可以更新。').format(version=release['version']) + (f'\n\n{notes}' if notes else '') + '\n\n' + \
            T('现在下载、安装并重启吗？\n重启前请先导出或保存正在处理的内容；原始数据文件不会被改动。')
        if not messagebox.askyesno(T('更新并重启'), question, parent=self.app):
            return
        self.busy, self.manual = True, True
        self.status.set(T('正在下载更新…'))
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] > 0.15 or done == total:
                self.events.put(('progress', (done, total)))
                last[0] = now
        self.work('downloaded', updater.download, release, progress)

    # ---------- event loop ----------
    def poll(self):
        self._poll_job = None
        try:
            while True:
                kind, value = self.events.get_nowait()
                getattr(self, f'on_{kind}')(value)
        except Empty:
            pass
        if self.installer and self.watch_installer():
            return  # the app is closing for the restart
        self._poll_job = self.app.after(100, self.poll)

    def on_error(self, message):
        self.busy = False
        self.installer = None
        self.status.set(T('更新失败：{error}').format(error=message))
        if self.manual:
            messagebox.showerror(T('更新'), message, parent=self.app)
        else:
            self.status.set(T('当前版本 {version}').format(version=APP_VERSION))  # offline at start-up: stay quiet

    def on_checked(self, release):
        self.busy = False
        self.release = release
        self.app.show_update_button(release)
        if release is None:
            self.status.set(T('当前版本 {version} 已是最新。').format(version=APP_VERSION))
            if self.manual:
                messagebox.showinfo(T('更新'), T('已经是最新版本。'), parent=self.app)
            return
        self.status.set(T('有新版本 {version} 可以更新。').format(version=release['version']))
        if self.manual:
            self.install()
        else:
            self.app.status(T('有新版本 {version} 可以更新，点右下角的按钮安装。').format(version=release['version']), 'busy', timeout=15000)

    def on_progress(self, value):
        done, total = value
        text = T('正在下载更新… {percent}%').format(percent=done * 100 // max(1, total))
        self.status.set(text)
        self.app.status(text, 'busy', timeout=None)

    def on_downloaded(self, package):
        self.status.set(T('正在准备安装…'))
        self.app.status(T('正在准备安装…'), 'busy', timeout=None)
        self.work('installer', updater.start_installer, package, self.release)

    def on_installer(self, value):
        process, ready = value
        self.installer = (process, ready, time.monotonic() + 180)
        self.status.set(T('正在校验并准备替换…'))

    def watch_installer(self):
        process, ready, deadline = self.installer
        if ready.exists():
            (ready.parent / 'install-approved').write_text('approved')
            self.app.status(T('正在重启以完成更新…'), 'busy', timeout=None)
            self.app.update_idletasks()
            self.app.close()  # the installer waits for this process to exit, then swaps and restarts
            return True
        if process.poll() is not None or time.monotonic() > deadline:
            if process.poll() is None:
                (ready.parent / 'cancel').write_text('cancelled')
            try:
                error = json.loads((ready.parent / 'result.json').read_text(encoding='utf-8')).get('error')
            except (OSError, ValueError):
                error = None
            self.on_error(error or T('安装程序没有准备好，详情见 {path}').format(path=ready.parent / 'installer.log'))
        return False
