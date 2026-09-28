import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlencode

from js import document
from pyodide.http import pyfetch


# Fixed USGS streamgages.
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
DISCHARGE_PARAMETER_CODE = "00060"
LOOKBACK_HOURS = 2
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
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M %Z")


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


def api_url(collection, params):
    return f"{USGS_API_ROOT}/{collection}/items?{urlencode(params)}"


async def get_json(url):
    response = await pyfetch(url)
    if not response.ok:
        raise RuntimeError(f"USGS API returned HTTP {response.status}.")
    return await response.json()


async def get_gage_name(site_number):
    params = {
        "f": "json",
        "monitoring_location_id": f"USGS-{site_number}",
        "limit": 1,
    }

    payload = await get_json(api_url("monitoring-locations", params))
    features = payload.get("features", [])

    if not features:
        return "Name unavailable"

    properties = features[0].get("properties", {})
    return properties.get("monitoring_location_name") or "Name unavailable"


async def get_latest_discharge(site_number):
    params = {
        "f": "json",
        "monitoring_location_id": f"USGS-{site_number}",
        "parameter_code": DISCHARGE_PARAMETER_CODE,
        "limit": 20,
    }

    payload = await get_json(api_url("latest-continuous", params))

    candidates = [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("parameter_code")
        == DISCHARGE_PARAMETER_CODE
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
        "parameter_code": DISCHARGE_PARAMETER_CODE,
        "datetime": f"{start_time.isoformat()}/{latest_dt.isoformat()}",
        "limit": 500,
    }

    payload = await get_json(api_url("continuous", params))

    observations = [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("parameter_code")
        == DISCHARGE_PARAMETER_CODE
    ]

    eligible = []

    for observation in observations:
        observation_time = parse_time(observation.get("time"))
        if observation_time is None:
            continue

        age_minutes = (latest_dt - observation_time).total_seconds() / 60

        if MIN_PRIOR_MINUTES <= age_minutes <= MAX_PRIOR_MINUTES:
            eligible.append((abs(age_minutes - 60), observation))

    if not eligible:
        return None

    eligible.sort(key=lambda item: item[0])
    return eligible[0][1]


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
        gage_name = row_data.get("gage_name") or "Name unavailable"
        latest = row_data["latest"]

        make_cell(row, gage_name, "no-data" if latest is None else "")

        if latest is None:
            make_cell(row, "—", "numeric no-data")
            make_cell(row, "No current discharge record", "no-data")
            make_cell(row, "—", "numeric no-data")
            tbody.appendChild(row)
            continue

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
        set_status(f"Retrieving USGS data for {len(SITE_NUMBERS)} gages…")

        latest_records, gage_names = await asyncio.gather(
            asyncio.gather(
                *[get_latest_discharge(site) for site in SITE_NUMBERS],
                return_exceptions=True,
            ),
            asyncio.gather(
                *[get_gage_name(site) for site in SITE_NUMBERS],
                return_exceptions=True,
            ),
        )

        rows = []
        for site_number, latest_result, name_result in zip(
            SITE_NUMBERS,
            latest_records,
            gage_names,
        ):
            latest = (
                latest_result
                if not isinstance(latest_result, Exception)
                else None
            )
            gage_name = (
                name_result
                if not isinstance(name_result, Exception)
                else "Name unavailable"
            )

            rows.append(
                {
                    "site_number": site_number,
                    "gage_name": gage_name,
                    "latest": latest,
                    "prior": None,
                    "rate": None,
                }
            )

        set_status("Calculating discharge change over the preceding hour…")

        prior_records = await asyncio.gather(
            *[
                get_prior_discharge(row["site_number"], row["latest"]["time"])
                if row["latest"] is not None
                else asyncio.sleep(0, result=None)
                for row in rows
            ],
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
