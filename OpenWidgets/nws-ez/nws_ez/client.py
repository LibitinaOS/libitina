from __future__ import annotations

import os
import time
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple, Union
from urllib.parse import urljoin

import requests

from .cache import TTLCache
from .errors import NWSHTTPError, NWSProblemDetail
from .models import AlertCollection, Forecast, Observation, PointInfo


Json = Dict[str, Any]


def _clamp_lat_lon(lat: float, lon: float) -> Tuple[float, float]:
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"latitude out of range: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"longitude out of range: {lon}")
    return float(lat), float(lon)


class NWSClient:
    """
    EZ wrapper for https://api.weather.gov

    Highlights:
      - forecast(lat, lon)
      - hourly(lat, lon)
      - latest_observation(lat, lon)
      - alerts_active(point=(lat, lon) or area="CA" or zone="CAZ041", etc.)
      - raw GET for everything else

    NWS requires a User-Agent header. Use a real contact email.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.weather.gov",
        user_agent: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_header: str = "API-Key",
        timeout_s: Union[float, Tuple[float, float]] = (5.0, 25.0),
        retries: int = 4,
        backoff_s: float = 0.8,
        cache_points_ttl_s: float = 900.0,
        use_system_proxy: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or os.getenv("NWS_USER_AGENT") or "nws-ez/0.1 (set NWS_USER_AGENT or pass user_agent)"
        self.api_key = api_key or os.getenv("NWS_API_KEY")
        self.api_key_header = api_key_header
        self.timeout_s = timeout_s
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)

        self._session = requests.Session()
        # requests uses proxy env vars / OS proxy settings when trust_env=True.
        # When false, it bypasses them (unless proxies are explicitly provided).
        self._session.trust_env = bool(use_system_proxy)
        self._points_cache = TTLCache(default_ttl_s=cache_points_ttl_s)

    # ---------------------------
    # Core request helpers
    # ---------------------------

    def _headers(self, *, accept: Optional[str] = None, feature_flags: Optional[Iterable[str]] = None) -> Dict[str, str]:
        h: Dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": accept or "application/geo+json, application/ld+json;q=0.9, application/json;q=0.8",
        }
        if self.api_key:
            h[self.api_key_header] = self.api_key
            # Some deployments/documentation mention X-Api-Key; harmless to include if you want:
            h.setdefault("X-Api-Key", self.api_key)

        if feature_flags:
            # OpenAPI defines Feature-Flags as a header array. Many servers accept comma-separated.
            h["Feature-Flags"] = ",".join(feature_flags)

        return h

    def _request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Json:
        url = path_or_url if path_or_url.startswith("http") else urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        last_exc: Optional[Exception] = None

        for attempt in range(self.retries + 1):
            try:
                resp = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_s,
                )

                # If NWS returns a Problem Details body, surface it nicely.
                ctype = (resp.headers.get("Content-Type") or "").lower()

                if 200 <= resp.status_code < 300:
                    # Sometimes you'll get empty bodies; handle that.
                    if not resp.content:
                        return {}
                    # If JSON-ish, parse.
                    if "json" in ctype or ctype.endswith("+json") or "geo+json" in ctype or "ld+json" in ctype:
                        return resp.json()
                    # Not JSON? return text in a wrapper.
                    return {"_text": resp.text, "_content_type": ctype}

                # Retry logic for transient issues
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    sleep_for = self.backoff_s * (2 ** attempt)
                    # If server tells us to chill, obey.
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        try:
                            sleep_for = max(sleep_for, float(ra))
                        except ValueError:
                            pass
                    time.sleep(sleep_for)
                    continue

                problem: Optional[NWSProblemDetail] = None
                if "application/problem+json" in ctype or "problem+json" in ctype:
                    try:
                        body = resp.json()
                        problem = NWSProblemDetail(
                            type=body.get("type", "about:blank"),
                            title=body.get("title", "NWS API Error"),
                            status=int(body.get("status")) if body.get("status") is not None else resp.status_code,
                            detail=body.get("detail"),
                            instance=body.get("instance"),
                            correlationId=body.get("correlationId"),
                            raw=body,
                        )
                    except Exception:
                        problem = None

                raise NWSHTTPError(
                    status_code=resp.status_code,
                    url=url,
                    message=resp.reason or "Request failed",
                    problem=problem,
                    response_text=resp.text,
                )

            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(self.backoff_s * (2 ** attempt))
                    continue
                raise

        # Should never hit, but fine.
        if last_exc:
            raise last_exc
        return {}

    def get(self, path_or_url: str, *, params: Optional[Dict[str, Any]] = None) -> Json:
        """
        Raw GET for *anything* in the OpenAPI.
        """
        return self._request_json("GET", path_or_url, params=params, headers=self._headers())

    # ---------------------------
    # Points → grid metadata
    # ---------------------------

    def point(self, lat: float, lon: float, *, use_cache: bool = True) -> PointInfo:
        lat, lon = _clamp_lat_lon(lat, lon)
        key = (round(lat, 4), round(lon, 4))

        if use_cache:
            cached = self._points_cache.get(key)
            if isinstance(cached, PointInfo):
                return cached

        data = self._request_json("GET", f"/points/{lat},{lon}", headers=self._headers())
        props = data.get("properties", {})

        grid_id = props.get("gridId")
        grid_x = props.get("gridX")
        grid_y = props.get("gridY")
        if not (grid_id and isinstance(grid_x, int) and isinstance(grid_y, int)):
            raise NWSHTTPError(
                status_code=500,
                url=f"{self.base_url}/points/{lat},{lon}",
                message="Missing grid metadata in /points response",
                response_text=str(data)[:1000],
            )

        pi = PointInfo(
            lat=lat,
            lon=lon,
            grid_id=str(grid_id),
            grid_x=int(grid_x),
            grid_y=int(grid_y),
            forecast_url=str(props.get("forecast")),
            forecast_hourly_url=str(props.get("forecastHourly")),
            forecast_grid_data_url=str(props.get("forecastGridData")),
            observation_stations_url=str(props.get("observationStations")),
            forecast_zone_url=props.get("forecastZone"),
            county_url=props.get("county"),
            time_zone=props.get("timeZone"),
            raw=data,
        )

        if use_cache:
            self._points_cache.set(key, pi)

        return pi

    # ---------------------------
    # Forecasts
    # ---------------------------

    def forecast(
        self,
        lat: float,
        lon: float,
        *,
        units: str = "us",
        feature_flags: Optional[Iterable[str]] = None,
        use_cache: bool = True,
    ) -> Forecast:
        """
        Multi-day forecast (typically 12h periods) for a point.
        """
        p = self.point(lat, lon, use_cache=use_cache)
        # Use the canonical gridpoint endpoint so it stays predictable.
        data = self._request_json(
            "GET",
            f"/gridpoints/{p.grid_id}/{p.grid_x},{p.grid_y}/forecast",
            params={"units": units} if units else None,
            headers=self._headers(feature_flags=feature_flags),
        )
        return Forecast.from_geojson(data)

    def hourly(
        self,
        lat: float,
        lon: float,
        *,
        units: str = "us",
        feature_flags: Optional[Iterable[str]] = None,
        use_cache: bool = True,
    ) -> Forecast:
        """
        Hourly forecast for a point.
        """
        p = self.point(lat, lon, use_cache=use_cache)
        data = self._request_json(
            "GET",
            f"/gridpoints/{p.grid_id}/{p.grid_x},{p.grid_y}/forecast/hourly",
            params={"units": units} if units else None,
            headers=self._headers(feature_flags=feature_flags),
        )
        return Forecast.from_geojson(data)

    def gridpoint_raw(self, lat: float, lon: float, *, use_cache: bool = True) -> Json:
        """
        Raw numerical grid data (/gridpoints/{wfo}/{x},{y}).
        Big. Nerdy. Useful.
        """
        p = self.point(lat, lon, use_cache=use_cache)
        return self._request_json("GET", f"/gridpoints/{p.grid_id}/{p.grid_x},{p.grid_y}", headers=self._headers())

    # ---------------------------
    # Observations
    # ---------------------------

    def gridpoint_stations(
        self,
        wfo: str,
        x: int,
        y: int,
        *,
        limit: int = 10,
        feature_flags: Optional[Iterable[str]] = None,
    ) -> Json:
        return self._request_json(
            "GET",
            f"/gridpoints/{wfo}/{x},{y}/stations",
            params={"limit": limit},
            headers=self._headers(feature_flags=feature_flags),
        )

    def stations_for_point(self, lat: float, lon: float, *, limit: int = 10, use_cache: bool = True) -> List[str]:
        """
        Returns station IDs near a point, best-effort.

        Uses /gridpoints/.../stations (preferred) because /points/.../stations is deprecated.
        """
        p = self.point(lat, lon, use_cache=use_cache)
        data = self.gridpoint_stations(p.grid_id, p.grid_x, p.grid_y, limit=limit)
        # Station collection often includes observationStations (URLs) and/or features with stationIdentifier.
        stations: List[str] = []

        features = data.get("features") or []
        for f in features:
            props = (f or {}).get("properties") or {}
            sid = props.get("stationIdentifier")
            if isinstance(sid, str):
                stations.append(sid)

        if stations:
            return stations[:limit]

        # Fallback: parse observationStations URLs if present
        obs_urls = data.get("observationStations") or []
        for u in obs_urls:
            if isinstance(u, str) and "/stations/" in u:
                stations.append(u.rstrip("/").split("/stations/")[-1])

        return stations[:limit]

    def observation_latest(self, station_id: str, *, require_qc: bool = False) -> Observation:
        data = self._request_json(
            "GET",
            f"/stations/{station_id}/observations/latest",
            params={"require_qc": "true"} if require_qc else None,
            headers=self._headers(),
        )
        return Observation.from_geojson(data)

    def latest_observation(
        self,
        lat: float,
        lon: float,
        *,
        station_id: Optional[str] = None,
        require_qc: bool = False,
        use_cache: bool = True,
    ) -> Observation:
        """
        Latest observation near a point.

        If station_id is provided, uses it directly.
        Otherwise selects the first station returned by the gridpoint stations list.
        """
        if station_id:
            return self.observation_latest(station_id, require_qc=require_qc)

        stations = self.stations_for_point(lat, lon, limit=10, use_cache=use_cache)
        if not stations:
            raise NWSHTTPError(
                status_code=404,
                url=f"{self.base_url}/gridpoints/.../stations",
                message="No observation stations found for point",
            )
        return self.observation_latest(stations[0], require_qc=require_qc)

    # ---------------------------
    # Alerts
    # ---------------------------

    def alerts_active(
        self,
        *,
        point: Optional[Tuple[float, float]] = None,
        area: Optional[Union[str, List[str]]] = None,
        zone: Optional[Union[str, List[str]]] = None,
        event: Optional[Union[str, List[str]]] = None,
        severity: Optional[Union[str, List[str]]] = None,
        urgency: Optional[Union[str, List[str]]] = None,
        certainty: Optional[Union[str, List[str]]] = None,
    ) -> AlertCollection:
        """
        Active alerts with common filters.

        Examples:
          alerts_active(point=(lat, lon))
          alerts_active(area="CA")
          alerts_active(zone="CAZ041")
        """
        params: Dict[str, Any] = {}

        if point is not None:
            lat, lon = _clamp_lat_lon(point[0], point[1])
            params["point"] = f"{lat},{lon}"

        def _maybe_list(v: Optional[Union[str, List[str]]]) -> Optional[str]:
            if v is None:
                return None
            if isinstance(v, list):
                return ",".join(v)
            return str(v)

        if area is not None:
            params["area"] = _maybe_list(area)
        if zone is not None:
            params["zone"] = _maybe_list(zone)
        if event is not None:
            params["event"] = _maybe_list(event)
        if severity is not None:
            params["severity"] = _maybe_list(severity)
        if urgency is not None:
            params["urgency"] = _maybe_list(urgency)
        if certainty is not None:
            params["certainty"] = _maybe_list(certainty)

        data = self._request_json("GET", "/alerts/active", params=params or None, headers=self._headers())
        return AlertCollection.from_geojson(data)

    def alert_types(self) -> Json:
        return self._request_json("GET", "/alerts/types", headers=self._headers())

    def alert(self, alert_id: str) -> Json:
        return self._request_json("GET", f"/alerts/{alert_id}", headers=self._headers())

    # ---------------------------
    # Pagination helper (generic)
    # ---------------------------

    def iter_paged(
        self,
        first_path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        item_key: str = "features",
        next_key_path: Tuple[str, ...] = ("pagination", "next"),
        max_pages: Optional[int] = None,
    ) -> Generator[Any, None, None]:
        """
        Generic paginator for endpoints that return pagination.next (like some collections).

        Yields items from item_key each page. Stops when no next link.
        """
        page = 0
        url = first_path_or_url
        local_params = dict(params or {})

        while True:
            data = self.get(url, params=local_params if url == first_path_or_url else None)
            items = data.get(item_key) or []
            for it in items:
                yield it

            # find pagination.next
            nxt = data
            for k in next_key_path:
                if not isinstance(nxt, dict):
                    nxt = None
                    break
                nxt = nxt.get(k)
            if not isinstance(nxt, str) or not nxt:
                break

            url = nxt
            local_params = {}
            page += 1
            if max_pages is not None and page >= max_pages:
                break
