import csv
import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime

import folium
import matplotlib.pyplot as plt
import pygame
import serial
import tkinter as tk

# Serial configuration
SERIAL_PORT = "COM4"
SERIAL_BAUD = 115200

# Vandalur Forest (Arignar Anna Zoological Park zone)
VANDALUR_LAT = 12.8797
VANDALUR_LON = 80.0810
LOCK_LOCATION_TO_VANDALUR = True

# Optional weather API for live temperature/humidity
WEATHER_UPDATE_INTERVAL = 300  # seconds
WEATHER_RETRY_INTERVAL = 60  # seconds
LOG_UPDATE_INTERVAL_MS = 1000  # milliseconds

# Theme
BG = "#0D1B2A"
CARD = "#1B263B"
ACCENT = "#00C2A8"
TEXT = "#E0E1DD"
MUTED = "#9AA4B2"
WARNING = "#F4A261"
DANGER = "#E63946"


def open_serial_connection():
    try:
        return serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
    except Exception as exc:
        print(f"Serial not connected ({exc}). Running in API/sim mode.")
        return None


ser = open_serial_connection()

pygame.mixer.init()

# CSV
csv_file = open("data_log.csv", "a", newline="")
csv_writer = csv.writer(csv_file)

# Database
conn = sqlite3.connect("data.db")
cursor = conn.cursor()
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS logs(
time TEXT, temp INT, humidity INT, hr INT, lat REAL, lon REAL, risk TEXT)
"""
)

# Graph data
temps = deque(maxlen=20)
times = deque(maxlen=20)

# Tracking
path = []
total_distance = 0.0
last_time = None
speed = 0.0

# Weather cache
last_weather_fetch = 0
last_weather_attempt = 0
weather_error_logged = False
cached_weather = {"temp": None, "humidity": None}


def calculate_distance(lat1, lon1, lat2, lon2):
    radius_km = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def calculate_risk(temp, hr):
    if temp > 45 or hr > 150:
        return "CRITICAL"
    if temp > 40 or hr > 120:
        return "HIGH"
    if temp > 35:
        return "MEDIUM"
    return "LOW"


def fetch_weather(lat, lon):
    global last_weather_fetch, last_weather_attempt, weather_error_logged, cached_weather

    now_epoch = time.time()

    # Use cached weather until the configured refresh interval expires.
    if now_epoch - last_weather_fetch < WEATHER_UPDATE_INTERVAL:
        return cached_weather

    # If API is down/offline, back off retries to avoid noisy logs.
    if now_epoch - last_weather_attempt < WEATHER_RETRY_INTERVAL:
        return cached_weather

    try:
        last_weather_attempt = now_epoch
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m"
        )
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))

        current = payload.get("current", {})
        temperature = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")

        cached_weather = {
            "temp": int(round(temperature)) if temperature is not None else None,
            "humidity": int(round(humidity)) if humidity is not None else None,
        }
        last_weather_fetch = now_epoch
        weather_error_logged = False
    except urllib.error.URLError as exc:
        if not weather_error_logged:
            print(
                f"Weather fetch unavailable ({exc}). Using sensor/default values.")
            weather_error_logged = True
    except Exception as exc:
        if not weather_error_logged:
            print(
                f"Weather fetch failed ({exc}). Using sensor/default values.")
            weather_error_logged = True

    return cached_weather


def parse_sensor_data(raw_data):
    parts = dict(x.split(":") for x in raw_data.split(","))
    temp = int(parts["T"])
    humidity = int(parts["H"])
    hr = int(parts["HR"])
    lat = float(parts["LAT"])
    lon = float(parts["LON"])
    return temp, humidity, hr, lat, lon


def show_graph():
    if not temps:
        return
    plt.clf()
    plt.plot(times, temps, color="#00C2A8", linewidth=2)
    plt.title("Temperature Over Time")
    plt.xlabel("Time")
    plt.ylabel("Temperature (C)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()


def show_map():
    map_center = path[-1] if path else (VANDALUR_LAT, VANDALUR_LON)
    map_obj = folium.Map(location=map_center, zoom_start=14)

    folium.Marker(
        [VANDALUR_LAT, VANDALUR_LON],
        tooltip="Vandalur Forest",
        icon=folium.Icon(color="green", icon="tree", prefix="fa"),
    ).add_to(map_obj)

    if path:
        folium.Marker(path[-1], tooltip="Current Location").add_to(map_obj)
        folium.PolyLine(path, color="cyan", weight=5).add_to(map_obj)

    map_obj.save("map.html")
    webbrowser.open("map.html")


def update_status_pill(risk):
    color = ACCENT
    if risk == "MEDIUM":
        color = WARNING
    elif risk in ("HIGH", "CRITICAL"):
        color = DANGER

    risk_value_var.set(risk)
    risk_badge.config(bg=color, fg="#FFFFFF")


def refresh_dashboard(temp, humidity, hr, lat, lon, risk):
    temp_value_var.set(f"{temp} C")
    humidity_value_var.set(f"{humidity} %")
    hr_value_var.set(str(hr))
    location_value_var.set(f"{lat:.5f}, {lon:.5f}")
    distance_value_var.set(f"{total_distance:.3f} km")
    speed_value_var.set(f"{speed:.2f} km/h")
    source_value_var.set(
        "Weather API" if use_weather_var.get() else "Serial Sensor")
    update_status_pill(risk)


def read_sample():
    sensor_values = None

    if ser and ser.in_waiting:
        raw = ser.readline().decode(errors="ignore").strip()
        if raw:
            print(raw)
            try:
                sensor_values = parse_sensor_data(raw)
            except Exception:
                sensor_values = None

    lat = VANDALUR_LAT
    lon = VANDALUR_LON
    hr = 80

    if sensor_values:
        temp, humidity, hr, serial_lat, serial_lon = sensor_values
        if not LOCK_LOCATION_TO_VANDALUR:
            lat, lon = serial_lat, serial_lon
    else:
        temp, humidity = 33, 60

    if use_weather_var.get():
        weather = fetch_weather(lat, lon)
        if weather["temp"] is not None:
            temp = weather["temp"]
        if weather["humidity"] is not None:
            humidity = weather["humidity"]

    return temp, humidity, hr, lat, lon


def update():
    global total_distance, last_time, speed

    temp, humidity, hr, lat, lon = read_sample()
    risk = calculate_risk(temp, hr)
    now = datetime.now().strftime("%H:%M:%S")

    temps.append(temp)
    times.append(now)

    current_time = time.time()
    if path:
        prev_lat, prev_lon = path[-1]
        dist = calculate_distance(prev_lat, prev_lon, lat, lon)
        total_distance += dist

        if last_time:
            time_diff_hours = (current_time - last_time) / 3600
            if time_diff_hours > 0:
                speed = dist / time_diff_hours

    last_time = current_time
    path.append((lat, lon))

    refresh_dashboard(temp, humidity, hr, lat, lon, risk)

    if risk == "CRITICAL":
        try:
            pygame.mixer.music.load("alert.wav")
            pygame.mixer.music.play()
        except Exception:
            pass

    csv_writer.writerow([now, temp, humidity, hr, lat, lon, risk])
    cursor.execute(
        "INSERT INTO logs VALUES (?,?,?,?,?,?,?)",
        (now, temp, humidity, hr, lat, lon, risk),
    )
    conn.commit()

    root.after(LOG_UPDATE_INTERVAL_MS, update)


# UI
root = tk.Tk()
root.title("WILDSAFE Dashboard")
root.geometry("960x560")
root.minsize(880, 520)
root.configure(bg=BG)

header = tk.Frame(root, bg=BG)
header.pack(fill="x", padx=24, pady=(18, 10))

title_label = tk.Label(
    header,
    text="WILDSAFE Monitoring Console",
    font=("Segoe UI Semibold", 22),
    fg=TEXT,
    bg=BG,
)
title_label.pack(anchor="w")

subtitle_label = tk.Label(
    header,
    text="Location locked to Vandalur Forest (12.8797, 80.0810)",
    font=("Segoe UI", 10),
    fg=MUTED,
    bg=BG,
)
subtitle_label.pack(anchor="w")

content = tk.Frame(root, bg=BG)
content.pack(fill="both", expand=True, padx=24, pady=(0, 16))

grid_frame = tk.Frame(content, bg=BG)
grid_frame.pack(side="left", fill="both", expand=True)

right_panel = tk.Frame(content, bg=CARD, padx=14, pady=14)
right_panel.pack(side="right", fill="y", padx=(12, 0))


def metric_card(parent, title, row, col, value_var):
    card = tk.Frame(parent, bg=CARD, padx=12, pady=12,
                    bd=0, highlightthickness=0)
    card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
    tk.Label(card, text=title, font=("Segoe UI", 10),
             fg=MUTED, bg=CARD).pack(anchor="w")
    tk.Label(
        card,
        textvariable=value_var,
        font=("Segoe UI Semibold", 18),
        fg=TEXT,
        bg=CARD,
    ).pack(anchor="w", pady=(8, 0))


for i in range(2):
    grid_frame.grid_columnconfigure(i, weight=1)
for j in range(4):
    grid_frame.grid_rowconfigure(j, weight=1)

temp_value_var = tk.StringVar(value="--")
humidity_value_var = tk.StringVar(value="--")
hr_value_var = tk.StringVar(value="--")
location_value_var = tk.StringVar(value="--")
distance_value_var = tk.StringVar(value="0.000 km")
speed_value_var = tk.StringVar(value="0.00 km/h")
source_value_var = tk.StringVar(value="Serial Sensor")
risk_value_var = tk.StringVar(value="LOW")

metric_card(grid_frame, "Temperature", 0, 0, temp_value_var)
metric_card(grid_frame, "Humidity", 0, 1, humidity_value_var)
metric_card(grid_frame, "Heart Rate", 1, 0, hr_value_var)
metric_card(grid_frame, "Live Location", 1, 1, location_value_var)
metric_card(grid_frame, "Distance", 2, 0, distance_value_var)
metric_card(grid_frame, "Speed", 2, 1, speed_value_var)
metric_card(grid_frame, "Data Source", 3, 0, source_value_var)

risk_card = tk.Frame(grid_frame, bg=CARD, padx=12, pady=12)
risk_card.grid(row=3, column=1, padx=8, pady=8, sticky="nsew")
tk.Label(risk_card, text="Risk Level", font=(
    "Segoe UI", 10), fg=MUTED, bg=CARD).pack(anchor="w")
risk_badge = tk.Label(
    risk_card,
    textvariable=risk_value_var,
    font=("Segoe UI Semibold", 12),
    bg=ACCENT,
    fg="#FFFFFF",
    padx=10,
    pady=4,
)
risk_badge.pack(anchor="w", pady=(10, 0))

tk.Label(
    right_panel,
    text="Actions",
    font=("Segoe UI Semibold", 12),
    fg=TEXT,
    bg=CARD,
).pack(anchor="w", pady=(0, 8))

btn_style = {
    "font": ("Segoe UI", 10),
    "bg": ACCENT,
    "fg": "#082032",
    "activebackground": "#0ED4B8",
    "activeforeground": "#082032",
    "bd": 0,
    "relief": "flat",
    "padx": 10,
    "pady": 7,
}

tk.Button(right_panel, text="Open Graph", command=show_graph, **btn_style).pack(
    fill="x", pady=5
)
tk.Button(right_panel, text="Open Map", command=show_map,
          **btn_style).pack(fill="x", pady=5)

use_weather_var = tk.BooleanVar(value=True)
tk.Checkbutton(
    right_panel,
    text="Use Weather API for Temp",
    variable=use_weather_var,
    bg=CARD,
    fg=TEXT,
    selectcolor=BG,
    activebackground=CARD,
    activeforeground=TEXT,
    font=("Segoe UI", 10),
).pack(anchor="w", pady=(12, 4))

tk.Label(
    right_panel,
    text="Weather source: Open-Meteo",
    font=("Segoe UI", 9),
    fg=MUTED,
    bg=CARD,
).pack(anchor="w", pady=(2, 0))

update()
root.mainloop()
