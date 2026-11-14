import html


def pixelnews_transparency_html() -> str:
    def esc(s: object) -> str:
        return html.escape(str(s if s is not None else ""), quote=True)

    def esc_js_single(s: str) -> str:
        # For embedding in onclick="... 'value' ..." (best-effort).
        return str(s).replace("\\", "\\\\").replace("'", "\\'")

    def lean_text(v: float) -> str:
        if v < 0.45:
            return "Left-leaning"
        if v > 0.55:
            return "Right-leaning"
        return "Center"

    def channels_out(raw) -> list[str]:
        out: list[str] = []
        if not isinstance(raw, list):
            return out
        for c in raw:
            if isinstance(c, dict):
                name = c.get("name")
                cid = c.get("id")
                val = name if isinstance(name, str) and name.strip() else cid
            else:
                val = c
            if isinstance(val, str) and val.strip():
                t = val.strip()
                if t not in out:
                    out.append(t)
        return out

    sources: list[dict] = []
    hidden_sources: set[str] = set()
    use_weighted_sources = True
    region_language = ""
    followed_channels_count: int | None = None
    try:
        from widgets.windows_central import main as wc_main

        settings, _cfg_mtime = wc_main._get_news_settings()
        sources = wc_main._normalize_sources(settings.get("sources"))
        use_weighted_sources = bool(settings.get("use_weighted_sources"))

        ucfg, _ucfg_mtime = wc_main._get_userfeed_config_cached()
        hs = ucfg.get("hidden_sources")
        if isinstance(hs, list):
            for it in hs:
                if isinstance(it, str) and it.strip():
                    hidden_sources.add(it.strip().lower())

        rl = ucfg.get("region_language")
        if isinstance(rl, str) and rl.strip():
            region_language = rl.strip()

        fc = ucfg.get("followed_channels")
        if isinstance(fc, list):
            followed_channels_count = len([c for c in fc if isinstance(c, str) and c.strip()])
    except Exception:
        # Best-effort: keep empty.
        sources = []

    total_sources = len(sources)
    hidden_count = 0
    for s in sources:
        sid = (s.get("id") or "").strip().lower()
        if sid and sid in hidden_sources:
            hidden_count += 1

    header_bits: list[str] = []
    header_bits.append('<div class="small" style="opacity: 0.9;">')
    header_bits.append(
        "PixelNews is a local, RSS-based feed that runs on your machine. We don’t build engagement profiles, and we don’t sell or share your reading history. Your personalization choices (like hidden sources, followed channels, and region/language) stay on your device."
    )
    header_bits.append(
        "We aim to support a free, plural, and neutral press by showing stories from many independent publishers and making it easy to understand where items come from (ownership, lean metadata). You control what appears in your feed."
    )
    header_bits.append(
        "We also try to avoid dark patterns: no infinite-scroll traps, no autoplay, no manipulative prompts, and no hidden personalization intended to keep you engaged. You can always see what sources are used and hide/unhide them."
    )
    header_bits.append(
        "Note: PixelNews does not strip ads, trackers, paywalls, or publisher-side scripts from the sites you open. For that, use your browser’s privacy protections and/or extensions (for example: built-in tracking protection, uBlock Origin, Privacy Badger, or similar tools)."
    )
    header_bits.append("</div>")
    header_bits.append('<div class="small" style="margin-top: 8px; opacity: 0.8;">')
    header_bits.append(
        "Personalization is stored locally on your device and isn't shared with anyone (followed channels, hidden sources, region/language)."
    )
    header_bits.append("</div>")

    stats_bits: list[str] = []
    stats_bits.append('<div class="small" style="margin-top: 10px; opacity: 0.85;">')
    stats_bits.append(f"Sources configured: <b>{int(total_sources)}</b>")
    if total_sources:
        stats_bits.append(f" • Hidden by you: <b>{int(hidden_count)}</b>")
    if region_language:
        stats_bits.append(f" • Region/language: <b>{esc(region_language)}</b>")
    if isinstance(followed_channels_count, int):
        stats_bits.append(f" • Followed channels: <b>{int(followed_channels_count)}</b>")
    stats_bits.append("</div>")

    sources_bits: list[str] = []
    sources_bits.append("<h3>Sources used</h3>")
    sources_bits.append(
        '<div class="small" style="opacity: 0.8;">Tap a source to see ownership, lean, and the exact feed URL.</div>'
    )

    if not sources:
        sources_bits.append(
            '<div class="small" style="margin-top: 8px; opacity: 0.75;">No sources are configured.</div>'
        )
    else:
        sources_bits.append('<div class="pn-source-list">')
        for s in sources:
            sid = (s.get("id") or "").strip()
            if not sid:
                continue
            name = (s.get("name") or sid).strip()
            tagline = (s.get("tagline") or "").strip()
            url = (s.get("url") or "").strip()
            owner_name = (s.get("owner_name") or "").strip()
            owner_url = (s.get("owner_url") or "").strip()
            ownership = (s.get("ownership_category") or "").strip()
            logo_path = (s.get("logo_path") or "").strip()
            desc = (s.get("description") or "").strip()
            ch = channels_out(s.get("channels"))
            lean = s.get("political_lean")
            try:
                lean_f = float(lean)
            except Exception:
                lean_f = 0.5
            lean_f = max(0.0, min(1.0, lean_f))

            is_hidden = sid.strip().lower() in hidden_sources
            owner_html = esc(owner_name) if owner_name else ""
            if owner_name and owner_url:
                # Stop click from opening the source dialog; open URL instead.
                owner_html = (
                    f'<a href="#" onclick="event.stopPropagation(); event.preventDefault(); '
                    f'if (window.WidgetHost && window.WidgetHost.openExternal) window.WidgetHost.openExternal(\'{esc_js_single(owner_url)}\');">'
                    f'{esc(owner_name)}</a>'
                )

            pct = lean_f * 100.0
            logo_opacity = "1" if logo_path else "0"
            tagline_html = f'<div class="pn-source-sub">{esc(tagline)}</div>' if tagline else ""
            desc_html = f'<div class="pn-source-desc">{esc(desc)}</div>' if desc else ""

            fields: list[str] = []
            if ownership:
                fields.append(f"<li><b>Ownership</b>: {esc(ownership)}</li>")
            if owner_html:
                fields.append(f"<li><b>Owner</b>: {owner_html}</li>")
            fields.append(f"<li><b>Lean</b>: {lean_f:.2f} ({esc(lean_text(lean_f))})</li>")
            if url:
                fields.append(f"<li><b>Feed</b>: {esc(url)}</li>")
            if is_hidden:
                fields.append("<li><b>Status</b>: Hidden by you</li>")
            fields_html = f'<ul class="pn-source-fields">{"".join(fields)}</ul>' if fields else ""

            chips_html = ""
            if ch:
                chips_html = '<div class="pn-chips">' + "".join([f'<span class="pn-chip">{esc(x)}</span>' for x in ch]) + "</div>"

            sources_bits.append(
                '<button class="pn-source-item" type="button" '
                f"onclick=\"openSourceInfo('{esc_js_single(sid)}')\">"
                f'<div class="pn-source-logo"><img alt="" src="{esc(logo_path)}" style="opacity: {logo_opacity};" /></div>'
                '<div class="pn-source-meta">'
                f'<div class="pn-source-name">{esc(name)}</div>'
                f"{tagline_html}"
                f"{desc_html}"
                f"{fields_html}"
                f"{chips_html}"
                '<div class="pn-lean">'
                '<div class="pn-lean-row"><div>Lean</div>'
                f"<div>{lean_f:.2f}</div></div>"
                f'<div class="pn-lean-track" aria-hidden="true"><div class="pn-lean-thumb" style="left: {pct:.1f}%;"></div></div>'
                '<div class="pn-lean-ticks"><div>Hard Left</div><div>Center</div><div>Hard Right</div></div>'
                "</div>"
                "</div>"
                "</button>"
            )
        sources_bits.append("</div>")

        sources_bits.append(
            '<details><summary>What does “hidden” mean?</summary>'
            '<div class="small" style="margin-top: 6px; opacity: 0.8;">'
            "Hidden sources are excluded from fetching and ranking. You can unhide them from Personalize PixelNews → Settings, or from an individual source dialog."
            "</div></details>"
        )

    algo_bits: list[str] = []
    algo_bits.append("<h3>How the ranking works (plain English)</h3>")
    algo_bits.append('<div class="small" style="opacity: 0.85;">')
    algo_bits.append(
        "PixelNews builds a short list from each source, then merges them into one feed. It’s designed to be predictable and to avoid “doomscroll” mechanics."
    )
    algo_bits.append("</div>")
    algo_bits.append('<ol class="small" style="margin: 8px 0 0 18px; opacity: 0.9;">')
    algo_bits.append("<li><b>Fetch</b>: download RSS from each source (skips sources you hid).</li>")
    algo_bits.append(
        "<li><b>Normalize</b>: extract title, link, date, author, categories, thumbnail.</li>"
    )
    algo_bits.append(
        "<li><b>Score</b>: prefer complete items (date/image/categories/author), and de-prioritize broken or repetitive items.</li>"
    )
    algo_bits.append(
        "<li><b>Mix</b>: interleave sources so one outlet can’t dominate the top of the feed.</li>"
    )
    algo_bits.append(
        "<li><b>Boost</b>: if you follow channels, those sources can appear a bit more often.</li>"
    )
    algo_bits.append("</ol>")

    algo_bits.append(
        '<div class="small" style="margin-top: 8px; opacity: 0.8;">'
        f"Mixing mode: <b>{'weighted' if use_weighted_sources else 'newest-first (simple merge)'}</b>."
        "</div>"
    )

    algo_bits.append(
        "<details><summary>What PixelNews does <b>not</b> do</summary>"
        '<div class="small" style="margin-top: 6px; opacity: 0.8;">'
        "No infinite autoplay, no hidden engagement score, no selling reading history, and no per-user behavioral targeting. Your followed channels and hidden sources stay on your device."
        "</div></details>"
    )

    weather_bits: list[str] = []
    weather_bits.append("<h3>Weather data</h3>")
    weather_bits.append('<div class="small" style="opacity: 0.85;">')
    weather_bits.append(
        "Weather comes from the National Weather Service (NWS). This app uses a local wrapper (nws-ez) and talks to NWS over HTTPS."
    )
    weather_bits.append("</div>")

    out = "\n".join(header_bits + stats_bits + sources_bits + algo_bits + weather_bits)
    return out
