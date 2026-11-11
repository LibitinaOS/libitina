"""Shared mobile safe-area layout in compositor logical pixels."""

from settings import load_config


def load_layout():
    return load_config()["mobile_layout"]


def inset_content(widget, layout, *, top=0, bottom=0, left=0, right=0):
    """Inset content while leaving its window background edge-to-edge."""
    widget.set_margin_top(layout["safe_inset_top"] + top)
    widget.set_margin_bottom(layout["safe_inset_bottom"] + bottom)
    widget.set_margin_start(layout["safe_inset_left"] + left)
    widget.set_margin_end(layout["safe_inset_right"] + right)
