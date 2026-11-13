#!/usr/bin/env python3
"""A small, touch-friendly Wayland lock screen for the Yui shell.
The password is checked against the current Unix user's password through PAM.
No password is stored by this program.  The PAM service defaults to
``login`` and can be overridden with the YUI_PAM_SERVICE environment variable.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import datetime as dt
import os
import pwd
import threading
from dataclasses import dataclass
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell

from settings import load_config
PAM_SUCCESS = 0
PAM_SYSTEM_ERR = 4
PAM_BUF_ERR = 5
PAM_CONV_ERR = 19
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4
class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]
class PamResponse(ctypes.Structure):
    # Keep resp as an opaque pointer: PAM takes ownership and frees it with
    # free(3), so exposing it as c_char_p would make ctypes copy its contents.
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]
PamConversationFunction = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(PamMessage)),
    ctypes.POINTER(ctypes.POINTER(PamResponse)),
    ctypes.c_void_p,
)
class PamConversation(ctypes.Structure):
    _fields_ = [("conv", PamConversationFunction), ("appdata_ptr", ctypes.c_void_p)]
@dataclass(frozen=True)
class AuthenticationResult:
    accepted: bool
    message: str
class PamAuthenticator:
    """Minimal libpam client for authenticating the current user."""
    def __init__(self, service: str = "login") -> None:
        library_name = ctypes.util.find_library("pam")
        if not library_name:
            raise RuntimeError("libpam is not installed")
        self._pam = ctypes.CDLL(library_name)
        self._libc = ctypes.CDLL(None)
        self._service = service.encode("utf-8")
        self._username = pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")
        self._pam.pam_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(PamConversation),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pam.pam_start.restype = ctypes.c_int
        self._pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_authenticate.restype = ctypes.c_int
        self._pam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_acct_mgmt.restype = ctypes.c_int
        self._pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_end.restype = ctypes.c_int
        self._pam.pam_strerror.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_strerror.restype = ctypes.c_char_p
        self._libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        self._libc.calloc.restype = ctypes.c_void_p
        self._libc.strdup.argtypes = [ctypes.c_char_p]
        self._libc.strdup.restype = ctypes.c_void_p
        self._libc.free.argtypes = [ctypes.c_void_p]
        self._libc.free.restype = None
    def authenticate(self, password: str) -> AuthenticationResult:
        if not password or "\0" in password:
            return AuthenticationResult(False, "Enter a password")
        secret = bytearray(password, "utf-8")
        handle = ctypes.c_void_p()
        callback = self._make_conversation(secret)
        conversation = PamConversation(callback, None)
        status = PAM_SYSTEM_ERR
        try:
            status = self._pam.pam_start(
                self._service,
                self._username,
                ctypes.byref(conversation),
                ctypes.byref(handle),
            )
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, self._error(handle, status))
            status = self._pam.pam_authenticate(handle, 0)
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, "Incorrect password")
            status = self._pam.pam_acct_mgmt(handle, 0)
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, self._error(handle, status))
            return AuthenticationResult(True, "Unlocked")
        finally:
            for index in range(len(secret)):
                secret[index] = 0
            if handle.value:
                self._pam.pam_end(handle, status)
    def _make_conversation(self, secret: bytearray) -> PamConversationFunction:
        libc = self._libc
        @PamConversationFunction
        def converse(message_count, messages, responses, _appdata):
            if message_count <= 0 or not messages or not responses:
                return PAM_CONV_ERR
            raw = libc.calloc(message_count, ctypes.sizeof(PamResponse))
            if not raw:
                return PAM_BUF_ERR
            reply_array = ctypes.cast(raw, ctypes.POINTER(PamResponse))
            for index in range(message_count):
                style = messages[index].contents.msg_style
                if style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                    duplicated = libc.strdup(bytes(secret))
                    if not duplicated:
                        for allocated in range(index):
                            if reply_array[allocated].resp:
                                libc.free(reply_array[allocated].resp)
                        libc.free(raw)
                        return PAM_BUF_ERR
                    reply_array[index].resp = duplicated
                elif style not in (PAM_ERROR_MSG, PAM_TEXT_INFO):
                    for allocated in range(index):
                        if reply_array[allocated].resp:
                            libc.free(reply_array[allocated].resp)
                    libc.free(raw)
                    return PAM_CONV_ERR
            responses[0] = reply_array
            return PAM_SUCCESS
        return converse
    def _error(self, handle: ctypes.c_void_p, status: int) -> str:
        text = self._pam.pam_strerror(handle, status)
        return text.decode("utf-8", errors="replace") if text else "PAM error"
class LockPageLayout(Gtk.Layout):
    """Allocate both pages before GTK lays out their contents and input windows."""

    def __init__(self, position, prepare_resize):
        super().__init__()
        self._position = position
        self._prepare_resize = prepare_resize
        position.connect("value-changed", self._position_changed)

    def _position_changed(self, *_args):
        self.queue_allocate()
        self.queue_draw()

    def do_size_allocate(self, allocation):
        self._prepare_resize(self, allocation)
        width, height = max(1, allocation.width), max(1, allocation.height)
        # Keep GTK's scrollable area at viewport size. The gesture owns a
        # separate position, so focus and keyboard resizes cannot scroll it.
        self.set_size(width, height)
        position = round(self._position.get_value())
        for index, page in enumerate(self.get_children()):
            page.set_size_request(width, height)
            self.move(page, 0, index * height - position)
        Gtk.Layout.do_size_allocate(self, allocation)


class LockScreen(Gtk.Window):
    MAX_PASSWORD_LENGTH = 0
    def __init__(self, authenticator: PamAuthenticator) -> None:
        super().__init__(title="Yui lock screen")
        self._desktop = load_config()["shell"] == "desktop"
        self._authenticator = authenticator
        self._authenticating = False
        self._keyboard_request = 0
        self._closing = False
        self.connect("destroy", self._on_destroy)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.connect("delete-event", lambda *_args: True)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_namespace(self, "yui-locker")
        # Respect the keyboard's exclusive zone so it remains visible and usable.
        GtkLayerShell.set_exclusive_zone(self, 0)
        self.set_name("yui-lock-screen")
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
            #yui-lock-screen { background: #101923; color: white; }
            #yui-lock-screen label { color: white; text-shadow: 0 2px 8px rgba(0,0,0,0.6); }
            #yui-lock-screen .lock-shade { background: rgba(5,12,22,0.45); }
            #yui-lock-screen .lock-clock { font-size: 88px; font-weight: 300; }
            #yui-lock-screen .lock-date { font-size: 22px; }
            #yui-lock-screen .lock-hint { font-size: 15px; }
            #yui-lock-screen .lock-username { font-size: 24px; font-weight: 500; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.connect("destroy", lambda *_: Gtk.StyleContext.remove_provider_for_screen(
            self.get_screen(), provider))
        from wallpaper import Wallpaper
        overlay = Gtk.Overlay()
        overlay.add(Wallpaper(lock_screen=True))
        self.add(overlay)
        self._page = 0
        self._page_height = 1
        self._page_size = None
        self._snap_source = None
        self._drag_claimed = False
        self._drag_moved = False
        self._drag_active = False
        self._drag_dy = 0
        self._offset = Gtk.Adjustment()
        self._pages = LockPageLayout(self._offset, self._layout_pages)
        self._pages.get_style_context().add_class("lock-shade")
        overlay.add_overlay(self._pages)
        self.connect("unmap", self._on_unmap)

        cover = Gtk.EventBox()
        cover.set_visible_window(False)
        cover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cover_box.set_border_width(24)
        cover.add(cover_box)
        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic", Gtk.IconSize.DIALOG)
        lock_icon.set_margin_top(24)
        cover_box.pack_start(lock_icon, False, False, 0)
        clock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clock.set_valign(Gtk.Align.CENTER)
        self._clock_label = Gtk.Label()
        self._clock_label.get_style_context().add_class("lock-clock")
        self._date_label = Gtk.Label()
        self._date_label.get_style_context().add_class("lock-date")
        clock.pack_start(self._clock_label, False, False, 0)
        clock.pack_start(self._date_label, False, False, 0)
        cover_box.pack_start(clock, True, True, 0)
        hint = Gtk.Label(label="⌃\nSwipe up or click to unlock" if self._desktop
                         else "⌃\nSwipe up to unlock")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.get_style_context().add_class("lock-hint")
        cover_box.pack_end(hint, False, False, 12)
        self._cover = cover
        self._pages.put(cover, 0, 0)
        # Match home-page gestures: capture, claim after 10px, follow the
        # pointer, then settle with the same 220ms cubic easing.
        self._drag = Gtk.GestureDrag.new(self)
        self._drag.set_button(Gdk.BUTTON_PRIMARY)
        self._drag.set_touch_only(False)
        self._drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._drag.connect("drag-begin", self._on_drag_begin)
        self._drag.connect("drag-update", self._on_drag_update)
        self._drag.connect("drag-end", self._on_drag_end)
        self._drag.connect("cancel", self._on_drag_cancel)
        self.connect("key-press-event", self._on_key_press)

        password_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        password_page.set_border_width(24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_size_request(260, -1)
        password_page.pack_start(box, True, True, 0)
        if self._desktop:
            username = Gtk.Label(label=pwd.getpwuid(os.getuid()).pw_name)
            username.get_style_context().add_class("lock-username")
            box.pack_start(username, False, False, 8)
        self._entry = Gtk.Entry()
        self._entry.set_visibility(False)
        self._entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._entry.set_max_length(self.MAX_PASSWORD_LENGTH)
        self._entry.set_placeholder_text("Password")
        self._entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
        self._entry.connect("changed", self._on_entry_changed)
        self._entry.connect("activate", self._submit)
        box.pack_start(self._entry, False, False, 0)
        keyboard = Gtk.Button(label="On-screen keyboard")
        keyboard.set_can_focus(False)
        keyboard.connect("clicked", self._show_keyboard)
        box.pack_start(keyboard, False, False, 0)
        self._submit_button = Gtk.Button(label="Unlock")
        self._submit_button.set_sensitive(False)
        self._submit_button.connect("clicked", self._submit)
        box.pack_start(self._submit_button, False, False, 0)
        self._status = Gtk.Label(label="")
        box.pack_start(self._status, False, False, 0)
        self._status.set_line_wrap(True)
        self._status.set_max_width_chars(32)
        self._status.set_justify(Gtk.Justification.CENTER)
        back = Gtk.Button(label="Back")
        back.connect("clicked", self._show_clock)
        box.pack_start(back, False, False, 0)
        self._password_page = password_page
        self._password_page.set_sensitive(False)
        self._pages.put(password_page, 0, 1)
        self._update_clock()
        timer = GLib.timeout_add_seconds(1, self._update_clock)
        self.connect("destroy", lambda *_: GLib.source_remove(timer))

    def _layout_pages(self, _widget, allocation):
        size = (max(1, allocation.width), max(1, allocation.height))
        if size == self._page_size:
            return
        # The keyboard can change the viewport while a finger is still down
        # or a snap is running. Preserve progress instead of jumping to _page.
        progress = self._offset.get_value() / self._page_height
        snapping = self._snap_source is not None
        self._page_size = size
        _width, self._page_height = size
        self._stop_snap()
        moving = self._drag_active or snapping
        position = progress * self._page_height if moving else self._page * self._page_height
        self._offset.configure(position, 0,
                               self._page_height * 2, 1, self._page_height, self._page_height)
        if self._drag_active:
            self._drag_start = position + self._drag_dy
        elif snapping:
            self._start_snap(position, self._page * self._page_height)
        # Never grab focus from size allocation: the old child geometry is
        # still installed here, and the input method can trigger another resize.

    def _stop_snap(self):
        if self._snap_source is not None:
            self._pages.remove_tick_callback(self._snap_source)
            self._snap_source = None

    def _on_unmap(self, *_args):
        self._drag.reset()
        self._drag_active = False
        self._stop_snap()
        self._offset.set_value(self._page * self._page_height)

    def _on_drag_begin(self, _gesture, _x, _y):
        if self._authenticating:
            return
        self._stop_snap()
        self._drag_active = True
        self._drag_claimed = False
        self._drag_moved = False
        self._drag_dy = 0
        self._drag_start = self._offset.get_value()
        self._drag_start_page = int(self._drag_start / self._page_height + 0.5)

    def _on_drag_update(self, gesture, dx, dy):
        if self._authenticating:
            return
        self._drag_dy = dy
        if max(abs(dx), abs(dy)) >= 10:
            self._drag_moved = True
        if not self._drag_claimed and abs(dy) >= 10 and abs(dy) > abs(dx):
            self._drag_claimed = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._password_page.set_sensitive(False)
            self.set_focus(None)
            self._set_keyboard_visible(False)
        if self._drag_claimed:
            self._offset.set_value(max(0, min(self._drag_start - dy, self._page_height)))

    def _on_drag_end(self, _gesture, dx, dy):
        if self._authenticating:
            return
        if self._drag_claimed:
            target = int(self._offset.get_value() / self._page_height + 0.5)
            if target == self._drag_start_page and abs(dy) >= max(48, self._page_height * 0.2):
                target += 1 if dy < 0 else -1
            target = max(0, min(1, target))
        elif self._desktop and not self._drag_moved and max(abs(dx), abs(dy)) < 10 and self._page == 0:
            target = 1
        else:
            target = self._page
        settle = (self._drag_claimed or target != self._page
                  or abs(self._offset.get_value() - target * self._page_height) > 0.5)
        self._drag_active = False
        self._drag_claimed = False
        self._drag_moved = False
        # A captured tap belongs to the child button/entry. Disabling its
        # parent, even briefly, cancels GTK's pending release/click handler.
        if settle:
            self._set_page(target)

    def _on_drag_cancel(self, *_args):
        settle = (self._drag_claimed
                  or abs(self._offset.get_value() - self._page * self._page_height) > 0.5)
        self._drag_active = False
        self._drag_claimed = False
        self._drag_moved = False
        # Child controls can claim the sequence themselves; leave their
        # sensitivity and focus alone when no page motion needs settling.
        if settle:
            self._set_page(self._page)

    def _set_page(self, page, *, animate=True):
        self._page = page
        self._stop_snap()
        self._password_page.set_sensitive(False)
        if page == 0:
            self._entry.set_text("")
            self._status.set_text("")
            self.set_focus(None)
            self._set_keyboard_visible(False)
        start = self._offset.get_value()
        target = page * self._page_height
        if animate and self.get_mapped() and abs(target - start) > 0.5:
            self._start_snap(start, target)
        else:
            self._offset.set_value(target)
            self._finish_page()

    def _start_snap(self, start, target):
        self._snap_start = start
        self._snap_target = target
        self._snap_started = GLib.get_monotonic_time()
        self._snap_source = self._pages.add_tick_callback(self._animate_snap)

    def _animate_snap(self, _widget, frame_clock):
        progress = max(0.0, min(1.0, (frame_clock.get_frame_time() - self._snap_started) / 220_000))
        eased = 1 - (1 - progress) ** 3
        self._offset.set_value(self._snap_start + (self._snap_target - self._snap_start) * eased)
        if progress >= 1:
            self._snap_source = None
            self._finish_page()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _finish_page(self):
        # Pin the endpoint before allowing entry focus / keyboard activation.
        self._offset.set_value(self._page * self._page_height)
        self._password_page.set_sensitive(self._page == 1)
        if self._page == 1 and not self._authenticating:
            self._entry.grab_focus()
            self._entry.set_position(-1)

    def _show_password(self):
        self._set_page(1)

    def _show_clock(self, *_args):
        if not self._authenticating:
            self._set_page(0)

    def _on_key_press(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self._show_clock()
            return True
        if self._page == 0:
            if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                self._show_password()
                return True
            if self._desktop and event.string and event.string.isprintable():
                self._set_page(1, animate=False)
                self._entry.set_text(event.string)
                self._entry.set_position(-1)
                return True
        return False
    def _show_keyboard(self, _button: Gtk.Button) -> None:
        self._entry.grab_focus()
        self._set_keyboard_visible(True)

    def _set_keyboard_visible(self, visible):
        self._keyboard_request += 1
        request = (self._keyboard_request, visible)
        Gio.bus_get(Gio.BusType.SESSION, None, self._keyboard_bus_ready, request)

    def _on_destroy(self, *_args):
        self._closing = True
        self._keyboard_request += 1

    def _keyboard_bus_ready(self, _source, result, request) -> None:
        serial, visible = request
        try:
            bus = Gio.bus_get_finish(result)
            if self._closing or serial != self._keyboard_request:
                return
            if visible and (self._page != 1 or self._drag_active or self._snap_source is not None):
                return
            bus.call(
                "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible",
                GLib.Variant("(b)", (visible,)), None, Gio.DBusCallFlags.NONE,
                3000, None, self._keyboard_shown, request,
            )
        except GLib.Error:
            if visible and not self._closing and serial == self._keyboard_request:
                self._status.set_text("On-screen keyboard unavailable. Start squeekboard first.")

    def _keyboard_shown(self, bus, result, request) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error:
            serial, visible = request
            if visible and not self._closing and serial == self._keyboard_request:
                self._status.set_text("On-screen keyboard unavailable. Start squeekboard first.")

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        if not self._authenticating:
            self._status.set_text("")
        self._submit_button.set_sensitive(
            bool(entry.get_text()) and not self._authenticating
        )
    def _submit(self, _widget: Gtk.Widget) -> None:
        if self._authenticating:
            return
        password = self._entry.get_text()
        if not password:
            return
        self._authenticating = True
        self._entry.set_sensitive(False)
        self._submit_button.set_sensitive(False)
        self._status.set_text("Checking...")
        threading.Thread(
            target=self._authenticate_worker,
            args=(password,),
            name="YuiPamAuthentication",
            daemon=True,
        ).start()
    def _authenticate_worker(self, password: str) -> None:
        try:
            result = self._authenticator.authenticate(password)
        except Exception as error:
            result = AuthenticationResult(False, f"Authentication error: {error}")
        GLib.idle_add(self._authentication_finished, result)
    def _authentication_finished(self, result: AuthenticationResult) -> bool:
        self._authenticating = False
        if result.accepted:
            self.destroy()
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE
        self._entry.set_text("")
        self._entry.set_sensitive(True)
        self._status.set_text(result.message)
        self._entry.grab_focus()
        return GLib.SOURCE_REMOVE
    def _update_clock(self) -> bool:
        now = dt.datetime.now().astimezone()
        self._clock_label.set_text(now.strftime("%H:%M"))
        self._date_label.set_text(now.strftime("%a, %b %-d"))
        return GLib.SOURCE_CONTINUE
def main() -> int:
    from lock_state import LockPresence

    service = os.environ.get("YUI_PAM_SERVICE", "login")
    try:
        authenticator = PamAuthenticator(service)
    except RuntimeError as error:
        print(f"locker: {error}", file=os.sys.stderr)
        return 1
    presence = LockPresence()
    try:
        locker = LockScreen(authenticator)
        locker.show_all()
        Gtk.main()
    finally:
        presence.close()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
