const SNAPSHOT_URL =
  "https://raw.githubusercontent.com/kmathew994/Fuel-Prices-prediction/main/data/latest_prices.json";
const CACHE_KEY = "fuelSnapshotV1";
const CACHE_TTL_MS = 15 * 60 * 1000; // matches the publish schedule
const SEARCH_RADIUS_KM = 5;

const postcodeInput = document.getElementById("postcodeInput");
const searchForm = document.getElementById("searchForm");
const filterRow = document.getElementById("filterRow");
const brandSelect = document.getElementById("brandSelect");
const fuelSelect = document.getElementById("fuelSelect");
const resultsEl = document.getElementById("results");
const resultsCountEl = document.getElementById("resultsCount");
const legendRow = document.getElementById("legendRow");
const dataFreshnessEl = document.getElementById("dataFreshness");

let snapshot = null; // { generated_at, count, records }
let postcodeRecords = []; // records within SEARCH_RADIUS_KM of the searched postcode's centroid
let searchedPostcode = "";

legendRow.style.display = "none";

// Haversine great-circle distance in km between two lat/long points
function distanceKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function hasCoords(r) {
  return typeof r.latitude === "number" && typeof r.longitude === "number";
}

// Approximates a postcode's location as the average position of its known
// stations (we have no separate postcode->coordinates lookup table).
function postcodeCentroid(records) {
  const withCoords = records.filter(hasCoords);
  if (!withCoords.length) return null;
  const lat = withCoords.reduce((sum, r) => sum + r.latitude, 0) / withCoords.length;
  const lon = withCoords.reduce((sum, r) => sum + r.longitude, 0) / withCoords.length;
  return { lat, lon };
}

function timeAgo(isoString) {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return `${hours} hr ago`;
}

async function loadSnapshot() {
  const cached = await chrome.storage.local.get(CACHE_KEY);
  const entry = cached[CACHE_KEY];
  const now = Date.now();

  if (entry && now - entry.fetchedAt < CACHE_TTL_MS) {
    return entry.data;
  }

  const res = await fetch(SNAPSHOT_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
  const data = await res.json();
  await chrome.storage.local.set({ [CACHE_KEY]: { data, fetchedAt: now } });
  return data;
}

function renderState(container, emoji, message) {
  container.innerHTML = `
    <div class="empty-state">
      <span class="emoji">${emoji}</span>${message}
    </div>
  `;
}

function populateFilters(records) {
  const brands = [...new Set(records.map((r) => r.brand).filter(Boolean))].sort();
  const fuels = [...new Set(records.map((r) => r.fueltype).filter(Boolean))].sort();

  brandSelect.innerHTML = '<option value="">All brands</option>';
  brands.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b;
    opt.textContent = b;
    brandSelect.appendChild(opt);
  });

  fuelSelect.innerHTML = "";
  fuels.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    fuelSelect.appendChild(opt);
  });
  if (fuels.includes("U91")) {
    fuelSelect.value = "U91";
  } else if (fuels.length) {
    fuelSelect.value = fuels[0];
  }
}

function priceTier(price, lowCut, highCut) {
  if (price <= lowCut) return "tier-green";
  if (price <= highCut) return "tier-amber";
  return "tier-red";
}

function renderResults() {
  const brand = brandSelect.value;
  const fuel = fuelSelect.value;

  let filtered = postcodeRecords.filter((r) => r.fueltype === fuel);
  if (brand) filtered = filtered.filter((r) => r.brand === brand);
  filtered = filtered.filter((r) => typeof r.price === "number" && r.price > 0);
  filtered.sort((a, b) => a.price - b.price);

  resultsCountEl.textContent = filtered.length
    ? `${filtered.length} station${filtered.length === 1 ? "" : "s"} · ${fuel} · within ${SEARCH_RADIUS_KM}km of ${searchedPostcode}`
    : "";

  if (!filtered.length) {
    legendRow.style.display = "none";
    renderState(resultsEl, "🤷", `No ${fuel || "matching"} stations found nearby for this brand.`);
    return;
  }

  legendRow.style.display = "flex";

  const prices = filtered.map((r) => r.price);
  const sorted = [...prices].sort((a, b) => a - b);
  const lowCut = sorted[Math.floor(sorted.length * 0.33)];
  const highCut = sorted[Math.floor(sorted.length * 0.66)];

  resultsEl.innerHTML = filtered
    .map((r) => {
      const tier = priceTier(r.price, lowCut, highCut);
      return `
        <div class="station-card ${tier}">
          <div class="station-top">
            <div class="station-name">${r.name || "Unknown station"}</div>
            <div class="station-price">$${r.price.toFixed(2)}</div>
          </div>
          <div class="station-brand">${r.brand || "Unknown"}</div>
          <div class="station-address">${r.address || ""} · ${r.distanceKm.toFixed(1)}km away</div>
          <div class="station-updated">Updated ${r.lastupdated || "—"}</div>
        </div>
      `;
    })
    .join("");
}

function handleSearch(event) {
  event.preventDefault();
  if (!snapshot) return;

  const postcode = postcodeInput.value.trim();
  if (!/^\d{4}$/.test(postcode)) {
    filterRow.classList.add("hidden");
    legendRow.style.display = "none";
    renderState(resultsEl, "📮", "Enter a valid 4-digit NSW postcode.");
    resultsCountEl.textContent = "";
    return;
  }

  const exactMatches = snapshot.records.filter((r) => r.postcode === postcode);
  const centroid = postcodeCentroid(exactMatches);

  if (!centroid) {
    filterRow.classList.add("hidden");
    legendRow.style.display = "none";
    renderState(resultsEl, "🤷", "That postcode isn't in the NSW FuelCheck data.");
    resultsCountEl.textContent = "";
    return;
  }

  searchedPostcode = postcode;
  postcodeRecords = snapshot.records
    .filter(hasCoords)
    .map((r) => ({ ...r, distanceKm: distanceKm(centroid.lat, centroid.lon, r.latitude, r.longitude) }))
    .filter((r) => r.distanceKm <= SEARCH_RADIUS_KM);

  if (!postcodeRecords.length) {
    filterRow.classList.add("hidden");
    legendRow.style.display = "none";
    renderState(resultsEl, "🤷", `No stations found within ${SEARCH_RADIUS_KM}km of ${postcode}.`);
    resultsCountEl.textContent = "";
    return;
  }

  filterRow.classList.remove("hidden");
  populateFilters(postcodeRecords);
  renderResults();
}

async function init() {
  renderState(resultsEl, "⏳", "Enter a postcode above to search.");
  try {
    snapshot = await loadSnapshot();
    dataFreshnessEl.textContent = `Data updated ${timeAgo(snapshot.generated_at)}`;
  } catch (err) {
    dataFreshnessEl.textContent = "Could not load price data";
    renderState(resultsEl, "⚠️", "Could not load fuel price data. Try again shortly.");
  }
}

searchForm.addEventListener("submit", handleSearch);
brandSelect.addEventListener("change", renderResults);
fuelSelect.addEventListener("change", renderResults);

init();
