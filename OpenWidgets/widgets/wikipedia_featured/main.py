import json
import os
import ssl
import threading
import time
import urllib.request
from datetime import date

from flask import jsonify


WIDGET_TITLE = "Wikipedia Featured Article"

_CONFIG_PATH = os.path.abspath(
  os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
)

_cache_lock = threading.Lock()
_cache_payload: dict | None = None
_cache_for_day: str | None = None
_cache_checked_at = 0.0


def _today_key() -> str:
    # Local-date is fine for a "today" widget.
    d = date.today()
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _use_system_proxy_for_requests() -> bool:
  try:
    if not os.path.exists(_CONFIG_PATH):
      return True
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
      cfg = json.load(f)
    if not isinstance(cfg, dict):
      return True
    exp = cfg.get("experiments")
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


def _fetch_json(url: str, *, timeout_seconds: float = 8.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            # Wikimedia APIs require a User-Agent identifying the app.
            "User-Agent": "Widgets/1.0 (local; wikipedia_featured widget)",
            "Accept": "application/json",
        },
        method="GET",
    )

    ctx = ssl.create_default_context()
    handlers: list[object] = []
    if not _use_system_proxy_for_requests():
      handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=float(timeout_seconds)) as resp:
        raw = resp.read()

    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        data = {}

    return data if isinstance(data, dict) else {}


def _extract_tfa(feed: dict) -> dict:
    tfa = feed.get("tfa") if isinstance(feed, dict) else None
    if not isinstance(tfa, dict):
        return {}

    title = (tfa.get("title") or "").strip()
    extract = (tfa.get("extract") or "").strip()

    url = None
    content_urls = tfa.get("content_urls")
    if isinstance(content_urls, dict):
        desktop = content_urls.get("desktop")
        if isinstance(desktop, dict):
            url = (desktop.get("page") or "").strip() or None

    thumb_url = None
    thumb = tfa.get("thumbnail")
    if isinstance(thumb, dict):
        thumb_url = (thumb.get("source") or "").strip() or None

    return {
        "title": title or None,
        "extract": extract or None,
        "url": url,
        "thumbnail_url": thumb_url,
    }


def _get_featured_payload_cached(ttl_seconds: float = 10 * 60) -> tuple[dict, float]:
    global _cache_payload, _cache_for_day, _cache_checked_at

    now = time.time()
    day_key = _today_key()

    with _cache_lock:
        if (
            _cache_payload is not None
            and _cache_for_day == day_key
            and (now - float(_cache_checked_at)) < float(ttl_seconds)
        ):
            return dict(_cache_payload), float(_cache_checked_at)

    # Pull from Wikimedia featured feed endpoint.
    y, m, d = day_key.split("-")
    url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{y}/{m}/{d}"

    try:
        feed = _fetch_json(url)
        tfa = _extract_tfa(feed)
        if not tfa.get("title"):
            raise RuntimeError("No featured article returned")

        payload = {
            "ok": True,
            "date": day_key,
            "title": tfa.get("title"),
            "extract": tfa.get("extract"),
            "url": tfa.get("url"),
            "thumbnail_url": tfa.get("thumbnail_url"),
        }
    except Exception as e:
        payload = {
            "ok": False,
            "date": day_key,
            "error": f"Failed to load featured article: {type(e).__name__}",
        }

    with _cache_lock:
        _cache_payload = dict(payload)
        _cache_for_day = day_key
        _cache_checked_at = time.time()
        return dict(_cache_payload), float(_cache_checked_at)


def get_widget():
    return {
        "name": WIDGET_TITLE,
        "html": """
<h2>Featured article:</h2>
<div class=\"wiki-row\">
  <img class=\"wiki-thumb\" alt=\"\" style=\"display:none\" data-wiki-thumb />
  <div class=\"wiki-main\">
    <a class=\"wiki-title\" href=\"#\" data-wiki-link data-external-url=\"\">Loading…</a>
    <div class=\"small wiki-extract\" data-wiki-extract></div>
    <div class=\"small\" data-wiki-status style=\"margin-top: 6px; opacity: 0.75;\">Loading…</div>
  </div>
</div>
""".strip(),
        "css": """
/* Scoped via #widget-wikipedia_featured */
#widget-wikipedia_featured h2 { margin-bottom: 8px; }
#widget-wikipedia_featured .wiki-row { display: flex; gap: 10px; align-items: flex-start; }
#widget-wikipedia_featured .wiki-thumb {
  width: 54px;
  height: 54px;
  border-radius: 10px;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.10);
  object-fit: cover;
  flex: 0 0 auto;
}
#widget-wikipedia_featured .wiki-main { min-width: 0; }
#widget-wikipedia_featured .wiki-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-decoration: none;
  color: rgba(255,255,255,0.92);
  font-weight: 700;
  line-height: 1.25;
}
#widget-wikipedia_featured .wiki-title:hover { text-decoration: underline; }
#widget-wikipedia_featured .wiki-extract {
  margin-top: 6px;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  opacity: 0.85;
}
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('wikipedia_featured') : null;
  if (!root) return;

  const titleLink = root.querySelector('[data-wiki-link]');
  const extractEl = root.querySelector('[data-wiki-extract]');
  const statusEl = root.querySelector('[data-wiki-status]');
  const thumbEl = root.querySelector('[data-wiki-thumb]');

  function setStatus(msg, isError){
    if (!statusEl) return;
    statusEl.textContent = String(msg || '');
    statusEl.className = 'small' + (isError ? ' error' : '');
  }

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  async function load(){
    setStatus('Loading…', false);
    try {
      const resp = await fetch('/widgets/wikipedia_featured/tfa?t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();

      if (!data || !data.ok) {
        if (titleLink) titleLink.textContent = 'Wikipedia';
        if (extractEl) extractEl.textContent = '';
        if (thumbEl) { thumbEl.style.display = 'none'; thumbEl.src = ''; }
        setStatus((data && data.error) ? data.error : 'Failed to load.', true);
        return;
      }

      const title = data.title || "Today's featured article";
      const extract = data.extract || '';
      const url = data.url || '';
      const thumb = data.thumbnail_url || '';

      if (titleLink) {
        titleLink.textContent = title;
        titleLink.setAttribute('data-external-url', url);
      }
      if (extractEl) extractEl.textContent = extract;

      if (thumbEl) {
        if (thumb) {
          thumbEl.src = thumb;
          thumbEl.style.display = '';
        } else {
          thumbEl.style.display = 'none';
          thumbEl.src = '';
        }
      }

      setStatus('', false);
    } catch (e) {
      if (titleLink) titleLink.textContent = 'Wikipedia';
      if (extractEl) extractEl.textContent = '';
      if (thumbEl) { thumbEl.style.display = 'none'; thumbEl.src = ''; }
      setStatus('Failed to load.', true);
    }
  }

  root.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[data-external-url]') : null;
    if (!a) return;
    e.preventDefault();
    const url = a.getAttribute('data-external-url') || '';
    if (url && window.WidgetHost && window.WidgetHost.openExternal) window.WidgetHost.openExternal(url);
  });

  load();
})();
""".strip(),
    }


def register(app, url_prefix: str = "/widgets/wikipedia_featured"):
    def tfa():
        payload, _ = _get_featured_payload_cached()
        return jsonify(payload)

    app.add_url_rule(
        f"{url_prefix}/tfa",
        endpoint="wikipedia_featured_tfa",
        view_func=tfa,
        methods=["GET"],
    )
