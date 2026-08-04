import requests
from datetime import datetime, timezone, timedelta
import pandas as pd
import json
import re
import time
import os
import paho.mqtt.client as mqtt
from threading import Thread
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
import folium
from dotenv import load_dotenv

load_dotenv()

# API credentials, read from environment (see .env.example)
API_Key = os.environ.get("FUELCHECK_API_KEY", "")
API_secret = os.environ.get("FUELCHECK_API_SECRET", "")
Auth_Header = os.environ.get("FUELCHECK_AUTH_HEADER", "")
# URL for NSW API
Base_URL = "https://api.onegov.nsw.gov.au/FuelPriceCheck/v1"

# Public MQTT broker so the producer and the deployed dashboard can reach each
# other from anywhere, not just the university network. Must match dashboard.py.
MQTT_broker  = os.environ.get("MQTT_BROKER", "broker.hivemq.com")
MQTT_port = int(os.environ.get("MQTT_PORT", 1883))
MQTT_topic  = os.environ.get("MQTT_TOPIC", "kmathew994/fuelcheck/nsw/prices")

# Append-only log of every cleaned snapshot, used later for price prediction
HISTORY_FILE = "fuelPrice_history.csv"

# Public snapshot the browser extension reads directly from GitHub (via
# raw.githubusercontent.com) - no server, no exposed API key.
SNAPSHOT_FILE = os.path.join("data", "latest_prices.json")
POSTCODE_PATTERN = re.compile(r"NSW\s+(\d{4})\s*$")

# Optional cap on records published per cycle. Unset by default (full ~10k
# NSW catalog) for continuous local runs; the scheduled GitHub Actions job
# sets this so a run finishes well under its interval, since publishing is
# deliberately throttled to 0.1s/record. This sample is random each cycle -
# it's just what the live map/MQTT feed shows, not what history logs.
MAX_PUBLISH_RECORDS = os.environ.get("MAX_PUBLISH_RECORDS")

# Optional cap on how many stations get logged to history per cycle. Unlike
# MAX_PUBLISH_RECORDS, this selects the SAME stations every cycle (sorted by
# stationid) so each one actually accumulates a real time series instead of
# random, disconnected data points.
HISTORY_MAX_STATIONS = os.environ.get("HISTORY_MAX_STATIONS")

# How long to keep rows in the history file before pruning them, so the
# git-committed CSV doesn't grow unbounded run over run.
HISTORY_RETENTION_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS", "45"))
# Placeholder for access token once obtained 
ACCESS_TOKEN = ""

CNT = 0

# This function returns token from NSW API using client credentials
def SecurityToken():

    auth_header = Auth_Header

    url = "https://api.onegov.nsw.gov.au/oauth/client_credential/accesstoken?grant_type=client_credentials"

    headers = {
        "accept": "application/json",
        "Authorization": auth_header
    }

    response = requests.get(url, headers=headers)
# If successful, return the token 
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")
        return access_token
# If not, print error information  
    else:
        print("Error encountered while retrieving the token:")  
        print(response.status_code, response.text)
        return ""


# Fetches latest fuel station and pricing data from the NSW API
def retrieve_data():

    global CNT
    global ACCESS_TOKEN

    prices_url = Base_URL + "/fuel/prices"
    # Generates currrent timestamps
    timestamp = datetime.now().strftime("%d/%m/%Y %I:%M:%S %p")
    CNT+=1 #increment request counter 

    headers = {"accept": "application/json", "Authorization": "Bearer "+ ACCESS_TOKEN, "Content-Type": "application/json; charset=utf-8",
        "apikey": API_Key, "transactionid": str(CNT), "requesttimestamp": timestamp}

    PriceResponse = requests.get(prices_url, headers=headers)
# HTTP status code for debugging
    print("Status:", PriceResponse.status_code)

    if PriceResponse.status_code == 200:
        prices_json = PriceResponse.json()
        print("Prices response keys:", prices_json.keys())
        # Returns the data about stations and prices from the response generated above 
        return prices_json.get("stations", []), prices_json.get("prices", [])
    else:
        print("API Error")
        print("Prices Response:", PriceResponse.text)
        return [],[]




