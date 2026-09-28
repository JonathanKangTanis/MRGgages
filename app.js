const DATA_URL = "./data/streamflow.json";

const refreshButton = document.getElementById("refresh-button");
const statusElement = document.getElementById("status");
const tableBody = document.getElementById("gage-results");

function setStatus(text, cssClass = "") {
  statusElement.textContent = text;
  statusElement.className = cssClass;
}

function formatNumber(value) {
  if (value === null || value === undefined) {
    return "—";
  }

  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

function formatLocalTime(utcText) {
  if (!utcText) {
    return "No current discharge record";
  }

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
    hour12: false,
  }).format(new Date(utcText));
}

function appendCell(row, text, cssClass = "") {
  const cell = document.createElement("td");
  cell.textContent = text;

  if (cssClass) {
    cell.className = cssClass;
  }

  row.appendChild(cell);
}

function renderTable(gages) {
  tableBody.innerHTML = "";

  for (const gage of gages) {
    const row = document.createElement("tr");

    appendCell(row, gage.gage_name);

    if (gage.discharge_cfs === null) {
      appendCell(row, "—", "numeric no-data");
      appendCell(row, "No current discharge record", "no-data");
      appendCell(row, "—", "numeric no-data");
      tableBody.appendChild(row);
      continue;
    }

    appendCell(row, formatNumber(gage.discharge_cfs), "numeric");
    appendCell(
      row,
      formatLocalTime(gage.measurement_time_utc)
    );

    if (gage.change_cfs_per_hour === null) {
      appendCell(row, "—", "numeric no-data");
    } else {
      const rate = Number(gage.change_cfs_per_hour);

      const className =
        rate > 0 ? "numeric positive" :
        rate < 0 ? "numeric negative" :
        "numeric";

      appendCell(
        row,
        `${rate >= 0 ? "+" : ""}${formatNumber(rate)}`,
        className
      );
    }

    tableBody.appendChild(row);
  }
}

async function loadData() {
  refreshButton.disabled = true;

  try {
    setStatus("Loading latest scheduled streamflow update…");

    const response = await fetch(
      `${DATA_URL}?v=${Date.now()}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(`Could not load streamflow.json: HTTP ${response.status}`);
    }

    const data = await response.json();

    renderTable(data.gages);

    const generated = new Date(data.generated_at_utc);

    setStatus(
      `Data generated ${new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(generated)}. ` +
      `Current discharge returned for ${data.gages_with_current_discharge} ` +
      `of ${data.gages.length} gages.`
    );
  } catch (error) {
    setStatus(`Unable to load streamflow data: ${error.message}`, "error");
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadData);
loadData();
