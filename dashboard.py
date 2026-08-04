import streamlit as st
import json
import os
import threading
import time
import pandas as pd
import numpy as np
import altair as alt
from filelock import FileLock, Timeout
import folium
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh
import paho.mqtt.client as mqtt

# Constants for configuration
# Public MQTT broker so this dashboard can receive live data wherever it's
# deployed, not just on the university network. Must match publisher.py.
mqtt_broker = os.environ.get("MQTT_BROKER", "broker.hivemq.com")
mqtt_topic = os.environ.get("MQTT_TOPIC", "kmathew994/fuelcheck/nsw/prices")
data_file = "fuel_data.json"
lock_file = data_file + ".lock"
initial_record_threshold = 100
history_file = "fuelPrice_history.csv"
prediction_horizon_hours = 24

# Visual theme palette, reused across custom CSS, map markers and charts
COLOR_BG = "#0b0e14"
COLOR_CARD = "#141a24"
COLOR_BORDER = "#232b3a"
COLOR_TEXT = "#f1f3f6"
COLOR_MUTED = "#8a93a6"
COLOR_ACCENT = "#ff8a3d"
COLOR_GOOD = "#2dd4bf"
COLOR_BAD = "#f4515c"
COLOR_WARN = "#f5b942"

# Global variables
buffer = []
existing_keys = set()
data_initialized = False
lock = threading.Lock()

# Streamlit session state initialization
if "mqtt_loop_running" not in st.session_state:
    st.session_state["mqtt_loop_running"] = False

if "app_start_time" not in st.session_state:
    st.session_state["app_start_time"] = time.time()

if "app_initialized" not in st.session_state:
    with lock:
        buffer.clear()
        existing_keys.clear()
        data_initialized = False
    st.session_state["app_initialized"] = True

# Data loading and saving fuel data from JSON file
def loadExistingData():
    try:
        if os.path.exists(data_file):
            with open(data_file, "r") as f:
                return json.load(f)
    except Exception as e:
        print("Error loading data file:", e)
    return []

# Write data to the JSON file
def saveData(data):
    file_lock = FileLock(lock_file, timeout=2)
    try:
        with file_lock:
            with open(data_file, "w") as f:
                json.dump(data, f, indent=2)
    except Timeout:
        print("Timeout: could not acquire file lock to save data.")

# Saving initial buffer
def saveInitialData():
    global data_initialized
    with lock:
        saveData(buffer)
        data_initialized = True

# MQTT callbacks

# Handling incoming MQTT messages and saving unique records
def onMessage(client, userdata, msg):
    global buffer, existing_keys, data_initialized
    new_entry = json.loads(msg.payload.decode())
    key = (new_entry.get("stationid"), new_entry.get("fueltype"), new_entry.get("lastupdated"))
    with lock:
        if key in existing_keys:
            return
        existing_keys.add(key)
        if not data_initialized:
            buffer.append(new_entry)
            if len(buffer) >= initial_record_threshold:
                threading.Thread(target=saveInitialData, daemon=True).start()
        else:
            current_data = loadExistingData()
            existing_keys_file = {
                (e.get("stationid"), e.get("fueltype"), e.get("lastupdated")) for e in current_data
            }
            if key not in existing_keys_file:
                current_data.append(new_entry)
                saveData(current_data)

# Subscribing MQTT
def onConnect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(mqtt_topic)

def onSubscribe(client, userdata, mid, granted_qos):
    pass

# Handling diconnection
def onDisconnect(client, userdata, rc):
    if rc != 0:
        try:
            client.reconnect()
        except Exception:
            pass

# Starting MQTT subscription loop
def mqttSubscriber():
    if st.session_state.get("mqtt_loop_running"):
        return
    st.session_state["mqtt_loop_running"] = True
    while True:
        try:
            client = mqtt.Client(protocol=mqtt.MQTTv311)
            client.on_connect = onConnect
            client.on_subscribe = onSubscribe
            client.on_message = onMessage
            client.on_disconnect = onDisconnect
            client.connect(mqtt_broker, int(os.environ.get("MQTT_PORT", 1883)), 60)
            client.loop_forever()
        except Exception:
            time.sleep(5)

# Checking file changes
def fileHasChanged():
    try:
        mtime = os.path.getmtime(data_file)
        if mtime != st.session_state.get("last_mtime", 0):
            st.session_state["last_mtime"] = mtime
            return True
    except Exception:
        return False
    return False

