#!/usr/bin/env python3
"""Compact GTK 3 first-run setup wizard (configuration preview only)."""
import json
import os
import re
import tempfile
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib


class SetupWizard(Gtk.Window):
    def __init__(self):
        super().__init__(title='System Setup')
        self.set_default_size(640, 400)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.current_page = 0
        self.config = {
            'language': 'en_US', 'keyboard': 'us',
            'timezone': 'America/New_York', 'fullname': '',
            'username': '', 'hostname': ''
        }
        self.pages = [self.page_welcome, self.page_language,
                      self.page_keyboard, self.page_timezone,
                      self.page_user, self.page_computer, self.page_finish]
        self.build_ui()
        self.show_page(0)

    def build_ui(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main)
        header = Gtk.HeaderBar(title='System Setup')
        header.set_show_close_button(True)
        self.set_titlebar(header)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content.set_border_width(20)
        main.pack_start(self.content, True, True, 0)
        main.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_border_width(12)
        self.progress = Gtk.Label(xalign=0)
        nav.pack_start(self.progress, True, True, 0)
        self.back_button = Gtk.Button(label='Back')
        self.back_button.connect('clicked', self.go_back)
        nav.pack_start(self.back_button, False, False, 0)
        self.next_button = Gtk.Button(label='Next')
        self.next_button.get_style_context().add_class('suggested-action')
        self.next_button.connect('clicked', self.go_next)
        nav.pack_start(self.next_button, False, False, 0)
        main.pack_end(nav, False, False, 0)

    def clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def add_heading(self, title):
        heading = Gtk.Label(xalign=0)
        heading.set_markup('<span size="x-large" weight="bold">' +
                           GLib.markup_escape_text(title) + '</span>')
        self.content.pack_start(heading, False, False, 0)

    def add_field(self, label, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text = Gtk.Label(label=label, xalign=0)
        box.pack_start(text, False, False, 0)
        box.pack_start(widget, False, False, 0)
        self.content.pack_start(box, False, False, 0)

    @staticmethod
    def create_combo(options, selected):
        combo = Gtk.ComboBoxText()
        for name, value in options:
            combo.append(value, name)
        combo.set_active_id(selected)
        return combo

    def page_welcome(self):
        self.add_heading('Welcome')

    def page_language(self):
        self.add_heading('Choose your language')
        self.language_combo = self.create_combo([
            ('English (United States)', 'en_US'), ('English (United Kingdom)', 'en_GB'),
            ('Español', 'es_ES'), ('Français', 'fr_FR'), ('Deutsch', 'de_DE'),
            ('Italiano', 'it_IT'), ('Português (Brasil)', 'pt_BR'),
            ('日本語', 'ja_JP'), ('简体中文', 'zh_CN')], self.config['language'])
        self.add_field('System language', self.language_combo)

    def page_keyboard(self):
        self.add_heading('Keyboard layout')
        self.keyboard_combo = self.create_combo([
            ('English (US)', 'us'), ('English (UK)', 'gb'), ('Spanish', 'es'),
            ('French', 'fr'), ('German', 'de'), ('Italian', 'it'),
            ('Japanese', 'jp')], self.config['keyboard'])
        self.add_field('Keyboard layout', self.keyboard_combo)
        self.keyboard_test = Gtk.Entry()
        self.add_field('Test your keyboard', self.keyboard_test)

    def page_timezone(self):
        self.add_heading('Date and time')
        self.timezone_combo = self.create_combo([
            ('New York', 'America/New_York'), ('Chicago', 'America/Chicago'),
            ('Denver', 'America/Denver'), ('Los Angeles', 'America/Los_Angeles'),
            ('London', 'Europe/London'), ('Paris', 'Europe/Paris'),
            ('Berlin', 'Europe/Berlin'), ('Tokyo', 'Asia/Tokyo'),
            ('Shanghai', 'Asia/Shanghai'), ('Sydney', 'Australia/Sydney'),
            ('UTC', 'UTC')], self.config['timezone'])
        self.add_field('Timezone', self.timezone_combo)

    def page_user(self):
        self.add_heading('Create your account')
        self.fullname_entry = Gtk.Entry(text=self.config['fullname'])
        self.add_field('Full name', self.fullname_entry)
        self.username_entry = Gtk.Entry(text=self.config['username'])
        self.add_field('Username', self.username_entry)
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.add_field('Password', self.password_entry)
        self.confirm_entry = Gtk.Entry()
        self.confirm_entry.set_visibility(False)
        self.confirm_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.add_field('Confirm password', self.confirm_entry)

    def page_computer(self):
        self.add_heading('Name your computer')
        self.hostname_entry = Gtk.Entry(text=self.config['hostname'])
        self.add_field('Computer name', self.hostname_entry)

    def page_finish(self):
        self.add_heading('Ready to go')
        summary = ('Language: {language}\nKeyboard: {keyboard}\n'
                   'Timezone: {timezone}\nName: {fullname}\n'
                   'Username: {username}\nComputer: {hostname}').format(**self.config)
        label = Gtk.Label(label=summary, xalign=0, yalign=0)
        label.set_selectable(True)
        self.content.pack_start(label, False, False, 0)

    def show_error(self, message):
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK, text=message)
        dialog.run()
        dialog.destroy()

    def save_current_page(self):
        if self.current_page == 1:
            self.config['language'] = self.language_combo.get_active_id()
        elif self.current_page == 2:
            self.config['keyboard'] = self.keyboard_combo.get_active_id()
        elif self.current_page == 3:
            self.config['timezone'] = self.timezone_combo.get_active_id()
        elif self.current_page == 4:
            fullname = self.fullname_entry.get_text().strip()
            username = self.username_entry.get_text().strip()
            password = self.password_entry.get_text()
            if not fullname:
                self.show_error('Please enter your full name.')
                return False
            if not re.fullmatch(r'[a-z_][a-z0-9_-]*', username):
                self.show_error('Enter a valid lowercase username.')
                return False
            if len(password) < 8:
                self.show_error('Password must be at least 8 characters.')
                return False
            if password != self.confirm_entry.get_text():
                self.show_error('Passwords do not match.')
                return False
            self.config['fullname'] = fullname
            self.config['username'] = username
            # Passwords are not stored or applied by this demo.
        elif self.current_page == 5:
            hostname = self.hostname_entry.get_text().strip().lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', hostname):
                self.show_error('Enter a valid hostname (1–63 characters).')
                return False
            self.config['hostname'] = hostname
        return True

    def show_page(self, index):
        self.current_page = index
        self.clear_content()
        self.pages[index]()
        self.progress.set_text(f'Step {index + 1} of {len(self.pages)}')
        self.back_button.set_sensitive(index > 0)
        self.next_button.set_label('Finish' if index == len(self.pages) - 1 else 'Next')
        self.content.show_all()

    def go_next(self, _button):
        if self.current_page == len(self.pages) - 1:
            self.finish_setup()
        elif self.save_current_page():
            self.show_page(self.current_page + 1)

    def go_back(self, _button):
        if self.current_page > 0:
            # Preserve non-sensitive selections without blocking Back on validation.
            if self.current_page in (1, 2, 3):
                self.save_current_page()
            elif self.current_page == 4:
                self.config['fullname'] = self.fullname_entry.get_text().strip()
                self.config['username'] = self.username_entry.get_text().strip()
            elif self.current_page == 5:
                self.config['hostname'] = self.hostname_entry.get_text().strip()
            self.show_page(self.current_page - 1)

    def finish_setup(self):
        directory = Path.home() / '.config' / 'os-setup'
        temp_name = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix='.setup-', suffix='.tmp', dir=str(directory))
            os.chmod(temp_name, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.config, stream, indent=4, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            output = directory / 'setup.json'
            os.replace(temp_name, output)
        except OSError as exc:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
            self.show_error(f'Could not save configuration:\n{exc}')
            return
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text='Setup complete')
        dialog.format_secondary_text(f'Configuration saved to:\n{output}')
        dialog.run()
        dialog.destroy()
        Gtk.main_quit()


if __name__ == '__main__':
    window = SetupWizard()
    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    Gtk.main()
