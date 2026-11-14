import json
import math
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


class LightspeedBlockedError(RuntimeError):
  pass


_ls_installed_cache: bool | None = None
_ls_installed_cache_at = 0.0


def _lightspeed_filter_agent_installed(ttl_seconds: float = 10.0) -> bool:
  """Best-effort check for Lightspeed Systems Filter Agent install on Windows."""
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


def _is_localhost_url(url: str) -> bool:
  try:
    host = (urllib.parse.urlsplit(str(url)).hostname or "").strip().lower()
  except Exception:
    host = ""
  return host in ("localhost", "127.0.0.1", "::1")


class _LightspeedRedirectGuard(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    if _lightspeed_filter_agent_installed() and _is_localhost_url(newurl):
      raise LightspeedBlockedError("blocked by Lightspeed Filter Agent")
    return super().redirect_request(req, fp, code, msg, headers, newurl)


def _use_system_proxy_for_requests() -> bool:
  try:
    cfg, _ = _get_config_cached()
    exp = cfg.get("experiments") if isinstance(cfg, dict) else None
    exp = exp if isinstance(exp, dict) else {}
    flags = exp.get("flags")
    flags = flags if isinstance(flags, dict) else {}
    choice = flags.get("use_system_proxy_for_requests")
    choice = choice.strip() if isinstance(choice, str) and choice.strip() else "default"
    if choice == "disabled":
      return False
    if choice == "enabled":
      return True
    return True
  except Exception:
    return True


def _urlopen_guarding_lightspeed(url: str, *, context: ssl.SSLContext | None, timeout: float):
  handlers: list[object] = []
  if not _use_system_proxy_for_requests():
    handlers.append(urllib.request.ProxyHandler({}))
  handlers.append(_LightspeedRedirectGuard())
  if context is not None:
    handlers.append(urllib.request.HTTPSHandler(context=context))
  opener = urllib.request.build_opener(*handlers)
  return opener.open(url, timeout=timeout)


_stories_lock = threading.Lock()
_stories_cache: list[dict] = []
_stories_updated_at = 0.0

_source_status_lock = threading.Lock()
_source_status: dict[str, dict] = {}
_last_refresh_status: dict[str, object] = {"at": 0.0, "ok": False, "error": None}


WIDGET_STORIES_LIMIT = 5
FEED_PAGE_SIZE = 15
MAX_STORIES = 200


_DEFAULT_SOURCES = [
]

_DEFAULT_SETTINGS = {
  # When false, behavior matches the previous implementation:
  # merge all sources and sort newest-to-oldest.
  "use_weighted_sources": True,
}

_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

_CONFIG_PATH = os.path.abspath(
  os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
)

_USERFEED_CONFIG_PATH = os.path.abspath(
  os.path.join(os.path.dirname(__file__), "..", "..", "userfeedconfig.json")
)

_DEFAULT_NEWS_PATH = os.path.abspath(
  os.path.join(os.path.dirname(__file__), "..", "..", "default_news.json")
)

_config_lock = threading.Lock()
_config_cache: dict | None = None
_config_mtime: float | None = None

_default_news_lock = threading.Lock()
_default_news_cache: dict | None = None
_default_news_mtime: float | None = None

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


def _get_userfeed_config_cached(ttl_seconds: float = 2.0) -> tuple[dict, float | None]:
  """Return parsed userfeedconfig.json (best-effort), cached by mtime + TTL."""
  global _userfeed_cache, _userfeed_mtime

  now = time.time()
  with _userfeed_lock:
    cached = _userfeed_cache
    cached_at = getattr(_get_userfeed_config_cached, "_cached_at", 0.0)
    if cached is not None and (now - float(cached_at)) < float(ttl_seconds):
      return dict(cached), _userfeed_mtime

  try:
    mtime = os.path.getmtime(_USERFEED_CONFIG_PATH) if os.path.exists(_USERFEED_CONFIG_PATH) else None
  except Exception:
    mtime = None

  with _userfeed_lock:
    if _userfeed_cache is not None and _userfeed_mtime == mtime and (now - float(getattr(_get_userfeed_config_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
      return dict(_userfeed_cache), _userfeed_mtime

  data = _load_userfeed_config_file()
  with _userfeed_lock:
    _userfeed_cache = dict(data)
    _userfeed_mtime = mtime
    setattr(_get_userfeed_config_cached, "_cached_at", now)
    return dict(_userfeed_cache), _userfeed_mtime


def _channel_id(raw: str) -> str:
  s = (raw or "").strip().lower()
  if not s:
    return ""
  s = re.sub(r"[^a-z0-9_-]+", "_", s).strip("_")
  return (s[:64] if s else "")


def _load_config_file() -> dict:
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


def _get_config_cached(ttl_seconds: float = 2.0) -> tuple[dict, float | None]:
  """Return parsed config.json (best-effort), cached by mtime + TTL."""
  global _config_cache, _config_mtime

  now = time.time()
  with _config_lock:
    cached = _config_cache
    cached_at = getattr(_get_config_cached, "_cached_at", 0.0)
    if cached is not None and (now - float(cached_at)) < float(ttl_seconds):
      return dict(cached), _config_mtime

  try:
    mtime = os.path.getmtime(_CONFIG_PATH) if os.path.exists(_CONFIG_PATH) else None
  except Exception:
    mtime = None

  with _config_lock:
    if _config_cache is not None and _config_mtime == mtime and (now - float(getattr(_get_config_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
      return dict(_config_cache), _config_mtime

  data = _load_config_file()
  with _config_lock:
    _config_cache = dict(data)
    _config_mtime = mtime
    setattr(_get_config_cached, "_cached_at", now)
    return dict(_config_cache), _config_mtime


def _load_default_news_file() -> dict:
  if not os.path.exists(_DEFAULT_NEWS_PATH):
    return {}

  try:
    with open(_DEFAULT_NEWS_PATH, "r", encoding="utf-8") as f:
      raw = f.read()
  except Exception:
    return {}

  try:
    data = json.loads(raw) if raw else {}
  except Exception:
    return {}

  return data if isinstance(data, dict) else {}


def _get_default_news_cached(ttl_seconds: float = 2.0) -> tuple[dict, float | None]:
  """Return parsed default_news.json (best-effort), cached by mtime + TTL."""
  global _default_news_cache, _default_news_mtime

  now = time.time()
  with _default_news_lock:
    cached = _default_news_cache
    cached_at = getattr(_get_default_news_cached, "_cached_at", 0.0)
    if cached is not None and (now - float(cached_at)) < float(ttl_seconds):
      return dict(cached), _default_news_mtime

  try:
    mtime = os.path.getmtime(_DEFAULT_NEWS_PATH) if os.path.exists(_DEFAULT_NEWS_PATH) else None
  except Exception:
    mtime = None

  with _default_news_lock:
    if _default_news_cache is not None and _default_news_mtime == mtime and (now - float(getattr(_get_default_news_cached, "_cached_at", 0.0))) < (ttl_seconds * 10):
      return dict(_default_news_cache), _default_news_mtime

  data = _load_default_news_file()
  with _default_news_lock:
    _default_news_cache = dict(data)
    _default_news_mtime = mtime
    setattr(_get_default_news_cached, "_cached_at", now)
    return dict(_default_news_cache), _default_news_mtime


def _parse_region_language(region_language: str | None) -> tuple[str | None, str | None]:
  if not isinstance(region_language, str) or not region_language.strip():
    return None, None
  s = region_language.strip()
  m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", s)
  if not m:
    return s, None
  region = (m.group(1) or "").strip() or s
  lang = (m.group(2) or "").strip() or None
  return region, lang


def _default_sources_for_region_language(region_language: str | None) -> list[dict]:
  data, _mtime = _get_default_news_cached()
  if not isinstance(data, dict) or not data:
    return list(_DEFAULT_SOURCES)

  def _pick_fallback_region_language() -> str | None:
    # Prefer a stable, familiar default if present.
    preferred = "United States (English)"

    regions = data.get("regions")
    if isinstance(regions, dict):
      if preferred in regions:
        return preferred
      for k in regions.keys():
        if isinstance(k, str) and k.strip():
          return k.strip()

    lr_map = data.get("language_region")
    if isinstance(lr_map, dict):
      # Prefer English/US if available.
      en = lr_map.get("English")
      if isinstance(en, dict):
        if "United States" in en:
          return preferred
        for r in en.keys():
          if isinstance(r, str) and r.strip():
            return f"{r.strip()} (English)"

      # Otherwise first language/region pair.
      for lang, reg_map in lr_map.items():
        if not (isinstance(lang, str) and lang.strip() and isinstance(reg_map, dict)):
          continue
        for r in reg_map.keys():
          if isinstance(r, str) and r.strip():
            return f"{r.strip()} ({lang.strip()})"

    # Back-compat drafts.
    langs = data.get("languages")
    if isinstance(langs, dict):
      for lang in langs.keys():
        if isinstance(lang, str) and lang.strip():
          return f"United States ({lang.strip()})" if lang.strip() == "English" else f"{lang.strip()}"

    language = data.get("language")
    if isinstance(language, dict):
      for lang in language.keys():
        if isinstance(lang, str) and lang.strip():
          return f"United States ({lang.strip()})" if lang.strip() == "English" else f"{lang.strip()}"

    return None

  # If the user hasn't picked a region/language yet, select a best-effort default.
  if not (isinstance(region_language, str) and region_language.strip()):
    region_language = _pick_fallback_region_language()

  region_key = region_language.strip() if isinstance(region_language, str) else None
  region, lang = _parse_region_language(region_language)

  lang_map = data.get("language")
  lr_map = data.get("language_region")

  def _pick_fallback_language() -> str | None:
    langs: list[str] = []

    if isinstance(lang_map, dict):
      for k in lang_map.keys():
        if isinstance(k, str) and k.strip():
          langs.append(k.strip())

    if isinstance(lr_map, dict):
      for k in lr_map.keys():
        if isinstance(k, str) and k.strip():
          langs.append(k.strip())

    seen: set[str] = set()
    unique: list[str] = []
    for k in langs:
      if k in seen:
        continue
      seen.add(k)
      unique.append(k)

    if "English" in seen:
      return "English"
    return unique[0] if unique else None

  # Tolerate swapped formats like "English (United States)".
  # The canonical internal representation is "Region (Language)".
  if isinstance(lang_map, dict) and isinstance(lang, str) and lang.strip():
    if lang not in lang_map and isinstance(region, str) and region.strip() and region in lang_map:
      region, lang = lang, region

  if not (isinstance(lang, str) and lang.strip()):
    lang = _pick_fallback_language()

  def _as_list(v) -> list[dict]:
    if not isinstance(v, list):
      return []
    out: list[dict] = []
    for it in v:
      if isinstance(it, dict):
        out.append(dict(it))
    return out

  combined: list[dict] = []

  # New schema:
  # - global: list
  # - language: { "English": [...] }
  # - language_region: { "English": { "United States": [...] } }
  combined.extend(_as_list(data.get("global")))
  if isinstance(lang_map, dict) and isinstance(lang, str) and lang:
    combined.extend(_as_list(lang_map.get(lang)))

  if isinstance(lr_map, dict) and isinstance(lang, str) and lang:
    reg_map = lr_map.get(lang)
    if isinstance(reg_map, dict) and isinstance(region_key, str) and region_key:
      combined.extend(_as_list(reg_map.get(region or "") or reg_map.get(region_key)))

  # Back-compat schema (older drafts):
  combined.extend(_as_list(data.get("default")))
  langs = data.get("languages")
  if isinstance(langs, dict) and isinstance(lang, str) and lang:
    combined.extend(_as_list(langs.get(lang)))
  regions = data.get("regions")
  if isinstance(regions, dict) and isinstance(region_key, str) and region_key:
    combined.extend(_as_list(regions.get(region_key)))

  by_id: dict[str, dict] = {}
  for it in combined:
    sid = (it.get("id") or "").strip().lower()
    if not sid:
      continue
    by_id[sid] = it

  return list(by_id.values()) if by_id else list(_DEFAULT_SOURCES)


def _normalize_sources(raw_sources, *, fallback_sources: list[dict] | None = None) -> list[dict]:
  if not isinstance(raw_sources, list):
    if isinstance(fallback_sources, list) and fallback_sources:
      return list(fallback_sources)
    return list(_DEFAULT_SOURCES)

  out: list[dict] = []

  def _num(v):
    try:
      if v is None:
        return None
      return float(v)
    except Exception:
      return None

  def _opt_str(v):
    if not isinstance(v, str):
      return None
    s = v.strip()
    return s if s else None

  def _clamp01(v, default: float = 0.5) -> float:
    n = _num(v)
    if n is None:
      return float(default)
    return max(0.0, min(1.0, float(n)))

  for item in raw_sources:
    if not isinstance(item, dict):
      continue

    url = (item.get("url") or "").strip()
    if not url:
      continue

    sid = (item.get("id") or "").strip().lower()
    if not sid or not _ID_RE.match(sid):
      sid = re.sub(r"[^a-z0-9_-]+", "_", sid or "source").strip("_") or "source"
      sid = sid[:64]

    name = (item.get("name") or sid).strip() or sid

    tagline = (item.get("tagline") or "").strip() or None
    if tagline and len(tagline) > 200:
      tagline = tagline[:200].rstrip()

    channels_in = item.get("channels")
    if isinstance(channels_in, str):
      channels_in = [c.strip() for c in channels_in.split(",") if c.strip()]
    channels: list[dict] = []
    if isinstance(channels_in, list):
      seen: set[str] = set()
      for c in channels_in:
        if not isinstance(c, str):
          continue
        name_c = c.strip()
        if not name_c:
          continue
        cid = _channel_id(name_c)
        if not cid or cid in seen:
          continue
        seen.add(cid)
        channels.append({"id": cid, "name": name_c})

    max_stories = 0
    try:
      max_stories = int(item.get("max_stories") if item.get("max_stories") is not None else item.get("allocate") or 0)
    except Exception:
      max_stories = 0
    if max_stories < 0:
      max_stories = 0

    try:
      priority = int(item.get("priority") if item.get("priority") is not None else 0)
    except Exception:
      priority = 0

    rnd = _num(item.get("randomness"))
    if rnd is None:
      rnd = _num(item.get("spread"))
    if rnd is None:
      rnd = 0.0
    rnd = max(0.0, min(1.0, float(rnd)))

    # Allow per-source field ignores for buggy feeds.
    # Supported fields: categories, author, thumb_url
    ignore: dict[str, bool] = {}
    raw_ignore = item.get("ignore")
    if isinstance(raw_ignore, dict):
      for k, v in raw_ignore.items():
        if isinstance(k, str):
          ignore[k.strip().lower()] = bool(v)

    raw_ignore_fields = item.get("ignore_fields")
    if isinstance(raw_ignore_fields, list):
      for k in raw_ignore_fields:
        if isinstance(k, str) and k.strip():
          ignore[k.strip().lower()] = True

    # Back-compat convenience keys.
    for k in ("ignore_categories", "ignore_author", "ignore_thumb_url"):
      if k in item:
        ignore[k.replace("ignore_", "").strip().lower()] = bool(item.get(k))

    out.append(
      {
        "id": sid,
        "name": name,
        "tagline": tagline,
        "url": url,
        "channels": list(channels),
        "max_stories": int(max_stories),
        "priority": int(priority),
        "randomness": float(rnd),
        "ignore": dict(ignore),

        # Source metadata (best-effort; used by UI dialogs)
        "ownership_category": _opt_str(item.get("ownership_category")) or "unknown / unclear ownership",
        "owner_name": _opt_str(item.get("owner_name")),
        "owner_url": _opt_str(item.get("owner_url")),
        "political_lean": _clamp01(item.get("political_lean"), 0.5),
        "logo_path": _opt_str(item.get("logo_path")),
        "description": _opt_str(item.get("description")),
      }
    )

  if out:
    return out
  if isinstance(fallback_sources, list) and fallback_sources:
    return list(fallback_sources)
  return list(_DEFAULT_SOURCES)


def _apply_source_ignores(stories: list[dict], source: dict) -> list[dict]:
  ign = source.get("ignore")
  if not isinstance(ign, dict) or not ign:
    return stories

  ignore_categories = bool(ign.get("categories"))
  ignore_author = bool(ign.get("author"))
  ignore_thumb = bool(ign.get("thumb_url"))

  if not (ignore_categories or ignore_author or ignore_thumb):
    return stories

  for st in stories:
    if not isinstance(st, dict):
      continue
    if ignore_categories:
      st["categories"] = []
    if ignore_author:
      st["author"] = None
    if ignore_thumb:
      st["thumb_url"] = None
  return stories


def _get_news_settings() -> tuple[dict, float | None]:
  cfg, mtime = _get_config_cached()

  ucfg, _ucfg_mtime = _get_userfeed_config_cached()
  region_language = ucfg.get("region_language") if isinstance(ucfg, dict) else None
  defaults = _default_sources_for_region_language(region_language if isinstance(region_language, str) else None)

  settings: dict | None = None
  if isinstance(cfg.get("widgets"), dict):
    w = cfg.get("widgets")
    if isinstance(w.get("windows_central"), dict):
      settings = w.get("windows_central")

  if not isinstance(settings, dict):
    out = dict(_DEFAULT_SETTINGS)
    out["sources"] = _normalize_sources(defaults, fallback_sources=defaults)
    return out, mtime

  out = dict(_DEFAULT_SETTINGS)

  use_weighted_sources = settings.get("use_weighted_sources")
  if isinstance(use_weighted_sources, bool):
    out["use_weighted_sources"] = bool(use_weighted_sources)

  if "sources" in settings:
    out["sources"] = _normalize_sources(settings.get("sources"), fallback_sources=defaults)
  else:
    out["sources"] = _normalize_sources(defaults, fallback_sources=defaults)

  return out, mtime


def _mix_sources_weighted(*, sources: list[dict], per_source: dict[str, list[dict]], limit: int, followed_channels: set[str] | None = None) -> list[dict]:
  followed_channels = set(followed_channels or set())

  # Story-quality penalties: prefer items with richer metadata.
  # - Missing images are penalized strongly.
  # - Missing categories/author/date are penalized mildly.
  _NO_IMAGE_WEIGHT = 0.35
  _NO_CATEGORIES_WEIGHT = 0.85
  _NO_AUTHOR_WEIGHT = 0.90
  _NO_DATE_WEIGHT = 0.80

  _NO_IMAGE_PENALTY_SECONDS = 6 * 3600
  _NO_CATEGORIES_PENALTY_SECONDS = 1 * 3600
  _NO_AUTHOR_PENALTY_SECONDS = 1 * 3600
  _NO_DATE_PENALTY_SECONDS = 2 * 3600

  def _has_thumb(story: dict) -> bool:
    v = story.get("thumb_url")
    return bool(isinstance(v, str) and v.strip())

  def _has_author(story: dict) -> bool:
    v = story.get("author")
    return bool(isinstance(v, str) and v.strip())

  def _has_categories(story: dict) -> bool:
    v = story.get("categories")
    return bool(isinstance(v, list) and any(isinstance(c, str) and c.strip() for c in v))

  def _has_date(story: dict) -> bool:
    ts = story.get("published_at")
    try:
      return float(ts) > 0.0
    except Exception:
      return False

  def _story_quality_weight(story: dict) -> float:
    w = 1.0
    if not _has_thumb(story):
      w *= _NO_IMAGE_WEIGHT
    if not _has_categories(story):
      w *= _NO_CATEGORIES_WEIGHT
    if not _has_author(story):
      w *= _NO_AUTHOR_WEIGHT
    if not _has_date(story):
      w *= _NO_DATE_WEIGHT
    # Avoid collapsing to 0 which can break weighted selection.
    return max(0.01, float(w))

  def _story_penalty_seconds(story: dict) -> float:
    p = 0.0
    if not _has_thumb(story):
      p += float(_NO_IMAGE_PENALTY_SECONDS)
    if not _has_categories(story):
      p += float(_NO_CATEGORIES_PENALTY_SECONDS)
    if not _has_author(story):
      p += float(_NO_AUTHOR_PENALTY_SECONDS)
    if not _has_date(story):
      p += float(_NO_DATE_PENALTY_SECONDS)
    return p

  def _effective_published_at(story: dict) -> float:
    ts = story.get("published_at")
    try:
      base = float(ts or 0.0)
    except Exception:
      base = 0.0
    return base - _story_penalty_seconds(story)

  def _channel_boost(src: dict) -> float:
    if not followed_channels:
      return 1.0
    raw = src.get("channels")
    if not isinstance(raw, list) or not raw:
      return 1.0
    overlap = 0
    for c in raw:
      if isinstance(c, dict):
        cid = c.get("id")
      else:
        cid = c
      if isinstance(cid, str) and cid in followed_channels:
        overlap += 1
    if overlap <= 0:
      return 1.0
    # First matching channel is a strong boost; additional matches taper off.
    return 1.60 * (1.15 ** max(0, overlap - 1))
  # If nothing is configured for randomness, fall back to the classic behavior.
  max_rnd = 0.0
  for s in sources:
    try:
      max_rnd = max(max_rnd, float(s.get("randomness") or 0.0))
    except Exception:
      continue

  if max_rnd <= 0.0:
    merged: list[dict] = []
    for sid, items in per_source.items():
      merged.extend(items)
    merged.sort(key=_effective_published_at, reverse=True)
    return merged[:limit]

  queues: dict[str, list[dict]] = {}
  for s in sources:
    sid = s.get("id")
    if not isinstance(sid, str):
      continue
    items = list(per_source.get(sid) or [])
    items.sort(key=_effective_published_at, reverse=True)
    queues[sid] = items

  now = time.time()
  combined: list[dict] = []
  last_sid: str | None = None
  run_len = 0

  def _time_weight(ts: float | None) -> float:
    if not ts:
      # Unknown dates should be mildly penalized (further reduced below by _NO_DATE_WEIGHT).
      return 0.25
    age_hours = max(0.0, (now - float(ts)) / 3600.0)
    # 24h half-life-ish: older items rapidly become less likely.
    return math.exp(-age_hours / 24.0)

  while len(combined) < int(limit):
    candidates: list[tuple[str, float, float, dict]] = []

    for s in sources:
      sid = s.get("id")
      if not isinstance(sid, str):
        continue
      q = queues.get(sid) or []
      if not q:
        continue

      story = q[0]
      ts = story.get("published_at")

      try:
        rnd = float(s.get("randomness") or 0.0)
      except Exception:
        rnd = 0.0
      rnd = max(0.0, min(1.0, rnd))

      try:
        priority = int(s.get("priority") or 0)
      except Exception:
        priority = 0

      w_time = _time_weight(ts)

      # Priority influences selection only insofar as randomness is enabled.
      # rnd=0 -> priority has no effect (pure newest-first)
      # rnd=1 -> strong effect
      # Use an exponential curve so negative priorities meaningfully suppress.
      # Clamp to avoid extreme weights.
      pri_clamped = max(-20.0, min(20.0, float(priority)))
      w_pri = math.exp(pri_clamped * rnd * 0.45)

      w = w_time * w_pri

      # Prefer higher-quality items.
      w *= _story_quality_weight(story)

      # Channel boost (personalization): sources in followed channels appear more often
      # and earlier in the combined feed.
      w *= _channel_boost(s)

      # Spread-out penalty (discourage repeating the same source).
      if last_sid == sid and rnd > 0:
        w *= math.pow(max(0.05, (1.0 - rnd)), max(1, int(run_len)))

      candidates.append((sid, w, rnd, story))

    if not candidates:
      break

    # With probability (1-max_rnd), pick deterministically by best weight.
    if random.random() > max_rnd:
      chosen_sid = max(candidates, key=lambda c: (c[1], _effective_published_at(c[3])))[0]
    else:
      total = sum(max(0.0, c[1]) for c in candidates)
      if total <= 0.0:
        chosen_sid = max(candidates, key=lambda c: _effective_published_at(c[3]))[0]
      else:
        r = random.random() * total
        acc = 0.0
        chosen_sid = candidates[-1][0]
        for sid, w, _rnd, _story in candidates:
          w2 = max(0.0, w)
          acc += w2
          if r <= acc:
            chosen_sid = sid
            break

    story = queues[chosen_sid].pop(0)
    combined.append(story)

    if chosen_sid == last_sid:
      run_len += 1
    else:
      last_sid = chosen_sid
      run_len = 1

  return combined[:limit]


def _parse_rss(xml_bytes: bytes, *, publication: str, limit: int) -> list[dict]:
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

  def parse_pubdate(raw):
    if not raw:
      return None
    try:
      dt = parsedate_to_datetime(raw)
      if dt is None:
        return None
      return dt.timestamp()
    except Exception:
      return None

  items = root.findall(".//item")
  stories = []
  for it in items[: max(0, int(limit))]:
    title = child_text(it, "title") or "Untitled"
    link = child_text(it, "link") or ""
    author_raw = child_text(it, "author") or child_text(it, "creator")
    author = extract_author(author_raw)
    published_at = parse_pubdate(child_text(it, "pubDate"))

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

    stories.append(
      {
        "title": title,
        "link": link,
        "author": author,
        "categories": cats,
        "thumb_url": thumb_url,
        "publication": publication,
        "published_at": published_at,
      }
    )

  return stories


def _fetch_source_stories(source: dict, *, limit: int) -> list[dict]:
  url = (source.get("url") or "").strip()
  if not url:
    return []
  ctx = ssl.create_default_context()
  with _urlopen_guarding_lightspeed(url, context=ctx, timeout=8) as resp:
    data = resp.read()
  publication = (source.get("name") or source.get("id") or "News").strip()
  stories = _parse_rss(data, publication=publication, limit=limit)
  sid = (source.get("id") or "").strip()
  if sid:
    for st in stories:
      st["source_id"] = sid
  return _apply_source_ignores(stories, source)


def _describe_fetch_error(source: dict, exc: Exception) -> str:
  sid = (source.get("id") or "").strip() or "source"
  name = (source.get("name") or sid).strip() or sid

  if isinstance(exc, LightspeedBlockedError):
    return f"{name} ({sid}): blocked by Lightspeed Filter Agent"

  # urllib errors
  if isinstance(exc, urllib.error.HTTPError):
    code = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None)
    if code:
      return f"{name} ({sid}): HTTP {int(code)}{(' ' + str(reason)) if reason else ''}".strip()
    return f"{name} ({sid}): HTTP error"

  if isinstance(exc, urllib.error.URLError):
    r = getattr(exc, "reason", None)
    if isinstance(r, socket.timeout):
      return f"{name} ({sid}): timed out"
    if isinstance(r, ssl.SSLError):
      return f"{name} ({sid}): SSL error"
    if isinstance(r, OSError) and getattr(r, "winerror", None):
      return f"{name} ({sid}): network error (WinError {int(r.winerror)})"
    if r:
      return f"{name} ({sid}): {str(r)}"
    return f"{name} ({sid}): network error"

  # XML parse errors
  if isinstance(exc, ET.ParseError):
    return f"{name} ({sid}): invalid RSS/XML"

  # Timeouts / TLS
  if isinstance(exc, socket.timeout):
    return f"{name} ({sid}): timed out"
  if isinstance(exc, ssl.SSLError):
    return f"{name} ({sid}): SSL error"

  # Generic
  msg = str(exc).strip()
  if msg:
    if len(msg) > 160:
      msg = msg[:160].rstrip() + "…"
    return f"{name} ({sid}): {msg}"
  return f"{name} ({sid}): {type(exc).__name__}"


def _summarize_failures(failures: list[str]) -> str:
  if not failures:
    return "Feed refresh failed."
  shown = failures[:3]
  more = len(failures) - len(shown)
  tail = (f" (+{more} more)") if more > 0 else ""
  return "All sources failed: " + "; ".join(shown) + tail


_stories_config_mtime: float | None = None
_stories_userfeed_mtime: float | None = None


def get_stories_cached(limit: int = 30, ttl_seconds: float = 10 * 60, *, force: bool = False) -> tuple[list[dict], float, str | None]:
  global _stories_cache, _stories_updated_at

  settings, cfg_mtime = _get_news_settings()
  global _stories_config_mtime
  global _stories_userfeed_mtime

  ucfg, ucfg_mtime = _get_userfeed_config_cached()

  now = time.time()
  with _stories_lock:
    if (not force) and _stories_cache and (now - _stories_updated_at) < ttl_seconds and (_stories_config_mtime == cfg_mtime) and (_stories_userfeed_mtime == ucfg_mtime):
      return list(_stories_cache)[:limit], float(_stories_updated_at), None

  sources = _normalize_sources(settings.get("sources"))
  use_weighted = bool(settings.get("use_weighted_sources"))

  # User preferences (dummy single-user): hide sources + follow channels.
  hidden_sources: set[str] = set()
  hs = ucfg.get("hidden_sources")
  if isinstance(hs, list):
    for it in hs:
      if isinstance(it, str) and it.strip():
        hidden_sources.add(it.strip().lower())

  followed_channels: set[str] = set()
  fc = ucfg.get("followed_channels")
  if isinstance(fc, list):
    for it in fc:
      if isinstance(it, str) and it.strip():
        cid = _channel_id(it)
        if cid:
          followed_channels.add(cid)

  if hidden_sources:
    sources = [s for s in sources if isinstance(s.get("id"), str) and str(s.get("id")).strip().lower() not in hidden_sources]

  # Avoid extremely large per-source fetches; the merged cache is capped anyway.
  fetch_limit = max(8, min(80, int(limit)))

  per_source_map: dict[str, list[dict]] = {}
  had_success = False
  failures: list[str] = []
  for src in list(sources):
    sid = (src.get("id") or "").strip()
    if not sid:
      continue
    attempt_started = time.time()
    try:
      items = _fetch_source_stories(src, limit=fetch_limit)
      ms = 0
      try:
        ms = int(src.get("max_stories") or 0)
      except Exception:
        ms = 0
      if ms > 0:
        items = items[:ms]

      per_source_map.setdefault(sid, []).extend(items)
      had_success = True

      with _source_status_lock:
        prev = _source_status.get(sid) if isinstance(_source_status.get(sid), dict) else {}
        last_success_at = float(prev.get("last_success_at") or 0.0)
        _source_status[sid] = {
          "id": sid,
          "name": (src.get("name") or sid),
          "url": src.get("url"),
          "ok": True,
          "last_attempt_at": float(attempt_started),
          "last_success_at": float(max(last_success_at, attempt_started)),
          "last_error": None,
          "last_items": int(len(items)),
          "updated_at": float(time.time()),
        }
    except Exception as e:
      msg = _describe_fetch_error(src, e)
      failures.append(msg)
      with _source_status_lock:
        prev = _source_status.get(sid) if isinstance(_source_status.get(sid), dict) else {}
        _source_status[sid] = {
          "id": sid,
          "name": (src.get("name") or sid),
          "url": src.get("url"),
          "ok": False,
          "last_attempt_at": float(attempt_started),
          "last_success_at": float(prev.get("last_success_at") or 0.0),
          "last_error": msg,
          "last_items": int(prev.get("last_items") or 0),
          "updated_at": float(time.time()),
        }
      continue

  if not had_success:
    err_msg = _summarize_failures(failures)

    with _source_status_lock:
      _last_refresh_status = {"at": float(time.time()), "ok": False, "error": err_msg}

    with _stories_lock:
      if _stories_cache:
        return list(_stories_cache)[:limit], float(_stories_updated_at or now), f"{err_msg} (showing cached)"
    return [], float(now), err_msg

  combined: list[dict] = []
  if use_weighted:
    combined = _mix_sources_weighted(
      sources=sources,
      per_source=per_source_map,
      limit=min(MAX_STORIES, int(limit)),
      followed_channels=followed_channels,
    )
  else:
    for sid, items in per_source_map.items():
      combined.extend(items)
    # Prefer newest first if timestamps exist.
    combined.sort(key=lambda s: (s.get("published_at") or 0.0), reverse=True)

  with _stories_lock:
    _stories_cache = list(combined)
    _stories_updated_at = time.time()
    _stories_config_mtime = cfg_mtime
    _stories_userfeed_mtime = ucfg_mtime

  with _source_status_lock:
    _last_refresh_status = {"at": float(_stories_updated_at), "ok": True, "error": None}

  return list(_stories_cache)[:limit], float(_stories_updated_at), None


def register(app, url_prefix: str):
  @app.route(f"{url_prefix}/stories.json")
  def news_stories_json():
    from flask import request

    force = request.args.get("force")
    force_refresh = isinstance(force, str) and force.strip().lower() in ("1", "true", "yes", "on")

    limit = FEED_PAGE_SIZE
    offset = 0
    try:
      limit = int(request.args.get("limit") or limit)
    except Exception:
      limit = FEED_PAGE_SIZE

    try:
      offset = int(request.args.get("offset") or offset)
    except Exception:
      offset = 0

    if limit < 1:
      limit = 1
    if limit > MAX_STORIES:
      limit = MAX_STORIES

    if offset < 0:
      offset = 0
    if offset > MAX_STORIES:
      offset = MAX_STORIES

    # Always hydrate a larger cache so offset-based pagination works.
    if force_refresh:
      stories_all, updated_at, err = get_stories_cached(limit=MAX_STORIES, ttl_seconds=0.0, force=True)
    else:
      stories_all, updated_at, err = get_stories_cached(limit=MAX_STORIES)
    stories = list(stories_all)[offset : offset + limit]
    has_more = (offset + limit) < len(stories_all)
    next_offset = (offset + len(stories)) if has_more else None
    settings, _cfg_mtime = _get_news_settings()
    sources = _normalize_sources(settings.get("sources"))

    # Expose channels for the PixelNews personalization UI.
    channel_map: dict[str, str] = {}
    for s in sources:
      raw = s.get("channels")
      if not isinstance(raw, list):
        continue
      for c in raw:
        if isinstance(c, dict):
          cid = c.get("id")
          cname = c.get("name")
        else:
          cid = c
          cname = c
        if isinstance(cid, str) and cid and isinstance(cname, str) and cname.strip():
          channel_map.setdefault(cid, cname.strip())
    payload = {
      "stories": stories,
      "updated_at": float(updated_at),
      "sources": [
        {
          "id": s.get("id"),
          "name": s.get("name"),
          "tagline": s.get("tagline"),
          "url": s.get("url"),
          "ownership_category": s.get("ownership_category"),
          "owner_name": s.get("owner_name"),
          "owner_url": s.get("owner_url"),
          "political_lean": float(s.get("political_lean") or 0.5),
          "logo_path": s.get("logo_path"),
          "description": s.get("description"),
          "channels": [
            (c.get("id") if isinstance(c, dict) else c)
            for c in (s.get("channels") if isinstance(s.get("channels"), list) else [])
            if isinstance((c.get("id") if isinstance(c, dict) else c), str)
          ],
          "max_stories": int(s.get("max_stories") or 0),
          "priority": int(s.get("priority") or 0),
          "randomness": float(s.get("randomness") or 0.0),
          "ignore": (s.get("ignore") if isinstance(s.get("ignore"), dict) else {}),
        }
        for s in sources
      ],
      "channels": [
        {"id": cid, "name": channel_map[cid]}
        for cid in sorted(channel_map.keys())
      ],
      "use_weighted_sources": bool(settings.get("use_weighted_sources")),
      "offset": int(offset),
      "limit": int(limit),
      "total": int(len(stories_all)),
      "has_more": bool(has_more),
      "next_offset": next_offset,
    }
    if err:
      payload["error"] = err

    return app.response_class(
      response=json.dumps(payload, ensure_ascii=False),
      mimetype="application/json",
      headers={"Cache-Control": "no-store"},
    )

  @app.route(f"{url_prefix}/status.json")
  def news_sources_status_json():
    from flask import request

    force = request.args.get("force")
    if isinstance(force, str) and force.strip().lower() in ("1", "true", "yes"):
      # Force a refresh so the debug UI can verify fetch works.
      get_stories_cached(limit=MAX_STORIES, ttl_seconds=0.0, force=True)

    settings, _cfg_mtime = _get_news_settings()
    sources = _normalize_sources(settings.get("sources"))
    ucfg, _ucfg_mtime = _get_userfeed_config_cached()

    hidden_sources: set[str] = set()
    hs = ucfg.get("hidden_sources")
    if isinstance(hs, list):
      for it in hs:
        if isinstance(it, str) and it.strip():
          hidden_sources.add(it.strip().lower())
    if hidden_sources:
      sources = [s for s in sources if isinstance(s.get("id"), str) and str(s.get("id")).strip().lower() not in hidden_sources]

    with _source_status_lock:
      snap_status = dict(_source_status)
      refresh = dict(_last_refresh_status)

    out_sources: list[dict] = []
    for s in sources:
      sid = (s.get("id") or "").strip()
      if not sid:
        continue
      st = snap_status.get(sid) if isinstance(snap_status.get(sid), dict) else None
      out_sources.append(
        {
          "id": sid,
          "name": (s.get("name") or sid),
          "url": s.get("url"),
          "ok": (st.get("ok") if st else None),
          "last_attempt_at": float(st.get("last_attempt_at") or 0.0) if st else 0.0,
          "last_success_at": float(st.get("last_success_at") or 0.0) if st else 0.0,
          "last_error": (st.get("last_error") if st else None),
          "last_items": int(st.get("last_items") or 0) if st else 0,
        }
      )

    payload = {
      "ok": True,
      "refresh": refresh,
      "cache": {
        "updated_at": float(_stories_updated_at or 0.0),
        "count": int(len(_stories_cache) if isinstance(_stories_cache, list) else 0),
      },
      "sources": out_sources,
    }

    return app.response_class(
      response=json.dumps(payload, ensure_ascii=False),
      mimetype="application/json",
      headers={"Cache-Control": "no-store"},
    )

def get_widget():
    return {
        "html": """
<div class=\"small\" data-wc-status>Loading…</div>
<div class=\"articles\" data-wc-articles></div>
""".strip(),
        "css": """
/* Scoped via #widget-windows_central */
#widget-windows_central .articles { display: flex; flex-direction: column; gap: 10px; }
#widget-windows_central .article { display: flex; gap: 10px; align-items: flex-start; }
#widget-windows_central .thumb {
  width: 72px; height: 54px; border-radius: 6px; object-fit: cover;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  flex: 0 0 auto;
}
#widget-windows_central .meta { min-width: 0; }
#widget-windows_central .title { font-size: 13px; line-height: 1.25; margin-bottom: 4px; }
#widget-windows_central .title a {
  color: rgba(255, 255, 255, 0.92);
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
#widget-windows_central .title a:hover { text-decoration: underline; }
#widget-windows_central .byline {
  font-size: 11px; opacity: 0.75; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
#widget-windows_central .byline a.pub {
  color: rgba(255, 255, 255, 0.92);
  text-decoration: none;
}
#widget-windows_central .byline a.pub:hover { text-decoration: underline; }
#widget-windows_central .cats { font-size: 11px; opacity: 0.70; margin-top: 2px; display: flex; flex-wrap: wrap; gap: 6px; }
#widget-windows_central .cat {
  display: inline-flex; align-items: center;
  padding: 2px 6px; border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.05);
}
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('windows_central') : null;
  if (!root) return;

  const wcStatus = root.querySelector('[data-wc-status]');
  const wcArticles = root.querySelector('[data-wc-articles]');
  if (!wcStatus || !wcArticles) return;

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function renderArticles(payload) {
    wcArticles.innerHTML = '';
    const items = payload && payload.stories ? payload.stories : (payload && payload.articles ? payload.articles : []);
    if (!items || !items.length) {
      wcStatus.textContent = payload && payload.error ? payload.error : 'No stories available.';
      wcStatus.className = payload && payload.error ? 'error' : 'small';
      return;
    }

    wcStatus.textContent = '';
    wcStatus.className = 'small';

    for (const a of items.slice(0, 5)) {
      const thumb = a.thumb_url ? `<img class="thumb" src="${esc(a.thumb_url)}" alt="" />` : `<div class="thumb"></div>`;
      const author = a.author ? ` • ${esc(a.author)}` : '';
      const cats = (a.categories || []).slice(0, 4).map(c => `<span class="cat">${esc(c)}</span>`).join('');
      const publication = a.publication ? String(a.publication) : 'News';
      const sourceId = a.source_id ? String(a.source_id) : '';
      const pubHtml = sourceId
        ? `<a class="pub" href="#" data-source-id="${esc(sourceId)}" title="About ${esc(publication)}">${esc(publication)}</a>`
        : `${esc(publication)}`;

      const el = document.createElement('div');
      el.className = 'article';
      el.innerHTML = `
        ${thumb}
        <div class="meta">
          <div class="title"><a href="#" data-url="${esc(a.link)}" title="${esc(a.title)}">${esc(a.title)}</a></div>
          <div class="byline">${pubHtml}${author}</div>
          ${cats ? `<div class="cats">${cats}</div>` : ''}
        </div>
      `;
      wcArticles.appendChild(el);
    }
  }

  wcArticles.addEventListener('click', (e) => {
    const src = e.target && e.target.closest ? e.target.closest('a[data-source-id]') : null;
    if (src) {
      e.preventDefault();
      const sid = src.getAttribute('data-source-id') || '';
      if (sid && window.openSourceInfo) window.openSourceInfo(sid);
      return;
    }

    const a = e.target && e.target.closest ? e.target.closest('a[data-url]') : null;
    if (!a) return;
    e.preventDefault();
    const url = a.getAttribute('data-url') || '';
    if (url && window.WidgetHost && window.WidgetHost.openExternal) window.WidgetHost.openExternal(url);
  });

  window.addEventListener('userfeedconfig-changed', refreshWC);

  async function refreshWC() {
    try {
      const resp = await fetch('/widgets/windows_central/stories.json?limit=5&offset=0&t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      renderArticles(data);
    } catch (e) {
      renderArticles({ stories: [], error: 'Failed to load feed.' });
    }
  }

  setInterval(refreshWC, 10 * 60 * 1000);
  window.addEventListener('focus', refreshWC);
  refreshWC();
})();
""".strip(),
    }
