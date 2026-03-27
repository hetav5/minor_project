import csv
import math
import sqlite3
import time
import webbrowser
from collections import deque
from datetime import datetime

import folium
import matplotlib.pyplot as plt
import pygame
import serial
import tkinter as tk

# ── Serial ────────────────────────────────────────────────────────────────────
SERIAL_PORT = "COM3"
SERIAL_BAUD = 74880

# ── Location ──────────────────────────────────────────────────────────────────
VANDALUR_LAT = 12.8797
VANDALUR_LON = 80.0810
LOCK_LOCATION_TO_VANDALUR = True

# ── Timing ────────────────────────────────────────────────────────────────────
LOG_UPDATE_INTERVAL_MS = 1000

# ── Theme ─────────────────────────────────────────────────────────────────────
BG       = "#0D1B2A"
CARD     = "#1B2F45"
CARD2    = "#162436"
ACCENT   = "#00C2A8"
ACCENT2  = "#0097A7"
TEXT     = "#E8EDF2"
MUTED    = "#7A8FA0"
BORDER   = "#243447"
LOW_COL  = "#00C2A8"
MED_COL  = "#F4A261"
HIGH_COL = "#E07B54"
CRIT_COL = "#E63946"

# ── Serial connection ─────────────────────────────────────────────────────────
def open_serial_connection():
    try:
        return serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
    except Exception as exc:
        print(f"Serial not connected ({exc}). Running in sim mode.")
        return None

ser = open_serial_connection()
pygame.mixer.init()

# ── CSV ───────────────────────────────────────────────────────────────────────
csv_file   = open("data_log.csv", "a", newline="")
csv_writer = csv.writer(csv_file)

# ── Database ──────────────────────────────────────────────────────────────────
conn   = sqlite3.connect("data.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS logs(
  time TEXT, temp REAL, pressure REAL,
  ax INT, ay INT, az INT,
  lat REAL, lon REAL,
  risk TEXT, fall_detected INT)
