
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkVnc", "2.0")

from gi.repository import Gdk, Gtk, GtkVnc


# Quality settings (applied before connecting; restart after changing).
# Depth options: FULL, MEDIUM, LOW, ULTRA_LOW, or DEFAULT (server default).
# Lower color depths reduce bandwidth at the expense of color fidelity.
COLOR_DEPTH = GtkVnc.DisplayDepthColor.FULL
# Allow JPEG/lossy encodings to reduce bandwidth when supported by the server.
ALLOW_LOSSY_ENCODING = False
# Smooth locally scaled pixels; False keeps hard pixel edges.
SMOOTH_SCALING = True


class VNCViewer(Gtk.Window):
    def __init__(self, host="172.16.42.1", port=5900):
        super().__init__(title="PyGObject VNC Viewer")

        self.set_default_size(1024, 768)
        self.connect("destroy", Gtk.main_quit)

        # Create the VNC display widget
        self.display = GtkVnc.Display()
        self.display.set_depth(COLOR_DEPTH)
        self.display.set_lossy_encoding(ALLOW_LOSSY_ENCODING)
        self.display.set_smoothing(SMOOTH_SCALING)

        # Enable keyboard and mouse input
        self.display.set_read_only(False)

        # Keep the server's resolution and scale locally when the user resizes.
        self.display.set_allow_resize(False)
        self.display.set_force_size(False)
        self.display.set_scaling(True)
        self.display.set_keep_aspect_ratio(True)

        # Add display to the GTK window
        self.add(self.display)

        # Connect VNC signals
        self.display.connect("vnc-connected", self.on_connected)
        self.display.connect("vnc-initialized", self.on_initialized)
        self.display.connect("vnc-desktop-resize", self.on_desktop_resize)
        self.display.connect("vnc-disconnected", self.on_disconnected)
        self.display.connect("vnc-auth-credential", self.on_auth_credential)

        # Show the window
        self.show_all()

        # Connect to the VNC server
        self.display.open_host(host, str(port))

    def on_connected(self, display):
        print("Connected to VNC server!")

    def on_initialized(self, display):
        self.on_desktop_resize(display, display.get_width(), display.get_height())

    def on_desktop_resize(self, display, width, height):
        if width <= 0 or height <= 0:
            return

        # Follow the session's dimensions and constrain subsequent window resizes.
        geometry = Gdk.Geometry()
        geometry.min_aspect = geometry.max_aspect = width / height
        self.set_geometry_hints(None, geometry, Gdk.WindowHints.ASPECT)
        self.resize(width, height)

    def on_disconnected(self, display):
        print("Disconnected from VNC server.")

    def on_auth_credential(self, display, credentials):
        print("VNC authentication required:", credentials)


if __name__ == "__main__":
    viewer = VNCViewer("172.16.42.1", 5900)
    Gtk.main()
