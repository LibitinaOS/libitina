from flask import Flask, request, Response
import feedparser
import requests
from feedgen.feed import FeedGenerator
import datetime
import random
import xml.etree.ElementTree as ET
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

app = Flask(__name__)

MRSS_NS = "http://search.yahoo.com/mrss/"

# On-disk cache settings (can be overridden via CLI)
CACHE_DIR = Path(os.environ.get("TIMESPOOF_CACHE_DIR", str(Path(__file__).resolve().parent / "_timespoof_cache")))
CACHE_ONLY = os.environ.get("TIMESPOOF_CACHE_ONLY", "0") == "1"

# Safety limits for media caching
MEDIA_MAX_BYTES = int(os.environ.get("TIMESPOOF_MEDIA_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB


_SESSION: requests.Session | None = None


def _use_system_proxy_for_requests() -> bool:
    """Best-effort: follow the app's experiments flag when available.

    Defaults to True to match requests' default behavior.
    """
    try:
        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))
        with open(config_path, "r", encoding="utf-8") as f:
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


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.trust_env = bool(_use_system_proxy_for_requests())
        _SESSION = s
    return _SESSION


def _ensure_cache_dirs():
    (CACHE_DIR / "feeds").mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "media").mkdir(parents=True, exist_ok=True)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _feed_cache_paths(feed_url: str):
    key = _sha256(feed_url)
    base = CACHE_DIR / "feeds" / key
    return {
        "key": key,
        "raw": base.with_suffix(".bin"),
        "meta": base.with_suffix(".json"),
    }


def _write_bytes_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _cache_feed_bytes(feed_url: str, content: bytes, headers: dict | None = None):
    _ensure_cache_dirs()
    paths = _feed_cache_paths(feed_url)
    _write_bytes_atomic(paths["raw"], content)
    meta = {
        "url": feed_url,
        "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "content_type": (headers or {}).get("Content-Type") if headers else None,
    }
    _write_bytes_atomic(paths["meta"], json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))


def _load_cached_feed_bytes(feed_url: str) -> bytes | None:
    paths = _feed_cache_paths(feed_url)
    try:
        return paths["raw"].read_bytes()
    except OSError:
        return None


def _looks_like_feed(parsed) -> bool:
    # feedparser sets .version for recognized feed types; HTML/non-feed often yields version None/"".
    version = getattr(parsed, "version", None)
    if not version:
        return False
    if parsed.entries:
        return True
    # Some feeds are valid but empty; accept if there is at least some feed metadata.
    title = (parsed.feed.get("title") or "").strip() if hasattr(parsed, "feed") else ""
    link = (parsed.feed.get("link") or "").strip() if hasattr(parsed, "feed") else ""
    return bool(title or link)


def _media_cache_paths(media_url: str):
    media_id = _sha256(media_url)
    base = CACHE_DIR / "media" / media_id
    return {
        "id": media_id,
        "bin": base.with_suffix(".bin"),
        "meta": base.with_suffix(".json"),
    }


def _guess_content_type(media_url: str, header_ct: str | None):
    if header_ct:
        return header_ct.split(";")[0].strip()
    path = urlparse(media_url).path.lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".jpg") or path.endswith(".jpeg"):
        return "image/jpeg"
    if path.endswith(".gif"):
        return "image/gif"
    if path.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


