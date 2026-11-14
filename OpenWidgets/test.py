from nws_ez import NWSClient

client = NWSClient(user_agent="my-weather-thing/1.0 (me@example.com)")

lat, lon = 38.61248, -77.306341 # NoVA

# Forecast
fc = client.forecast(lat, lon)
for p in fc.periods[:3]:
    print(p.name, p.temperature, p.temperature_unit, "-", p.short_forecast)

# Hourly
h = client.hourly(lat, lon)
print("Next hour:", h.periods[0].start_time, h.periods[0].temperature, h.periods[0].short_forecast)

# Latest obs near the point
obs = client.latest_observation(lat, lon)
print("Obs:", obs.timestamp, obs.temperature_c, "C", "-", obs.text_description)
print(obs)
# Active alerts for the point
alerts = client.alerts_active(point=(lat, lon))
for a in alerts.features[:5]:
    print(a.event, "-", a.headline)