# Loading file data with file lock
def loadDataForDisplay():
    file_lock = FileLock(lock_file, timeout=2)
    try:
        with file_lock:
            with open(data_file, "r") as f:
                return json.load(f)
    except Timeout:
        pass
    except Exception:
        pass
    return []

# Data grouping and filtering
def groupDataByStation(data):
    stations = {}
    for entry in data:
        sid = entry.get("stationid")
        if not sid:
            continue
        if sid not in stations:
            stations[sid] = {
                "stationname": entry.get("name", "Unknown"),
                "brand": entry.get("brand", "Unknown"),
                "address": entry.get("address", "No address"),
                "latitude": entry.get("latitude"),
                "longitude": entry.get("longitude"),
                "lastupdated": entry.get("lastupdated", ""),
                "fuels": {}
            }
        fueltype = entry.get("fueltype")
        price = entry.get("price")
        if fueltype and price is not None:
            stations[sid]["fuels"][fueltype] = price
        if entry.get("lastupdated") > stations[sid]["lastupdated"]:
            stations[sid]["lastupdated"] = entry.get("lastupdated")
    return stations

# Fits a simple linear trend to a station's price history and projects it forward
def predictPrice(station_id, fueltype, hours_ahead=prediction_horizon_hours):
    if not os.path.exists(history_file):
        return None
    history_df = pd.read_csv(history_file)
    sub = history_df[
        (history_df["stationid"].astype(str) == str(station_id)) &
        (history_df["fueltype"] == fueltype)
    ].dropna(subset=["price", "fetched_at"]).copy()
    if len(sub) < 2:
        return None

    sub["fetched_at"] = pd.to_datetime(sub["fetched_at"], format="mixed", utc=True)
    sub = sub.sort_values("fetched_at")
    sub["hours"] = (sub["fetched_at"] - sub["fetched_at"].iloc[0]).dt.total_seconds() / 3600

    slope, intercept = np.polyfit(sub["hours"], sub["price"], 1)
    predicted_hour = sub["hours"].iloc[-1] + hours_ahead
    predicted_price = slope * predicted_hour + intercept

    return {"history": sub, "slope_per_hour": slope, "predicted_price": predicted_price}