""")
conn.commit()

def ensure_logs_schema():
    cursor.execute("PRAGMA table_info(logs)")
    existing = {row[1] for row in cursor.fetchall()}
    required = {
        "time": "TEXT", "temp": "REAL", "pressure": "REAL",
        "ax": "INT", "ay": "INT", "az": "INT",
        "lat": "REAL", "lon": "REAL",
        "risk": "TEXT", "fall_detected": "INT",
    }
    for name, col_type in required.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE logs ADD COLUMN {name} {col_type}")
    conn.commit()

ensure_logs_schema()

# ── State ─────────────────────────────────────────────────────────────────────
temps = deque(maxlen=30)
times = deque(maxlen=30)
path  = []

last_known = {
    "temp": None, "pressure": None,
    "ax": None, "ay": None, "az": None,
    "lat": VANDALUR_LAT, "lon": VANDALUR_LON,
    "risk": None,
}


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def parse_sensor_data(raw):
    for prefix in ("RAW:", "DATA:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
    if "T:" not in raw or "LAT:" not in raw:
        return None
    parts = {}
    for token in raw.split(","):
        if ":" not in token:
            continue
        k, v = token.split(":", 1)
        parts[k.strip()] = v.strip()
    try:
        return (
            float(parts["T"]),
            float(parts["P"]),
            int(parts["AX"]),
            int(parts["AY"]),
            int(parts["AZ"]),
            float(parts["LAT"]),
            float(parts["LON"]),
        )
    except (KeyError, ValueError):
        return None


def read_sample():
    global last_known
    if ser and ser.in_waiting:
        raw = ser.readline().decode(errors="ignore").strip()
        if raw:
            print(raw)
            if raw.startswith("RISK:"):
                last_known["risk"] = raw[5:].strip()
            else:
                result = parse_sensor_data(raw)
                if result:
                    temp, pressure, ax, ay, az, s_lat, s_lon = result
                    last_known["temp"]     = temp
                    last_known["pressure"] = pressure
                    last_known["ax"]       = ax
                    last_known["ay"]       = ay
                    last_known["az"]       = az
                    if not LOCK_LOCATION_TO_VANDALUR:
                        last_known["lat"] = s_lat
                        last_known["lon"] = s_lon
    return (
        last_known["temp"],     last_known["pressure"],
        last_known["ax"],       last_known["ay"],
        last_known["az"],       last_known["lat"],
        last_known["lon"],
    )


def risk_color(risk):
    return {
        "LOW":      LOW_COL,
        "MEDIUM":   MED_COL,
        "HIGH":     HIGH_COL,
        "CRITICAL": CRIT_COL,
    }.get(risk, LOW_COL)


# ═════════════════════════════════════════════════════════════════════════════
# Graph / Map
# ═════════════════════════════════════════════════════════════════════════════

def show_graph():
    if not temps:
        return
    fig, ax = plt.subplots(figsize=(9, 4), facecolor="#0D1B2A")
    ax.set_facecolor("#1B2F45")
    ax.plot(list(times), list(temps), color="#00C2A8", linewidth=2.5,
            marker="o", markersize=4, markerfacecolor="#00C2A8")
    ax.fill_between(range(len(temps)), list(temps), alpha=0.15, color="#00C2A8")
    ax.set_title("Temperature Over Time", color="#E8EDF2", fontsize=13, pad=12)
    ax.set_ylabel("°C", color="#7A8FA0")
    ax.tick_params(colors="#7A8FA0", labelsize=9)
    ax.set_xticks(range(len(times)))
    ax.set_xticklabels(list(times), rotation=45, ha="right", fontsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#243447")
    plt.tight_layout()
    plt.show()


def show_map():
    center  = path[-1] if path else (VANDALUR_LAT, VANDALUR_LON)
    map_obj = folium.Map(location=center, zoom_start=14,
                         tiles="CartoDB dark_matter")
    folium.Marker(
        [VANDALUR_LAT, VANDALUR_LON],
        tooltip="Vandalur Forest Base",
        icon=folium.Icon(color="green", icon="tree", prefix="fa"),
    ).add_to(map_obj)
    if path:
        folium.Marker(path[-1], tooltip="Current Position",
                      icon=folium.Icon(color="blue", icon="circle")).add_to(map_obj)
        folium.PolyLine(path, color="#00C2A8", weight=4, opacity=0.8).add_to(map_obj)
    map_obj.save("map.html")
    webbrowser.open("map.html")


# ═════════════════════════════════════════════════════════════════════════════
# Main update loop
# ═════════════════════════════════════════════════════════════════════════════

def update():
    temp, pressure, ax, ay, az, lat, lon = read_sample()

    if temp is None:
        root.after(LOG_UPDATE_INTERVAL_MS, update)
        return

    # Risk comes from ESP via RISK: line — default LOW until first arrives
    risk = last_known["risk"] if last_known["risk"] else "LOW"
    fall = (risk == "CRITICAL")

    # G-force for display only (math stays on ESP)
    accel_g = math.sqrt(ax**2 + ay**2 + az**2) / 16384.0

    now = datetime.now().strftime("%H:%M:%S")
    temps.append(temp)
    times.append(now)
    path.append((lat, lon))

    # ── Cards ─────────────────────────────────────────────────────────────────
    temp_val.config(text=f"{temp:.1f}")
    temp_unit.config(text="°C")

    pressure_val.config(text=f"{pressure:.1f}")
    pressure_unit.config(text="hPa")

    ax_val.config(text=str(ax))
    ay_val.config(text=str(ay))
    az_val.config(text=str(az))

    g_val.config(text=f"{accel_g:.2f}")
    g_unit.config(text="g")

    loc_val.config(text=f"{lat:.5f}, {lon:.5f}")
    time_val.config(text=now)

    # ── Risk badge ────────────────────────────────────────────────────────────
    risk_val.config(text=risk, bg=risk_color(risk))

    # ── Fall badge ────────────────────────────────────────────────────────────
    fall_val.config(
        text="FALL DETECTED" if fall else "Normal",
        bg=CRIT_COL if fall else ACCENT2,
    )

    # ── Status bar ────────────────────────────────────────────────────────────
    status_var.set(f"Last update: {now}   |   Risk: {risk}   |   g-force: {accel_g:.2f} g")

    # ── Alert sound ───────────────────────────────────────────────────────────
    if risk == "CRITICAL":
        try:
            pygame.mixer.music.load("alert.wav")
            pygame.mixer.music.play()
        except Exception:
            pass

    # ── Log ───────────────────────────────────────────────────────────────────
    csv_writer.writerow([now, temp, pressure, ax, ay, az, lat, lon, risk, int(fall)])
    cursor.execute(
        """INSERT INTO logs (time, temp, pressure, ax, ay, az, lat, lon, risk, fall_detected)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (now, temp, pressure, ax, ay, az, lat, lon, risk, int(fall)),
    )
    conn.commit()

    root.after(LOG_UPDATE_INTERVAL_MS, update)


