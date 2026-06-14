const state = {
  summary: null,
  filter: "",
  windowDays: 14,
};

const fmt = new Intl.NumberFormat();
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function $(id) {
  return document.getElementById(id);
}

function parseDate(value) {
  return new Date(value);
}

function dateFromKey(dateKey) {
  return parseDate(`${dateKey}T12:00:00`);
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

function pluralDays(days) {
  return `${fmt.format(days)} day${days === 1 ? "" : "s"}`;
}

function clampWindowDays(value) {
  const max = state.summary?.maxWindowDays || state.summary?.daily?.length || 365;
  return Math.min(Math.max(Number(value) || 1, 1), max);
}

function buildWindowSummary() {
  const summary = state.summary;
  const days = clampWindowDays(state.windowDays);
  const selectedDaily = summary.daily.slice(-days);
  const lastOverallDay = summary.daily[summary.daily.length - 1]?.date;
  const participantById = new Map(summary.senders.map((sender) => [sender.id, sender]));
  const senderCounts = new Map();
  const hourlyCounts = Array.from({ length: 24 }, () => 0);

  let totalMessages = 0;
  let attachmentMessages = 0;
  let textLengthSum = 0;
  let textMessageCount = 0;

  selectedDaily.forEach((day) => {
    totalMessages += day.count || 0;
    attachmentMessages += day.attachmentMessages || 0;
    textLengthSum += day.textLengthSum || 0;
    textMessageCount += day.textMessageCount || 0;

    Object.entries(day.bySender || {}).forEach(([senderId, count]) => {
      senderCounts.set(senderId, (senderCounts.get(senderId) || 0) + count);
    });

    (day.byHour || []).forEach((count, hour) => {
      hourlyCounts[hour] += count || 0;
    });
  });

  const senders = Array.from(senderCounts.entries())
    .map(([senderId, count]) => {
      const base = participantById.get(senderId) || {
        id: senderId,
        label: "Participant",
        detail: "participant",
        initials: "PA",
      };

      return {
        ...base,
        count,
        share: totalMessages ? roundOne((count / totalMessages) * 100) : 0,
      };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));

  senders.forEach((sender, index) => {
    sender.rank = index + 1;
  });

  return {
    days,
    actualDays: selectedDaily.length,
    totalMessages,
    participantCount: senders.length,
    attachmentMessages,
    averagePerDay: selectedDaily.length ? roundOne(totalMessages / selectedDaily.length) : totalMessages,
    averageTextLength: textMessageCount ? roundOne(textLengthSum / textMessageCount) : 0,
    windowStartDate: selectedDaily[0]?.date || summary.daily[0]?.date,
    windowEndDate: selectedDaily[selectedDaily.length - 1]?.date || lastOverallDay,
    senders,
    daily: selectedDaily,
    hourly: hourlyCounts.map((count, hour) => ({ hour, count })),
  };
}

function roundOne(value) {
  return Math.round(value * 10) / 10;
}

function updateMetrics(windowSummary) {
  setText("totalMessages", fmt.format(windowSummary.totalMessages));
  setText("participantCount", fmt.format(windowSummary.participantCount));
  setText("averagePerDay", fmt.format(windowSummary.averagePerDay));
  setText("topSender", windowSummary.senders[0]?.label || "None");
  setText("windowTitle", `Last ${pluralDays(windowSummary.days)}`);
  setText("dailyTitle", `Last ${pluralDays(windowSummary.days)}`);

  const start = shortDate.format(dateFromKey(windowSummary.windowStartDate));
  const end = shortDate.format(dateFromKey(windowSummary.windowEndDate));
  setText("windowLabel", `${start} - ${end}`);
  setText("windowValue", pluralDays(windowSummary.days));
  setText("dataDepth", `${fmt.format(state.summary.maxWindowDays || state.summary.days)} days loaded`);
}

function renderWindowControls() {
  const max = state.summary.maxWindowDays || state.summary.daily.length;
  const slider = $("windowSlider");
  if (slider) {
    slider.max = String(max);
    slider.value = String(state.windowDays);
  }

  document.querySelectorAll("[data-window-days]").forEach((button) => {
    const days = Number(button.dataset.windowDays);
    button.hidden = days > max;
    button.classList.toggle("active", days === state.windowDays);
  });
}

function renderSenders(windowSummary) {
  const root = $("senderBars");
  if (!root) return;

  const query = state.filter.trim().toLowerCase();
  const filtered = windowSummary.senders.filter((sender) => {
    const haystack = `${sender.label} ${sender.detail}`.toLowerCase();
    return haystack.includes(query);
  });

  if (!filtered.length) {
    root.innerHTML = `<div class="empty">No sender matches this filter.</div>`;
    return;
  }

  const max = Math.max(...windowSummary.senders.map((sender) => sender.count), 1);
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

function renderDaily(windowSummary) {
  const root = $("dailyPulse");
  if (!root) return;

  root.className = "daily-grid";
  if (windowSummary.daily.length > 180) {
    root.classList.add("mini");
  } else if (windowSummary.daily.length > 45) {
    root.classList.add("compact");
  }

  const max = Math.max(...windowSummary.daily.map((day) => day.count), 1);
  root.innerHTML = windowSummary.daily
    .map((day) => {
      const heat = Math.round((day.count / max) * 78);
      const label = escapeHtml(shortDate.format(dateFromKey(day.date)));
      return `
        <div class="day-cell" style="--heat: ${heat}%" title="${label}: ${fmt.format(day.count)} messages">
          <span>${label}</span>
          <strong>${fmt.format(day.count)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderHourly(windowSummary) {
  const root = $("hourlyChart");
  if (!root) return;

  const max = Math.max(...windowSummary.hourly.map((hour) => hour.count), 1);
  root.innerHTML = windowSummary.hourly
    .map((hour) => {
      const height = Math.max((hour.count / max) * 128, 8).toFixed(1);
      const label = hour.hour % 6 === 0 ? String(hour.hour).padStart(2, "0") : "";
      return `<div class="hour-bar" style="--h: ${height}px" data-hour="${label}" title="${hour.hour}:00 - ${fmt.format(hour.count)} messages"></div>`;
    })
    .join("");
}

function render() {
  const windowSummary = buildWindowSummary();
  renderWindowControls();
  updateMetrics(windowSummary);
  renderSenders(windowSummary);
  renderDaily(windowSummary);
  renderHourly(windowSummary);
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
    state.windowDays = clampWindowDays(summary.defaultWindowDays || 14);
    render();
  } catch (error) {
    const root = $("senderBars");
    if (root) {
      root.innerHTML = `<div class="empty">Run <code>npm run generate</code> to create dashboard data.</div>`;
    }
    console.error(error);
  }

  $("senderFilter")?.addEventListener("input", (event) => {
    state.filter = event.target.value;
    render();
  });

  $("windowSlider")?.addEventListener("input", (event) => {
    state.windowDays = clampWindowDays(event.target.value);
    render();
  });

  document.querySelectorAll("[data-window-days]").forEach((button) => {
    button.addEventListener("click", () => {
      state.windowDays = clampWindowDays(button.dataset.windowDays);
      render();
    });
  });
}

init();
