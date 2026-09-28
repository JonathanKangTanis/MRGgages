import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from js import document
from pyodide.http import pyfetch


# Edit only the text after "name" to customize table labels.
# The site number remains internal and is not displayed in the table.
GAGES = [
    {"site": "08313000", "name": "Rio Grande at Otowi Bridge, NM"},
    {"site": "08317400", "name": "Rio Grande below Cochiti Dam, NM"},
    {"site": "08328950", "name": "Rio Grande at Alameda Bridge, NM"},
    {"site": "08329900", "name": "Rio Grande at Albuquerque, NM"},
    {"site": "08329918", "name": "Rio Grande at Central Avenue, Albuquerque, NM"},
    {"site": "08329928", "name": "Rio Grande at Barelas Bridge, Albuquerque, NM"},
    {"site": "08330000", "name": "Rio Grande at Albuquerque, NM"},
    {"site": "08330775", "name": "Rio Grande at Rio Bravo Boulevard, Albuquerque, NM"},
    {"site": "08330830", "name": "Rio Grande at I-25, Albuquerque, NM"},
    {"site": "08330875", "name": "Rio Grande at Isleta Lakes, NM"},
    {"site": "08331160", "name": "Rio Grande near Bosque Farms, NM"},
    {"site": "08331510", "name": "Rio Grande near Belen, NM"},
    {"site": "08332010", "name": "Rio Grande near Bernardo, NM"},
    {"site": "08353000", "name": "Rio Puerco above Arroyo Chico, NM"},
    {"site": "08354900", "name": "Rio Salado near San Acacia, NM"},
    {"site": "08355050", "name": "Rio Grande below Elephant Butte Dam, NM"},
    {"site": "08355490", "name": "Rio Grande below Caballo Dam, NM"},
    {"site": "08358300", "name": "Rio Grande at Leasburg Dam, NM"},
    {"site": "08358400", "name": "Rio Grande below Leasburg Dam, NM"},
    {"site": "08359500", "name": "Rio Grande below Percha Dam, NM"},
]

USGS_API_ROOT = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
DISCHARGE_PARAMETER = "00060"

# Retrieve enough history to accommodate 15-, 30-, or 60-minute reporting.
LOOKBACK_HOURS = 2

# Use a record between 30 and 90 minutes before the current observation.
MIN_PRIOR_MINUTES = 30
MAX_PRIOR_MINUTES = 90


def set_status(text, css_class=""):
    element = document.getElementById("status")
    element.textContent = text
    element.className = css_class


def make_cell(row, value, css_class=""):
    cell = document.createElement("td")
    cell.textContent = str(value)
    if css_class:
        cell.className = css_class
    row.appendChild(cell)


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_local_time(value):
    timestamp = parse_time(value)
    if timestamp is None:
        return "—"
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def format_flow(value):
    if value in (None, ""):
        return "—"

    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return str(value)


def numeric_flow(properties):
    try:
        return float(properties.get("value"))
    except (TypeError, ValueError):
        return None


def site_id(site_number):
    return f"USGS-{site_number}"


def collection_url(collection, params):
    # doseq=True is essential: it turns a Python list into repeated
    # monitoring_location_id=query parameters.
    query_string = urlencode(params, doseq=True)
    return f"{USGS_API_ROOT}/{collection}/items?{query_string}"


async def get_json(url):
    response = await pyfetch(url)

    if not response.ok:
        raise RuntimeError(f"USGS API returned HTTP {response.status}.")

    return await response.json()


def discharge_properties(payload):
    return [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("parameter_code")
        == DISCHARGE_PARAMETER
    ]


async def get_latest_observations():
    params = {
        "f": "json",
        "monitoring_location_id": [site_id(gage["site"]) for gage in GAGES],
        "parameter_code": DISCHARGE_PARAMETER,
        "limit": 1000,
    }

    payload = await get_json(collection_url("latest-continuous", params))
    observations = discharge_properties(payload)

    latest_by_site = {}

    for observation in observations:
        location_id = observation.get("monitoring_location_id")
        observation_time = observation.get("time", "")

        current = latest_by_site.get(location_id)

        if current is None or observation_time > current.get("time", ""):
            latest_by_site[location_id] = observation

    return latest_by_site


