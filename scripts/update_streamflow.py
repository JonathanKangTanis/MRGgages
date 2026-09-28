import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests


# Change only each display-name string. Site numbers stay internal.
GAGES = [
    {"site": "08313000", "name": "Rio Grande at Otowi Bridge"},
    {"site": "08317400", "name": "Rio Grande below Cochiti Dam"},
    {"site": "08328950", "name": "Jemez River below Jemez Canyon Dam"},
    {"site": "08329900", "name": "North Diversion Channel"},
    {"site": "08329918", "name": "Rio Grande at Alameda Bridge"},
    {"site": "08329928", "name": "Rio Grande at Paseo del Norte"},
    {"site": "08330000", "name": "Rio Grande at Central Bridge"},
    {"site": "08330775", "name": "South Diversion Channel"},
    {"site": "08330830", "name": "Rio Grande at Valle del Oro"},
    {"site": "08330875", "name": "Rio Grande at Isleta Lakes"},
    {"site": "08331160", "name": "Rio Grande near Bosque Farms"},
    {"site": "08331510", "name": "Rio Grande at Hwy 346"},
    {"site": "08332010", "name": "Rio Grande near Bernardo"},
    {"site": "08353000", "name": "Rio Puerco"},
    {"site": "08354900", "name": "Rio Grande at San Acacia"},
    {"site": "08355050", "name": "Rio Grande at Escondida"},
    {"site": "08355490", "name": "Rio Grande above Hwy 380"},
    {"site": "08358300", "name": "Conveyance Channel at San Marcial"},
    {"site": "08358400", "name": "Rio Grande at San Marcial"},
    {"site": "08359500", "name": "Elephant Butte Narrows"},
]

API_ROOT = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
DISCHARGE_PARAMETER = "00060"

LOOKBACK_HOURS = 2
MIN_PRIOR_MINUTES = 30
MAX_PRIOR_MINUTES = 90

OUTPUT_PATH = Path("data/streamflow.json")


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def api_url(collection, params):
    return f"{API_ROOT}/{collection}/items?{urlencode(params)}"


def get_json(session, collection, params):
    response = session.get(
        api_url(collection, params),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def discharge_observations(payload):
    return [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("parameter_code")
        == DISCHARGE_PARAMETER
    ]


def get_latest_discharge(session, site_number):
    payload = get_json(
        session,
        "latest-continuous",
        {
            "f": "json",
            "monitoring_location_id": f"USGS-{site_number}",
            "parameter_code": DISCHARGE_PARAMETER,
            "limit": 20,
        },
    )

    observations = discharge_observations(payload)

    if not observations:
        return None

    observations.sort(
        key=lambda observation: observation.get("time", ""),
        reverse=True,
    )
    return observations[0]


def get_prior_discharge(session, site_number, latest_time):
    latest_dt = parse_time(latest_time)

    if latest_dt is None:
        return None

    start_dt = latest_dt - timedelta(hours=LOOKBACK_HOURS)

    payload = get_json(
        session,
        "continuous",
        {
            "f": "json",
            "monitoring_location_id": f"USGS-{site_number}",
            "parameter_code": DISCHARGE_PARAMETER,
            "datetime": (
                f"{start_dt.isoformat()}/{latest_dt.isoformat()}"
            ),
            "limit": 500,
        },
    )

    candidates = []

    for observation in discharge_observations(payload):
        observation_time = parse_time(observation.get("time"))

        if observation_time is None:
            continue

        age_minutes = (
            latest_dt - observation_time
        ).total_seconds() / 60

        if MIN_PRIOR_MINUTES <= age_minutes <= MAX_PRIOR_MINUTES:
            candidates.append(
                (abs(age_minutes - 60), observation)
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def calculate_rate(latest, prior):
    if latest is None or prior is None:
        return None

    try:
        latest_flow = float(latest["value"])
        prior_flow = float(prior["value"])
    except (KeyError, TypeError, ValueError):
        return None

    latest_time = parse_time(latest.get("time"))
    prior_time = parse_time(prior.get("time"))

    if latest_time is None or prior_time is None:
        return None

    elapsed_hours = (
        latest_time - prior_time
    ).total_seconds() / 3600

    if elapsed_hours <= 0:
        return None

    return (latest_flow - prior_flow) / elapsed_hours


def make_session():
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/geo+json, application/json",
            "User-Agent": "NM-streamflow-dashboard/1.0",
        }
    )

    api_key = os.getenv("USGS_API_KEY")

    if api_key:
        session.headers["X-Api-Key"] = api_key

    return session


def main():
    rows = []

    with make_session() as session:
        for gage in GAGES:
            latest = get_latest_discharge(session, gage["site"])

            prior = (
                get_prior_discharge(
                    session,
                    gage["site"],
                    latest["time"],
                )
                if latest is not None
                else None
            )

            rows.append(
                {
                    "site_number": gage["site"],
                    "gage_name": gage["name"],
                    "discharge_cfs": (
                        float(latest["value"])
                        if latest is not None
                        else None
                    ),
                    "measurement_time_utc": (
                        latest.get("time")
                        if latest is not None
                        else None
                    ),
                    "change_cfs_per_hour": calculate_rate(
                        latest,
                        prior,
                    ),
                }
            )

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "USGS Water Data OGC API",
        "parameter_code": DISCHARGE_PARAMETER,
        "units": {
            "discharge": "ft3/s",
            "change_rate": "ft3/s/hour",
        },
        "gages_with_current_discharge": sum(
            row["discharge_cfs"] is not None
            for row in rows
        ),
        "gages": rows,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
