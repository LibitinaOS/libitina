#!/usr/bin/env python3
"""Compact GTK 3 first-run setup wizard (configuration export)."""
import json
import os
import re
import secrets
import string
import subprocess
import tempfile
import threading
from urllib.error import URLError

from github_profile import fetch_profile
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf


class SetupWizard(Gtk.Window):
    def __init__(self):
        super().__init__(title='System Setup')
        self.set_default_size(640, 400)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.current_page = 0
        self.config = {
            'language': 'en_US', 'keyboard': 'us',
            'timezone': 'America/New_York', 'fullname': '',
            'username': '', 'password': '', 'hostname': '',
            'account_source': 'manual', 'github_username': '',
            'profile_picture': None, 'appearance': 'light'
        }
        self.pages = [self.page_welcome, self.page_language,
                      self.page_keyboard, self.page_timezone,
                      self.page_account, self.page_user, self.page_computer,
                      self.page_appearance, self.page_finish]
        self.loading_profile = False
        self.build_ui()
        self.show_page(0)

    def do_key_press_event(self, event):
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if event.keyval == Gdk.KEY_F10 and modifiers == Gdk.ModifierType.SHIFT_MASK:
            try:
                subprocess.Popen(['foot'], start_new_session=True)
            except OSError as exc:
                self.show_error(f'Could not open foot:\n{exc}')
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not modifiers:
            if self.next_button.is_sensitive():
                self.next_button.clicked()
            return True
        return Gtk.Window.do_key_press_event(self, event)

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
        message = Gtk.Label(
            label='Welcome to your new device running LibitinaOS! '
                  'Let’s get everything set up and ready for you.',
            xalign=0)
        message.set_line_wrap(True)
        self.content.pack_start(message, False, False, 0)

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
        self._keyboard_phrase_matched = False
        self.keyboard_test.connect('changed', self.on_keyboard_test_changed)
        self.add_field('Test your keyboard', self.keyboard_test)

    def on_keyboard_test_changed(self, entry):
        text = entry.get_text().replace(',', '').casefold().strip()
        matched = text == 'every day i imagine a future where i can be with you'
        already_matched = self._keyboard_phrase_matched
        self._keyboard_phrase_matched = matched
        if matched and not already_matched:
            dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL,
                                       message_type=Gtk.MessageType.INFO,
                                       buttons=Gtk.ButtonsType.OK, text='Just Monika')
            try:
                dialog.run()
            finally:
                dialog.destroy()

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

    def page_account(self):
        self.add_heading('How would you like to set up your account?')
        self.account_combo = self.create_combo([
            ('I have a GitHub account', 'github'),
            ('Enter my information manually', 'manual')], self.config['account_source'])
        self.add_field('Account details', self.account_combo)
        self.github_entry = Gtk.Entry(text=self.config['github_username'])
        self.add_field('GitHub username', self.github_entry)
        github_field = self.content.get_children()[-1]
        github_field.set_no_show_all(True)

        def update_github_field(combo):
            if combo.get_active_id() == 'github':
                github_field.show()
                for child in github_field.get_children():
                    child.show()
            else:
                github_field.hide()

        self.account_combo.connect('changed', update_github_field)
        update_github_field(self.account_combo)

    def load_github_profile(self, username):
        self.loading_profile = True
        self.next_button.set_sensitive(False)
        self.back_button.set_sensitive(False)
        self.progress.set_text('Downloading GitHub profile…')

        def worker():
            try:
                profile = fetch_profile(username)
                error = None
            except (OSError, ValueError, KeyError, TypeError, URLError) as exc:
                profile, error = None, str(exc)
            GLib.idle_add(complete, profile, error)

        def complete(profile, error):
            self.loading_profile = False
            self.next_button.set_sensitive(True)
            self.back_button.set_sensitive(True)
            self.progress.set_text(f'Step 5 of {len(self.pages)}')
            if error:
                self.show_error(f'Could not load GitHub profile. Check the username and connection, '
                                f'or enter your information manually.\n{error}')
                return False
            try:
                GdkPixbuf.Pixbuf.new_from_file_at_scale(profile['profile_picture'], 128, 128, True)
            except GLib.Error:
                Path(profile['profile_picture']).unlink(missing_ok=True)
                self.show_error('GitHub returned an unreadable profile picture. Please try again.')
                return False
            self.config.update(profile)
            self.show_page(self.current_page + 1)
            return False

        threading.Thread(target=worker, daemon=True).start()

    def page_user(self):
        if self.config['account_source'] == 'github':
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                self.config['profile_picture'], 128, 128, True)
            picture = Gtk.Image.new_from_pixbuf(pixbuf)
            picture.set_halign(Gtk.Align.CENTER)
            self.content.pack_start(picture, False, False, 0)
            self.add_heading(f"Hello, {self.config['fullname']}!")
            self.content.get_children()[-1].set_halign(Gtk.Align.CENTER)
            explanation = Gtk.Label(label=f'Your GitHub password is separate from your LibitinaOS '
                                   f'password. Set a password for LibitinaOS below.\n(For reference, your GitHub username is {self.config['github_username']})')
            explanation.set_line_wrap(True)
            explanation.set_max_width_chars(60)
            self.content.pack_start(explanation, False, False, 0)
        else:
            self.add_heading('Create your account')
            self.fullname_entry = Gtk.Entry(text=self.config['fullname'])
            self.add_field('Full name', self.fullname_entry)
            self.username_entry = Gtk.Entry(text=self.config['username'])
            self.add_field('Username', self.username_entry)
        self.password_entry = Gtk.Entry(text=self.config['password'])
        self.password_entry.set_visibility(False)
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.add_field('Password', self.password_entry)
        self.confirm_entry = Gtk.Entry(text=self.config['password'])
        self.confirm_entry.set_visibility(False)
        self.confirm_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.add_field('Confirm password', self.confirm_entry)

    def page_computer(self):
        self.add_heading('Name your computer')
        self.hostname_entry = Gtk.Entry(text=self.config['hostname'])
        self.add_field('Computer name', self.hostname_entry)

    def page_appearance(self):
        self.add_heading('Choose your appearance')
        self.appearance_combo = self.create_combo(
            [('Light', 'light'), ('Dark', 'dark')], self.config['appearance'])
        self.add_field('Color mode', self.appearance_combo)
        self.appearance_combo.connect('changed', self.update_appearance)
        self.update_appearance(self.appearance_combo)

    def update_appearance(self, combo):
        mode = combo.get_active_id()
        self.config['appearance'] = mode
        settings = Gtk.Settings.get_default()
        settings.set_property('gtk-theme-name',
                              'DeletescapeUi-Dark' if mode == 'dark' else 'DeletescapeUi')
        settings.set_property('gtk-application-prefer-dark-theme', mode == 'dark')

    def page_finish(self):
        self.add_heading('Ready to go')
        summary = ('Language: {language}\nKeyboard: {keyboard}\n'
                   'Timezone: {timezone}\nName: {fullname}\n'
                   'Username: {username}\nComputer: {hostname}\nAppearance: {appearance}').format(**self.config)
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
            source = self.account_combo.get_active_id()
            if source == 'github':
                username = self.github_entry.get_text().strip()
                if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?', username):
                    self.show_error('Enter a valid GitHub username.')
                    return False
                self.load_github_profile(username)
                return False
            if self.config['account_source'] != 'manual':
                self.config.update(fullname='', username='', github_username='', profile_picture=None)
            self.config['account_source'] = source
        elif self.current_page == 5:
            fullname = self.config['fullname']
            username = self.config['username']
            if self.config['account_source'] == 'manual':
                fullname = self.fullname_entry.get_text().strip()
                username = self.username_entry.get_text().strip()
            password = self.password_entry.get_text()
            if not fullname:
                self.show_error('Please enter your full name.')
                return False
            if (not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', username)
                    or username in {'root', 'defaultuser0'}):
                self.show_error('Enter a lowercase username of at most 32 characters (root and defaultuser0 are reserved).')
                return False
            if len(password) < 3:
                self.show_error('Password must be at least 3 characters.')
                return False
            if password != self.confirm_entry.get_text():
                self.show_error('Passwords do not match.')
                return False
            self.config['fullname'] = fullname
            self.config['username'] = username
            # The account provisioner needs plaintext for passwd; it must delete
            # setup.json after successfully applying the configuration.
            self.config['password'] = password
        elif self.current_page == 6:
            hostname = self.hostname_entry.get_text().strip()
            if not hostname:
                hostname = 'Libitina-' + ''.join(
                    secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', hostname, re.IGNORECASE):
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
        if self.loading_profile:
            return
        if self.current_page == len(self.pages) - 1:
            self.finish_setup()
        elif self.save_current_page():
            self.show_page(self.current_page + 1)

    def go_back(self, _button):
        if self.loading_profile:
            return
        if self.current_page > 0:
            # Preserve non-sensitive selections without blocking Back on validation.
            if self.current_page in (1, 2, 3):
                self.save_current_page()
            elif self.current_page == 5 and self.config['account_source'] == 'manual':
                self.config['fullname'] = self.fullname_entry.get_text().strip()
                self.config['username'] = self.username_entry.get_text().strip()
            elif self.current_page == 6:
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
        dialog.format_secondary_text('Your account and system settings will now be applied. '
                                     'The computer will restart when setup is finished.'
                                     if os.environ.get('USER') == 'defaultuser0' else
                                     f'Configuration saved to:\n{output}')
        dialog.run()
        dialog.destroy()
        Gtk.main_quit()


def confirm_restart_setup():
    dialog = Gtk.MessageDialog(
        flags=Gtk.DialogFlags.MODAL,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text='Provisioning LibitinaOS failed for an unknown reason. '
             'You can attempt to restart setup, but it is recommended that you '
             'reinstall LibitinaOS.')
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_deletable(False)
    dialog.add_button('Shut Down', Gtk.ResponseType.REJECT)
    dialog.add_button('Continue', Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.REJECT)
    try:
        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.ACCEPT:
                return True
            if response == Gtk.ResponseType.REJECT:
                try:
                    subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'poweroff'], check=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    error = Gtk.MessageDialog(
                        transient_for=dialog, flags=Gtk.DialogFlags.MODAL,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK, text='Could not shut down')
                    error.format_secondary_text(str(exc))
                    error.run()
                    error.destroy()
                else:
                    return False
    finally:
        dialog.destroy()


def main():
    if Path('/var/lib/libitina-setup-in-progress').exists() and not confirm_restart_setup():
        return
    window = SetupWizard()
    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
