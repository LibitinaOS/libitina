from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _deep_get(d: Dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


@dataclass(frozen=True)
class PointInfo:
    lat: float
    lon: float
    grid_id: str
    grid_x: int
    grid_y: int
    forecast_url: str
    forecast_hourly_url: str
    forecast_grid_data_url: str
    observation_stations_url: str
    forecast_zone_url: Optional[str] = None
    county_url: Optional[str] = None
    time_zone: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ForecastPeriod:
    number: int
    name: Optional[str]
    start_time: str
    end_time: str
    is_daytime: Optional[bool]
    temperature: Optional[float]
    temperature_unit: Optional[str]
    wind_speed: Optional[str]
    wind_direction: Optional[str]
    short_forecast: Optional[str]
    detailed_forecast: Optional[str]
    probability_of_precipitation: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class Forecast:
    updated: Optional[str]
    units: Optional[str]
    periods: List[ForecastPeriod]
    raw: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_geojson(data: Dict[str, Any]) -> "Forecast":
        props = data.get("properties", data)
        periods_in = props.get("periods") or []
        periods: List[ForecastPeriod] = []

        for p in periods_in:
            pop = _deep_get(p, "probabilityOfPrecipitation", "value")
            periods.append(
                ForecastPeriod(
                    number=int(p.get("number", 0) or 0),
                    name=p.get("name"),
                    start_time=str(p.get("startTime")),
                    end_time=str(p.get("endTime")),
                    is_daytime=p.get("isDaytime"),
                    temperature=(p.get("temperature") if isinstance(p.get("temperature"), (int, float)) else _deep_get(p, "temperature", "value")),
                    temperature_unit=p.get("temperatureUnit") or _deep_get(p, "temperature", "unitCode"),
                    wind_speed=str(p.get("windSpeed")) if p.get("windSpeed") is not None else None,
                    wind_direction=p.get("windDirection"),
                    short_forecast=p.get("shortForecast"),
                    detailed_forecast=p.get("detailedForecast"),
                    probability_of_precipitation=pop if isinstance(pop, (int, float)) else None,
                    raw=p,
                )
            )

        return Forecast(
            updated=props.get("updateTime") or props.get("updated"),
            units=props.get("units"),
            periods=periods,
            raw=data,
        )


@dataclass(frozen=True)
class Observation:
    timestamp: str
    text_description: Optional[str]
    temperature_c: Optional[float]
    dewpoint_c: Optional[float]
    wind_direction_deg: Optional[float]
    wind_speed_mps: Optional[float]
    wind_gust_mps: Optional[float]
    relative_humidity_pct: Optional[float]
    pressure_pa: Optional[float]
    visibility_m: Optional[float]
    station_id: Optional[str] = None
    station_name: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_geojson(data: Dict[str, Any]) -> "Observation":
        props = data.get("properties", data)

        def v(path_key: str) -> Optional[float]:
            val = _deep_get(props, path_key, "value")
            return float(val) if isinstance(val, (int, float)) else None

        return Observation(
            timestamp=str(props.get("timestamp")),
            text_description=props.get("textDescription"),
            temperature_c=v("temperature"),
            dewpoint_c=v("dewpoint"),
            wind_direction_deg=v("windDirection"),
            wind_speed_mps=v("windSpeed"),
            wind_gust_mps=v("windGust"),
            relative_humidity_pct=v("relativeHumidity"),
            pressure_pa=v("barometricPressure"),
            visibility_m=v("visibility"),
            station_id=props.get("stationId"),
            station_name=props.get("stationName"),
            raw=data,
        )


@dataclass(frozen=True)
class AlertFeature:
    id: Optional[str]
    event: Optional[str]
    headline: Optional[str]
    severity: Optional[str]
    urgency: Optional[str]
    certainty: Optional[str]
    area_desc: Optional[str]
    sent: Optional[str]
    effective: Optional[str]
    expires: Optional[str]
    ends: Optional[str]
    instruction: Optional[str]
    description: Optional[str]
    raw: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_geojson_feature(feat: Dict[str, Any]) -> "AlertFeature":
        props = feat.get("properties", {})
        return AlertFeature(
            id=props.get("id") or feat.get("id"),
            event=props.get("event"),
            headline=props.get("headline"),
            severity=props.get("severity"),
            urgency=props.get("urgency"),
            certainty=props.get("certainty"),
            area_desc=props.get("areaDesc"),
            sent=props.get("sent"),
            effective=props.get("effective"),
            expires=props.get("expires"),
            ends=props.get("ends"),
            instruction=props.get("instruction"),
            description=props.get("description"),
            raw=feat,
        )


@dataclass(frozen=True)
class AlertCollection:
    title: Optional[str]
    updated: Optional[str]
    features: List[AlertFeature]
    raw: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_geojson(data: Dict[str, Any]) -> "AlertCollection":
        feats_in = data.get("features") or []
        feats = [AlertFeature.from_geojson_feature(f) for f in feats_in if isinstance(f, dict)]
        return AlertCollection(
            title=data.get("title"),
            updated=data.get("updated"),
            features=feats,
            raw=data,
        )
