import sys
import os
import socket
import subprocess
import threading
from flask import Flask, render_template_string, request, send_file
import io
import time
import asyncio
import platform
import getpass

from widget_system import (
    apply_widget_settings,
    attach_widget_static_map,
    load_widgets,
    register_widget_static,
    render_widget_bundle,
    widgets_settings_payload,
)

from PySide6.QtCore import Qt, QUrl, QSize, QEvent, QTimer, Signal, Slot, QMetaObject, Q_ARG
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QColor
import ctypes
if sys.platform == "win32":
    from ctypes import wintypes
    import comtypes
    from comtypes import GUID
import urllib.request
import urllib.error
import ssl
import xml.etree.ElementTree as ET
import re
import json
import html
import webbrowser
import shutil
from urllib.parse import urlparse
from PySide6.QtWebEngineCore import QWebEngineSettings

from pixelnews_transparency_report import pixelnews_transparency_html


class LightspeedBlockedError(RuntimeError):
    pass


_ls_installed_cache: bool | None = None
_ls_installed_cache_at = 0.0


def _lightspeed_filter_agent_installed(ttl_seconds: float = 10.0) -> bool:
    global _ls_installed_cache, _ls_installed_cache_at
    now = time.time()
    if _ls_installed_cache is not None and (now - float(_ls_installed_cache_at)) < float(ttl_seconds):
        return bool(_ls_installed_cache)

    system_drive = os.environ.get("SystemDrive") or "C:"
    candidate = os.path.join(system_drive, "Program Files", "Lightspeed Systems", "Filter Agent")
    installed = bool(os.path.isdir(candidate))
    _ls_installed_cache = installed
    _ls_installed_cache_at = now
    return installed


class _LightspeedRedirectGuard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            host = (urlparse(str(newurl)).hostname or "").strip().lower()
        except Exception:
            host = ""
        if _lightspeed_filter_agent_installed() and host in ("localhost", "127.0.0.1", "::1"):
            raise LightspeedBlockedError("blocked by Lightspeed Filter Agent")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen_guarding_lightspeed(url: str, *, context: ssl.SSLContext | None, timeout: float):
    return _urlopen_with_proxy_setting(url, timeout=timeout, context=context, guard_lightspeed=True)

_WEBVIEW_REMOTE_DEBUG = os.environ.get("WEBVIEW_REMOTE_DEBUG", "1").strip().lower() in ("1", "true", "yes", "on")
try:
    _WEBVIEW_REMOTE_DEBUG_PORT = int(os.environ.get("WEBVIEW_REMOTE_DEBUG_PORT", "9222"))
except Exception:
    _WEBVIEW_REMOTE_DEBUG_PORT = 9222

if _WEBVIEW_REMOTE_DEBUG:
    # Prefer Chromium flags; also set QTWEBENGINE_REMOTE_DEBUGGING for compatibility.
    existing_flags = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    if "--remote-debugging-port=" not in existing_flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (existing_flags + f" --remote-debugging-port={_WEBVIEW_REMOTE_DEBUG_PORT}").strip()
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", str(_WEBVIEW_REMOTE_DEBUG_PORT))

# Ensure the local `nws_ez` package (in ./nws-ez) is importable without installing.
_WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
_NWS_EZ_DIR = os.path.join(_WORKSPACE_DIR, "nws-ez")
if _NWS_EZ_DIR not in sys.path:
    sys.path.insert(0, _NWS_EZ_DIR)

_FALLBACK_BG_PATH = os.path.join(_WORKSPACE_DIR, "static", "fallbackbg.png")


def _ensure_fallback_bg_exists() -> None:
    """Ensure /static/fallbackbg.png exists.

    We keep this lightweight by copying an existing shipped image if present.
    """
    try:
        if os.path.exists(_FALLBACK_BG_PATH):
            return
        src = os.path.join(_WORKSPACE_DIR, "static", "downside.png")
        if os.path.exists(src):
            os.makedirs(os.path.dirname(_FALLBACK_BG_PATH), exist_ok=True)
            shutil.copyfile(src, _FALLBACK_BG_PATH)
    except Exception:
        # Best-effort; if missing, the UI will still work (just no fallback image).
        pass


_ensure_fallback_bg_exists()


DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_MICA = 2

dwmapi = ctypes.WinDLL("dwmapi") if sys.platform == "win32" else None

def enable_mica(hwnd):
    if dwmapi is None:
        return
    value = ctypes.c_int(DWMSBT_MICA)
    dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_int(DWMWA_SYSTEMBACKDROP_TYPE),
        ctypes.byref(value),
        ctypes.sizeof(value)
    )

if sys.platform == "win32":
    CLSID_VirtualDesktopManager = GUID("{AA509086-5CA9-4C25-8F95-589D3C07B48A}")
    IID_IVirtualDesktopManager = GUID("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}")

    HRESULT = ctypes.c_long

    class IVirtualDesktopManager(comtypes.IUnknown):
        _iid_ = IID_IVirtualDesktopManager
        _methods_ = [
            comtypes.COMMETHOD(
                [],
                HRESULT,
                "IsWindowOnCurrentVirtualDesktop",
                (["in"], wintypes.HWND, "hwnd"),
                (["out"], ctypes.POINTER(wintypes.BOOL), "pfOnCurrentDesktop"),
            ),
            comtypes.COMMETHOD(
                [],
                HRESULT,
                "GetWindowDesktopId",
                (["in"], wintypes.HWND, "hwnd"),
                (["out"], ctypes.POINTER(GUID), "pDesktopId"),
            ),
            comtypes.COMMETHOD(
                [],
                HRESULT,
                "MoveWindowToDesktop",
                (["in"], wintypes.HWND, "hwnd"),
                (["in"], ctypes.POINTER(GUID), "pDesktopId"),
            ),
        ]


def force_to_current_desktop(hwnd):
    if sys.platform != "win32":
        return
    manager = comtypes.CoCreateInstance(
        CLSID_VirtualDesktopManager,
        interface=IVirtualDesktopManager
    )

    is_on_current = wintypes.BOOL()
    manager.IsWindowOnCurrentVirtualDesktop(hwnd, ctypes.byref(is_on_current))

    if not is_on_current.value:
        desktop_id = GUID()
        manager.GetWindowDesktopId(
            ctypes.windll.user32.GetForegroundWindow(),
            ctypes.byref(desktop_id)
        )
        manager.MoveWindowToDesktop(hwnd, ctypes.byref(desktop_id))

# -------------------------
# Localhost-only web server
# -------------------------

app = Flask(__name__)

_WIDGETS_DIR = os.path.join(_WORKSPACE_DIR, "widgets")
register_widget_static(app=app, widgets_dir=_WIDGETS_DIR)

# Load widgets once at startup so they can register Flask endpoints.
_ALL_WIDGETS = load_widgets(app=app, widgets_dir=_WIDGETS_DIR)
attach_widget_static_map(app=app, widgets_dir=_WIDGETS_DIR, widgets=_ALL_WIDGETS)

_bg_lock = threading.Lock()
_bg_png: bytes | None = None
_bg_updated_at = 0.0

_effects_lock = threading.Lock()
# Runtime disable (e.g. reported by the frontend when software rendering is detected).
_runtime_disable_visual_effects = False

_articles_lock = threading.Lock()
_articles_cache: list[dict] = []
_articles_updated_at = 0.0

_WIDGET_HOST = None

DEFAULT_HOST_WIDTH = 335
FEED_EXPANDED_HOST_WIDTH = 965

_weather_lock = threading.Lock()
_weather_cache: dict[tuple[float, float], dict] = {}
_weather_updated_at: dict[tuple[float, float], float] = {}

_device_loc_lock = threading.Lock()
_device_loc_cache: tuple[float, float] | None = None
_device_loc_updated_at = 0.0

# IP-based (best-effort) location cache (via ip-api.com)
_ip_loc_lock = threading.Lock()
_ip_loc_cache: dict | None = None
_ip_loc_updated_at = 0.0

# Internet connectivity cache
_inet_lock = threading.Lock()
_inet_online: bool | None = None
_inet_checked_at = 0.0

_WEATHER_ICON_DIR = os.path.join(_WORKSPACE_DIR, "static", "weather", "icons")


def has_internet_cached(ttl_seconds: float = 3.0, timeout_seconds: float = 1.5, *, force: bool = False) -> tuple[bool, float]:
    """Best-effort internet reachability check with small TTL cache.

    Uses a direct TCP connect to known public DNS anycast IPs (no DNS required).
    """
    global _inet_online, _inet_checked_at

    now = time.time()
    with _inet_lock:
        if not force and _inet_online is not None and (now - _inet_checked_at) < float(ttl_seconds):
            return bool(_inet_online), float(_inet_checked_at)

    online = False
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            sock = socket.create_connection((host, 53), timeout=float(timeout_seconds))
            try:
                sock.close()
            except Exception:
                pass
            online = True
            break
        except Exception:
            continue

    with _inet_lock:
        _inet_online = bool(online)
        _inet_checked_at = time.time()
        return bool(_inet_online), float(_inet_checked_at)

with open("index.html", "r", encoding="utf-8") as f:
    HTML = f.read()

DEFAULT_LAT = float(os.environ.get("NWS_LAT", "38.8977"))
DEFAULT_LON = float(os.environ.get("NWS_LON", "-77.0365"))
DEFAULT_NWS_USER_AGENT = os.environ.get("NWS_USER_AGENT", "widgets/1.0 (local)")

