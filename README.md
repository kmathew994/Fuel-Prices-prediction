# Fuel Prices Dashboard

[![Live App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://fuel-prices-prediction.streamlit.app)

**🟢 Live now: [fuel-prices-prediction.streamlit.app](https://fuel-prices-prediction.streamlit.app)** — continuously updated every 15 minutes via a scheduled GitHub Action.

Live NSW fuel price monitoring: NSW FuelCheck API → MQTT → Streamlit dashboard, with a simple linear-trend price prediction feature.

## Architecture
1. `publisher.py` fetches live station + price data from the NSW FuelCheck API, cleans and merges it, logs it to a running history CSV, and publishes each record to a public MQTT broker.
2. `dashboard.py` subscribes to that broker, buffers incoming records to a local JSON file, and renders them on an interactive Streamlit + Folium map with fuel type / brand filters and a price-prediction panel.

## Run locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-publisher.txt
cp .env.example .env   # fill in your NSW FuelCheck API credentials
```

`requirements.txt` covers `dashboard.py` (what gets deployed). `requirements-publisher.txt` adds what `publisher.py` needs on top (requests, matplotlib, etc.) — only required when running the producer locally.

In one terminal, run the producer:
```bash
python publisher.py
```

In another terminal, run the dashboard:
```bash
streamlit run dashboard.py
```

## Deployment
`dashboard.py` needs no secrets to run — it only subscribes to the public MQTT broker — so it can be deployed as-is (e.g. on Streamlit Community Cloud). `publisher.py` must be run separately (locally or on any always-on machine) with valid NSW FuelCheck credentials to keep feeding it live data.