async def get_historical_observations(latest_by_site):
    latest_times = [
        parse_time(observation.get("time"))
        for observation in latest_by_site.values()
        if parse_time(observation.get("time")) is not None
    ]

    if not latest_times:
        return []

    # A single shared query window covers all gages. The end buffer handles
    # small timestamp differences among latest station observations.
    query_end = max(latest_times) + timedelta(minutes=1)
    query_start = min(latest_times) - timedelta(hours=LOOKBACK_HOURS)

    params = {
        "f": "json",
        "monitoring_location_id": [site_id(gage["site"]) for gage in GAGES],
        "parameter_code": DISCHARGE_PARAMETER,
        "datetime": f"{query_start.isoformat()}/{query_end.isoformat()}",
        "limit": 10000,
    }

    payload = await get_json(collection_url("continuous", params))
    return discharge_properties(payload)


def choose_prior_observations(latest_by_site, historical_observations):
    history_by_site = {}

    for observation in historical_observations:
        location_id = observation.get("monitoring_location_id")
        history_by_site.setdefault(location_id, []).append(observation)

    prior_by_site = {}

    for location_id, latest in latest_by_site.items():
        latest_time = parse_time(latest.get("time"))

        if latest_time is None:
            continue

        candidates = []

        for observation in history_by_site.get(location_id, []):
            observation_time = parse_time(observation.get("time"))

            if observation_time is None:
                continue

            age_minutes = (latest_time - observation_time).total_seconds() / 60

            if MIN_PRIOR_MINUTES <= age_minutes <= MAX_PRIOR_MINUTES:
                candidates.append((abs(age_minutes - 60), observation))

        if candidates:
            candidates.sort(key=lambda item: item[0])
            prior_by_site[location_id] = candidates[0][1]

    return prior_by_site


def calculate_rate(latest, prior):
    if latest is None or prior is None:
        return None

    latest_value = numeric_flow(latest)
    prior_value = numeric_flow(prior)
    latest_time = parse_time(latest.get("time"))
    prior_time = parse_time(prior.get("time"))

    if (
        latest_value is None
        or prior_value is None
        or latest_time is None
        or prior_time is None
    ):
        return None

    elapsed_hours = (latest_time - prior_time).total_seconds() / 3600

    if elapsed_hours <= 0:
        return None

    return (latest_value - prior_value) / elapsed_hours


def render_table(latest_by_site, prior_by_site):
    tbody = document.getElementById("gage-results")
    tbody.innerHTML = ""

    for gage in GAGES:
        row = document.createElement("tr")

        location_id = site_id(gage["site"])
        latest = latest_by_site.get(location_id)
        prior = prior_by_site.get(location_id)

        make_cell(row, gage["name"])

        if latest is None:
            make_cell(row, "—", "numeric no-data")
            make_cell(row, "No current discharge record", "no-data")
            make_cell(row, "—", "numeric no-data")
            tbody.appendChild(row)
            continue

        make_cell(row, format_flow(latest.get("value")), "numeric")
        make_cell(row, format_local_time(latest.get("time")))

        rate = calculate_rate(latest, prior)

        if rate is None:
            make_cell(row, "—", "numeric no-data")
        else:
            css_class = (
                "positive"
                if rate > 0
                else "negative"
                if rate < 0
                else ""
            )
            make_cell(row, f"{rate:+,.1f}", f"numeric {css_class}")

        tbody.appendChild(row)


async def load_all_gages(event=None):
    button = document.getElementById("refresh-button")
    button.disabled = True

    try:
        set_status(f"Requesting current discharge for {len(GAGES)} gages…")

        latest_by_site = await get_latest_observations()

        set_status("Retrieving the preceding two hours of discharge observations…")

        historical_observations = await get_historical_observations(
            latest_by_site
        )
        prior_by_site = choose_prior_observations(
            latest_by_site,
            historical_observations,
        )

        render_table(latest_by_site, prior_by_site)

        successful = sum(
            1
            for gage in GAGES
            if site_id(gage["site"]) in latest_by_site
        )

        updated_time = datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M %Z"
        )

        set_status(
            f"Updated {updated_time}. Current discharge returned for "
            f"{successful} of {len(GAGES)} gages."
        )

    except Exception as exc:
        set_status(f"Unable to load USGS data: {exc}", "error")

    finally:
        button.disabled = False


document.getElementById("refresh-button").addEventListener(
    "click",
    lambda event: asyncio.ensure_future(load_all_gages(event)),
)

asyncio.ensure_future(load_all_gages())