_CONFIG_PATH = os.path.join(_WORKSPACE_DIR, "config.json")
_config_lock = threading.Lock()
_config_cache: dict | None = None
_config_mtime: float | None = None

_FLAGS_PATH = os.path.join(_WORKSPACE_DIR, "flags.json")
_flags_lock = threading.Lock()
_flags_cache: dict | None = None
_flags_mtime: float | None = None


def _load_flags_file() -> dict:
    if not os.path.exists(_FLAGS_PATH):
        return {}
    try:
        with open(_FLAGS_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_flags_cached(ttl_seconds: float = 2.0) -> dict:
    """Return parsed flags.json (best-effort), cached by mtime + TTL."""
    global _flags_cache, _flags_mtime

    now = time.time()
    with _flags_lock:
        cached = _flags_cache
        cached_at = getattr(get_flags_cached, "_cached_at", 0.0)
        if cached is not None and (now - float(cached_at)) < ttl_seconds:
            return dict(cached)

    try:
        mtime = os.path.getmtime(_FLAGS_PATH) if os.path.exists(_FLAGS_PATH) else None
    except Exception:
        mtime = None

    with _flags_lock:
        if _flags_cache is not None and _flags_mtime == mtime and (now - float(getattr(get_flags_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
            return dict(_flags_cache)

    data = _load_flags_file()
    with _flags_lock:
        _flags_cache = dict(data)
        _flags_mtime = mtime
        setattr(get_flags_cached, "_cached_at", now)
        return dict(_flags_cache)


def _normalize_advanced_settings(cfg: dict | None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("advanced_settings")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "disable_visual_effects": bool(raw.get("disable_visual_effects", False)),
    }


def _normalize_experiments_state(cfg: dict | None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("experiments")
    raw = raw if isinstance(raw, dict) else {}
    flags = raw.get("flags")
    flags = flags if isinstance(flags, dict) else {}
    out_flags: dict[str, str] = {}
    for k, v in flags.items():
        if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip():
            out_flags[k.strip()] = v.strip()
        elif isinstance(k, str) and k.strip() and isinstance(v, str):
            out_flags[k.strip()] = "default"
    return {"flags": out_flags}


def _get_experiment_choice(flag_id: str) -> str:
    cfg = get_config_cached()
    exp_state = _normalize_experiments_state(cfg if isinstance(cfg, dict) else {})
    stored_flags = exp_state.get("flags") if isinstance(exp_state, dict) else {}
    stored_flags = stored_flags if isinstance(stored_flags, dict) else {}
    v = stored_flags.get(flag_id)
    return v.strip() if isinstance(v, str) and v.strip() else "default"


def _get_flag_default_choice(flag_id: str, *, fallback: str = "disabled") -> str:
    flags_doc = get_flags_cached()
    defs = flags_doc.get("flags") if isinstance(flags_doc, dict) else None
    defs = defs if isinstance(defs, list) else []
    for f in defs:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        if not isinstance(fid, str) or fid.strip() != flag_id:
            continue
        d = f.get("default")
        return d.strip() if isinstance(d, str) and d.strip() else fallback
    return fallback


def _resolve_bool_experiment_flag(flag_id: str, *, default_value: bool) -> bool:
    choice = _get_experiment_choice(flag_id)
    if choice == "enabled":
        return True
    if choice == "disabled":
        return False

    default_choice = _get_flag_default_choice(flag_id, fallback=("enabled" if default_value else "disabled"))
    if default_choice == "enabled":
        return True
    if default_choice == "disabled":
        return False
    return bool(default_value)


def _use_system_proxy_for_requests() -> bool:
    return _resolve_bool_experiment_flag("use_system_proxy_for_requests", default_value=True)


def _get_effective_experiment_choice(flag_id: str, *, fallback: str = "disabled") -> str:
    choice = _get_experiment_choice(flag_id)
    if choice == "default":
        return _get_flag_default_choice(flag_id, fallback=fallback)
    return choice


_DISPLAY_NAME_LOCK = threading.Lock()
_DISPLAY_NAME_CACHE: dict | None = None
_DISPLAY_NAME_CACHE_AT = 0.0


def _windows_display_name_best_effort() -> str | None:
    if os.name != "nt":
        return None

    # Prefer the full display name ("John Doe") via Secur32 GetUserNameExW.
    try:
        NameDisplay = 3
        secur32 = ctypes.WinDLL("secur32", use_last_error=True)
        GetUserNameExW = secur32.GetUserNameExW
        GetUserNameExW.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        GetUserNameExW.restype = ctypes.c_int

        size = ctypes.c_ulong(0)
        # First call to get required buffer size.
        GetUserNameExW(NameDisplay, None, ctypes.byref(size))
        if int(size.value) > 1:
            buf = ctypes.create_unicode_buffer(int(size.value))
            ok = bool(GetUserNameExW(NameDisplay, buf, ctypes.byref(size)))
            if ok:
                s = str(buf.value or "").strip()
                if s:
                    return s
    except Exception:
        pass

    # Fall back to environment / getpass.
    try:
        u = (os.environ.get("USERNAME") or "").strip()
        if u:
            return u
    except Exception:
        pass

    try:
        u = str(getpass.getuser() or "").strip()
        return u or None
    except Exception:
        return None


def _posix_display_name_best_effort() -> str | None:
    # Works for Linux/macOS and similar POSIX OSes.
    try:
        import pwd

        gecos = pwd.getpwuid(os.getuid()).pw_gecos
        s = str(gecos or "").split(",", 1)[0].strip()
        if s:
            return s
    except Exception:
        pass

    try:
        u = str(getpass.getuser() or "").strip()
        return u or None
    except Exception:
        return None


def _get_display_name_from_userfeed_email() -> str | None:
    try:
        cfg = get_userfeed_config_cached()
        cfg = cfg if isinstance(cfg, dict) else {}
        account = cfg.get("account")
        account = account if isinstance(account, dict) else {}
        email = account.get("email")
        email = email.strip() if isinstance(email, str) else ""
        return email or None
    except Exception:
        return None


def _expanded_greeting_display_name_payload() -> dict:
    flag_id = "show_display_name_in_expanded_greeting"
    choice = _get_effective_experiment_choice(flag_id, fallback="disabled")

    if choice == "disabled":
        return {"enabled": False, "choice": choice, "display_name": None, "source": None}

    # Map choice -> detection method.
    method = "auto"
    if choice == "enabled":
        method = "auto"
    elif choice == "enabled_force_win":
        method = "win"
    elif choice == "enabled_force_darwin":
        method = "darwin"
    elif choice == "enabled_force_linux":
        method = "linux"
    elif choice == "enabled_force_accmail":
        method = "accmail"
    elif choice == "enabled_force_maarcr":
        method = "maarcr"

    display_name: str | None = None
    source: str | None = None

    if method == "maarcr":
        display_name = "maia arson crimew"
        source = "override"
    elif method == "accmail":
        display_name = _get_display_name_from_userfeed_email()
        source = "accmail" if display_name else None
    else:
        # Auto: pick based on platform.
        if method == "auto":
            if os.name == "nt":
                method = "win"
            else:
                sysname = str(platform.system() or "").strip().lower()
                method = "darwin" if sysname == "darwin" else "linux"

        if method == "win":
            display_name = _windows_display_name_best_effort()
            source = "win" if display_name else None
        elif method in ("darwin", "linux"):
            display_name = _posix_display_name_best_effort()
            source = method if display_name else None

    if isinstance(display_name, str):
        display_name = display_name.strip()
        if not display_name:
            display_name = None
            source = None

    return {"enabled": True, "choice": choice, "display_name": display_name, "source": source}


def _urlopen_with_proxy_setting(
    req_or_url,
    *,
    timeout: float,
    context: ssl.SSLContext | None = None,
    guard_lightspeed: bool = False,
):
    handlers: list[object] = []
    if not _use_system_proxy_for_requests():
        handlers.append(urllib.request.ProxyHandler({}))
    if guard_lightspeed:
        handlers.append(_LightspeedRedirectGuard())
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    return opener.open(req_or_url, timeout=timeout)


def _load_config_file() -> dict:
    """Load optional config.json from workspace root.

    Expected shape (recommended):
    {
      "weather": {
        "lat": 38.8977,
        "lon": -77.0365,
        "city_name": "Washington, DC",
        "country_code": "US",
        "temp_unit": "F"  // or "C"
      }
    }

    For convenience, the weather keys may also appear at the top-level.
    """
    if not os.path.exists(_CONFIG_PATH):
        return {}

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return {}

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def get_config_cached(ttl_seconds: float = 2.0) -> dict:
    """Return parsed config.json (best-effort), cached by mtime + TTL."""
    global _config_cache, _config_mtime

    now = time.time()
    with _config_lock:
        cached = _config_cache
        cached_at = getattr(get_config_cached, "_cached_at", 0.0)
        if cached is not None and (now - float(cached_at)) < ttl_seconds:
            return dict(cached)

    try:
        mtime = os.path.getmtime(_CONFIG_PATH) if os.path.exists(_CONFIG_PATH) else None
    except Exception:
        mtime = None

    with _config_lock:
        if _config_cache is not None and _config_mtime == mtime and (now - float(getattr(get_config_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
            return dict(_config_cache)

    data = _load_config_file()
    with _config_lock:
        _config_cache = dict(data)
        _config_mtime = mtime
        setattr(get_config_cached, "_cached_at", now)
        return dict(_config_cache)


def save_config_file(data: dict) -> bool:
    """Persist config.json (best-effort). Returns True on success."""
    if not isinstance(data, dict):
        return False

    tmp = _CONFIG_PATH + ".tmp"
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return False

    with _config_lock:
        try:
            os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(raw)
                f.write("\n")
            os.replace(tmp, _CONFIG_PATH)
            try:
                mtime = os.path.getmtime(_CONFIG_PATH)
            except Exception:
                mtime = None
            global _config_cache, _config_mtime
            _config_cache = dict(data)
            _config_mtime = mtime
            setattr(get_config_cached, "_cached_at", time.time())
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False


_USERFEED_CONFIG_PATH = os.path.join(_WORKSPACE_DIR, "userfeedconfig.json")
_userfeed_lock = threading.Lock()
_userfeed_cache: dict | None = None
_userfeed_mtime: float | None = None


def _load_userfeed_config_file() -> dict:
    if not os.path.exists(_USERFEED_CONFIG_PATH):
        return {}

    try:
        with open(_USERFEED_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return {}

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def get_userfeed_config_cached(ttl_seconds: float = 2.0) -> dict:
    """Return parsed userfeedconfig.json (best-effort), cached by mtime + TTL."""
    global _userfeed_cache, _userfeed_mtime

    now = time.time()
    with _userfeed_lock:
        cached = _userfeed_cache
        cached_at = getattr(get_userfeed_config_cached, "_cached_at", 0.0)
        if cached is not None and (now - float(cached_at)) < ttl_seconds:
            return dict(cached)

    try:
        mtime = os.path.getmtime(_USERFEED_CONFIG_PATH) if os.path.exists(_USERFEED_CONFIG_PATH) else None
    except Exception:
        mtime = None

    with _userfeed_lock:
        if _userfeed_cache is not None and _userfeed_mtime == mtime and (now - float(getattr(get_userfeed_config_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
            return dict(_userfeed_cache)

    data = _load_userfeed_config_file()
    with _userfeed_lock:
        _userfeed_cache = dict(data)
        _userfeed_mtime = mtime
        setattr(get_userfeed_config_cached, "_cached_at", now)
        return dict(_userfeed_cache)


def save_userfeed_config_file(data: dict) -> bool:
    """Persist userfeedconfig.json (best-effort). Returns True on success."""
    if not isinstance(data, dict):
        return False

    tmp = _USERFEED_CONFIG_PATH + ".tmp"
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return False

    with _userfeed_lock:
        try:
            os.makedirs(os.path.dirname(_USERFEED_CONFIG_PATH), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(raw)
                f.write("\n")
            os.replace(tmp, _USERFEED_CONFIG_PATH)
            try:
                mtime = os.path.getmtime(_USERFEED_CONFIG_PATH)
            except Exception:
                mtime = None
            global _userfeed_cache, _userfeed_mtime
            _userfeed_cache = dict(data)
            _userfeed_mtime = mtime
            setattr(get_userfeed_config_cached, "_cached_at", time.time())
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False


_PROFILE_PIC_LOCK = threading.Lock()
_PROFILE_PIC_PATH: str | None = None
_PROFILE_PIC_CHECKED_AT = 0.0


def _try_extract_windows_profile_picture_png() -> str | None:
    """Best-effort lookup of the current Windows account picture path.

    Matches the behavior of this PowerShell approach:
    - Get current SID
    - Read HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AccountPicture\\Users\\<sid>
    - Return the first existing Image* path
    """

    if os.name != "nt":
        return None

    try:
        import csv
        import winreg

        def _get_current_sid() -> str | None:
            try:
                raw = subprocess.check_output(
                    ["whoami", "/user", "/fo", "csv", "/nh"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
                raw = (raw or "").strip()
                if not raw:
                    return None
                row = next(csv.reader([raw]))
                # Expected: "USERNAME","SID"
                sid = (row[1] if len(row) >= 2 else "").strip()
                return sid or None
            except Exception:
                return None

        sid = _get_current_sid()
        if not sid:
            return None

        subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\AccountPicture\Users\{sid}"

        access = winreg.KEY_READ
        # Prefer 64-bit registry view when available.
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            access |= winreg.KEY_WOW64_64KEY

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, access) as key:
            value_names = [
                "Image96",
                "Image64",
                "Image48",
                "Image40",
                "Image32",
                "Image192",
                "Image208",
                "Image240",
                "Image424",
                "Image448",
                "Image1080",
            ]

            for name in value_names:
                try:
                    value, _value_type = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                except OSError:
                    continue

                if isinstance(value, str):
                    path = value.strip()
                    if path and os.path.exists(path):
                        return path

        return None
    except Exception:
        return None


def get_profile_picture_path_cached(ttl_seconds: float = 60.0) -> str | None:
    global _PROFILE_PIC_PATH, _PROFILE_PIC_CHECKED_AT

    now = time.time()
    with _PROFILE_PIC_LOCK:
        if _PROFILE_PIC_PATH and (now - float(_PROFILE_PIC_CHECKED_AT)) < float(ttl_seconds) and os.path.exists(_PROFILE_PIC_PATH):
            return _PROFILE_PIC_PATH

    path = _try_extract_windows_profile_picture_png()
    with _PROFILE_PIC_LOCK:
        _PROFILE_PIC_PATH = path
        _PROFILE_PIC_CHECKED_AT = now
        return _PROFILE_PIC_PATH


def _normalize_weather_config(cfg: dict) -> dict:
    """Extract and validate weather config from config.json content."""
    if not isinstance(cfg, dict):
        return {}

    w = cfg.get("weather")
    if not isinstance(w, dict):
        # Allow top-level keys as a convenience.
        w = cfg

    out: dict = {}

    def _num(v):
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    lat = _num(w.get("lat"))
    lon = _num(w.get("lon"))
    if lat is not None and lon is not None and (-90.0 <= lat <= 90.0) and (-180.0 <= lon <= 180.0):
        out["lat"] = float(lat)
        out["lon"] = float(lon)

    city_name = w.get("city_name")
    if isinstance(city_name, str) and city_name.strip():
        out["city_name"] = city_name.strip()

    country_code = w.get("country_code")
    if isinstance(country_code, str) and country_code.strip():
        out["country_code"] = country_code.strip().upper()

    temp_unit = w.get("temp_unit")
    if isinstance(temp_unit, str) and temp_unit.strip():
        u = temp_unit.strip().upper()
        if u in ("F", "C"):
            out["temp_unit"] = u

    return out


def _normalize_ui_config(cfg: dict) -> dict:
    """Extract and validate UI config from config.json content."""
    if not isinstance(cfg, dict):
        return {}
    ui = cfg.get("ui")
    ui = ui if isinstance(ui, dict) else {}
    out: dict = {}
    if isinstance(ui.get("feed_expanded"), bool):
        out["feed_expanded"] = bool(ui.get("feed_expanded"))
    return out


def _get_ipapi_location_cached(ttl_seconds: float = 60 * 60) -> tuple[dict | None, float, str | None]:
    """Best-effort location via public IP lookup (ip-api.com).

    Returns: (info_or_none, updated_at, error_or_none)

    info includes: lat, lon, city, country_code, timezone

    Notes:
    - Uses an external service over HTTP.
    - Accuracy may be coarse (typically city/ISP level).
    - Cached to avoid frequent network calls.
    """
    global _ip_loc_cache, _ip_loc_updated_at

    now = time.time()
    with _ip_loc_lock:
        if _ip_loc_cache and (now - _ip_loc_updated_at) < ttl_seconds:
            return dict(_ip_loc_cache), float(_ip_loc_updated_at), None

    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/",
            headers={"User-Agent": "widgets/1.0 (local)"},
            method="GET",
        )
        with _urlopen_with_proxy_setting(req, timeout=4, context=None, guard_lightspeed=False) as resp:
            raw = resp.read()

        data = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
        if not isinstance(data, dict) or data.get("status") != "success":
            raise RuntimeError("ip-api lookup failed")

        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            raise RuntimeError("ip-api missing lat/lon")

        lat_f = float(lat)
        lon_f = float(lon)
        if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
            raise RuntimeError("ip-api invalid lat/lon")

        city = data.get("city")
        country_code = data.get("countryCode")
        timezone = data.get("timezone")

    except Exception:
        with _ip_loc_lock:
            if _ip_loc_cache:
                return dict(_ip_loc_cache), float(_ip_loc_updated_at or now), "IP location lookup failed (using cached)."
        return None, float(now), "IP location lookup failed."

    with _ip_loc_lock:
        _ip_loc_cache = {
            "lat": float(lat_f),
            "lon": float(lon_f),
            "city": str(city).strip() if isinstance(city, str) and city.strip() else None,
            "country_code": str(country_code).strip().upper() if isinstance(country_code, str) and country_code.strip() else None,
            "timezone": str(timezone).strip() if isinstance(timezone, str) and timezone.strip() else None,
        }
        _ip_loc_updated_at = time.time()
        return dict(_ip_loc_cache), float(_ip_loc_updated_at), None


def _nws_icon_to_local(icon_url: str | None, is_daytime: bool | None) -> str | None:
    """Return a local icon path for an NWS *condition name*.

    Note: We intentionally do not parse NWS icon URL codes here. Callers should
    pass a condition name like "Mostly Cloudy", "Rain", etc.
    """
    if not icon_url:
        return None

    raw_condition = (icon_url or "").strip()
    if not raw_condition:
        return None

    # Prefer the PC's local time zone for day/night selection.
    try:
        local_hour = time.localtime().tm_hour
        day = 6 <= local_hour < 18
    except Exception:
        day = True

    def _norm(s: str) -> str:
        s = s.strip().lower()
        s = re.sub(r"[\t\r\n]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^a-z0-9 ]+", "", s)
        return s

    key = _norm(raw_condition)

    # Exact condition-name mapping (requested set).
    condition_to_icons: dict[str, tuple[str, str]] = {
        # Sky cover
        "clear": ("SunnyDay", "ClearNight"),
        "mostly clear": ("MostlySunnyDay", "MostlyClearNight"),
        "partly cloudy": ("PartlyCloudyDay", "PartlyCloudyNight"),
        "mostly cloudy": ("MostlyCloudyDay", "MostlyCloudyNight"),
        "cloudy": ("Cloudy", "Cloudy"),
        "overcast": ("VeryCloudy", "VeryCloudy"),
        # Visibility / air
        "fog": ("Fog", "Fog"),
        "dense fog": ("Fog", "Fog"),
        "mist": ("Fog", "Fog"),
        "haze": ("HazeSmoke", "HazeSmokeNight"),
        "smoke": ("HazeSmoke", "HazeSmokeNight"),
        # Precip
        "rain": ("LightRain", "LightRain"),
        "drizzle": ("LightRainShowerDay", "LightRainShowerNight"),
        "freezing rain": ("FreezingRain", "FreezingRain"),
        "snow": ("LightSnow", "LightSnow"),
        "snow flurries": ("SnowShowersDay", "SnowShowersNight"),
        "snow squall": ("HeavySnow", "HeavySnow"),
        "sleet": ("FreezingRain", "FreezingRain"),
        "hail": ("HailDay", "HailNight"),
        # Storms
        "thunderstorm": ("Thunder", "Thunder"),
        "showers": ("RainShowersDay", "RainShowersNight"),
        # Blowing / dust
        "blowing snow": ("BlowingSnow", "BlowingSnow"),
        "blowing dust": ("HazeSmoke", "HazeSmokeNight"),
        "dust storm": ("HazeSmoke", "HazeSmokeNight"),
        # Wind
        "windy": ("Windy", "Windy"),
        "breezy": ("Windy", "Windy"),
        # Severe
        "funnel cloud": ("Thunder", "Thunder"),
        "tornado": ("Thunder", "Thunder"),
    }

    pair = condition_to_icons.get(key)

    # Best-effort substring matching for common forecast phrasing.
    if not pair:
        # Order matters: more specific first.
        patterns: list[tuple[str, tuple[str, str]]] = [
            ("freezing rain", condition_to_icons["freezing rain"]),
            ("dense fog", condition_to_icons["dense fog"]),
            ("snow squall", condition_to_icons["snow squall"]),
            ("snow flurr", condition_to_icons["snow flurries"]),
            ("blowing snow", condition_to_icons["blowing snow"]),
            ("dust storm", condition_to_icons["dust storm"]),
            ("blowing dust", condition_to_icons["blowing dust"]),
            ("thunder", condition_to_icons["thunderstorm"]),
            ("tstorm", condition_to_icons["thunderstorm"]),
            ("showers", condition_to_icons["showers"]),
            ("drizzle", condition_to_icons["drizzle"]),
            ("sleet", condition_to_icons["sleet"]),
            ("hail", condition_to_icons["hail"]),
            ("fog", condition_to_icons["fog"]),
            ("mist", condition_to_icons["mist"]),
            ("haze", condition_to_icons["haze"]),
            ("smoke", condition_to_icons["smoke"]),
            ("overcast", condition_to_icons["overcast"]),
            ("mostly clear", condition_to_icons["mostly clear"]),
            ("partly cloudy", condition_to_icons["partly cloudy"]),
            ("mostly cloudy", condition_to_icons["mostly cloudy"]),
            ("cloudy", condition_to_icons["cloudy"]),
            ("clear", condition_to_icons["clear"]),
            ("windy", condition_to_icons["windy"]),
            ("breezy", condition_to_icons["breezy"]),
            ("snow", condition_to_icons["snow"]),
            ("rain", condition_to_icons["rain"]),
        ]
        for pat, v in patterns:
            if pat in key:
                pair = v
                break

    if not pair:
        # Final fallback so it's never empty.
        candidate = os.path.join(_WEATHER_ICON_DIR, "Cloudy.png")
        return "/static/weather/icons/Cloudy.png" if os.path.exists(candidate) else None

    mapped = pair[0] if day else pair[1]
    candidate = os.path.join(_WEATHER_ICON_DIR, f"{mapped}.png")
    if os.path.exists(candidate):
        return f"/static/weather/icons/{mapped}.png"

    # Fallback if the specific icon file is missing.
    candidate = os.path.join(_WEATHER_ICON_DIR, "Cloudy.png")
    return "/static/weather/icons/Cloudy.png" if os.path.exists(candidate) else None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _fetch_weather(lat: float, lon: float) -> dict:
    import importlib

    nws_ez = importlib.import_module("nws_ez")
    client = nws_ez.NWSClient(user_agent=DEFAULT_NWS_USER_AGENT, use_system_proxy=_use_system_proxy_for_requests())

    fc = client.forecast(lat, lon)
    next_period = fc.periods[0] if getattr(fc, "periods", None) else None

    hourly = None
    try:
        hourly = client.hourly(lat, lon)
    except Exception:
        hourly = None

    obs = client.latest_observation(lat, lon)

    alerts = client.alerts_active(point=(lat, lon))
    alerts_count = len(getattr(alerts, "features", []) or [])

    def _safe_float(x):
        try:
            return float(x) if x is not None else None
        except Exception:
            return None

    temp_c = _safe_float(getattr(obs, "temperature_c", None))
    temp_f = None
    if temp_c is not None:
        temp_f = round((temp_c * 9.0 / 5.0) + 32.0, 1)

    payload: dict = {
        "place_name": None,
        "location": {"lat": float(lat), "lon": float(lon)},
        "icon": None,
        "observation": {
            "timestamp": str(getattr(obs, "timestamp", "")) if getattr(obs, "timestamp", None) else None,
            "text_description": getattr(obs, "text_description", None),
            "temperature_c": temp_c,
            "temperature_f": temp_f,
        },
        "forecast": {
            "next": None,
        },
        "hourly": {
            "periods": [],
        },
        "alerts": {"count": int(alerts_count)},
    }

    if next_period is not None:
        # Use condition names (observation/forecast text) for icon mapping.
        # Prefer current observation, then forecast short text.
        condition_name = getattr(obs, "text_description", None) or getattr(next_period, "short_forecast", None)
        payload["icon"] = _nws_icon_to_local(condition_name, getattr(next_period, "is_daytime", None))
        payload["forecast"]["next"] = {
            "name": getattr(next_period, "name", None),
            "short_forecast": getattr(next_period, "short_forecast", None),
            "temperature": getattr(next_period, "temperature", None),
            "temperature_unit": getattr(next_period, "temperature_unit", None),
        }

    if hourly is not None and getattr(hourly, "periods", None):
        periods_out = []
        for p in list(getattr(hourly, "periods", []) or [])[:12]:
            condition_name = getattr(p, "short_forecast", None)
            periods_out.append(
                {
                    "start_time": getattr(p, "start_time", None),
                    "temperature": getattr(p, "temperature", None),
                    "temperature_unit": getattr(p, "temperature_unit", None),
                    "pop": getattr(p, "probability_of_precipitation", None),
                    "icon": _nws_icon_to_local(condition_name, getattr(p, "is_daytime", None)),
                }
            )
        payload["hourly"]["periods"] = periods_out

    return payload


def get_weather_cached(lat: float, lon: float, ttl_seconds: float = 7 * 60) -> tuple[dict | None, float, str | None]:
    key = (round(float(lat), 4), round(float(lon), 4))
    now = time.time()

    with _weather_lock:
        updated_at = _weather_updated_at.get(key, 0.0)
        cached = _weather_cache.get(key)
        if cached and (now - updated_at) < ttl_seconds:
            return dict(cached), float(updated_at), None

    try:
        data = _fetch_weather(lat, lon)
    except Exception:
        with _weather_lock:
            updated_at = _weather_updated_at.get(key, 0.0)
            cached = _weather_cache.get(key)
            if cached:
                return dict(cached), float(updated_at or now), "Weather update failed (showing cached)."
        return None, float(now), "Weather update failed."

    with _weather_lock:
        _weather_cache[key] = dict(data)
        _weather_updated_at[key] = time.time()
        return dict(data), float(_weather_updated_at[key]), None


def _parse_windowscentral_feed(xml_bytes: bytes, limit: int = 6) -> list[dict]:
    root = ET.fromstring(xml_bytes)

    def child_text(elem, name):
        for c in elem:
            tag = c.tag
            if tag == name or tag.endswith("}" + name):
                return (c.text or "").strip()
        return None

    def find_children_local(elem, name):
        out = []
        for c in elem:
            tag = c.tag
            if tag == name or tag.endswith("}" + name):
                out.append(c)
        return out

    def find_first_local(elem, name):
        for c in elem:
            tag = c.tag
            if tag == name or tag.endswith("}" + name):
                return c
        return None

    def extract_author(raw):
        if not raw:
            return None
        raw = raw.strip()
        m = re.search(r"\(([^)]+)\)", raw)
        if m:
            return m.group(1).strip()
        return raw

    items = root.findall('.//item')
    articles = []
    for it in items[: max(0, limit)]:
        title = child_text(it, "title") or "Untitled"
        link = child_text(it, "link") or ""
        author_raw = child_text(it, "author") or child_text(it, "creator")
        author = extract_author(author_raw)

        cats = []
        for c in find_children_local(it, "category"):
            if c.text and c.text.strip():
                cats.append(c.text.strip())

        thumb_url = None
        thumb_elem = find_first_local(it, "thumbnail")
        if thumb_elem is not None:
            thumb_url = thumb_elem.attrib.get("url") or thumb_elem.attrib.get("{http://search.yahoo.com/mrss/}url")

        if not thumb_url:
            content_elem = find_first_local(it, "content")
            if content_elem is not None:
                thumb_url = content_elem.attrib.get("url")
        if not thumb_url:
            encl = find_first_local(it, "enclosure")
            if encl is not None:
                thumb_url = encl.attrib.get("url")

        articles.append({
            "title": title,
            "link": link,
            "author": author,
            "categories": cats,
            "thumb_url": thumb_url,
        })

    return articles


def _fetch_windowscentral_articles(limit: int = 6) -> tuple[list[dict], str | None]:
    FEED_URL = "https://www.windowscentral.com/feeds.xml"
    ctx = ssl.create_default_context()
    try:
        with _urlopen_guarding_lightspeed(FEED_URL, context=ctx, timeout=8) as resp:
            data = resp.read()
        return _parse_windowscentral_feed(data, limit=limit), None
    except LightspeedBlockedError:
        return [], "Windows Central feed blocked by Lightspeed Filter Agent"
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", None)
        reason = getattr(e, "reason", None)
        if code:
            return [], f"Windows Central feed HTTP {int(code)}{(' ' + str(reason)) if reason else ''}".strip()
        return [], "Windows Central feed HTTP error"
    except urllib.error.URLError as e:
        r = getattr(e, "reason", None)
        if isinstance(r, socket.timeout):
            return [], "Windows Central feed timed out"
        if isinstance(r, ssl.SSLError):
            return [], "Windows Central feed SSL error"
        if r:
            return [], f"Windows Central feed network error: {str(r)}"
        return [], "Windows Central feed network error"
    except ET.ParseError:
        return [], "Windows Central feed returned invalid XML"
    except socket.timeout:
        return [], "Windows Central feed timed out"
    except ssl.SSLError:
        return [], "Windows Central feed SSL error"
    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        if len(msg) > 180:
            msg = msg[:180].rstrip() + "…"
        return [], f"Windows Central feed failed: {msg}"


def get_windowscentral_articles_cached(limit: int = 6, ttl_seconds: float = 10 * 60) -> tuple[list[dict], float, str | None]:
    global _articles_cache, _articles_updated_at

    now = time.time()
    with _articles_lock:
        if _articles_cache and (now - _articles_updated_at) < ttl_seconds:
            return list(_articles_cache)[:limit], _articles_updated_at, None

    # Fetch outside the lock (avoid blocking UI requests).
    try:
        articles, err = _fetch_windowscentral_articles(limit=limit)
        if err:
            raise RuntimeError(err)
    except Exception as e:
        detail = str(e).strip() or "Feed refresh failed"
        with _articles_lock:
            # Serve stale cache if we have it.
            if _articles_cache:
                return list(_articles_cache)[:limit], _articles_updated_at, f"{detail} (showing cached)"
        return [], now, detail

    with _articles_lock:
        _articles_cache = list(articles)
        _articles_updated_at = time.time()
        return list(_articles_cache)[:limit], _articles_updated_at, None


@app.route("/")
def index():
    import datetime

    is_win11 = False
    try:
        wv = sys.getwindowsversion()
        # Windows 11 first shipped as build 22000.
        is_win11 = int(getattr(wv, "build", 0) or 0) >= 22000
    except Exception:
        is_win11 = False

    cfg = get_config_cached()
    widgets_settings = cfg.get("widgets") if isinstance(cfg, dict) else None
    active_widgets = apply_widget_settings(_ALL_WIDGETS, widgets_settings if isinstance(widgets_settings, dict) else None)
    widgets_html, widgets_css, widgets_js = render_widget_bundle(active_widgets)

    return render_template_string(
        HTML,
        time=datetime.datetime.now().strftime("%H:%M:%S"),
        widgets_html=widgets_html,
        widgets_css=widgets_css,
        widgets_js=widgets_js,
        is_win11=is_win11,
    )


def _normalize_experimental_settings(cfg: dict | None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("experimental_settings")
    raw = raw if isinstance(raw, dict) else {}

    shell = raw.get("shell_integration")
    shell = shell if isinstance(shell, dict) else {}

    taskbar = shell.get("taskbar")
    taskbar = taskbar if isinstance(taskbar, dict) else {}

    cycle = taskbar.get("cycle")
    cycle = cycle if isinstance(cycle, dict) else {}
    weather = cycle.get("weather")
    weather = weather if isinstance(weather, dict) else {}
    news = cycle.get("news")
    news = news if isinstance(news, dict) else {}
    bdays = cycle.get("birthdays")
    bdays = bdays if isinstance(bdays, dict) else {}

    return {
        "shell_integration": {
            "override_win_w": bool(shell.get("override_win_w", False)),
            "touch_gesture": bool(shell.get("touch_gesture", False)),
            "taskbar": {
                "overlay": bool(taskbar.get("overlay", False)),
                "dynamic": bool(taskbar.get("dynamic", False)),
                "open_on_hover": bool(taskbar.get("open_on_hover", False)),
                "cycle": {
                    "weather": {
                        "current_conditions": bool(weather.get("current_conditions", True)),
                        "next_precip": bool(weather.get("next_precip", True)),
                    },
                    "news": {
                        "top_story": bool(news.get("top_story", True)),
                        "random_story": bool(news.get("random_story", False)),
                    },
                    "birthdays": {
                        "science": bool(bdays.get("science", True)),
                        "history": bool(bdays.get("history", True)),
                        "arts": bool(bdays.get("arts", True)),
                        "computer_science": bool(bdays.get("computer_science", True)),
                        "hackers": bool(bdays.get("hackers", True)),
                        "journalists": bool(bdays.get("journalists", True)),
                    },
                },
            },
        }
    }


@app.route("/settings/experimental", methods=["GET", "POST"])
def settings_experimental_api():
    if request.method == "GET":
        cfg = get_config_cached()
        out = _normalize_experimental_settings(cfg if isinstance(cfg, dict) else {})
        return app.response_class(
            response=json.dumps(out, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    # Best-effort validate/normalize; accept unknown keys but only persist known structure.
    next_settings = _normalize_experimental_settings({"experimental_settings": body} if "shell_integration" in body else {"experimental_settings": {"shell_integration": body.get("shell_integration")}})

    cfg = get_config_cached()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = dict(cfg)
    cfg["experimental_settings"] = next_settings
    if not save_config_file(cfg):
        return ("", 500)
    return ("", 204)


@app.route("/settings/advanced", methods=["GET", "POST"])
def settings_advanced_api():
    if request.method == "GET":
        cfg = get_config_cached()
        out = _normalize_advanced_settings(cfg if isinstance(cfg, dict) else {})
        return app.response_class(
            response=json.dumps(out, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    next_adv = {
        "disable_visual_effects": bool(body.get("disable_visual_effects", False)),
    }

    cfg = get_config_cached()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = dict(cfg)
    cfg["advanced_settings"] = next_adv
    if not save_config_file(cfg):
        return ("", 500)
    return ("", 204)


@app.route("/settings/experiments", methods=["GET", "POST"])
def settings_experiments_api():
    flags_doc = get_flags_cached()
    defs = flags_doc.get("flags") if isinstance(flags_doc, dict) else None
    defs = defs if isinstance(defs, list) else []

    cfg = get_config_cached()
    exp_state = _normalize_experiments_state(cfg if isinstance(cfg, dict) else {})
    stored_flags = exp_state.get("flags") if isinstance(exp_state, dict) else {}
    stored_flags = stored_flags if isinstance(stored_flags, dict) else {}

    if request.method == "GET":
        out_flags: list[dict] = []
        for f in defs:
            if not isinstance(f, dict):
                continue
            fid = f.get("id")
            if not isinstance(fid, str) or not fid.strip():
                continue
            fid = fid.strip()
            default_choice = f.get("default")
            default_choice = default_choice.strip() if isinstance(default_choice, str) and default_choice.strip() else "disabled"

            choices_def = f.get("choices")
            choices_def = choices_def if isinstance(choices_def, list) else []
            out_choices: list[dict] = []
            for c in choices_def:
                if not isinstance(c, dict):
                    continue
                cid = c.get("id")
                if not isinstance(cid, str) or not cid.strip():
                    continue
                cid = cid.strip()
                out_choices.append(
                    {
                        "id": cid,
                        "name": c.get("name") if isinstance(c.get("name"), str) else cid,
                        "description": c.get("description"),
                    }
                )

            # Ensure Default choice exists (Chrome-flags style).
            if not any((isinstance(c, dict) and c.get("id") == "default") for c in out_choices):
                out_choices.insert(0, {"id": "default", "name": "Default", "description": "Use the default behavior for this flag."})

            cur_choice = stored_flags.get(fid) if isinstance(stored_flags.get(fid), str) else "default"

            default_choice_name = None
            for c in out_choices:
                if c.get("id") == default_choice:
                    default_choice_name = c.get("name")
                    break
            if not default_choice_name:
                default_choice_name = default_choice

            out_flags.append(
                {
                    "id": fid,
                    "name": f.get("name"),
                    "description": f.get("description"),
                    "default_choice": default_choice,
                    "default_choice_name": default_choice_name,
                    "current_choice": cur_choice,
                    "choices": out_choices,
                }
            )

        return app.response_class(
            response=json.dumps({"flags": out_flags}, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    incoming = body.get("flags")
    if not isinstance(incoming, dict):
        return ("", 400)

    # Only allow flags that exist in flags.json.
    known: dict[str, dict] = {}
    for f in defs:
        if isinstance(f, dict) and isinstance(f.get("id"), str) and f.get("id").strip():
            known[f.get("id").strip()] = f

    out_store: dict[str, str] = {}
    for fid, choice in incoming.items():
        if not isinstance(fid, str) or fid.strip() not in known:
            continue
        fid = fid.strip()
        if not isinstance(choice, str):
            continue
        choice = choice.strip() or "default"

        # Validate choice IDs against the flag's known choices.
        choices_def = known[fid].get("choices")
        choices_def = choices_def if isinstance(choices_def, list) else []
        known_choice_ids = {"default"}
        for c in choices_def:
            if isinstance(c, dict) and isinstance(c.get("id"), str) and c.get("id").strip():
                known_choice_ids.add(c.get("id").strip())

        if choice not in known_choice_ids:
            continue
        out_store[fid] = choice

    cfg = get_config_cached()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = dict(cfg)
    cur = cfg.get("experiments")
    cur = cur if isinstance(cur, dict) else {}
    cur = dict(cur)
    cur["flags"] = out_store
    cfg["experiments"] = cur
    if not save_config_file(cfg):
        return ("", 500)
    return ("", 204)


@app.route("/api/windows/dwm/forceeffectmode")
def api_windows_forceeffectmode():
    if os.name != "nt":
        return app.response_class(
            response=json.dumps({"value": None}, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    value = None
    try:
        import winreg

        subkey = r"SOFTWARE\Microsoft\Windows\Dwm"
        access = winreg.KEY_READ
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            access |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, access) as key:
            try:
                v, t = winreg.QueryValueEx(key, "ForceEffectMode")
                if t == winreg.REG_DWORD:
                    value = int(v)
            except FileNotFoundError:
                value = None
            except OSError:
                value = None
    except Exception:
        value = None

    return app.response_class(
        response=json.dumps({"value": value}, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/open-taskbar-settings", methods=["POST"])
def open_taskbar_settings():
    uri = "ms-settings:taskbar"
    try:
        os.startfile(uri)
    except Exception:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
        except Exception:
            return ("", 500)
    return ("", 204)


@app.route("/about/version")
def about_version():
    try:
        wv = sys.getwindowsversion()
        os_str = f"Windows {wv.major}.{wv.minor} (build {wv.build})"
    except Exception:
        os_str = "Windows"

    widgets_out: list[dict] = []
    try:
        for w in _ALL_WIDGETS:
            widgets_out.append(
                {
                    "id": w.widget_id,
                    "name": w.manifest.name,
                    "version": w.manifest.version,
                    "author": w.manifest.author,
                }
            )
    except Exception:
        widgets_out = []

    payload = {
        "openwidgets_version": None,
        "python": sys.version.split("(", 1)[0].strip(),
        "os": os_str,
        "widgets": widgets_out,
    }
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _read_text_best_effort(path: str, max_bytes: int = 200_000) -> str:
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            data = f.read(max_bytes)
        return data
    except Exception:
        return ""


@app.route("/about/pixelnews/transparency")
def about_pixelnews_transparency():
    payload = {"html": pixelnews_transparency_html()}
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/settings/widgets", methods=["GET"])
def settings_widgets_get():
    cfg = get_config_cached()
    settings = cfg.get("widgets") if isinstance(cfg, dict) else None
    payload = widgets_settings_payload(_ALL_WIDGETS, settings if isinstance(settings, dict) else None)
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/settings/widgets", methods=["POST"])
def settings_widgets_post():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    enabled = body.get("enabled")
    order = body.get("order")
    if enabled is not None and not isinstance(enabled, dict):
        return ("", 400)
    if order is not None and not isinstance(order, list):
        return ("", 400)

    # Validate IDs against known widgets.
    known = {w.widget_id for w in _ALL_WIDGETS}
    out_enabled: dict[str, bool] = {}
    if isinstance(enabled, dict):
        for k, v in enabled.items():
            if isinstance(k, str):
                wid = k.strip().lower()
                if wid in known:
                    out_enabled[wid] = bool(v)

    out_order: list[str] = []
    if isinstance(order, list):
        for item in order:
            if isinstance(item, str):
                wid = item.strip().lower()
                if wid in known and wid not in out_order:
                    out_order.append(wid)

    cfg = get_config_cached()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = dict(cfg)
    cfg["widgets"] = {
        "enabled": out_enabled,
        "order": out_order,
    }

    if not save_config_file(cfg):
        return ("", 500)

    return ("", 204)


@app.route("/open", methods=["POST"])
def open_link():
    url = (request.args.get("url") or "").strip()
    if not url or len(url) > 2048:
        return ("", 400)

    try:
        p = urlparse(url)
    except Exception:
        return ("", 400)
    try:
        webbrowser.open(url, new=2, autoraise=True)
    except Exception:
        return ("", 500)

    return ("", 204)


@app.route("/api/internet/status")
def internet_status():
    force = (request.args.get("force") or "").strip() in ("1", "true", "yes", "on")
    online, checked_at = has_internet_cached(force=force)
    payload = {
        "online": bool(online),
        "checked_at": float(checked_at),
    }
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/api/profile/picture.png")
def profile_picture_png():
    # Return Windows account picture if available; otherwise fall back to app icon.
    path = get_profile_picture_path_cached()
    if path and os.path.exists(path):
        try:
            import mimetypes

            mt = mimetypes.guess_type(path)[0] or "image/png"
        except Exception:
            mt = "image/png"
        return send_file(path, mimetype=mt, max_age=0)

    fallback = os.path.join(_WORKSPACE_DIR, "AppIcons", "Icon-@1x.png")
    if os.path.exists(fallback):
        return send_file(fallback, mimetype="image/png", max_age=0)
    return ("", 404)


@app.route("/api/profile/display-name")
def profile_display_name():
    # Best-effort, gated behind experiments flag.
    now = time.time()
    with _DISPLAY_NAME_LOCK:
        global _DISPLAY_NAME_CACHE, _DISPLAY_NAME_CACHE_AT
        if _DISPLAY_NAME_CACHE is not None and (now - float(_DISPLAY_NAME_CACHE_AT)) < 30.0:
            payload = dict(_DISPLAY_NAME_CACHE)
        else:
            payload = _expanded_greeting_display_name_payload()
            _DISPLAY_NAME_CACHE = dict(payload)
            _DISPLAY_NAME_CACHE_AT = now

    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/api/userfeedconfig", methods=["GET", "POST"])
def userfeedconfig_api():
    """User-specific feed preferences (dummy MSA) stored in userfeedconfig.json."""
    DUMMY_EMAIL = "nyancrimew@outlook.com"
    id_re = re.compile(r"^[a-z0-9_-]{1,64}$")

    if request.method == "GET":
        cfg = get_userfeed_config_cached()
        if not isinstance(cfg, dict):
            cfg = {}
        cfg = dict(cfg)

        # OOBE status: distinguish "defaulted response" vs "persisted user config file".
        # This avoids first-run confusion where widgets read the file directly.
        try:
            userfeed_exists = bool(os.path.exists(_USERFEED_CONFIG_PATH))
        except Exception:
            userfeed_exists = False
        cfg["oobe"] = {
            "userfeedconfig_exists": userfeed_exists,
            "needs_setup": (not userfeed_exists),
        }

        cfg.setdefault("schema_version", 1)
        cfg["account"] = {"email": DUMMY_EMAIL, "provider": "Microsoft Account"}
        cfg.setdefault("region_language", "United States (English)")

        hs = cfg.get("hidden_sources")
        if not isinstance(hs, list):
            cfg["hidden_sources"] = []
        else:
            cfg["hidden_sources"] = [str(x).strip().lower() for x in hs if isinstance(x, str) and x.strip()]

        fc = cfg.get("followed_channels")
        if not isinstance(fc, list):
            cfg["followed_channels"] = []
        else:
            out_fc: list[str] = []
            for x in fc:
                if not isinstance(x, str) or not x.strip():
                    continue
                cid = re.sub(r"[^a-z0-9_-]+", "_", x.strip().lower()).strip("_")
                if cid and id_re.match(cid) and cid not in out_fc:
                    out_fc.append(cid)
            cfg["followed_channels"] = out_fc

        return app.response_class(
            response=json.dumps(cfg, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    region_language = body.get("region_language")
    if region_language is not None and not (isinstance(region_language, str) and region_language.strip()):
        region_language = None

    hidden_sources = body.get("hidden_sources")
    if hidden_sources is not None and not isinstance(hidden_sources, list):
        return ("", 400)

    followed_channels = body.get("followed_channels")
    if followed_channels is not None and not isinstance(followed_channels, list):
        return ("", 400)

    out_hidden: list[str] = []
    if isinstance(hidden_sources, list):
        for item in hidden_sources:
            if isinstance(item, str):
                sid = item.strip().lower()
                if sid and id_re.match(sid) and sid not in out_hidden:
                    out_hidden.append(sid)

    out_followed: list[str] = []
    if isinstance(followed_channels, list):
        for item in followed_channels:
            if isinstance(item, str):
                cid = re.sub(r"[^a-z0-9_-]+", "_", item.strip().lower()).strip("_")
                if cid and id_re.match(cid) and cid not in out_followed:
                    out_followed.append(cid)

    cfg = get_userfeed_config_cached()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = dict(cfg)
    cfg["schema_version"] = 1
    cfg["account"] = {"email": DUMMY_EMAIL, "provider": "Microsoft Account"}
    if region_language:
        cfg["region_language"] = region_language.strip()
    cfg["hidden_sources"] = out_hidden
    if followed_channels is not None:
        cfg["followed_channels"] = out_followed

    if not save_userfeed_config_file(cfg):
        return ("", 500)
    return ("", 204)


@app.route("/api/oobe/status")
def oobe_status_api():
    try:
        userfeed_exists = bool(os.path.exists(_USERFEED_CONFIG_PATH))
    except Exception:
        userfeed_exists = False
    payload = {
        "needs_oobe": (not userfeed_exists),
        "userfeedconfig_exists": userfeed_exists,
    }
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/api/oobe/ip-location")
def oobe_ip_location_api():
    loc, updated_at, err = _get_ipapi_location_cached()
    payload: dict = {
        "updated_at": float(updated_at),
        "location": loc if isinstance(loc, dict) else None,
    }
    if err:
        payload["error"] = err
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/api/oobe/complete", methods=["POST"])
def oobe_complete_api():
    """Complete first-run setup.

    Writes:
    - userfeedconfig.json (region_language, hidden_sources)
    - config.json weather settings (temp_unit + location mode)
    """

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    region_language = body.get("region_language")
    if not (isinstance(region_language, str) and region_language.strip()):
        return ("", 400)
    region_language = region_language.strip()

    hidden_sources = body.get("hidden_sources")
    if hidden_sources is not None and not isinstance(hidden_sources, list):
        return ("", 400)

    out_hidden: list[str] = []
    id_re = re.compile(r"^[a-z0-9_-]{1,64}$")
    if isinstance(hidden_sources, list):
        for item in hidden_sources:
            if isinstance(item, str):
                sid = item.strip().lower()
                if sid and id_re.match(sid) and sid not in out_hidden:
                    out_hidden.append(sid)

    # Units
    temp_unit = body.get("temp_unit")
    temp_unit = temp_unit.strip().upper() if isinstance(temp_unit, str) and temp_unit.strip() else None
    if temp_unit not in (None, "F", "C"):
        return ("", 400)

    # Location mode
    location_mode = body.get("location_mode")
    location_mode = location_mode.strip().lower() if isinstance(location_mode, str) else "ip"
    if location_mode not in ("ip", "manual"):
        return ("", 400)

    manual = body.get("manual")
    manual = manual if isinstance(manual, dict) else {}

    def _num(v):
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    lat = _num(manual.get("lat"))
    lon = _num(manual.get("lon"))
    city_name = manual.get("city_name")
    city_name = city_name.strip() if isinstance(city_name, str) and city_name.strip() else None
    country_code = manual.get("country_code")
    country_code = country_code.strip().upper() if isinstance(country_code, str) and country_code.strip() else None
    if country_code and len(country_code) != 2:
        country_code = None

    if location_mode == "manual":
        if lat is None or lon is None:
            return ("", 400)
        if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
            return ("", 400)

    # 1) Persist userfeedconfig.json
    DUMMY_EMAIL = "nyancrimew@outlook.com"
    userfeed_doc: dict = {
        "schema_version": 1,
        "account": {"email": DUMMY_EMAIL, "provider": "Microsoft Account"},
        "region_language": region_language,
        "hidden_sources": out_hidden,
        "followed_channels": [],
    }
    if not save_userfeed_config_file(userfeed_doc):
        return ("", 500)

    # 2) Update config.json weather section
    cfg = get_config_cached()
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    weather = cfg.get("weather")
    weather = dict(weather) if isinstance(weather, dict) else {}

    if temp_unit in ("F", "C"):
        weather["temp_unit"] = temp_unit

    if location_mode == "ip":
        # Allow /weather.json to use IP/device location by not forcing lat/lon.
        weather.pop("lat", None)
        weather.pop("lon", None)
        # Avoid pinning a stale label; allow IP-derived labels.
        weather.pop("city_name", None)
        weather.pop("country_code", None)
    else:
        weather["lat"] = float(lat) if lat is not None else weather.get("lat")
        weather["lon"] = float(lon) if lon is not None else weather.get("lon")
        if city_name:
            weather["city_name"] = city_name
        if country_code:
            weather["country_code"] = country_code

    cfg["weather"] = weather

    if not save_config_file(cfg):
        return ("", 500)

    return ("", 204)


@app.route("/api/userfeedconfig/delete", methods=["POST"])
def userfeedconfig_delete_api():
    # Delete userfeedconfig.json (best-effort).
    try:
        with _userfeed_lock:
            global _userfeed_cache, _userfeed_mtime
            _userfeed_cache = None
            _userfeed_mtime = None
            setattr(get_userfeed_config_cached, "_cached_at", 0.0)
        if os.path.exists(_USERFEED_CONFIG_PATH):
            os.remove(_USERFEED_CONFIG_PATH)
    except Exception:
        return ("", 500)
    return ("", 204)


@app.route("/open-network-settings", methods=["POST"])
def open_network_settings():
    # Open Windows network settings (best-effort).
    uri = "ms-settings:network-status"
    try:
        os.startfile(uri)
    except Exception:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
        except Exception:
            return ("", 500)
    return ("", 204)


@app.route("/ui/feed", methods=["POST"])
def ui_feed_toggle():
    expanded = (request.args.get("expanded") or "").strip() in ("1", "true", "yes", "on")
    host = _WIDGET_HOST
    if host is not None:
        try:
            host.request_set_width(FEED_EXPANDED_HOST_WIDTH if expanded else DEFAULT_HOST_WIDTH)
        except Exception:
            pass

    # Persist the UI state so the next launch restores the user's preference.
    try:
        cfg = get_config_cached()
        cfg = dict(cfg) if isinstance(cfg, dict) else {}
        ui = cfg.get("ui")
        ui = dict(ui) if isinstance(ui, dict) else {}
        ui["feed_expanded"] = bool(expanded)
        cfg["ui"] = ui
        save_config_file(cfg)
    except Exception:
        # Best-effort; feed toggle should still work even if persistence fails.
        pass
    return ("", 204)


@app.route("/api/ui/state", methods=["GET"])
def api_ui_state():
    cfg = get_config_cached()
    ui = _normalize_ui_config(cfg if isinstance(cfg, dict) else {})
    payload = {
        "feed_expanded": bool(ui.get("feed_expanded", False)),
    }
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/articles.json")
def articles_json():
    arts, updated_at, err = get_windowscentral_articles_cached(limit=6)
    payload = {
        "articles": arts,
        "updated_at": float(updated_at),
    }
    if err:
        payload["error"] = err
    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/weather.json")
def weather_json():
    cfg = _normalize_weather_config(get_config_cached())

    lat = _parse_float(request.args.get("lat"))
    lon = _parse_float(request.args.get("lon"))

    forced_location = False
    if "lat" in cfg and "lon" in cfg:
        lat = float(cfg["lat"])
        lon = float(cfg["lon"])
        forced_location = True

    loc_err = None
    city = None
    country_code = None
    # Config can force display name / country code regardless of how we got coordinates.
    if isinstance(cfg.get("city_name"), str) and cfg.get("city_name"):
        city = cfg.get("city_name")
    if isinstance(cfg.get("country_code"), str) and cfg.get("country_code"):
        country_code = cfg.get("country_code")

    if (lat is None or lon is None) and not forced_location:
        # If the client didn't provide coordinates, prefer Windows' device location.
        # Fall back to a best-effort IP-based lookup, then configured defaults.
        loc, _loc_updated_at, loc_err = _get_ipapi_location_cached()
        if loc is not None:
            try:
                lat = float(loc.get("lat"))
                lon = float(loc.get("lon"))
            except Exception:
                lat = None
                lon = None
            if city is None:
                city = loc.get("city") if isinstance(loc.get("city"), str) else None
            if country_code is None:
                country_code = loc.get("country_code") if isinstance(loc.get("country_code"), str) else None

    if lat is None:
        lat = DEFAULT_LAT
    if lon is None:
        lon = DEFAULT_LON

    preferred_temp_unit = cfg.get("temp_unit") if isinstance(cfg.get("temp_unit"), str) else "F"
    if preferred_temp_unit not in ("F", "C"):
        preferred_temp_unit = "F"

    # If we *know* we're outside the US, skip NWS calls.
    if country_code and country_code != "US":
        payload = {
            "updated_at": float(time.time()),
            "place_name": city.strip() if isinstance(city, str) and city.strip() else None,
            "temp_unit": preferred_temp_unit,
            "error": "Weather data is currently available only in the United States, as our data source is limited to the National Weather Service (NWS).",
        }
        if loc_err:
            payload["geoip_error"] = loc_err
        return app.response_class(
            response=json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )

    data, updated_at, err = get_weather_cached(lat, lon)
    payload: dict = {
        "updated_at": float(updated_at),
        "temp_unit": preferred_temp_unit,
    }
    if data is not None:
        payload.update(data)
    if isinstance(city, str) and city.strip():
        payload["place_name"] = city.strip()
    if loc_err and "error" not in payload:
        # Non-fatal informational error.
        payload["error"] = loc_err
    if err:
        payload["error"] = err

    return app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )

@app.route("/update_bg", methods=["POST"])
def update_bg():
    global _bg_png, _bg_updated_at
    if visual_effects_disabled():
        # Ignore background updates when effects are disabled/unavailable.
        return ("", 204)
    data = request.get_data(cache=False)
    if not data:
        return ("", 400)
    with _bg_lock:
        _bg_png = data
        _bg_updated_at = time.time()
    return ("", 204)


@app.route("/api/runtime/visual-effects", methods=["POST"])
def runtime_visual_effects_api():
    """Update runtime visual-effects state.

    This is used by the frontend to report auto-disabled effects (e.g. software
    rendering), so the Qt host can avoid taking acrylic screenshots and avoid
    resize animations.
    """
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ("", 400)

    # Support either {disabled: bool} or {enabled: bool}.
    if "disabled" in body:
        disabled = bool(body.get("disabled", False))
    elif "enabled" in body:
        disabled = not bool(body.get("enabled", True))
    else:
        disabled = False

    global _runtime_disable_visual_effects
    with _effects_lock:
        _runtime_disable_visual_effects = bool(disabled)
    return ("", 204)

@app.route("/bg.png")
def bg_png():
    if visual_effects_disabled():
        if os.path.exists(_FALLBACK_BG_PATH):
            return send_file(_FALLBACK_BG_PATH, mimetype="image/png", max_age=0)
        return ("", 404)
    with _bg_lock:
        data = _bg_png
    if not data:
        return ("", 404)
    return send_file(io.BytesIO(data), mimetype="image/png", max_age=0)


def _runtime_visual_effects_disabled() -> bool:
    with _effects_lock:
        return bool(_runtime_disable_visual_effects)


def _config_visual_effects_disabled() -> bool:
    try:
        cfg = get_config_cached()
        adv = _normalize_advanced_settings(cfg if isinstance(cfg, dict) else {})
        return bool(adv.get("disable_visual_effects", False))
    except Exception:
        return False


def visual_effects_disabled() -> bool:
    return _config_visual_effects_disabled() or _runtime_visual_effects_disabled()


def run_server():
    app.run(
        host="127.0.0.1",
        port=8765,
        debug=False,
        use_reloader=False,
        threaded=True
    )

# -------------------------
# Qt Widget Host
# -------------------------

class WidgetHost(QWidget):
    widthRequested = Signal(int)

    def __init__(self):
        super().__init__()
        
        # Enable dev extras (useful alongside remote debugging).
        try:
            QWebEngineSettings.globalSettings().setAttribute(
                QWebEngineSettings.WebAttribute.DeveloperExtrasEnabled,
                True,
            )
        except Exception:
            pass
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.BypassWindowManagerHint
        )

        self.setFocusPolicy(Qt.StrongFocus)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        screen = QApplication.primaryScreen().availableGeometry()
        width = DEFAULT_HOST_WIDTH

        self.setFixedSize(QSize(width, screen.height()))
        self.move(screen.left(), screen.top())

        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.view.setAttribute(Qt.WA_NoSystemBackground, True)
        self.view.setAutoFillBackground(False)
        self.view.setStyleSheet("background: transparent;")
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))

        self.view.setGeometry(0, 0, width, screen.height())
        self.view.setUrl(QUrl("http://127.0.0.1:8765"))

        hwnd = int(self.winId())
        enable_mica(hwnd)

        self._bg_timer = QTimer(self)
        self._bg_timer.setInterval(25500)
        self._bg_timer.timeout.connect(self.capture_and_report_bg)

        self._width_anim_timer = QTimer(self)
        self._width_anim_timer.setInterval(9)  # ~60fps
        self._width_anim_timer.timeout.connect(self._tick_width_animation)
        self._target_width: int | None = None
        self._animating_width = False

        self.widthRequested.connect(self._start_width_animation)

    def request_set_width(self, width: int):
        """Request a host resize from any thread.

        Flask handlers run on a background thread; Qt widgets must be resized on
        the GUI thread. We queue the resize onto the widget's thread.
        """
        try:
            width_i = int(width)
        except Exception:
            return

        try:
            QMetaObject.invokeMethod(
                self,
                "_start_width_animation",
                Qt.QueuedConnection,
                Q_ARG(int, width_i),
            )
        except Exception:
            # Best-effort fallback; avoid crashing if invokeMethod/Q_ARG fails.
            try:
                self.widthRequested.emit(int(width_i))
            except Exception:
                pass

    @Slot(int)
    def _start_width_animation(self, width: int):
        screen = QApplication.primaryScreen().availableGeometry()
        width = int(width)
        if width < 320:
            width = 320
        if width > max(320, int(screen.width())):
            width = int(screen.width())

        if visual_effects_disabled():
            # No animations when effects are disabled/unavailable.
            self._target_width = None
            try:
                self._width_anim_timer.stop()
            except Exception:
                pass
            self._animating_width = False
            self._set_width_now(int(width))
            return

        self._target_width = int(width)
        if not self._width_anim_timer.isActive():
            self._width_anim_timer.start()

    def _set_width_now(self, width: int) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        width = int(width)
        if width < 320:
            width = 320
        if width > max(320, int(screen.width())):
            width = int(screen.width())
        self.setFixedSize(QSize(width, screen.height()))
        self.view.setGeometry(0, 0, width, screen.height())

    def _tick_width_animation(self) -> None:
        if visual_effects_disabled():
            target = self._target_width
            self._target_width = None
            self._animating_width = False
            self._width_anim_timer.stop()
            if target is not None:
                self._set_width_now(int(target))
            return

        target = self._target_width
        if target is None:
            self._animating_width = False
            self._width_anim_timer.stop()
            return

        cur = int(self.width())
        diff = int(target) - int(cur)

        if diff == 0:
            self._animating_width = False
            self._width_anim_timer.stop()
            if not visual_effects_disabled():
                QTimer.singleShot(0, self.capture_and_report_bg)
            return

        self._animating_width = True

        ad = abs(diff)
        step = max(2, int(ad * 0.25))   # ease-out feel
        if step > 56:
            step = 56

        next_w = cur + (step if diff > 0 else -step)
        if (diff > 0 and next_w > target) or (diff < 0 and next_w < target):
            next_w = target

        self._set_width_now(int(next_w))

    @Slot(int)
    def _apply_width(self, width: int):
        # Optional: keep an "instant" helper for manual calls/debugging.
        self._set_width_now(int(width))
        if not visual_effects_disabled():
            QTimer.singleShot(0, self.capture_and_report_bg)

    def _post_png_to_server(self, png_bytes: bytes):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8765/update_bg",
                data=png_bytes,
                method="POST",
                headers={"Content-Type": "image/png"},
            )
            with _urlopen_with_proxy_setting(req, timeout=0.5, context=None, guard_lightspeed=False):
                pass
        except Exception:
            # Best-effort; if it fails, the UI still works (it just won't update the acrylic).
            pass

    def capture_and_report_bg(self):
        if visual_effects_disabled():
            return
        # Capture the screen area behind this window and POST it to the local web server.
        # To avoid capturing ourselves, temporarily make the window fully transparent.
        try:
            old_opacity = self.windowOpacity()
        except Exception:
            old_opacity = 1.0

        try:
            self.setWindowOpacity(0.0)
            QApplication.processEvents()

            geo = self.frameGeometry()
            screen = self.windowHandle().screen() if self.windowHandle() else QApplication.primaryScreen()
            if not screen:
                return

            pix = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            if pix.isNull():
                return

            # PNG encode
            from PySide6.QtCore import QBuffer, QByteArray
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.WriteOnly)
            pix.save(buf, "PNG")
            buf.close()

            self._post_png_to_server(bytes(ba))
        finally:
            self.setWindowOpacity(old_opacity)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

        if not visual_effects_disabled():
            # Prime the acrylic background ASAP, then keep it fresh.
            QTimer.singleShot(0, self.capture_and_report_bg)
            if not self._bg_timer.isActive():
                self._bg_timer.start()

    def event(self, e):
        if e.type() == QEvent.WindowDeactivate:
            # When remote debugging is enabled, keep the window open so DevTools
            # stays attached even if focus changes.
            if not _WEBVIEW_REMOTE_DEBUG:
                self.close()
                return True
            return False  # allow normal handling without closing

        if e.type() in (QEvent.Move, QEvent.Resize):
            if not self._animating_width and not visual_effects_disabled():
                QTimer.singleShot(0, self.capture_and_report_bg)
        return super().event(e)

# -------------------------
# Main
# -------------------------

if __name__ == "__main__":
    run_server()
