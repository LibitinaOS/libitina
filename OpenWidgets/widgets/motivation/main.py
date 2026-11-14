import datetime
import json
import random

from flask import request


AUTHOR = "maia arson crimew"

BANNERS = [
    "what if we just absolutely ended surveillance capitalism in two days?\n",
    "holy shit, we actually have the nofly list. holy fucking bingle. what?! :3\n",
    "maia arson crimew is spelled in all lowercase. this is important.\n",
    "apt-69420 arson cats welcome you warmly\n",
    "this system runs on curiosity, spite, and duct tape\n",
    "caring about literally nothing but profit definitely doesn't result in security\n",
    "you are now entering a profit-free security thought experiment\n",
    "some data should be public when there is a public interest in that data\n",
    "just how broadly we're being surveilled, and how little care is put into securing it\n",
    "i love helping companies open source their code\n",
    "tech support for the revolution (please reboot capitalism)\n",
    "i'm just a swiss girl in a tiny fucking country\n",
    "the euphoria and adrenaline are not bugs, they're features\n",
    "this quote brought to you by collective hacktivism\n",
    "floating through life, sometimes ending up on the front page\n",
    "don't meow if the feds are around\n",
    "strong moral compass, weak patience for bullshit\n",
    "security theater ends here. take a number.\n",
]


def _hour_seed(now: datetime.datetime) -> int:
    # Deterministic per local hour (updates every hour)
    # and stable across restarts.
    return int(now.year * 1000000 + now.month * 10000 + now.day * 100 + now.hour)


def _pick_quote_for_hour(now: datetime.datetime) -> str:
    quotes = BANNERS or ["keep going\n"]
    rng = random.Random(_hour_seed(now))
    q = rng.choice(quotes)
    # Avoid extra empty lines in the UI while preserving internal newlines.
    return str(q).rstrip("\r\n")


def get_widget():
    return {
        "html": """
<div class=\"quote\" data-motivation-quote>Loading…</div>
<div class=\"small\" style=\"opacity:0.75; margin-top:6px;\" data-motivation-author></div>
""".strip(),
        "css": """
/* Scoped via #widget-motivation */
#widget-motivation .quote {
  font-size: 14px;
  line-height: 1.35;
  font-weight: 600;
  opacity: 0.95;
  white-space: pre-line;
}
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('motivation') : null;
  if (!root) return;

  const out = root.querySelector('[data-motivation-quote]');
  const author = root.querySelector('[data-motivation-author]');
  if (!out || !author) return;

  async function load(){
    out.textContent = 'Loading…';
    author.textContent = '';
    try {
      const resp = await fetch('/widgets/motivation/quote?t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      const q = (data && data.quote) ? String(data.quote) : '…';
      out.textContent = '“' + q + '”';
      author.textContent = (data && data.author) ? ('— ' + String(data.author)) : '';
    } catch (e) {
      out.textContent = 'Failed.';
      author.textContent = '';
    }
  }

  load();
})();
""".strip(),
    }


def register(app, url_prefix: str = "/widgets/motivation"):
    @app.route(f"{url_prefix}/quote")
    def motivation_quote_json():
        # Best-effort: allow overriding time for debugging/testing.
        # (We keep it simple and safe: only accept integer unix seconds.)
        t = request.args.get("t")
        now = datetime.datetime.now()
        if isinstance(t, str) and t.isdigit():
            try:
                now = datetime.datetime.fromtimestamp(int(t))
            except Exception:
                now = datetime.datetime.now()

        payload = {
            "ok": True,
            "author": AUTHOR,
            "quote": _pick_quote_for_hour(now),
            "seed": _hour_seed(now),
        }

        return app.response_class(
            response=json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )
