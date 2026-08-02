# Privacy Policy — NSW Fuel Prices Extension

**Last updated: 2026-08-02**

## What this extension does
NSW Fuel Prices lets you search NSW petrol station prices by postcode, brand, and fuel type.

## Data collection
This extension does **not** collect, transmit, or sell any personal data. Specifically:

- No account, sign-in, or personal information is required or requested.
- The postcode you type is used only locally, inside your browser, to filter the price data already loaded into the popup. It is never sent anywhere.
- The extension fetches a public, read-only fuel price dataset from `raw.githubusercontent.com` (a static JSON file with no query parameters, cookies, or identifiers attached).
- The extension caches that dataset locally in your browser's extension storage (`chrome.storage.local`) for up to 15 minutes to avoid unnecessary re-downloads. This cache never leaves your device.
- No analytics, tracking, or advertising code is included.

## Data source
Fuel price data originates from the NSW Government's FuelCheck API and is republished as a public file in this project's GitHub repository: https://github.com/kmathew994/Fuel-Prices-prediction

## Contact
Questions about this extension can be raised via GitHub Issues on the repository above.
