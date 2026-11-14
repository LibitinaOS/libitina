import importlib.util
import inspect
import json
import os
import re
from dataclasses import dataclass


_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class WidgetManifest:
    widget_id: str
    name: str
    author: str | None = None
    description: str | None = None
    url: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class WidgetSpec:
    widget_id: str
    folder_name: str
    manifest: WidgetManifest
    html: str
    css: str
    js: str


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_module_from_path(module_name: str, path: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to create spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call_register(register_fn, *, app, url_prefix: str) -> None:
    try:
        sig = inspect.signature(register_fn)
    except Exception:
        register_fn(app, url_prefix)
        return

    params = list(sig.parameters.values())
    if len(params) <= 1:
        register_fn(app)
        return

    try:
        register_fn(app, url_prefix)
    except TypeError:
        register_fn(app=app, url_prefix=url_prefix)


def _manifest_from_dict(widget_id: str, d: dict) -> WidgetManifest:
    def _opt_str(v):
        return v.strip() if isinstance(v, str) and v.strip() else None

    name = _opt_str(d.get("name")) or widget_id
    return WidgetManifest(
        widget_id=widget_id,
        name=name,
        author=_opt_str(d.get("author")),
        description=_opt_str(d.get("description")),
        url=_opt_str(d.get("url")),
        version=_opt_str(d.get("version")),
    )


def load_widgets(*, app, widgets_dir: str) -> list[WidgetSpec]:
    """Discover and load widgets from ./widgets.

    Required per widget folder:
      - widgets/<id>/manifest.json
      - widgets/<id>/main.py

    Backend contract (main.py):
      - Optional: def get_widget() -> dict with keys: html/css/js/name
      - Optional: def register(app, url_prefix="/widgets/<id>")

    Static assets (optional):
      - widgets/<id>/static/... served at /widgets-static/<id>/...
    """

    out: list[WidgetSpec] = []
    if not widgets_dir or not os.path.isdir(widgets_dir):
        return out

    for entry in sorted(os.listdir(widgets_dir)):
        if entry.startswith("."):
            continue

        folder_name = entry
        widget_id = entry.strip().lower()
        if not _ID_RE.match(widget_id):
            continue

        widget_path = os.path.join(widgets_dir, folder_name)
        if not os.path.isdir(widget_path):
            continue

        manifest_path = os.path.join(widget_path, "manifest.json")
        backend_path = os.path.join(widget_path, "main.py")
        if not os.path.exists(manifest_path) or not os.path.exists(backend_path):
            continue

        manifest = _manifest_from_dict(widget_id, _read_json(manifest_path))
        html = ""
        css = ""
        js = ""

        module_name = f"widgets_{widget_id}"
        try:
            mod = _load_module_from_path(module_name, backend_path)
        except Exception as e:
            out.append(
                WidgetSpec(
                    widget_id=widget_id,
                    folder_name=folder_name,
                    manifest=manifest,
                    html=(
                        "<div class=\"small error\">"
                        + f"Widget backend failed to load: {type(e).__name__}: {str(e)}"
                        + "</div>"
                    ),
                    css="",
                    js="",
                )
            )
            continue

        get_widget = getattr(mod, "get_widget", None)
        if callable(get_widget):
            try:
                payload = get_widget()
            except Exception as e:
                payload = {
                    "html": (
                        "<div class=\"small error\">"
                        + f"Widget get_widget() failed: {type(e).__name__}: {str(e)}"
                        + "</div>"
                    ),
                    "css": "",
                    "js": "",
                }

            if isinstance(payload, dict):
                if isinstance(payload.get("name"), str) and payload["name"].strip():
                    manifest = WidgetManifest(
                        widget_id=manifest.widget_id,
                        name=payload["name"].strip(),
                        author=manifest.author,
                        description=manifest.description,
                        url=manifest.url,
                        version=manifest.version,
                    )
                if isinstance(payload.get("html"), str):
                    html = payload.get("html") or ""
                if isinstance(payload.get("css"), str):
                    css = payload.get("css") or ""
                if isinstance(payload.get("js"), str):
                    js = payload.get("js") or ""

        register = getattr(mod, "register", None)
        if callable(register):
            try:
                _call_register(register, app=app, url_prefix=f"/widgets/{widget_id}")
            except Exception:
                pass

        out.append(
            WidgetSpec(
                widget_id=widget_id,
                folder_name=folder_name,
                manifest=manifest,
                html=html,
                css=css,
                js=js,
            )
        )

    return out


def apply_widget_settings(widgets: list[WidgetSpec], settings: dict | None) -> list[WidgetSpec]:
    enabled_map: dict[str, bool] = {}
    order: list[str] = []

    if isinstance(settings, dict):
        enabled = settings.get("enabled")
        if isinstance(enabled, dict):
            for k, v in enabled.items():
                if isinstance(k, str):
                    wid = k.strip().lower()
                    if _ID_RE.match(wid):
                        enabled_map[wid] = bool(v)

        raw_order = settings.get("order")
        if isinstance(raw_order, list):
            for item in raw_order:
                if isinstance(item, str):
                    wid = item.strip().lower()
                    if _ID_RE.match(wid) and wid not in order:
                        order.append(wid)

    by_id = {w.widget_id: w for w in widgets}

    def is_enabled(w: WidgetSpec) -> bool:
        return enabled_map.get(w.widget_id, True)

    out: list[WidgetSpec] = []
    for wid in order:
        w = by_id.get(wid)
        if w and is_enabled(w):
            out.append(w)

    for w in widgets:
        if w.widget_id in order:
            continue
        if is_enabled(w):
            out.append(w)

    return out


def render_widget_bundle(widgets: list[WidgetSpec]) -> tuple[str, str, str]:
    widgets_html_parts: list[str] = []
    css_parts: list[str] = []
    js_parts: list[str] = []

    for w in widgets:
        widgets_html_parts.append(
            "\n".join(
                [
                    f'<div class="widget" id="widget-{w.widget_id}" data-widget="{w.widget_id}">',
                    f'  <div class="widget-body" id="widget-{w.widget_id}-body" data-widget-body="{w.widget_id}">',
                    (w.html or ""),
                    "  </div>",
                    "</div>",
                ]
            )
        )
        if w.css:
            css_parts.append(f"\n/* widget:{w.widget_id} */\n{w.css}\n")
        if w.js:
            js_parts.append(
                "\n".join(
                    [
                        "<script>",
                        f"// widget:{w.widget_id}",
                        w.js,
                        "</script>",
                    ]
                )
            )

    return ("\n".join(widgets_html_parts), "\n".join(css_parts), "\n".join(js_parts))


def widgets_settings_payload(widgets: list[WidgetSpec], settings: dict | None) -> dict:
    enabled_map: dict[str, bool] = {}
    order: list[str] = []

    if isinstance(settings, dict):
        enabled = settings.get("enabled")
        if isinstance(enabled, dict):
            for k, v in enabled.items():
                if isinstance(k, str):
                    wid = k.strip().lower()
                    if _ID_RE.match(wid):
                        enabled_map[wid] = bool(v)

        raw_order = settings.get("order")
        if isinstance(raw_order, list):
            for item in raw_order:
                if isinstance(item, str):
                    wid = item.strip().lower()
                    if _ID_RE.match(wid) and wid not in order:
                        order.append(wid)

    items = []
    for w in widgets:
        m = w.manifest
        items.append(
            {
                "id": w.widget_id,
                "name": m.name,
                "author": m.author,
                "description": m.description,
                "url": m.url,
                "version": m.version,
                "enabled": enabled_map.get(w.widget_id, True),
            }
        )

    return {"widgets": items, "order": order}


def register_widget_static(*, app, widgets_dir: str) -> None:
    from flask import send_from_directory

    app.config.setdefault("WIDGET_STATIC_MAP", {})

    @app.route("/widgets-static/<widget_id>/<path:filename>")
    def widget_static(widget_id: str, filename: str):
        wid = (widget_id or "").strip().lower()
        if not _ID_RE.match(wid):
            return ("", 404)

        static_map = app.config.get("WIDGET_STATIC_MAP")
        if not isinstance(static_map, dict):
            return ("", 404)

        folder = static_map.get(wid)
        if not isinstance(folder, str) or not os.path.isdir(folder):
            return ("", 404)

        return send_from_directory(folder, filename)


def attach_widget_static_map(*, app, widgets_dir: str, widgets: list[WidgetSpec]) -> None:
    static_map: dict[str, str] = {}
    for w in widgets:
        folder = os.path.join(widgets_dir, w.folder_name, "static")
        if os.path.isdir(folder):
            static_map[w.widget_id] = folder
    app.config["WIDGET_STATIC_MAP"] = static_map


def _escape_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
