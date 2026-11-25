"""Dismissible GTK warnings for shell components that fail to start."""

from gi.repository import GLib


def show_startup_warning(title: str, details: str) -> bool:
    """Show a warning from the GTK main loop and remove the idle source."""
    from gi.repository import Gtk

    dialog = Gtk.MessageDialog(
        title=title,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text=title,
    )
    dialog.format_secondary_text(details)
    dialog.add_button("Okay!", Gtk.ResponseType.OK)
    dialog.connect("response", lambda widget, _response: widget.destroy())
    dialog.show_all()
    return GLib.SOURCE_REMOVE