# Combines station metadata with fuel price data and stores the result in a DataFrame, and saves it into a CSV file. 
def transform_save (stations, prices):

    records = []
    # lookup dictionary from station code to station information 
    station_lookup = {st.get("code"):
                          {'brandid': st.get("brandid"), 'stationid': st.get("stationid"), 'brand': st.get("brand"),
                           'code': st.get("code"), 'name': st.get("name"), 'address': st.get("address"), 'latitude': st.get("location", {}).get("latitude"),
                           'longitude': st.get("location", {}).get("longitude"),'isAdBlueAvailable': st.get("isAdBlueAvailable")
                          }
                      for st in stations} # loop through each station in the list 

    for p in prices:

        station_id = p.get("stationcode")
        station_info = station_lookup.get(station_id)

        if station_info: # combine station info with price data into one record 
            record = {
                **station_info,
                "fueltype": p.get("fueltype"),
                "price": p.get("price"),
                "lastupdated": p.get("lastupdated")
            }
            records.append(record)

    df = pd.DataFrame(records)
    df.to_csv("fuelPrice_data.csv", index=False)
    return df


# def basic_eda(df, n=5):
#     print("DataFrame shape:", df.shape)
#     print("\nColumn names:", df.columns.tolist())
#     print("\nFirst few rows:")
#     print(df.head(n))
#     print("\nLast few rows:")
#     print(df.tail(n))
#     print(f"\nRandom {n} samples:")
#     print(df.sample(n))
#     print("\nInfo:")
#     print(df.info())
#     print("\nDescription (numeric columns):")
#     print(df.describe())
#     print("\nMissing values per column:")
#     print(df.isnull().sum())
#     print("\nUnique values per column:")
#     print(df.nunique())

# def eda_graphs(df):
#     sns.set_theme(style="whitegrid")

#     plt.figure(figsize=(10, 6))
#     sns.histplot(df['price'], bins=30, kde=True)
#     plt.title("Fuel Price Distribution (Raw Data)")
#     plt.xlabel("Price")
#     plt.ylabel("Frequency")
#     plt.show()


def clean_dataset(df):
    # fields that must not contain missing values 
    fields = ["stationid", "address", "latitude", "longitude"]
    df_clean = df.dropna(subset=fields) # dropping rows with missing values 
    #convert lat and long to numeric value 
    df_clean["latitude"] = pd.to_numeric(df_clean["latitude"], errors="coerce")
    df_clean["longitude"] = pd.to_numeric(df_clean["longitude"], errors="coerce")

    # convert the 'isAdBlueAvailable' column to boolean type
    df_clean["isAdBlueAvailable"] = df_clean["isAdBlueAvailable"].astype(bool)

    df_clean = df_clean.dropna(subset=["latitude", "longitude"])

    # NSW approx bounding box: lat -38 to -28, lon 140 to 154
    df_clean = df_clean[
        (df_clean["latitude"].between(-38, -28)) &
        (df_clean["longitude"].between(140, 154))
    ]
    # Clean text fields
    df_clean["brand"] = df_clean["brand"].str.strip().str.title() # Capitalize each word
    df_clean["name"] = df_clean["name"].str.strip()

    return df_clean # returns cleaned dataframe 




# Appends the cleaned snapshot to a running history log, tagged with fetch time.
# This is the data source the dashboard's price prediction feature reads from.
# Committed back to the repo by the GitHub Actions workflow so it actually
# accumulates across scheduled runs instead of resetting every cycle.
def append_history(df):
    snapshot = df.copy()
    snapshot["fetched_at"] = datetime.now(timezone.utc).isoformat()
    write_header = not os.path.exists(HISTORY_FILE)
    snapshot.to_csv(HISTORY_FILE, mode="a", header=write_header, index=False)


# Drops history rows older than HISTORY_RETENTION_DAYS so the git-committed
# CSV stays bounded instead of growing forever.
def prune_history():
    if not os.path.exists(HISTORY_FILE):
        return
    history_df = pd.read_csv(HISTORY_FILE)
    if "fetched_at" not in history_df.columns or history_df.empty:
        return

    fetched_at = pd.to_datetime(history_df["fetched_at"], errors="coerce", utc=True, format="mixed")
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
    kept = history_df[fetched_at >= cutoff]

    if len(kept) < len(history_df):
        kept.to_csv(HISTORY_FILE, index=False)
        print(f"Pruned history: {len(history_df) - len(kept)} rows older than {HISTORY_RETENTION_DAYS} days removed")


