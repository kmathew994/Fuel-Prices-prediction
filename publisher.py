import requests
from datetime import datetime
import pandas as pd
import json
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

# Optional cap on records published/logged per cycle. Unset by default (full
# ~10k-record NSW catalog) for continuous local runs; the scheduled GitHub
# Actions job sets this so a run finishes in well under its interval, since
# publishing is deliberately throttled to 0.1s/record.
MAX_PUBLISH_RECORDS = os.environ.get("MAX_PUBLISH_RECORDS")
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
def append_history(df):
    snapshot = df.copy()
    snapshot["fetched_at"] = datetime.now().isoformat()
    write_header = not os.path.exists(HISTORY_FILE)
    snapshot.to_csv(HISTORY_FILE, mode="a", header=write_header, index=False)


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

    if MAX_PUBLISH_RECORDS:
        limit = int(MAX_PUBLISH_RECORDS)
        if len(df) > limit:
            df = df.sample(n=limit)

    # Log this snapshot for the price prediction feature, then publish to MQTT broker
    append_history(df)
    publish_to_mqtt(df)
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