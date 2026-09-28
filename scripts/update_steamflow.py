import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests


GAGES = [
    {"site": "08313000", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08317400", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08328950", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08329900", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08329918", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08329928", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08330000", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08330775", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08330830", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08330875", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08331160", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08331510", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08332010", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08353000", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08354900", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08355050", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08355490", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08358300", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08358400", "name": "REPLACE WITH GAGE NAME"},
    {"site": "08359500", "name": "REPLACE WITH GAGE NAME"},
]

SITE_IDS = [gage["site"] for gage in GAGES]
NAME_BY_SITE = {gage["site"]: gage["name"] for gage in GAGES}

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
LOOKBACK_HOURS = 2
MIN_PRIOR_MINUTES = 30
MAX_PRIOR_MINUTES = 90


def parse_usgs_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def select_prior_value(values, latest_time):
    candidates = []

    for item in values:
        try:
            observation_time = parse_usgs_time(item["dateTime"])
            discharge = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue

        age_minutes = (
            latest_time - observation_time
        ).total_seconds() / 60

        if MIN_PRIOR_MINUTES <= age_minutes <= MAX_PRIOR_MINUTES:
            candidates.append(
                (
                    abs(age_minutes - 60),
                    discharge,
                    observation_time,
                )
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    _, discharge, observation_time = candidates[0]

    return {
        "discharge_cfs": discharge,
        "time_utc": observation_time.isoformat(),
    }


def retrieve_discharge():
    params = {
        "format": "json",
        "sites": ",".join(SITE_IDS),
        "parameterCd": "00060",
        "siteStatus": "all",
        "period": f"PT{LOOKBACK_HOURS}H",
    }

    api_key = os.environ.get("USGS_API_KEY")

    if api_key:
        params["api_key"] = api_key

    response = requests.get(
        USGS_IV_URL,
        params=params,
        timeout=60,
        headers={"User-Agent": "GitHub streamflow dashboard"},
    )
    response.raise_for_status()

    return response.json()


def extract_rows(payload):
    series = payload.get("value", {}).get("timeSeries", [])
    records_by_site = {}

    for time_series in series:
        source_info = time_series.get("sourceInfo", {})
        site_code_list = source_info.get("siteCode", [])

        if not site_code_list:
            continue

        site_number = site_code_list[0].get("value")

        if site_number not in NAME_BY_SITE:
            continue

        value_sets = time_series.get("values", [])

        if not value_sets:
            continue

        values = value_sets[0].get("value", [])

        parsed_values = []

        for item in values:
            try:
                parsed_values.append(
                    (
                        parse_usgs_time(item["dateTime"]),
                        float(item["value"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        if not parsed_values:
            continue

        parsed_values.sort(key=lambda item: item[0])
        latest_time, latest_discharge = parsed_values[-1]

        prior = select_prior_value(values, latest_time)

        rate = None

        if prior is not None:
            prior_time = parse_usgs_time(prior["time_utc"])
            elapsed_hours = (
                latest_time - prior_time
            ).total_seconds() / 3600

            if elapsed_hours > 0:
                rate = (
                    latest_discharge - prior["discharge_cfs"]
                ) / elapsed_hours

        records_by_site[site_number] = {
            "site_number": site_number,
            "gage_name": NAME_BY_SITE[site_number],
            "discharge_cfs": latest_discharge,
            "measurement_time_utc": latest_time.isoformat(),
            "change_cfs_per_hour": rate,
        }

    rows = []

    for gage in GAGES:
        site = gage["site"]

        rows.append(
            records_by_site.get(
                site,
                {
                    "site_number": site,
                    "gage_name": gage["name"],
                    "discharge_cfs": None,
                    "measurement_time_utc": None,
                    "change_cfs_per_hour": None,
                },
            )
        )

    return rows


def main():
    payload = retrieve_discharge()
    rows = extract_rows(payload)

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "USGS NWIS instantaneous values",
        "parameter_code": "00060",
        "units": {
            "discharge": "ft3/s",
            "change_rate": "ft3/s/hour",
        },
        "gages_with_current_discharge": sum(
            row["discharge_cfs"] is not None for row in rows
        ),
        "gages": rows,
    }

    output_path = Path("data/streamflow.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