# Writes the full cleaned snapshot as a public JSON file the browser extension
# fetches directly from GitHub. Runs on the full dataset (not the MQTT-sampled
# subset) since this is a plain file write, not throttled like MQTT publish.
def save_latest_snapshot(df):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)

    records = []
    for _, row in df.iterrows():
        address = row.get("address", "") or ""
        match = POSTCODE_PATTERN.search(address)
        records.append({
            "stationid": row.get("stationid"),
            "name": row.get("name"),
            "brand": row.get("brand"),
            "address": address,
            "postcode": match.group(1) if match else None,
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "fueltype": row.get("fueltype"),
            "price": row.get("price"),
            "lastupdated": row.get("lastupdated"),
        })

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f)
    print(f"Wrote {len(records)} records to {SNAPSHOT_FILE}")


# Publishes each row of the cleaned DataFrame to MQTT topic
def publish_to_mqtt(df):
    
    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.connect(MQTT_broker , MQTT_port, 60)  # connecting to MQTT broker 
    
    # publishes each row of the DataFrame as JSON message to the MQTT topic with a slight delay 
    for idx, row in df.iterrows():
        message = row.to_json()
        client.publish(MQTT_topic , message)
        print(f"Published record {idx+1}/{len(df)}")
        time.sleep(0.1)
        
    client.disconnect()
    print("All records published to MQTT")


# Runs a single fetch -> clean -> log -> publish cycle. Used both by the
# continuous local loop and by the scheduled GitHub Actions run (which needs
# a job that actually terminates rather than looping forever).
def run_cycle():
    global ACCESS_TOKEN

    ACCESS_TOKEN = SecurityToken()
    if not ACCESS_TOKEN:
        print("Could not obtain an access token.")
        return False

    print(f"[{datetime.now()}] Fetching prices...")

    stations, prices = retrieve_data()
    print("Retrieved " + str(len(stations)) + " station records.")
    print("Retrieved " + str(len(prices)) + " price records.")

    # Merge station and price data, save to CSV
    df = transform_save (stations, prices)
    print("Retrieved data with " + str(df.shape) + " rows and columns.")

    df = clean_dataset(df)

    # Snapshot the full cleaned dataset for the browser extension before any sampling
    save_latest_snapshot(df)

    # Log a deterministic subset (same stations every cycle) so each one
    # builds a real time series, then prune anything past the retention window
    history_subset = df
    if HISTORY_MAX_STATIONS:
        limit = int(HISTORY_MAX_STATIONS)
        if len(history_subset) > limit:
            history_subset = history_subset.sort_values("stationid").iloc[:limit]
    append_history(history_subset)
    prune_history()

    # The live map/MQTT feed can show a different random sample each cycle -
    # that's just what's currently visible, unrelated to the history log above
    mqtt_df = df
    if MAX_PUBLISH_RECORDS:
        limit = int(MAX_PUBLISH_RECORDS)
        if len(mqtt_df) > limit:
            mqtt_df = mqtt_df.sample(n=limit)
    publish_to_mqtt(mqtt_df)
    return True


def run_service():
    while True:
        try:
            ok = run_cycle()
            if not ok:
                time.sleep(60)
                continue
        except Exception as e:
            print("Error during this cycle, will retry next cycle:", e)

        time.sleep(60) # delays for 60 seconds

if __name__ == "__main__":
    if not API_Key or not Auth_Header:
        raise RuntimeError(
            "Missing NSW FuelCheck credentials. Copy .env.example to .env and fill in "
            "FUELCHECK_API_KEY / FUELCHECK_API_SECRET / FUELCHECK_AUTH_HEADER."
        )

    # RUN_ONCE=true is used by the scheduled GitHub Actions workflow, which needs
    # a single cycle per job run rather than an infinite loop.
    if os.environ.get("RUN_ONCE", "").lower() == "true":
        run_cycle()
    else:
        run_service()