"""Power actions shared by the desktop launcher and both control centers."""

from gi.repository import GLib, Gtk

from backend import run_session_action
from shell_widgets import show_error_dialog


def create_power_menu(parent, on_success, on_error=None):
    """Build session actions with the same confirmations and error handling."""
    def activate(_item, label, command, confirm):
        if confirm:
            dialog = Gtk.MessageDialog(
                transient_for=parent,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.CANCEL,
                text=f"{label}?",
            )
            dialog.format_secondary_text("Any unsaved work may be lost.")
            dialog.add_button(label, Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
        try:
            run_session_action(command)
            on_success()
        except GLib.Error as exc:
            if on_error is not None:
                on_error(f"Could not {label.lower()}", exc.message)
            else:
                show_error_dialog(parent, f"Could not {label.lower()}", exc.message)

    menu = Gtk.Menu()
    for label, command, confirm in (
        ("Lock", ("loginctl", "lock-session"), False),
        ("Suspend", ("systemctl", "suspend"), True),
        ("Restart", ("systemctl", "reboot"), True),
        ("Power Off", ("systemctl", "poweroff"), True),
    ):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", activate, label, command, confirm)
        menu.append(item)
    menu.append(Gtk.SeparatorMenuItem())
    kill_shell = Gtk.MenuItem(label="Kill Shell")
    kill_shell.connect("activate", lambda _item: Gtk.main_quit())
    menu.append(kill_shell)
    menu.show_all()
    return menu
