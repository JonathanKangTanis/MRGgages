import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from js import document
from pyodide.http import pyfetch


# Fixed gages requested by the user.
SITE_NUMBERS = [
    "08313000",
    "08317400",
    "08328950",
    "08329900",
    "08329918",
    "08329928",
    "08330000",
    "08330775",
    "08330830",
    "08330875",
    "08331160",
    "08331510",
    "08332010",
    "08353000",
    "08354900",
    "08355050",
    "08355490",
    "08358300",
    "08358400",
    "08359500",
]

USGS_API_ROOT = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
DISCHARGE_CODE = "00060"

# Looks back farther than one hour so the code can find a valid historical
# sample if a gage has a 15-, 30-, or 60-minute reporting interval.
LOOKBACK_HOURS = 2

# Require the selected previous record to be 30–90 minutes before latest.
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


def format_local_time(timestamp_text):
    timestamp = parse_time(timestamp_text)
    if timestamp is None:
        return "—"

    local_time = timestamp.astimezone()
    return local_time.strftime("%Y-%m-%d %H:%M %Z")


def format_flow(value):
    if value in (None, ""):
        return "—"

    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return str(value)


def flow_value(properties):
    try:
        return float(properties.get("value"))
    except (TypeError, ValueError):
        return None


def location_name(properties):
    return (
        properties.get("monitoring_location_name")
        or properties.get("site_name")
        or properties.get("monitoring_location_id")
        or "Unnamed USGS gage"
    )


def api_url(collection, params):
    return f"{USGS_API_ROOT}/{collection}/items?{urlencode(params)}"


async def get_json(url):
    response = await pyfetch(url)

    if not response.ok:
        raise RuntimeError(f"USGS API returned HTTP {response.status}.")

    return await response.json()


async def get_latest_discharge(site_number):
    params = {
        "f": "json",
        "monitoring_location_id": f"USGS-{site_number}",
        "parameter_code": DISCHARGE_CODE,
        "limit": 20,
    }

    payload = await get_json(api_url("latest-continuous", params))
    features = payload.get("features", [])

    if not features:
        return None

    candidates = [
        feature.get("properties", {})
        for feature in features
        if feature.get("properties", {}).get("parameter_code") == DISCHARGE_CODE
    ]

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.get("time", ""), reverse=True)
    return candidates[0]


async def get_prior_discharge(site_number, latest_time):
    latest_dt = parse_time(latest_time)

    if latest_dt is None:
        return None

    start_time = latest_dt - timedelta(hours=LOOKBACK_HOURS)

    params = {
        "f": "json",
        "monitoring_location_id": f"USGS-{site_number}",
        "parameter_code": DISCHARGE_CODE,
        "datetime": (
            f"{start_time.isoformat()}/{latest_dt.isoformat()}"
        ),
        "limit": 500,
    }

    payload = await get_json(api_url("continuous", params))

    observations = [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("parameter_code") == DISCHARGE_CODE
    ]

    valid = []

    for observation in observations:
        observation_time = parse_time(observation.get("time"))
        if observation_time is None:
            continue

        age_minutes = (latest_dt - observation_time).total_seconds() / 60

        if MIN_PRIOR_MINUTES <= age_minutes <= MAX_PRIOR_MINUTES:
            valid.append((abs(age_minutes - 60), observation_time, observation))

    if not valid:
        return None

    valid.sort(key=lambda item: item[0])
    return valid[0][2]


def calculate_rate(latest_properties, prior_properties):
    if latest_properties is None or prior_properties is None:
        return None

    latest_q = flow_value(latest_properties)
    prior_q = flow_value(prior_properties)

    latest_time = parse_time(latest_properties.get("time"))
    prior_time = parse_time(prior_properties.get("time"))

    if (
        latest_q is None
        or prior_q is None
        or latest_time is None
        or prior_time is None
    ):
        return None

    elapsed_hours = (latest_time - prior_time).total_seconds() / 3600

    if elapsed_hours <= 0:
        return None

    return (latest_q - prior_q) / elapsed_hours


def render_table(rows):
    tbody = document.getElementById("gage-results")
    tbody.innerHTML = ""

    for row_data in rows:
        row = document.createElement("tr")

        latest = row_data["latest"]

        if latest is None:
            make_cell(row, f"USGS-{row_data['site_number']}", "no-data")
            make_cell(row, "—", "numeric no-data")
            make_cell(row, "No current discharge record", "no-data")
            make_cell(row, "—", "numeric no-data")
            tbody.appendChild(row)
            continue

        make_cell(row, location_name(latest))
        make_cell(row, format_flow(latest.get("value")), "numeric")
        make_cell(row, format_local_time(latest.get("time")))

        rate = row_data["rate"]

        if rate is None:
            make_cell(row, "—", "numeric no-data")
        else:
            rate_class = "positive" if rate > 0 else "negative" if rate < 0 else ""
            make_cell(row, f"{rate:+,.1f}", f"numeric {rate_class}")

        tbody.appendChild(row)


async def load_all_gages(event=None):
    button = document.getElementById("refresh-button")
    button.disabled = True

    try:
        set_status(f"Retrieving latest discharge for {len(SITE_NUMBERS)} gages…")

        latest_records = await asyncio.gather(
            *[get_latest_discharge(site) for site in SITE_NUMBERS],
            return_exceptions=True,
        )

        rows = []

        for site_number, result in zip(SITE_NUMBERS, latest_records):
            latest = result if not isinstance(result, Exception) else None
            rows.append(
                {
                    "site_number": site_number,
                    "latest": latest,
                    "prior": None,
                    "rate": None,
                }
            )

        set_status("Retrieving observations needed for hourly-change calculations…")

        prior_tasks = [
            get_prior_discharge(row["site_number"], row["latest"]["time"])
            if row["latest"] is not None
            else asyncio.sleep(0, result=None)
            for row in rows
        ]

        prior_records = await asyncio.gather(
            *prior_tasks,
            return_exceptions=True,
        )

        for row, prior_result in zip(rows, prior_records):
            prior = prior_result if not isinstance(prior_result, Exception) else None
            row["prior"] = prior
            row["rate"] = calculate_rate(row["latest"], prior)

        render_table(rows)

        successful = sum(row["latest"] is not None for row in rows)
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

        set_status(
            f"Updated {timestamp}. Current discharge returned for "
            f"{successful} of {len(SITE_NUMBERS)} gages."
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
