import asyncio
from datetime import datetime, timezone
from js import document
from pyodide.http import pyfetch


API_ROOT = (
    "https://api.waterdata.usgs.gov/ogcapi/v1/collections/"
    "latest-continuous/items"
)

DISCHARGE_PARAMETER = "00060"


def set_status(message, css_class="muted"):
    status = document.getElementById("status")
    status.textContent = message
    status.className = css_class


def clear_table():
    tbody = document.getElementById("results-body")
    tbody.innerHTML = ""
    return tbody


def add_cell(row, value, css_class=""):
    cell = document.createElement("td")
    cell.textContent = "" if value is None else str(value)
    if css_class:
        cell.className = css_class
    row.appendChild(cell)


def normalize_sites(raw_text):
    values = raw_text.replace("\n", ",").split(",")
    sites = [site.strip() for site in values if site.strip()]

    invalid = [site for site in sites if not site.isdigit()]
    if invalid:
        raise ValueError(
            "USGS site numbers should contain digits only. "
            f"Invalid value(s): {', '.join(invalid)}"
        )

    return list(dict.fromkeys(sites))


def format_discharge(value):
    if value in (None, ""):
        return "—"

    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return value


def status_text(properties):
    statuses = (
        properties.get("approval_status")
        or properties.get("approvals_status")
        or []
    )

    if isinstance(statuses, list):
        return ", ".join(statuses) if statuses else "Unknown"

    return str(statuses)


def render_rows(records, requested_sites):
    tbody = clear_table()

    if not records:
        row = document.createElement("tr")
        add_cell(
            row,
            (
                "No current discharge records were returned. The gages may not "
                "publish parameter 00060, may be offline, or the API query may "
                "need adjustment."
            ),
            "muted",
        )
        row.cells[0].colSpan = 6
        tbody.appendChild(row)
        return

    found_sites = set()

    for properties in records:
        monitoring_location_id = properties.get("monitoring_location_id", "")
        site_number = monitoring_location_id.removeprefix("USGS-")
        found_sites.add(site_number)

        row = document.createElement("tr")
        approval = status_text(properties)
        approval_class = (
            "approved"
            if "Approved" in approval
            else "provisional"
            if "Provisional" in approval
            else ""
        )

        add_cell(row, monitoring_location_id or "—")
        add_cell(row, properties.get("time", "—"))
        add_cell(row, format_discharge(properties.get("value")), "numeric")
        add_cell(row, properties.get("unit_of_measure", "ft³/s"))
        add_cell(row, approval, approval_class)

        qualifier = properties.get("qualifier")
        if isinstance(qualifier, list):
            qualifier = ", ".join(qualifier)
        add_cell(row, qualifier or "—")

        tbody.appendChild(row)

    missing = [
        site for site in requested_sites
        if site not in found_sites and f"USGS-{site}" not in found_sites
    ]

    if missing:
        set_status(
            f"Loaded {len(records)} discharge record(s). "
            f"No matching current-discharge record for: {', '.join(missing)}.",
            "muted",
        )
    else:
        set_status(
            f"Loaded {len(records)} current discharge record(s) at "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}.",
            "muted",
        )


async def fetch_current_discharge(site_numbers):
    records = []

    for site in site_numbers:
        params = {
            "f": "json",
            "limit": 20,
            "monitoring_location_id": f"USGS-{site}",
            "parameter_code": DISCHARGE_PARAMETER,
        }

        query_string = "&".join(
            f"{key}={value}" for key, value in params.items()
        )
        url = f"{API_ROOT}?{query_string}"

        response = await pyfetch(url)

        if not response.ok:
            raise RuntimeError(
                f"USGS returned HTTP {response.status} for site {site}."
            )

        payload = await response.json()

        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            if properties.get("parameter_code") == DISCHARGE_PARAMETER:
                records.append(properties)

    records.sort(key=lambda r: r.get("monitoring_location_id", ""))
    return records


async def load_data(event=None):
    button = document.getElementById("load-button")
    raw_sites = document.getElementById("site-input").value

    try:
        site_numbers = normalize_sites(raw_sites)

        if not site_numbers:
            raise ValueError("Enter at least one USGS site number.")

        button.disabled = True
        set_status("Requesting the latest USGS discharge observations…")

        records = await fetch_current_discharge(site_numbers)
        render_rows(records, site_numbers)

    except Exception as exc:
        clear_table()
        tbody = document.getElementById("results-body")
        row = document.createElement("tr")
        add_cell(row, str(exc), "error")
        row.cells[0].colSpan = 6
        tbody.appendChild(row)
        set_status("Could not load USGS data.", "error")

    finally:
        button.disabled = False


document.getElementById("load-button").addEventListener(
    "click",
    lambda event: asyncio.ensure_future(load_data(event)),
)

set_status("Ready. Enter one or more gage IDs and select “Load current discharge.”")
