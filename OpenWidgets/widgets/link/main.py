def get_widget():
    return {
        "html": """
<div class=\"small\">
  You can even link <a href=\"#\" data-external-url=\"https://en.wikipedia.org/wiki/Vivienne_Medrano\">any website you want!</a>
</div>
""".strip(),
        "css": """
/* Scoped via #widget-link */
#widget-link a { color: rgba(255,255,255,0.92); text-decoration: underline; }
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('link') : null;
  if (!root) return;

  root.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[data-external-url]') : null;
    if (!a) return;
    e.preventDefault();
    const url = a.getAttribute('data-external-url') || '';
    if (url && window.WidgetHost && window.WidgetHost.openExternal) window.WidgetHost.openExternal(url);
  });
})();
""".strip(),
    }
