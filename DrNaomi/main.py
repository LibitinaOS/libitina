#!/usr/bin/env python3

import os
import signal
import threading
import subprocess
import shlex

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk, GLib, Gdk
from systemd import journal


COREDUMP_MESSAGE_ID = "fc2e22bc6ee647b6b90729ab34a250b1"


class CrashDialog(Gtk.Dialog):
    def __init__(self, entry):
        super().__init__(title="Application Crash")

        self.entry = entry
        self.exe = str(entry.get("COREDUMP_EXE", ""))
        self.pid = entry.get("COREDUMP_PID", "Unknown")
        self.name = str(
            entry.get("COREDUMP_COMM")
            or os.path.basename(self.exe)
            or "Unknown application"
        )

        try:
            self.signal = signal.Signals(
                int(entry.get("COREDUMP_SIGNAL", 0))
            ).name
        except (ValueError, TypeError):
            self.signal = "Unknown"

        self.set_default_size(520, 240)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("dialog-error")
        self.set_resizable(True)
        self.set_border_width(12)
        self.set_urgency_hint(True)

        self.add_button("_Close", Gtk.ResponseType.CLOSE)

        self.add_button("Copy _Details", 100)

        if self.exe and os.access(self.exe, os.X_OK):
            self.add_button("_Restart Application", 101)

        self.set_default_response(Gtk.ResponseType.CLOSE)

        content = self.get_content_area()
        content.set_spacing(16)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16
        )

        icon = Gtk.Image.new_from_icon_name(
            "dialog-error",
            Gtk.IconSize.DIALOG
        )
        icon.set_valign(Gtk.Align.START)
        header.pack_start(icon, False, False, 0)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8
        )

        title = Gtk.Label()
        title.set_markup(
            "<span size='large' weight='bold'>"
            f"{GLib.markup_escape_text(self.name)} "
            "has crashed"
            "</span>"
        )
        title.set_xalign(0)
        title.set_line_wrap(True)

        description = Gtk.Label(
            label=(
                "The application terminated unexpectedly.\n"
                "You may restart the application or view "
                "technical information about the crash."
            )
        )
        description.set_xalign(0)
        description.set_line_wrap(True)

        text_box.pack_start(title, False, False, 0)
        text_box.pack_start(description, False, False, 0)

        header.pack_start(text_box, True, True, 0)
        content.pack_start(header, False, False, 0)

        grid = Gtk.Grid(
            column_spacing=16,
            row_spacing=6
        )

        fields = [
            ("Application:", self.name),
            ("Executable:", self.exe or "Unknown"),
            ("Process ID:", str(self.pid)),
            ("Signal:", self.signal),
        ]

        for row, (key, value) in enumerate(fields):
            key_label = Gtk.Label(label=key)
            key_label.set_xalign(0)

            value_label = Gtk.Label(label=value)
            value_label.set_xalign(0)
            value_label.set_selectable(True)
            value_label.set_line_wrap(True)

            grid.attach(key_label, 0, row, 1, 1)
            grid.attach(value_label, 1, row, 1, 1)

        content.pack_start(grid, False, False, 0)

        self.details = self.generate_details()

        expander = Gtk.Expander(
            label="Technical Details"
        )

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(220)
        scrolled.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC
        )

        details_view = Gtk.TextView()
        details_view.set_editable(False)
        details_view.set_cursor_visible(False)
        details_view.set_monospace(True)
        details_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        details_view.get_buffer().set_text(self.details)

        scrolled.add(details_view)
        expander.add(scrolled)

        content.pack_start(expander, True, True, 0)

        self.connect("response", self.on_response)
        self.show_all()

    def generate_details(self):
        lines = [
            "Application Crash Report",
            "=" * 50,
            f"Application: {self.name}",
            f"Executable: {self.exe}",
            f"PID: {self.pid}",
            f"Signal: {self.signal}",
            "",
            "Systemd Coredump Information",
            "=" * 50,
        ]

        for key in sorted(self.entry):
            if key.startswith("COREDUMP_"):
                value = self.entry[key]

                # Do not display binary dumps or environment
                # variables that may contain credentials.
                if key in (
                    "COREDUMP",
                    "COREDUMP_ENVIRON"
                ):
                    continue

                if isinstance(value, bytes):
                    continue

                lines.append(f"{key}: {value}")

        message = self.entry.get("MESSAGE")

        if message:
            lines.extend([
                "",
                "Journal Message",
                "=" * 50,
                str(message)
            ])

        return "\n".join(lines)

    def restart_application(self):
        command = self.entry.get("COREDUMP_CMDLINE", "")

        try:
            args = shlex.split(str(command))

            if args:
                args[0] = self.exe
            else:
                args = [self.exe]

            cwd = self.entry.get("COREDUMP_CWD")

            if not cwd or not os.path.isdir(str(cwd)):
                cwd = None

            subprocess.Popen(
                args,
                cwd=cwd,
                start_new_session=True
            )

            return True

        except (OSError, ValueError) as error:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Failed to restart application"
            )

            dialog.format_secondary_text(str(error))
            dialog.run()
            dialog.destroy()

            return False

    def on_response(self, dialog, response):
        if response == 100:
            clipboard = Gtk.Clipboard.get(
                Gdk.SELECTION_CLIPBOARD
            )
            clipboard.set_text(self.details, -1)
            clipboard.store()

        elif response == 101:
            if self.restart_application():
                self.destroy()

        else:
            self.destroy()


class CrashMonitor:
    def __init__(self):
        self.uid = os.getuid()

        self.reader = journal.Reader()
        self.reader.this_boot()

        self.reader.add_match(
            MESSAGE_ID=COREDUMP_MESSAGE_ID
        )

        self.reader.add_match(
            COREDUMP_UID=str(self.uid)
        )

        # Ignore crashes that occurred before startup.
        self.reader.seek_tail()
        self.reader.get_previous()

    def show_crash(self, entry):
        dialog = CrashDialog(entry)
        dialog.present()

        return GLib.SOURCE_REMOVE

    def monitor(self):
        print("Monitoring systemd-coredump events...")

        while True:
            event = self.reader.wait()

            if event in (journal.APPEND, journal.INVALIDATE):
                for entry in self.reader:
                    GLib.idle_add(
                        self.show_crash,
                        dict(entry)
                    )

    def run(self):
        thread = threading.Thread(
            target=self.monitor,
            daemon=True
        )

        thread.start()
        Gtk.main()


if __name__ == "__main__":
    monitor = CrashMonitor()
    monitor.run()