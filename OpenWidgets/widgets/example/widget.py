import time

from flask import jsonify


WIDGET_TITLE = "Example Widget"


def get_widget():
    return {
        "html": """
<div class=\"small\">Backend-powered widget with a browser fetch.</div>
<div style=\"display:flex; gap:8px; align-items:center; margin-top:8px;\">
  <button class=\"btn\" type=\"button\" id=\"example-ping\">Ping backend</button>
  <div class=\"small\" id=\"example-out\"></div>
</div>
""".strip(),
        "css": """
/* Scoped via #widget-example */
#widget-example #example-out { opacity: 0.85; }
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('example') : null;
  if (!root) return;

  const btn = root.querySelector('#example-ping');
  const out = root.querySelector('#example-out');
  if (!btn || !out) return;

  async function ping(){
    out.textContent = 'Loading…';
    try {
      const resp = await fetch('/widgets/example/ping?t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      out.textContent = data && data.message ? data.message : 'OK';
    } catch (e) {
      out.textContent = 'Failed.';
    }
  }

  btn.addEventListener('click', ping);
})();
""".strip(),
    }


def register(app, url_prefix: str = "/widgets/example"):
    def ping():
        return jsonify({"ok": True, "message": f"pong @ {time.strftime('%H:%M:%S')}"})

    app.add_url_rule(
        f"{url_prefix}/ping",
        endpoint="example_widget_ping",
        view_func=ping,
        methods=["GET"],
    )
