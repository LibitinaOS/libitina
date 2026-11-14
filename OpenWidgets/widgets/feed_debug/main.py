import json
import time


def _fmt_rel(ts: float | int | None) -> str:
  try:
    t = float(ts or 0.0)
  except Exception:
    t = 0.0
  if t <= 0.0:
    return "never"
  d = max(0.0, time.time() - t)
  if d < 5:
    return "just now"
  if d < 60:
    return f"{int(d)}s ago"
  if d < 3600:
    return f"{int(d // 60)}m ago"
  if d < 48 * 3600:
    return f"{int(d // 3600)}h ago"
  return f"{int(d // 86400)}d ago"


def get_widget():
  return {
    "html": """
<div style=\"display:flex; align-items:center; justify-content:space-between; gap:10px;\">
  <div class=\"small\" data-fd-summary>Loading…</div>
  <button class=\"btn\" type=\"button\" data-fd-refresh>Refresh</button>
</div>
<div style=\"margin-top:8px;\" data-fd-list></div>
""".strip(),
    "css": """
/* Scoped via #widget-feed_debug */
#widget-feed_debug [data-fd-list] { display: flex; flex-direction: column; gap: 8px; }
#widget-feed_debug .row {
  display: flex; flex-direction: column; gap: 2px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  border-radius: 10px;
  padding: 8px 10px;
}
#widget-feed_debug .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
#widget-feed_debug .name { font-weight: 700; opacity: 0.95; }
#widget-feed_debug .badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.14);
  opacity: 0.9;
}
#widget-feed_debug .badge.ok { background: rgba(0, 255, 0, 0.12); }
#widget-feed_debug .badge.err { background: rgba(255, 64, 64, 0.12); }
#widget-feed_debug .badge.unk { background: rgba(255, 255, 255, 0.06); }
#widget-feed_debug .meta { font-size: 11px; opacity: 0.78; }
#widget-feed_debug .error { font-size: 11px; opacity: 0.85; }
""".strip(),
    "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('feed_debug') : null;
  if (!root) return;

  const summary = root.querySelector('[data-fd-summary]');
  const list = root.querySelector('[data-fd-list]');
  const btn = root.querySelector('[data-fd-refresh]');
  if (!summary || !list || !btn) return;

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function rel(ts){
    const t = Number(ts || 0);
    if (!t) return 'never';
    const d = Math.max(0, Date.now()/1000 - t);
    if (d < 5) return 'just now';
    if (d < 60) return Math.floor(d) + 's ago';
    if (d < 3600) return Math.floor(d/60) + 'm ago';
    if (d < 48*3600) return Math.floor(d/3600) + 'h ago';
    return Math.floor(d/86400) + 'd ago';
  }

  function render(payload){
    const r = payload && payload.refresh ? payload.refresh : null;
    const cache = payload && payload.cache ? payload.cache : null;

    const refreshOk = r && r.ok === true;
    const refreshErr = r && r.error ? String(r.error) : '';
    const cacheCount = cache && Number.isFinite(cache.count) ? cache.count : 0;

    if (refreshOk) {
      summary.textContent = 'Last refresh: ' + rel(r.at) + ' • cache ' + cacheCount;
      summary.className = 'small';
    } else if (refreshErr) {
      summary.textContent = 'Refresh error: ' + refreshErr;
      summary.className = 'small error';
    } else {
      summary.textContent = 'No refresh info yet.';
      summary.className = 'small';
    }

    list.innerHTML = '';
    const sources = payload && Array.isArray(payload.sources) ? payload.sources : [];
    for (const s of sources) {
      const ok = (s.ok === true);
      const unk = (s.ok === null || typeof s.ok === 'undefined');
      const badgeClass = ok ? 'ok' : (unk ? 'unk' : 'err');
      const badgeText = ok ? 'OK' : (unk ? 'Unknown' : 'Error');

      const name = s.name ? String(s.name) : (s.id ? String(s.id) : 'source');
      const items = Number.isFinite(Number(s.last_items)) ? Number(s.last_items) : 0;
      const lastAttempt = rel(s.last_attempt_at);
      const lastSuccess = rel(s.last_success_at);
      const err = s.last_error ? String(s.last_error) : '';

      const el = document.createElement('div');
      el.className = 'row';
      el.innerHTML = `
        <div class="top">
          <div class="name">${esc(name)}</div>
          <span class="badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="meta">attempt: ${esc(lastAttempt)} • success: ${esc(lastSuccess)} • items: ${items}</div>
        ${err ? `<div class="error">${esc(err)}</div>` : ''}
      `;
      list.appendChild(el);
    }

    if (!sources.length) {
      const el = document.createElement('div');
      el.className = 'small';
      el.style.opacity = '0.8';
      el.textContent = 'No sources configured.';
      list.appendChild(el);
    }
  }

  async function load(){
    summary.textContent = 'Loading…';
    summary.className = 'small';
    try {
      const resp = await fetch('/widgets/windows_central/status.json?t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      render(data);
    } catch (e) {
      summary.textContent = 'Failed to load status.';
      summary.className = 'small error';
      list.innerHTML = '';
    }
  }

  btn.addEventListener('click', load);
  load();
})();
""".strip(),
  }


def register(app, url_prefix: str = "/widgets/feed_debug"):
  @app.route(f"{url_prefix}/sample")
  def feed_debug_sample():
    payload = {
      "ok": True,
      "example": True,
      "note": "This widget reads its data from /widgets/windows_central/status.json",
    }
    return app.response_class(
      response=json.dumps(payload, ensure_ascii=False),
      mimetype="application/json",
      headers={"Cache-Control": "no-store"},
    )
