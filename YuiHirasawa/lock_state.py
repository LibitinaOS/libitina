"""Share the running locker's lifetime with shell surfaces over the session bus."""

from gi.repository import Gio

LOCK_NAME = "org.libitina.Yui.LockScreen"


class LockPresence:
    """Publish lock state; D-Bus also releases it if the locker process exits."""

    def __init__(self):
        self._owner = Gio.bus_own_name(
            Gio.BusType.SESSION, LOCK_NAME, Gio.BusNameOwnerFlags.NONE,
            None, None, None)

    def close(self):
        if self._owner is not None:
            Gio.bus_unown_name(self._owner)
            self._owner = None


class LockStateMonitor:
    """Resolve existing lock state as well as subsequent lock/unlock changes."""

    def __init__(self, changed):
        self._changed = changed
        self._watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, LOCK_NAME, Gio.BusNameWatcherFlags.NONE,
            self._appeared, self._vanished)

    def _appeared(self, *_args):
        self._changed(True)

    def _vanished(self, *_args):
        self._changed(False)

    def close(self):
        if self._watch is not None:
            Gio.bus_unwatch_name(self._watch)
            self._watch = None