def _cache_media(media_url: str) -> str:
    """Ensure media is cached (if allowed); returns media_id even if not cached."""
    _ensure_cache_dirs()
    paths = _media_cache_paths(media_url)

    # If already cached, don't re-fetch.
    if paths["bin"].exists() and paths["meta"].exists():
        return paths["id"]

    if CACHE_ONLY:
        return paths["id"]

    try:
        r = _get_session().get(media_url, timeout=10, stream=True, allow_redirects=True)
        r.raise_for_status()

        total = 0
        chunks = []
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MEDIA_MAX_BYTES:
                # Don't cache unexpectedly large files.
                return paths["id"]
            chunks.append(chunk)

        data = b"".join(chunks)
        if not data:
            return paths["id"]

        ct = _guess_content_type(media_url, r.headers.get("Content-Type"))
        _write_bytes_atomic(paths["bin"], data)
        meta = {
            "url": media_url,
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "content_type": ct,
            "size": len(data),
        }
        _write_bytes_atomic(paths["meta"], json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        return paths["id"]
    except requests.RequestException:
        return paths["id"]


@app.route("/media/<media_id>")
def serve_media(media_id: str):
    _ensure_cache_dirs()
    base = CACHE_DIR / "media" / media_id
    bin_path = base.with_suffix(".bin")
    meta_path = base.with_suffix(".json")

    try:
        data = bin_path.read_bytes()
    except OSError:
        return "media not in cache", 404

    ctype = "application/octet-stream"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ctype = (meta.get("content_type") or ctype).split(";")[0].strip()
    except OSError:
        pass
    except json.JSONDecodeError:
        pass

    return Response(data, content_type=ctype)


def _dt_from_struct_time(st):
    """Convert feedparser's struct_time/tuple to a timezone-aware UTC datetime."""
    if not st:
        return None
    return datetime.datetime(*st[:6], tzinfo=datetime.timezone.utc)


def _extract_media_url(entry):
    """Best-effort extraction of a media URL from Media RSS fields parsed by feedparser."""
    # feedparser normalizes media:* into keys like media_content/media_thumbnail
    for key in ("media_thumbnail", "media_content"):
        items = entry.get(key)
        if not items:
            continue
        if isinstance(items, (list, tuple)):
            for it in items:
                if isinstance(it, dict) and it.get("url"):
                    return it["url"]
        elif isinstance(items, dict) and items.get("url"):
            return items["url"]

    # RSS enclosures sometimes show up here
    for enc in entry.get("enclosures", []) or []:
        if not isinstance(enc, dict):
            continue
        href = enc.get("href") or enc.get("url")
        if href:
            return href

    # Fallback: Atom enclosures sometimes show up as links
    for l in entry.get("links", []) or []:
        if not isinstance(l, dict):
            continue
        href = l.get("href")
        if not href:
            continue
        rel = (l.get("rel") or "").lower()
        if rel == "enclosure":
            return href
    return None


def _add_media_thumbnails_to_rss(rss_bytes, thumb_by_id_or_link):
    """Inject <media:thumbnail url="..."/> into each RSS <item> by matching guid/link."""
    if not rss_bytes or not thumb_by_id_or_link:
        return rss_bytes

    def _txt(el):
        if el is None or el.text is None:
            return ""
        return el.text.strip()

    ET.register_namespace("media", MRSS_NS)
    try:
        root = ET.fromstring(rss_bytes)
    except ET.ParseError:
        return rss_bytes

    channel = root.find("channel")
    if channel is None:
        return rss_bytes

    items = channel.findall("item")
    for item in items:
        guid = _txt(item.find("guid"))
        link = _txt(item.find("link"))
        url = ""
        if guid:
            url = thumb_by_id_or_link.get(("id", guid), "")
        if not url and link:
            url = thumb_by_id_or_link.get(("link", link), "")
        if not url:
            continue
        # Avoid duplicating if already present
        if item.find(f"{{{MRSS_NS}}}thumbnail") is not None:
            continue
        thumb_el = ET.Element(f"{{{MRSS_NS}}}thumbnail")
        thumb_el.set("url", url)
        item.append(thumb_el)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@app.route("/feed")
def proxy_feed():
    url = request.args.get("url")
    if not url:
        return "missing ?url=", 400

    mode = request.args.get("mode", "rss")          # rss | atom
    rewrite = request.args.get("rewrite_dates", "1") == "1"
    base_hours = int(request.args.get("base_hours_ago", "3"))
    step_minutes = int(request.args.get("step_minutes", "10"))

    _ensure_cache_dirs()

    raw_bytes = None
    used_cache = False

    if not CACHE_ONLY:
        try:
            raw = _get_session().get(url, timeout=10)
            raw.raise_for_status()
            parsed_probe = feedparser.parse(raw.content)
            if _looks_like_feed(parsed_probe):
                raw_bytes = raw.content
                _cache_feed_bytes(url, raw_bytes, headers=dict(raw.headers))
            else:
                # Upstream returned non-feed data; fall back to cache.
                raw_bytes = _load_cached_feed_bytes(url)
                used_cache = raw_bytes is not None
        except requests.RequestException:
            raw_bytes = _load_cached_feed_bytes(url)
            used_cache = raw_bytes is not None
    else:
        raw_bytes = _load_cached_feed_bytes(url)
        used_cache = raw_bytes is not None

    if raw_bytes is None:
        return "unable to fetch feed and no cached copy available", 502

    parsed = feedparser.parse(raw_bytes)
    if not _looks_like_feed(parsed):
        # Cached data is also not a feed.
        return "cached data is not a valid feed", 502

    fg = FeedGenerator()

    # feed-level metadata
    fg.id(parsed.feed.get("id", url))
    fg.title(parsed.feed.get("title", ""))
    fg.link(href=parsed.feed.get("link", url), rel="alternate")
    fg.subtitle(parsed.feed.get("subtitle", ""))

    now = datetime.datetime.now(datetime.timezone.utc)
    base_time = now - datetime.timedelta(hours=base_hours)

    if rewrite:
        fg.updated(base_time)
    elif "updated_parsed" in parsed.feed and parsed.feed.updated_parsed:
        dt = _dt_from_struct_time(parsed.feed.updated_parsed)
        if dt:
            fg.updated(dt)

    current_time = base_time
    step = datetime.timedelta(minutes=step_minutes)

    thumb_by_id_or_link = {}

    # Build base URL for local media proxy
    host_url = (request.host_url or "").rstrip("/")

    for entry in parsed.entries:
        fe = fg.add_entry()
        entry_id = entry.get("id", entry.get("link", ""))
        fe.id(entry_id)
        fe.title(entry.get("title", ""))
        entry_link = entry.get("link", "")
        fe.link(href=entry_link)

        if "summary" in entry:
            fe.summary(entry.summary)

        if "content" in entry and entry.content:
            fe.content(entry.content[0].value, type="html")

        if rewrite:
            fe.published(current_time)
            fe.updated(current_time)
        else:
            if entry.get("published_parsed"):
                dt = _dt_from_struct_time(entry.published_parsed)
                if dt:
                    fe.published(dt)
            if entry.get("updated_parsed"):
                dt = _dt_from_struct_time(entry.updated_parsed)
                if dt:
                    fe.updated(dt)

        thumb_url = _extract_media_url(entry)
        if thumb_url:
            media_id = _cache_media(thumb_url)
            # Only rewrite to the local proxy if the media is actually present in cache.
            paths = _media_cache_paths(thumb_url)
            if host_url and paths["bin"].exists() and paths["meta"].exists():
                proxied = f"{host_url}/media/{media_id}"
            else:
                proxied = thumb_url

            if entry_id:
                thumb_by_id_or_link[("id", entry_id)] = proxied
            if entry_link:
                thumb_by_id_or_link[("link", entry_link)] = proxied

        current_time -= step

    if mode == "atom":
        out = fg.atom_str(pretty=True)
        ctype = "application/atom+xml"
    else:
        out = fg.rss_str(pretty=True)
        out = _add_media_thumbnails_to_rss(out, thumb_by_id_or_link)
        ctype = "application/rss+xml"

    resp = Response(out, content_type=ctype)
    resp.headers["X-Timespoof-Cache"] = "HIT" if used_cache else "MISS"
    resp.headers["X-Timespoof-Cache-Only"] = "1" if CACHE_ONLY else "0"
    return resp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxy and rewrite feed timestamps with local caching.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--cache-dir", default=str(CACHE_DIR), help="Directory for feed/media cache")
    parser.add_argument(
        "--cache-only",
        "--offline",
        dest="cache_only",
        action="store_true",
        help="Never connect to upstream; only use cached content",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    CACHE_DIR = Path(args.cache_dir)
    CACHE_ONLY = bool(args.cache_only)
    _ensure_cache_dirs()

    app.run(host=args.host, port=args.port, debug=args.debug)
