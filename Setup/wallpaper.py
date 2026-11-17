#!/usr/bin/env python3
"""Standalone Wayland background and build watermark for the OOBE session."""

import json
from pathlib import Path
import platform
import signal
import subprocess

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell


def internal_watermark_text():
    """Match Yui's watermark without loading the shell or its user settings."""
    try:
        pretty_name = platform.freedesktop_os_release().get('PRETTY_NAME', 'Linux')
    except OSError:
        pretty_name = 'Linux'

    def git_value(*args):
        try:
            return subprocess.check_output(
                ['git', *args], cwd=Path(__file__).resolve().parent,
                stderr=subprocess.DEVNULL, text=True, timeout=2,
            ).strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    branch = git_value('rev-parse', '--abbrev-ref', 'HEAD')
    commit = git_value('rev-parse', 'HEAD')
    if branch is None or commit is None:
        try:
            metadata = json.loads(Path('/etc/libitina-info').read_text())
        except (OSError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        def stored_value(key):
            value = metadata.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else 'unknown'

        branch = branch or stored_value('git_branch')
        commit = commit or stored_value('git_commit')
    return f'{pretty_name}\n{platform.release()}\nb={branch} c={commit}\nI <3 Jamie Paige'


class SetupBackground:
    """Keep a background-layer surface on every connected monitor."""

    def __init__(self):
        self._display = Gdk.Display.get_default()
        self._windows = {}
        self._text = internal_watermark_text()
        self._handlers = [self._display.connect(name, self._monitors_changed)
                          for name in ('monitor-added', 'monitor-removed')]
        self._monitors_changed()

    def _monitors_changed(self, *_args):
        monitors = [self._display.get_monitor(i)
                    for i in range(self._display.get_n_monitors())]
        for monitor in list(self._windows):
            if monitor not in monitors:
                self._windows.pop(monitor).destroy()
        for monitor in monitors:
            if monitor in self._windows:
                continue
            window = Gtk.Window(title='Setup Background')
            window.set_decorated(False)
            window.set_accept_focus(False)
            window.set_name('setup-background')
            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_monitor(window, monitor)
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BACKGROUND)
            for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                         GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
                GtkLayerShell.set_anchor(window, edge, True)
            GtkLayerShell.set_exclusive_zone(window, -1)
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(window, 'libitina-setup-background')
            window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            window.connect('button-press-event', lambda *_: True)
            label = Gtk.Label(label=self._text, xalign=1)
            label.set_justify(Gtk.Justification.RIGHT)
            label.set_halign(Gtk.Align.END)
            label.set_valign(Gtk.Align.END)
            label.set_margin_end(8)
            label.set_margin_bottom(8)
            provider = Gtk.CssProvider()
            provider.load_from_data(
                b'#setup-background { background-color: #202020; }'
                b'label { color: white; font-size: 13px; text-shadow: 0 1px 2px black; }')
            for widget in (window, label):
                widget.get_style_context().add_provider(
                    provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window.add(label)
            window.show_all()
            self._windows[monitor] = window

    def close(self):
        for handler in self._handlers:
            self._display.disconnect(handler)
        for window in self._windows.values():
            window.destroy()
        self._windows.clear()


def main():
    background = SetupBackground()

    def stop():
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    signals = [GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
               for signum in (signal.SIGTERM, signal.SIGINT)]
    try:
        Gtk.main()
    finally:
        for source in signals:
            # A delivered signal removes its own source.
            if GLib.MainContext.default().find_source_by_id(source):
                GLib.source_remove(source)
        background.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