# ═════════════════════════════════════════════════════════════════════════════
# UI
# ═════════════════════════════════════════════════════════════════════════════

root = tk.Tk()
root.title("WILDSAFE — Animal Monitoring System")
root.geometry("1020x620")
root.minsize(900, 560)
root.configure(bg=BG)

# ── Header ────────────────────────────────────────────────────────────────────
hdr = tk.Frame(root, bg=BG)
hdr.pack(fill="x", padx=28, pady=(20, 0))

tk.Label(hdr, text="WILDSAFE", font=("Segoe UI", 26, "bold"),
         fg=ACCENT, bg=BG).pack(side="left")
tk.Label(hdr, text="  Animal Monitoring System", font=("Segoe UI", 14),
         fg=MUTED, bg=BG).pack(side="left", pady=(6, 0))

dot_canvas = tk.Canvas(hdr, width=10, height=10, bg=BG, highlightthickness=0)
dot_canvas.pack(side="right", padx=(0, 4), pady=(8, 0))
dot_canvas.create_oval(1, 1, 9, 9, fill=ACCENT, outline="")
tk.Label(hdr, text="LIVE", font=("Segoe UI", 10),
         fg=ACCENT, bg=BG).pack(side="right", pady=(6, 0))

tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=28, pady=(10, 0))
tk.Label(root, text="Vandalur Forest  ·  12.8797°N, 80.0810°E",
         font=("Segoe UI", 9), fg=MUTED, bg=BG).pack(anchor="w", padx=28, pady=(5, 12))

# ── Main layout ───────────────────────────────────────────────────────────────
main = tk.Frame(root, bg=BG)
main.pack(fill="both", expand=True, padx=24, pady=(0, 8))

left = tk.Frame(main, bg=BG)
left.pack(side="left", fill="both", expand=True)

right = tk.Frame(main, bg=CARD2, padx=16, pady=16,
                 highlightbackground=BORDER, highlightthickness=1)
right.pack(side="right", fill="y", padx=(14, 0), ipadx=4)

# ── Card factory ──────────────────────────────────────────────────────────────
def make_card(parent, label, row, col, rowspan=1, colspan=1):
    f = tk.Frame(parent, bg=CARD, padx=14, pady=12,
                 highlightbackground=BORDER, highlightthickness=1)
    f.grid(row=row, column=col, rowspan=rowspan, columnspan=colspan,
           padx=6, pady=6, sticky="nsew")
    tk.Label(f, text=label, font=("Segoe UI", 9), fg=MUTED, bg=CARD).pack(anchor="w")
    return f

for c in range(3):
    left.grid_columnconfigure(c, weight=1)
for r in range(3):
    left.grid_rowconfigure(r, weight=1)

# Temperature
c_temp = make_card(left, "Temperature", 0, 0)
row_t  = tk.Frame(c_temp, bg=CARD)
row_t.pack(anchor="w", pady=(6, 0))
temp_val  = tk.Label(row_t, text="--", font=("Segoe UI", 30, "bold"), fg=TEXT, bg=CARD)
temp_val.pack(side="left")
temp_unit = tk.Label(row_t, text="", font=("Segoe UI", 14), fg=MUTED, bg=CARD)
temp_unit.pack(side="left", pady=(10, 0))

# Pressure
c_pres = make_card(left, "Pressure", 0, 1)
row_p  = tk.Frame(c_pres, bg=CARD)
row_p.pack(anchor="w", pady=(6, 0))
pressure_val  = tk.Label(row_p, text="--", font=("Segoe UI", 30, "bold"), fg=TEXT, bg=CARD)
pressure_val.pack(side="left")
pressure_unit = tk.Label(row_p, text="", font=("Segoe UI", 14), fg=MUTED, bg=CARD)
pressure_unit.pack(side="left", pady=(10, 0))

# G-force
c_g   = make_card(left, "G-Force", 0, 2)
row_g = tk.Frame(c_g, bg=CARD)
row_g.pack(anchor="w", pady=(6, 0))
g_val  = tk.Label(row_g, text="--", font=("Segoe UI", 30, "bold"), fg=ACCENT, bg=CARD)
g_val.pack(side="left")
g_unit = tk.Label(row_g, text="", font=("Segoe UI", 14), fg=MUTED, bg=CARD)
g_unit.pack(side="left", pady=(10, 0))

# Accelerometer
c_accel   = make_card(left, "Accelerometer  (AX / AY / AZ)", 1, 0, colspan=2)
accel_row = tk.Frame(c_accel, bg=CARD)
accel_row.pack(anchor="w", pady=(6, 0), fill="x")

