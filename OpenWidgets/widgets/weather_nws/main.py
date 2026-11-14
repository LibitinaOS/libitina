def get_widget():
    return {
        "html": """
<h2 data-nws-place>Weather</h2>
<div class=\"weather-row\">
  <img class=\"weather-icon\" alt=\"\" style=\"display:none\" data-nws-icon />
  <div>
    <div class=\"weather-temp\" data-nws-temp></div>
    <div class=\"weather-lines\">
      <div class=\"small\" data-nws-now></div>
    </div>
  </div>
</div>
<div class=\"small\" data-nws-status style=\"padding-top: 10px;\">Loading…</div>
<div class=\"hourly-strip\" aria-label=\"Hourly forecast\" data-nws-hourly></div>
<div class=\"btn-row\">
  <button class=\"btn\" type=\"button\" disabled data-nws-full>See full forecast</button>
</div>
""".strip(),
        "css": """
/* Scoped via #widget-weather_nws */
#widget-weather_nws h2 { margin-bottom: 8px; }
#widget-weather_nws .weather-row { display: flex; gap: 10px; align-items: center; }
#widget-weather_nws .weather-icon { width: 44px; height: 44px; border-radius: 10px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); object-fit: contain; }
#widget-weather_nws .weather-temp { font-size: 28px; font-weight: 700; line-height: 1.0; }
#widget-weather_nws .weather-lines { display: flex; flex-direction: column; gap: 2px; }
#widget-weather_nws .hourly-strip { margin-top: 10px; display: flex; gap: 8px; overflow: hidden; }
#widget-weather_nws .hour-card { flex: 1 1 0; min-width: 0; border-radius: 10px; padding: 8px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10); text-align: center; }
#widget-weather_nws .hour-label { font-size: 11px; opacity: 0.75; margin-bottom: 4px; }
#widget-weather_nws .hour-icon { width: 26px; height: 26px; margin: 0 auto 4px auto; object-fit: contain; }
#widget-weather_nws .hour-temp { font-size: 13px; font-weight: 700; }
#widget-weather_nws .hour-pop { font-size: 11px; opacity: 0.78; line-height: 1.0; }
""".strip(),
        "js": """
(function(){
  const root = window.WidgetHost && window.WidgetHost.body ? window.WidgetHost.body('weather_nws') : null;
  if (!root) return;

  const card = window.WidgetHost && window.WidgetHost.el ? window.WidgetHost.el('weather_nws') : null;
  const bgTarget = card || root;

  const nwsPlace = root.querySelector('[data-nws-place]');
  const nwsStatus = root.querySelector('[data-nws-status]');
  const nwsIcon = root.querySelector('[data-nws-icon]');
  const nwsTemp = root.querySelector('[data-nws-temp]');
  const nwsNow = root.querySelector('[data-nws-now]');
  const nwsHourly = root.querySelector('[data-nws-hourly]');
  const nwsFullBtn = root.querySelector('[data-nws-full]');

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function getForecastUrlFromPayload(payload) {
    try {
      const loc = payload && payload.location ? payload.location : null;
      const lat = loc && typeof loc.lat === 'number' ? loc.lat : null;
      const lon = loc && typeof loc.lon === 'number' ? loc.lon : null;
      if (lat == null || lon == null || !isFinite(lat) || !isFinite(lon)) return null;
      return `https://forecast.weather.gov/MapClick.php?lat=${encodeURIComponent(String(lat))}&lon=${encodeURIComponent(String(lon))}`;
    } catch (e) {
      return null;
    }
  }

  function fmtHour(iso) {
    try {
      const d = new Date(iso);
      if (!isFinite(d.getTime())) return '';
      return d.toLocaleString(undefined, { hour: 'numeric', hour12: true });
    } catch (e) {
      return '';
    }
  }

  function convertTemp(value, fromUnit, toUnit) {
    try {
      if (value == null || isNaN(value)) return null;
      const v = Number(value);
      const f = String(fromUnit || '').toUpperCase();
      const t = String(toUnit || '').toUpperCase();
      if (!isFinite(v)) return null;
      if (!f || !t || f === t) return v;
      if (f === 'F' && t === 'C') return (v - 32) * (5 / 9);
      if (f === 'C' && t === 'F') return (v * (9 / 5)) + 32;
      return v;
    } catch (e) {
      return null;
    }
  }

  function norm(s) {
    return String(s || '')
      .toLowerCase()
      .replace(/[\\t\\r\\n]+/g, ' ')
      .replace(/[^a-z0-9 ]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function pickBackgroundName(conditionText) {
    const key = norm(conditionText);
    if (!key) return 'All';

    // Exact condition-name mapping (conversion table).
    const conditionToBg = {
      // Sky cover
      'clear': 'Clear',
      'sunny': 'Clear',
      'mostly clear': 'Clear',
      'partly cloudy': 'PartlyCloudy',
      'mostly cloudy': 'PartlyCloudy',
      'cloudy': 'PartlyCloudy',
      'overcast': 'Overcast',
      // Visibility / air
      'fog': 'Fog',
      'dense fog': 'Fog',
      'mist': 'Fog',
      'haze': 'Fog',
      'smoke': 'Fog',
      // Precip
      'rain': 'Rain',
      'showers': 'Rain',
      'drizzle': 'Rain',
      'freezing rain': 'Rain',
      'snow': 'Snow',
      'snow flurries': 'Snow',
      'snow squall': 'Snow',
      'sleet': 'Snow',
      'hail': 'Thundery',
      // Storms
      'thunderstorm': 'Thundery',
      'tornado': 'Thundery',
    };

    const direct = conditionToBg[key];
    if (direct) return direct;

    // Best-effort substring matching for common forecast phrasing.
    // Order matters: more specific first.
    const patterns = [
      ['freezing rain', 'Rain'],
      ['dense fog', 'Fog'],
      ['snow squall', 'Snow'],
      ['snow flurr', 'Snow'],
      ['blizzard', 'Snow'],
      ['thunder', 'Thundery'],
      ['tstorm', 'Thundery'],
      ['lightning', 'Thundery'],
      ['hail', 'Thundery'],
      ['sleet', 'Snow'],
      ['ice', 'Snow'],
      ['showers', 'Rain'],
      ['drizzle', 'Rain'],
      ['rain', 'Rain'],
      ['snow', 'Snow'],
      ['fog', 'Fog'],
      ['mist', 'Fog'],
      ['haze', 'Fog'],
      ['smoke', 'Fog'],
      ['overcast', 'Overcast'],
      ['partly cloudy', 'PartlyCloudy'],
      ['mostly cloudy', 'PartlyCloudy'],
      ['cloudy', 'PartlyCloudy'],
      ['cloud', 'PartlyCloudy'],
      ['mostly clear', 'Clear'],
      ['clear', 'Clear'],
      ['sunny', 'Clear'],
    ];
    for (const [pat, bg] of patterns) {
      if (key.includes(pat)) return bg;
    }

    return 'All';
  }

  function applyBackground(payload) {
    return "test";  // Disable dynamic backgrounds for now."
  }

  function applyPlace(payload) {
    if (!nwsPlace) return;
    const place = payload && payload.place_name ? String(payload.place_name).trim() : '';
    if (place) {
      nwsPlace.textContent = place;
      return;
    }
    try {
      const loc = payload && payload.location ? payload.location : null;
      const lat = loc && typeof loc.lat === 'number' ? loc.lat : null;
      const lon = loc && typeof loc.lon === 'number' ? loc.lon : null;
      if (lat != null && lon != null && isFinite(lat) && isFinite(lon)) {
        nwsPlace.textContent = `${lat.toFixed(2)}, ${lon.toFixed(2)}`;
        return;
      }
    } catch (e) {
      // ignore
    }
    nwsPlace.textContent = 'Weather';
  }

  function renderHourly(periods, preferredUnit) {
    if (!nwsHourly) return;
    nwsHourly.innerHTML = '';
    if (!periods || !periods.length) return;

    const take = periods.slice(0, 5);
    for (const p of take) {
      const hour = fmtHour(p.start_time);
      const unitFrom = p.temperature_unit || 'F';
      const converted = convertTemp(p.temperature, unitFrom, preferredUnit);
      const temp = (converted != null && !isNaN(converted)) ? (Math.round(converted) + '°') : '';
      const pop = (p.pop == null || isNaN(p.pop)) ? 0 : Math.max(0, Math.min(100, Math.round(p.pop)));

      const card = document.createElement('div');
      card.className = 'hour-card';
      const iconHtml = p.icon ? `<img class="hour-icon" src="${esc(p.icon)}" alt="" />` : `<div class="hour-icon"></div>`;
      card.innerHTML = `
        <div class="hour-label">${esc(hour)}</div>
        ${iconHtml}
        <div class="hour-temp">${esc(temp)}</div>
        <div class="hour-pop">${esc(String(pop))} %</div>
      `;
      nwsHourly.appendChild(card);
    }
  }

  function renderWeather(payload) {
    const preferredUnit = (payload && payload.temp_unit) ? String(payload.temp_unit).toUpperCase() : 'F';

    applyPlace(payload);
    applyBackground(payload);

    const forecastUrl = getForecastUrlFromPayload(payload);
    if (nwsFullBtn) {
      if (forecastUrl) {
        nwsFullBtn.disabled = false;
        nwsFullBtn.dataset.url = forecastUrl;
      } else {
        nwsFullBtn.disabled = true;
        nwsFullBtn.dataset.url = '';
      }
    }

    if (!payload || payload.error) {
      const msg = payload && payload.error ? payload.error : 'Failed to load weather.';
      if (nwsStatus) {
        nwsStatus.textContent = msg;
        nwsStatus.className = 'error';
      }
      if (nwsIcon) {
        nwsIcon.src = '';
        nwsIcon.style.display = 'none';
      }
      if (nwsTemp) nwsTemp.textContent = '';
      if (nwsNow) nwsNow.textContent = (payload && payload.place_name) ? payload.place_name : '';
      if (nwsHourly) nwsHourly.innerHTML = '';
      return;
    }

    const updated = payload.updated_at ? new Date(payload.updated_at * 1000).toLocaleTimeString() : '';
    if (nwsStatus) {
      nwsStatus.textContent = updated ? ('Updated ' + updated) : 'Updated';
      nwsStatus.className = 'small';
    }

    if (nwsIcon) {
      if (payload.icon) {
        nwsIcon.src = payload.icon;
        nwsIcon.style.display = 'block';
      } else {
        nwsIcon.src = '';
        nwsIcon.style.display = 'none';
      }
    }

    const obs = payload && payload.observation ? payload.observation : null;
    const tv = obs ? (preferredUnit === 'C' ? obs.temperature_c : obs.temperature_f) : null;
    if (nwsTemp) {
      if (tv != null && !isNaN(tv)) nwsTemp.textContent = Math.round(tv) + '°';
      else nwsTemp.textContent = '';
    }

    if (nwsNow) {
      if (payload.observation && payload.observation.text_description) nwsNow.textContent = payload.observation.text_description;
      else nwsNow.textContent = 'No recent observation.';
    }

    if (payload.hourly && payload.hourly.periods) renderHourly(payload.hourly.periods, preferredUnit);
    else if (nwsHourly) nwsHourly.innerHTML = '';
  }

  async function refreshWeather() {
    try {
      const resp = await fetch('/weather.json?t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      renderWeather(data);
    } catch (e) {
      renderWeather({ error: 'Failed to load weather.' });
    }
  }

  // Set a default background immediately so there's no blank state.
  bgTarget.style.setProperty('--weather-bg-image', `url(/static/weather/backgrounds/All.png)`);

  if (nwsFullBtn) {
    nwsFullBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const url = nwsFullBtn.dataset.url || '';
      if (url && window.WidgetHost && window.WidgetHost.openExternal) window.WidgetHost.openExternal(url);
    });
  }

  setInterval(refreshWeather, 7 * 60 * 1000);
  window.addEventListener('focus', refreshWeather);
  refreshWeather();
})();
""".strip(),
    }