# Injects the app's visual theme: fonts, colors, cards, and chrome cleanup
def injectTheme():
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        #MainMenu {{visibility: hidden;}}
        footer {{visibility: hidden;}}
        header {{visibility: hidden;}}

        .stApp {{
            background: radial-gradient(circle at 15% 0%, #141a2a 0%, {COLOR_BG} 45%);
        }}

        .block-container {{
            padding-top: 2rem;
            padding-bottom: 3rem;
            max-width: 1400px;
        }}

        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: {COLOR_BG}; }}
        ::-webkit-scrollbar-thumb {{ background: {COLOR_BORDER}; border-radius: 8px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: {COLOR_ACCENT}; }}

        /* Header */
        .app-header {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6em;
            margin-bottom: 0.1em;
        }}
        .app-header .icon-badge {{
            font-size: 2.1rem;
            background: linear-gradient(135deg, {COLOR_ACCENT}, #ffb27a);
            -webkit-background-clip: text;
            background-clip: text;
        }}
        .app-title {{
            text-align: center;
            font-size: 2.6rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin: 0;
            background: linear-gradient(135deg, #ffffff 30%, {COLOR_ACCENT} 120%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        .app-subtitle {{
            text-align: center;
            color: {COLOR_MUTED};
            font-size: 0.98rem;
            margin-top: 0.35em;
            margin-bottom: 1.6em;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5em;
        }}
        .live-dot {{
            width: 8px; height: 8px; border-radius: 50%;
            background: {COLOR_GOOD};
            box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.7);
            animation: pulse 2s infinite;
            display: inline-block;
        }}
        @keyframes pulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.55); }}
            70% {{ box-shadow: 0 0 0 8px rgba(45, 212, 191, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(45, 212, 191, 0); }}
        }}

        /* Generic card */
        .card {{
            background: {COLOR_CARD};
            border: 1px solid {COLOR_BORDER};
            border-radius: 18px;
            padding: 1.25em 1.4em;
            box-shadow: 0 8px 24px rgba(0,0,0,0.25);
        }}

        /* Stat tiles */
        .stat-tile {{
            background: {COLOR_CARD};
            border: 1px solid {COLOR_BORDER};
            border-radius: 16px;
            padding: 1em 1.2em;
            display: flex;
            align-items: center;
            gap: 0.75em;
            box-shadow: 0 6px 18px rgba(0,0,0,0.2);
        }}
        .stat-tile .stat-icon {{
            font-size: 1.6rem;
            width: 2.2em; height: 2.2em;
            border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            background: rgba(255, 138, 61, 0.12);
        }}
        .stat-tile .stat-value {{
            font-size: 1.5rem;
            font-weight: 700;
            color: {COLOR_TEXT};
            line-height: 1.1;
        }}
        .stat-tile .stat-label {{
            font-size: 0.8rem;
            color: {COLOR_MUTED};
            font-weight: 500;
            letter-spacing: 0.02em;
        }}

        .section-label {{
            text-transform: uppercase;
            font-size: 0.72rem;
            letter-spacing: 0.12em;
            color: {COLOR_MUTED};
            font-weight: 700;
            margin-bottom: 0.5em;
        }}

        h2 {{
            font-weight: 700 !important;
            letter-spacing: -0.01em;
        }}

        /* Inputs */
        div[data-testid="stSelectbox"] > div,
        div[data-testid="stMultiSelect"] > div {{
            background: #0f141d;
            border-radius: 12px;
            border: 1px solid {COLOR_BORDER};
        }}
        div[data-baseweb="tag"] {{
            background: {COLOR_ACCENT} !important;
            border-radius: 6px !important;
        }}

        /* Map card wrapper */
        .map-frame iframe {{
            border-radius: 18px;
            border: 1px solid {COLOR_BORDER};
            box-shadow: 0 10px 30px rgba(0,0,0,0.35);
        }}

        .legend-row {{
            display: flex;
            gap: 1.4em;
            align-items: center;
            margin-top: 0.7em;
            color: {COLOR_MUTED};
            font-size: 0.85rem;
        }}
        .legend-dot {{
            width: 10px; height: 10px; border-radius: 50%;
            display: inline-block;
            margin-right: 0.4em;
        }}

        /* Metric styling */
        div[data-testid="stMetric"] {{
            background: {COLOR_CARD};
            border: 1px solid {COLOR_BORDER};
            border-radius: 16px;
            padding: 0.9em 1.1em;
        }}

        /* Trend badge */
        .trend-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.4em;
            padding: 0.3em 0.8em;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 600;
            margin-top: 0.6em;
        }}

        .empty-state {{
            text-align: center;
            padding: 3em 1em;
            color: {COLOR_MUTED};
            background: {COLOR_CARD};
            border: 1px dashed {COLOR_BORDER};
            border-radius: 18px;
        }}
        .empty-state .emoji {{
            font-size: 2.2rem;
            display: block;
            margin-bottom: 0.4em;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

# Renders a single stat tile as a small HTML card
def statTile(icon, value, label):
    st.markdown(
        f"""
        <div class="stat-tile">
            <div class="stat-icon">{icon}</div>
            <div>
                <div class="stat-value">{value}</div>
                <div class="stat-label">{label}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Picks a marker/price color tier from percentile cutoffs
def priceTier(price, low_cut, high_cut):
    if price is None or low_cut is None or high_cut is None:
        return COLOR_MUTED, "gray"
    if price <= low_cut:
        return COLOR_GOOD, "green"
    if price <= high_cut:
        return COLOR_WARN, "orange"
    return COLOR_BAD, "red"

# Dashboard layout
def main():
    st.set_page_config(page_title="Fuel Prices Dashboard", page_icon="⛽", layout="wide")
    injectTheme()

    # Dashboard title and subtitle
    st.markdown("<div class='app-header'><span class='icon-badge'>⛽</span></div>", unsafe_allow_html=True)
    st.markdown("<h1 class='app-title'>Fuel Prices Dashboard</h1>", unsafe_allow_html=True)
    st.markdown(
        "<div class='app-subtitle'><span class='live-dot'></span> Live NSW fuel prices &middot; "
        "auto-refreshing every 10 seconds</div>",
        unsafe_allow_html=True,
    )

    # Starting MQTT subscriber thread only once
    if "mqtt_thread_started" not in st.session_state:
        threading.Thread(target=mqttSubscriber, daemon=True).start()
        st.session_state["mqtt_thread_started"] = True
        st.session_state["last_mtime"] = 0
        st.session_state["data"] = []

    # Refreshing the dashboard in 10 seconds
    st_autorefresh(interval=10000, limit=None, key="fuel_autorefresh")

    now = time.time()
    last_reload = st.session_state.get("last_data_reload", 0)

    # Reloading data from file
    if not st.session_state["data"] or (now - last_reload) > 10:
        st.session_state["data"] = loadDataForDisplay()
        st.session_state["last_data_reload"] = now

    data = st.session_state["data"]
    stations = groupDataByStation(data)

    if not data:
        st.markdown(
            """
            <div class="empty-state">
                <span class="emoji">⏳</span>
                <b>Waiting for live fuel data…</b><br>
                The dashboard will populate automatically once the first MQTT messages arrive.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()

    # Filters for all unique fuel types and brands
    fuel_types = set()
    brands = set()
    for info in stations.values():
        fuel_types.update(info.get("fuels", {}).keys())
        brands.add(info.get("brand", "Unknown"))
    fuel_types = sorted(fuel_types)
    brands = sorted(brands)

    # Filter widgets
    with st.container():
        st.markdown("<div class='section-label'>Filters</div>", unsafe_allow_html=True)
        filter_col1, filter_col2 = st.columns([1, 2], gap="medium")

        with filter_col1:
            if not fuel_types:
                fuel_types = ["Regular"]
            default_fuel = "U91" if "U91" in fuel_types else fuel_types[0]
            selected_fuel = st.selectbox("Fuel type", fuel_types, index=fuel_types.index(default_fuel), key="fuel_type")

        with filter_col2:
            selected_brands = st.multiselect(
                "Brands",
                options=brands,
                default=[],
                key="brand_multiselect",
                placeholder="All Brands"
            )

    # Filter stations by selected fuel and brands
    filtered_stations = {
        sid: info for sid, info in stations.items()
        if selected_fuel in info.get("fuels", {}) and
           (info.get("brand", "Unknown") in selected_brands if selected_brands else True)
    }

    prices_list = [info["fuels"].get(selected_fuel) for info in filtered_stations.values() if info["fuels"].get(selected_fuel) is not None]
    avg_price = sum(prices_list) / len(prices_list) if prices_list else None
    low_cut = np.percentile(prices_list, 33) if prices_list else None
    high_cut = np.percentile(prices_list, 66) if prices_list else None

    # Stat tiles
    st.write("")
    stat_col1, stat_col2, stat_col3 = st.columns(3, gap="medium")
    with stat_col1:
        statTile("📍", f"{len(filtered_stations)}", f"Stations · {selected_fuel}")
    with stat_col2:
        statTile("📡", f"{len(data)}", "Records received")
    with stat_col3:
        statTile("💲", f"${avg_price:.2f}" if avg_price is not None else "—", f"Average {selected_fuel} price")

    st.write("")

    # Centering and zooming the map around the currently filtered stations
    if filtered_stations:
        lats = [s["latitude"] for s in filtered_stations.values() if s["latitude"] is not None]
        lons = [s["longitude"] for s in filtered_stations.values() if s["longitude"] is not None]
        if lats and lons:
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)
            lat_spread = max(lats) - min(lats) if len(lats) > 1 else 0
            zoom = 11 if lat_spread > 1 else 12
        else:
            center_lat, center_lon, zoom = -33.8688, 151.2093, 11
    else:
        center_lat, center_lon, zoom = -33.8688, 151.2093, 11

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True, tiles="CartoDB dark_matter")

    # Marker for each station, colored by price tier
    for sid, info in filtered_stations.items():
        lat = info["latitude"]
        lon = info["longitude"]
        if lat is None or lon is None:
            continue
        price = info["fuels"].get(selected_fuel)
        accent, marker_color = priceTier(price, low_cut, high_cut)
        price_display = f"${price:.2f}" if price is not None else "N/A"
        popup_html = f"""
        <div style="font-family: 'Inter', sans-serif; min-width: 220px;">
            <div style="font-weight:700; font-size:1.02em; margin-bottom:2px;">{info['stationname']}</div>
            <div style="display:inline-block; background:#f0f0f0; color:#333; border-radius:999px;
                        padding:1px 9px; font-size:0.75em; font-weight:600; margin-bottom:6px;">{info['brand']}</div>
            <div style="color:#666; font-size:0.85em; margin-bottom:8px;">{info['address']}</div>
            <div style="font-size:1.3em; font-weight:800; color:{accent};">{selected_fuel} {price_display}</div>
            <div style="color:#888; font-size:0.78em; margin-top:4px;">Updated {info['lastupdated']}</div>
        </div>
        """
        folium.Marker(
            [lat, lon],
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=f"{info['stationname']} · {price_display}",
            icon=folium.Icon(color=marker_color, icon="gas-pump", prefix='fa')
        ).add_to(m)

    # Displaying map inside a styled wrapper
    st.markdown("<div class='section-label'>Station Map</div>", unsafe_allow_html=True)
    st.markdown("<div class='map-frame'>", unsafe_allow_html=True)
    st_folium(m, height=560, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="legend-row">
            <span><span class="legend-dot" style="background:{COLOR_GOOD};"></span>Cheapest third</span>
            <span><span class="legend-dot" style="background:{COLOR_WARN};"></span>Mid-range</span>
            <span><span class="legend-dot" style="background:{COLOR_BAD};"></span>Priciest third</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Price prediction section
    st.write("")
    st.markdown("## 📈 Price Prediction")
    if not filtered_stations:
        st.markdown(
            "<div class='empty-state'><span class='emoji'>🤷</span>No stations match the current filters.</div>",
            unsafe_allow_html=True,
        )
    else:
        station_labels = {
            sid: f"{info['stationname']} ({info['brand']})" for sid, info in filtered_stations.items()
        }
        selected_station_id = st.selectbox(
            "Select a station to view its price trend and prediction",
            options=list(station_labels.keys()),
            format_func=lambda sid: station_labels[sid],
            key="prediction_station",
        )

        result = predictPrice(selected_station_id, selected_fuel)
        if result is None:
            st.markdown(
                f"""
                <div class="empty-state">
                    <span class="emoji">📉</span>
                    Not enough historical data yet for this station's <b>{selected_fuel}</b> price.<br>
                    Predictions need at least two logged snapshots — keep the publisher running to build up history.
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            history = result["history"]
            slope = result["slope_per_hour"]
            predicted_price = result["predicted_price"]
            last_price = history["price"].iloc[-1]

            pred_col1, pred_col2 = st.columns([2, 1], gap="medium")
            with pred_col1:
                line = alt.Chart(history).mark_line(color=COLOR_ACCENT, strokeWidth=3, point=alt.OverlayMarkDef(color=COLOR_ACCENT, size=40)).encode(
                    x=alt.X("fetched_at:T", title=None, axis=alt.Axis(labelColor=COLOR_MUTED, gridColor=COLOR_BORDER)),
                    y=alt.Y("price:Q", title="Price ($)", scale=alt.Scale(zero=False), axis=alt.Axis(labelColor=COLOR_MUTED, gridColor=COLOR_BORDER, titleColor=COLOR_MUTED)),
                    tooltip=[alt.Tooltip("fetched_at:T", title="Time"), alt.Tooltip("price:Q", title="Price", format="$.2f")],
                )
                area = alt.Chart(history).mark_area(opacity=0.18, color=COLOR_ACCENT).encode(
                    x="fetched_at:T",
                    y=alt.Y("price:Q", scale=alt.Scale(zero=False)),
                )
                chart = (area + line).properties(height=280, background="transparent").configure_view(strokeWidth=0)
                st.altair_chart(chart, use_container_width=True)
            with pred_col2:
                st.metric(
                    f"Predicted price in {prediction_horizon_hours}h",
                    f"${predicted_price:.2f}",
                    delta=f"{predicted_price - last_price:+.2f}",
                    delta_color="inverse",
                )
                if slope > 0.001:
                    trend, tcolor, ticon = "Rising", COLOR_BAD, "↑"
                elif slope < -0.001:
                    trend, tcolor, ticon = "Falling", COLOR_GOOD, "↓"
                else:
                    trend, tcolor, ticon = "Stable", COLOR_MUTED, "→"
                st.markdown(
                    f"""
                    <div class="trend-badge" style="background:{tcolor}22; color:{tcolor};">
                        {ticon} {trend} · ~${abs(slope):.4f}/hour
                    </div>
                    <div style="color:{COLOR_MUTED}; font-size:0.82rem; margin-top:0.6em;">
                        Based on {len(history)} logged snapshots
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

# Entry point
if __name__ == "__main__":
    main()