ax_val = ay_val = az_val = None
for lbl_text, attr in [("AX", "ax"), ("AY", "ay"), ("AZ", "az")]:
    col_f = tk.Frame(accel_row, bg=CARD)
    col_f.pack(side="left", expand=True, fill="x", padx=(0, 12))
    tk.Label(col_f, text=lbl_text, font=("Segoe UI", 9), fg=MUTED, bg=CARD).pack(anchor="w")
    lbl = tk.Label(col_f, text="--", font=("Segoe UI", 20, "bold"), fg=TEXT, bg=CARD)
    lbl.pack(anchor="w")
    if attr == "ax":   ax_val = lbl
    elif attr == "ay": ay_val = lbl
    else:              az_val = lbl

# Location
c_loc = make_card(left, "GPS Location", 1, 2)
loc_val = tk.Label(c_loc, text="--", font=("Segoe UI", 13, "bold"),
                   fg=TEXT, bg=CARD, wraplength=170)
loc_val.pack(anchor="w", pady=(6, 0))

# Risk
c_risk = make_card(left, "Risk Level", 2, 0)
risk_val = tk.Label(c_risk, text="--", font=("Segoe UI", 16, "bold"),
                    bg=ACCENT, fg="#FFFFFF", padx=14, pady=6)
risk_val.pack(anchor="w", pady=(8, 0))

# Fall detection
c_fall = make_card(left, "Fall Detection", 2, 1)
fall_val = tk.Label(c_fall, text="--", font=("Segoe UI", 16, "bold"),
                    bg=ACCENT2, fg="#FFFFFF", padx=14, pady=6)
fall_val.pack(anchor="w", pady=(8, 0))

# Last update
c_time = make_card(left, "Last Update", 2, 2)
time_val = tk.Label(c_time, text="--", font=("Segoe UI", 20, "bold"), fg=MUTED, bg=CARD)
time_val.pack(anchor="w", pady=(6, 0))

# ── Right panel ───────────────────────────────────────────────────────────────
tk.Label(right, text="Actions", font=("Segoe UI", 11, "bold"),
         fg=TEXT, bg=CARD2).pack(anchor="w", pady=(0, 10))

btn_cfg = dict(font=("Segoe UI", 10), bg=ACCENT, fg="#072F2A",
               activebackground="#00A890", activeforeground="#072F2A",
               bd=0, relief="flat", padx=10, pady=8, cursor="hand2")

tk.Button(right, text="  Temperature Graph",
          command=show_graph, **btn_cfg).pack(fill="x", pady=4)
tk.Button(right, text="  Open Live Map",
          command=show_map,   **btn_cfg).pack(fill="x", pady=4)

tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=14)

tk.Label(right, text="Risk Levels", font=("Segoe UI", 10, "bold"),
         fg=TEXT, bg=CARD2).pack(anchor="w", pady=(0, 8))

for level, color, val in [
    ("LOW",      LOW_COL,  "Stable / no movement"),
    ("MEDIUM",   MED_COL,  "Moderate movement"),
    ("HIGH",     HIGH_COL, "Strong movement"),
    ("CRITICAL", CRIT_COL, "Fall / extreme event"),
]:
    row = tk.Frame(right, bg=CARD2)
    row.pack(fill="x", pady=2)
    tk.Label(row, text="  ", bg=color, width=2).pack(side="left")
    tk.Label(row, text=f" {level}", font=("Segoe UI", 9, "bold"),
             fg=color, bg=CARD2, width=9, anchor="w").pack(side="left")
    tk.Label(row, text=val, font=("Segoe UI", 9),
             fg=MUTED, bg=CARD2).pack(side="left")

tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=14)

tk.Label(right, text="Risk Source", font=("Segoe UI", 10, "bold"),
         fg=TEXT, bg=CARD2).pack(anchor="w")
tk.Label(right,
         text="Calculated on\nESP8266 receiver.\nDelta-g + fall\ndetection logic.",
         font=("Segoe UI", 9), fg=MUTED, bg=CARD2,
         justify="left").pack(anchor="w", pady=(4, 0))

# ── Status bar ────────────────────────────────────────────────────────────────
status_var = tk.StringVar(value="Waiting for first packet...")
tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
tk.Label(root, textvariable=status_var, font=("Segoe UI", 9),
         fg=MUTED, bg="#0A1520", anchor="w", pady=5).pack(fill="x", padx=16)

update()
root.mainloop()