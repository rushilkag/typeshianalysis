const state = {
  summary: null,
  filter: "",
};

const fmt = new Intl.NumberFormat();
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function $(id) {
  return document.getElementById(id);
}

function parseDate(value) {
  return new Date(value);
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function updateMetrics(summary) {
  setText("totalMessages", fmt.format(summary.totalMessages));
  setText("participantCount", fmt.format(summary.participantCount));
  setText("averagePerDay", fmt.format(summary.averagePerDay));
  setText("topSender", summary.senders[0]?.label || "None");

  const start = shortDate.format(parseDate(summary.windowStart));
  const end = shortDate.format(parseDate(summary.windowEnd));
  setText("windowLabel", `${start} - ${end}`);
}

function renderSenders() {
  const root = $("senderBars");
  const summary = state.summary;
  if (!root || !summary) return;

  const query = state.filter.trim().toLowerCase();
  const filtered = summary.senders.filter((sender) => {
    const haystack = `${sender.label} ${sender.detail}`.toLowerCase();
    return haystack.includes(query);
  });

  if (!filtered.length) {
    root.innerHTML = `<div class="empty">No sender matches this filter.</div>`;
    return;
  }

  const max = Math.max(...summary.senders.map((sender) => sender.count), 1);
  root.innerHTML = filtered
    .map((sender) => {
      const width = Math.max((sender.count / max) * 100, 2).toFixed(2);
      const label = escapeHtml(sender.label);
      const detail = escapeHtml(sender.detail);
      return `
        <div class="bar-row">
          <div class="rank">${sender.rank}</div>
          <div class="person">
            <strong title="${label}">${label}</strong>
            <span>${detail} / ${sender.share}% share</span>
          </div>
          <div class="meter" aria-label="${label}: ${sender.count} messages">
            <div class="meter-fill" style="--w: ${width}%"></div>
          </div>
          <div class="count">
            <strong>${fmt.format(sender.count)}</strong>
            <span>texts</span>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderDaily(summary) {
  const root = $("dailyPulse");
  if (!root) return;

  const max = Math.max(...summary.daily.map((day) => day.count), 1);
  root.innerHTML = summary.daily
    .map((day) => {
      const heat = Math.round((day.count / max) * 78);
      return `
        <div class="day-cell" style="--heat: ${heat}%">
          <span>${shortDate.format(parseDate(`${day.date}T12:00:00`))}</span>
          <strong>${fmt.format(day.count)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderHourly(summary) {
  const root = $("hourlyChart");
  if (!root) return;

  const max = Math.max(...summary.hourly.map((hour) => hour.count), 1);
  root.innerHTML = summary.hourly
    .map((hour) => {
      const height = Math.max((hour.count / max) * 128, 8).toFixed(1);
      const label = hour.hour % 6 === 0 ? String(hour.hour).padStart(2, "0") : "";
      return `<div class="hour-bar" style="--h: ${height}px" data-hour="${label}" title="${hour.hour}:00 - ${hour.count} messages"></div>`;
    })
    .join("");
}

async function loadSummary() {
  const response = await fetch("./data/summary.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not load summary.json: ${response.status}`);
  }
  return response.json();
}

async function init() {
  try {
    const summary = await loadSummary();
    state.summary = summary;
    updateMetrics(summary);
    renderSenders();
    renderDaily(summary);
    renderHourly(summary);
  } catch (error) {
    const root = $("senderBars");
    if (root) {
      root.innerHTML = `<div class="empty">Run <code>python3 scripts/generate_data.py</code> to create dashboard data.</div>`;
    }
    console.error(error);
  }

  $("senderFilter")?.addEventListener("input", (event) => {
    state.filter = event.target.value;
    renderSenders();
  });
}

init();
