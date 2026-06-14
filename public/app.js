const state = {
  summary: null,
  filter: "",
  windowDays: 14,
};

const AWARD_MIN_MESSAGES = 25;
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
  const vibeBySender = new Map();
  const reactionBySender = new Map();
  const slurBySender = new Map();
  const slurByCategory = new Map();
  const mentionEdges = new Map();
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

    Object.entries(day.vibesBySender || {}).forEach(([senderId, scores]) => {
      const target = vibeBySender.get(senderId) || {};
      Object.entries(scores).forEach(([bucket, score]) => {
        target[bucket] = (target[bucket] || 0) + score;
      });
      vibeBySender.set(senderId, target);
    });

    Object.entries(day.reactionBySender || {}).forEach(([senderId, count]) => {
      reactionBySender.set(senderId, (reactionBySender.get(senderId) || 0) + count);
    });

    Object.entries(day.slurBySender || {}).forEach(([senderId, scores]) => {
      const target = slurBySender.get(senderId) || {};
      Object.entries(scores).forEach(([category, count]) => {
        target[category] = (target[category] || 0) + count;
      });
      slurBySender.set(senderId, target);
    });

    Object.entries(day.slurByCategory || {}).forEach(([category, count]) => {
      slurByCategory.set(category, (slurByCategory.get(category) || 0) + count);
    });

    (day.mentions || []).forEach((edge) => {
      const key = `${edge.from}>${edge.to}`;
      mentionEdges.set(key, (mentionEdges.get(key) || 0) + edge.count);
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

  const startDate = selectedDaily[0]?.date || summary.daily[0]?.date;
  const endDate = selectedDaily[selectedDaily.length - 1]?.date || lastOverallDay;
  const reactions = (summary.reactionMessages || [])
    .filter((message) => message.date >= startDate && message.date <= endDate)
    .sort((a, b) => b.reactionCount - a.reactionCount || a.timestamp.localeCompare(b.timestamp))
    .slice(0, 12);

  const vibeRows = Array.from(vibeBySender.entries()).map(([senderId, scores]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: senderCounts.get(senderId) || 0,
    scores,
  }));

  const reactionRows = Array.from(reactionBySender.entries()).map(([senderId, reactionCount]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: senderCounts.get(senderId) || 0,
    reactionCount,
  }));

  const slurRows = Array.from(slurBySender.entries()).map(([senderId, scores]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    scores,
    total: Object.values(scores).reduce((sum, count) => sum + count, 0),
  }));

  const mentions = Array.from(mentionEdges.entries())
    .map(([edge, count]) => {
      const [from, to] = edge.split(">");
      return {
        from,
        to,
        fromLabel: participantById.get(from)?.label || "Participant",
        toLabel: participantById.get(to)?.label || "Participant",
        count,
      };
    })
    .sort((a, b) => b.count - a.count || a.fromLabel.localeCompare(b.fromLabel))
    .slice(0, 12);

  return {
    days,
    actualDays: selectedDaily.length,
    totalMessages,
    participantCount: senders.length,
    attachmentMessages,
    averagePerDay: selectedDaily.length ? roundOne(totalMessages / selectedDaily.length) : totalMessages,
    averageTextLength: textMessageCount ? roundOne(textLengthSum / textMessageCount) : 0,
    windowStartDate: startDate,
    windowEndDate: endDate,
    senders,
    daily: selectedDaily,
    hourly: hourlyCounts.map((count, hour) => ({ hour, count })),
    vibeRows,
    reactionRows,
    mentions,
    reactionMessages: reactions,
    slurRows,
    slurByCategory: Array.from(slurByCategory.entries()).map(([category, count]) => ({ category, count })),
    slurTotal: Array.from(slurByCategory.values()).reduce((sum, count) => sum + count, 0),
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

function percent(numerator, denominator) {
  return denominator ? roundOne((numerator / denominator) * 100) : 0;
}

function topVibe(windowSummary, bucket) {
  return windowSummary.vibeRows
    .map((row) => {
      const count = row.scores[bucket] || 0;
      return {
        label: row.label,
        count,
        denominator: row.messageCount || 0,
        rate: percent(count, row.messageCount || 0),
        basis: "messages",
      };
    })
    .filter((row) => row.denominator >= AWARD_MIN_MESSAGES)
    .sort((a, b) => b.rate - a.rate || b.count - a.count || a.label.localeCompare(b.label))[0];
}

function topReactionWarrior(windowSummary) {
  return windowSummary.reactionRows
    .map((row) => ({
      label: row.label,
      count: row.reactionCount || 0,
      denominator: row.messageCount || 0,
      rate: percent(row.reactionCount || 0, row.messageCount || 0),
      basis: "reactions / messages",
    }))
    .filter((row) => row.denominator >= AWARD_MIN_MESSAGES)
    .sort((a, b) => b.rate - a.rate || b.count - a.count || a.label.localeCompare(b.label))[0];
}

function renderVibes(windowSummary) {
  const root = $("vibeAwards");
  if (!root) return;

  const awards = [
    ["Biggest hater", topVibe(windowSummary, "hater"), "hater"],
    ["Biggest glazer", topVibe(windowSummary, "glazer"), "glazer"],
    ["Pick-me radar", topVibe(windowSummary, "pickMe"), "pick-me"],
    ["Self-insert king", topVibe(windowSummary, "selfInsert"), "self-insert"],
    ["Laugh merchant", topVibe(windowSummary, "laugh"), "laugh"],
    ["Reaction warrior", topReactionWarrior(windowSummary), "reaction-warrior"],
  ];

  root.innerHTML = awards
    .map(([title, winner, flavor]) => {
      const hasSignal = winner?.count > 0;
      const label = hasSignal ? winner.label : "No signal";
      const rate = winner?.rate || 0;
      const count = winner?.count || 0;
      const denominator = winner?.denominator || 0;
      return `
        <div class="award-card ${flavor}">
          <span>${escapeHtml(title)}</span>
          <strong>${escapeHtml(label)}</strong>
          <em>${fmt.format(count)} / ${fmt.format(denominator)} ${escapeHtml(winner?.basis || "messages")} · min ${AWARD_MIN_MESSAGES} msgs</em>
          <b>${fmt.format(rate)}%</b>
        </div>
      `;
    })
    .join("");
}

function renderMentions(windowSummary) {
  const root = $("mentionEdges");
  if (!root) return;

  if (!windowSummary.mentions.length) {
    root.innerHTML = `<div class="empty">No name mentions detected in this window.</div>`;
    return;
  }

  root.innerHTML = windowSummary.mentions
    .map((edge, index) => `
      <div class="edge-row">
        <span>${index + 1}</span>
        <strong>${escapeHtml(edge.fromLabel)} → ${escapeHtml(edge.toLabel)}</strong>
        <b>${fmt.format(edge.count)}</b>
      </div>
    `)
    .join("");
}

function reactionBreakdown(reactionTypes = {}) {
  return Object.entries(reactionTypes)
    .map(([type, count]) => `${escapeHtml(type)} ${fmt.format(count)}`)
    .join(" / ");
}

function renderReactions(windowSummary) {
  const root = $("reactionMessages");
  if (!root) return;

  if (!windowSummary.reactionMessages.length) {
    root.innerHTML = `<div class="empty">No reacted messages found in this window.</div>`;
    return;
  }

  root.innerHTML = windowSummary.reactionMessages
    .map((message, index) => {
      const date = shortDate.format(dateFromKey(message.date));
      const preview = message.preview ? escapeHtml(message.preview) : "Message preview hidden in share-safe build";
      return `
        <div class="reaction-row">
          <div class="rank">${index + 1}</div>
          <div>
            <strong>${escapeHtml(message.authorLabel)} · ${date}</strong>
            <p>${preview}</p>
            <span>${reactionBreakdown(message.reactionTypes)}</span>
          </div>
          <b>${fmt.format(message.reactionCount)}</b>
        </div>
      `;
    })
    .join("");
}

function renderSlurs(windowSummary) {
  const root = $("slurStats");
  if (!root) return;

  const configured = Boolean(state.summary.analysis?.slurLexiconConfigured);
  if (!configured) {
    root.innerHTML = `
      <div class="slur-total">
        <span>Not configured</span>
        <strong>0</strong>
      </div>
      <p class="note">Add <code>config/slur_terms.local.json</code> locally and regenerate to enable category counts.</p>
    `;
    return;
  }

  const topSender = [...windowSummary.slurRows].sort((a, b) => b.total - a.total)[0];
  const categories = windowSummary.slurByCategory
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
    .map((item) => `<span>${escapeHtml(item.category)} <b>${fmt.format(item.count)}</b></span>`)
    .join("");

  root.innerHTML = `
    <div class="slur-total">
      <span>Total detected</span>
      <strong>${fmt.format(windowSummary.slurTotal)}</strong>
    </div>
    <p class="note">Top sender: ${escapeHtml(topSender?.label || "None")}</p>
    <div class="category-pills">${categories || "<span>No hits</span>"}</div>
  `;
}

function render() {
  const windowSummary = buildWindowSummary();
  renderWindowControls();
  updateMetrics(windowSummary);
  renderSenders(windowSummary);
  renderDaily(windowSummary);
  renderHourly(windowSummary);
  renderVibes(windowSummary);
  renderMentions(windowSummary);
  renderReactions(windowSummary);
  renderSlurs(windowSummary);
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
