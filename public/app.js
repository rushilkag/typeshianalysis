const state = {
  summary: null,
  sentiments: null,
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
  const rawSenderCounts = new Map();
  const vibeBySender = new Map();
  const reactionBySender = new Map();
  const reactionByAuthor = new Map();
  const slurBySender = new Map();
  const slurByCategory = new Map();
  const wordWatchBySender = new Map();
  const wordWatchByTerm = new Map();
  const swearBySender = new Map();
  const swearByTerm = new Map();
  const mentionEdges = new Map();
  const hourlyCounts = Array.from({ length: 24 }, () => 0);

  let totalMessages = 0;
  let totalTurns = 0;
  let attachmentMessages = 0;
  let attachmentTurns = 0;
  let textLengthSum = 0;
  let textMessageCount = 0;

  selectedDaily.forEach((day) => {
    totalMessages += day.count || 0;
    totalTurns += day.turnCount ?? day.count ?? 0;
    attachmentMessages += day.attachmentMessages || 0;
    attachmentTurns += day.attachmentTurns ?? day.attachmentMessages ?? 0;
    textLengthSum += day.textLengthSum || 0;
    textMessageCount += day.textMessageCount || 0;

    Object.entries(day.bySender || {}).forEach(([senderId, count]) => {
      rawSenderCounts.set(senderId, (rawSenderCounts.get(senderId) || 0) + count);
    });

    Object.entries(day.turnsBySender || day.bySender || {}).forEach(([senderId, count]) => {
      senderCounts.set(senderId, (senderCounts.get(senderId) || 0) + count);
    });

    (day.turnsByHour || day.byHour || []).forEach((count, hour) => {
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

    Object.entries(day.reactionByAuthor || {}).forEach(([senderId, count]) => {
      reactionByAuthor.set(senderId, (reactionByAuthor.get(senderId) || 0) + count);
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

    Object.entries(day.wordWatchBySender || {}).forEach(([senderId, scores]) => {
      const target = wordWatchBySender.get(senderId) || {};
      Object.entries(scores).forEach(([category, count]) => {
        target[category] = (target[category] || 0) + count;
      });
      wordWatchBySender.set(senderId, target);
    });

    Object.entries(day.wordWatchByTerm || {}).forEach(([category, count]) => {
      wordWatchByTerm.set(category, (wordWatchByTerm.get(category) || 0) + count);
    });

    Object.entries(day.swearBySender || {}).forEach(([senderId, scores]) => {
      const target = swearBySender.get(senderId) || {};
      Object.entries(scores).forEach(([category, count]) => {
        target[category] = (target[category] || 0) + count;
      });
      swearBySender.set(senderId, target);
    });

    Object.entries(day.swearByTerm || {}).forEach(([category, count]) => {
      swearByTerm.set(category, (swearByTerm.get(category) || 0) + count);
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
        rawCount: rawSenderCounts.get(senderId) || count,
        share: totalTurns ? roundOne((count / totalTurns) * 100) : 0,
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
    messageCount: rawSenderCounts.get(senderId) || senderCounts.get(senderId) || 0,
    turnCount: senderCounts.get(senderId) || 0,
    scores,
  }));

  const reactionRows = Array.from(reactionBySender.entries()).map(([senderId, reactionCount]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: rawSenderCounts.get(senderId) || senderCounts.get(senderId) || 0,
    turnCount: senderCounts.get(senderId) || 0,
    reactionCount,
  }));

  const reactionReceivedRows = Array.from(reactionByAuthor.entries()).map(([senderId, reactionCount]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: rawSenderCounts.get(senderId) || senderCounts.get(senderId) || 0,
    turnCount: senderCounts.get(senderId) || 0,
    reactionCount,
    rate: percent(reactionCount, senderCounts.get(senderId) || rawSenderCounts.get(senderId) || 0),
  }));

  const slurRows = Array.from(slurBySender.entries()).map(([senderId, scores]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    scores,
    total: Object.values(scores).reduce((sum, count) => sum + count, 0),
  }));

  const wordWatchRows = Array.from(wordWatchBySender.entries()).map(([senderId, scores]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: rawSenderCounts.get(senderId) || senderCounts.get(senderId) || 0,
    turnCount: senderCounts.get(senderId) || 0,
    scores,
    total: Object.values(scores).reduce((sum, count) => sum + count, 0),
  }));

  const swearRows = Array.from(swearBySender.entries()).map(([senderId, scores]) => ({
    ...(participantById.get(senderId) || { id: senderId, label: "Participant" }),
    messageCount: rawSenderCounts.get(senderId) || senderCounts.get(senderId) || 0,
    turnCount: senderCounts.get(senderId) || 0,
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
    totalTurns,
    participantCount: senders.length,
    attachmentMessages,
    attachmentTurns,
    burstReductionPercent: totalMessages ? roundOne(((totalMessages - totalTurns) / totalMessages) * 100) : 0,
    averagePerDay: selectedDaily.length ? roundOne(totalTurns / selectedDaily.length) : totalTurns,
    averageTextLength: textMessageCount ? roundOne(textLengthSum / textMessageCount) : 0,
    windowStartDate: startDate,
    windowEndDate: endDate,
    senders,
    daily: selectedDaily,
    hourly: hourlyCounts.map((count, hour) => ({ hour, count })),
    vibeRows,
    reactionRows,
    reactionReceivedRows,
    mentions,
    reactionMessages: reactions,
    slurRows,
    slurByCategory: Array.from(slurByCategory.entries()).map(([category, count]) => ({ category, count })),
    slurTotal: Array.from(slurByCategory.values()).reduce((sum, count) => sum + count, 0),
    wordWatchRows,
    wordWatchByTerm: Array.from(wordWatchByTerm.entries()).map(([category, count]) => ({ category, count })),
    wordWatchTotal: Array.from(wordWatchByTerm.values()).reduce((sum, count) => sum + count, 0),
    swearRows,
    swearByTerm: Array.from(swearByTerm.entries()).map(([category, count]) => ({ category, count })),
    swearTotal: Array.from(swearByTerm.values()).reduce((sum, count) => sum + count, 0),
  };
}

function roundOne(value) {
  return Math.round(value * 10) / 10;
}

function updateMetrics(windowSummary) {
  setText("totalMessages", fmt.format(windowSummary.totalTurns));
  setText("participantCount", fmt.format(windowSummary.participantCount));
  setText("averagePerDay", fmt.format(windowSummary.averagePerDay));
  setText("topSender", windowSummary.senders[0]?.label || "None");
  setText("windowTitle", `Last ${pluralDays(windowSummary.days)}`);
  setText("dailyTitle", `Last ${pluralDays(windowSummary.days)}`);

  const start = shortDate.format(dateFromKey(windowSummary.windowStartDate));
  const end = shortDate.format(dateFromKey(windowSummary.windowEndDate));
  setText("windowLabel", `${start} - ${end}`);
  setText("windowValue", pluralDays(windowSummary.days));
  setText(
    "dataDepth",
    `${fmt.format(state.summary.maxWindowDays || state.summary.days)} days loaded · ${state.summary.turnGapSeconds || 30}s normalization`
  );
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
            <span>${detail} / ${sender.share}% share / ${fmt.format(sender.rawCount)} bubbles</span>
          </div>
          <div class="meter" aria-label="${label}: ${sender.count} normalized turns">
            <div class="meter-fill" style="--w: ${width}%"></div>
          </div>
          <div class="count">
            <strong>${fmt.format(sender.count)}</strong>
            <span>turns</span>
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

  const max = Math.max(...windowSummary.daily.map((day) => day.turnCount ?? day.count), 1);
  root.innerHTML = windowSummary.daily
    .map((day) => {
      const count = day.turnCount ?? day.count;
      const rawCount = day.count || count;
      const heat = Math.round((count / max) * 78);
      const label = escapeHtml(shortDate.format(dateFromKey(day.date)));
      return `
        <div class="day-cell" style="--heat: ${heat}%" title="${label}: ${fmt.format(count)} turns / ${fmt.format(rawCount)} bubbles">
          <span>${label}</span>
          <strong>${fmt.format(count)}</strong>
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

function renderReactionRanks(windowSummary) {
  const sentRoot = $("reactionDemon");
  const receivedRoot = $("mostLiked");

  if (sentRoot) {
    const sentRows = [...windowSummary.reactionRows]
      .sort((a, b) => b.reactionCount - a.reactionCount || a.label.localeCompare(b.label))
      .slice(0, 10);
    sentRoot.innerHTML = renderRankRows(sentRows, {
      empty: "No reactions sent in this window.",
      valueLabel: "sent",
    });
  }

  if (receivedRoot) {
    const receivedRows = [...windowSummary.reactionReceivedRows]
      .sort((a, b) => b.reactionCount - a.reactionCount || b.rate - a.rate || a.label.localeCompare(b.label))
      .slice(0, 10);
    receivedRoot.innerHTML = renderRankRows(receivedRows, {
      empty: "No reactions received in this window.",
      valueLabel: "received",
      detail: (row) => `${fmt.format(row.reactionCount)} reactions / ${fmt.format(row.turnCount || 0)} turns`,
    });
  }
}

function renderRankRows(rows, options) {
  if (!rows.length) {
    return `<div class="empty">${escapeHtml(options.empty)}</div>`;
  }

  const max = Math.max(...rows.map((row) => row.reactionCount), 1);
  return rows
    .map((row, index) => {
      const width = Math.max((row.reactionCount / max) * 100, 3).toFixed(2);
      const detail = options.detail
        ? options.detail(row)
        : `${fmt.format(row.reactionCount)} reactions ${options.valueLabel}`;
      return `
        <div class="reaction-rank-row">
          <span>${index + 1}</span>
          <div>
            <strong>${escapeHtml(row.label)}</strong>
            <em>${escapeHtml(detail)}</em>
            <i style="--w: ${width}%"></i>
          </div>
          <b>${fmt.format(row.reactionCount)}</b>
        </div>
      `;
    })
    .join("");
}

function reactionBreakdown(reactionTypes = {}) {
  return Object.entries(reactionTypes)
    .map(([type, count]) => `${escapeHtml(type)} ${fmt.format(count)}`)
    .join(" / ");
}

function reactionEvidence(message) {
  if (message.preview) {
    return escapeHtml(message.preview);
  }

  if (message.attachmentCount) {
    const types = (message.attachmentTypes || []).join(", ") || "attachment";
    return `${fmt.format(message.attachmentCount)} media attachment${message.attachmentCount === 1 ? "" : "s"} · ${escapeHtml(types)}`;
  }

  return "No text body";
}

function reactionMedia(message) {
  const media = message.media || [];
  if (!media.length) return "";

  return `
    <div class="reaction-media">
      ${media
        .map(
          (item) => `
            <img src="${escapeHtml(item.src)}" alt="Reacted message attachment" loading="lazy" />
          `
        )
        .join("")}
    </div>
  `;
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
      const preview = reactionEvidence(message);
      return `
        <div class="reaction-row">
          <div class="rank">${index + 1}</div>
          <div>
            <strong>${escapeHtml(message.authorLabel)} · ${date}</strong>
            <p>${preview}</p>
            ${reactionMedia(message)}
            <span>${reactionBreakdown(message.reactionTypes)}</span>
          </div>
          <b>${fmt.format(message.reactionCount)}</b>
        </div>
      `;
    })
    .join("");
}

function renderWordWatch(windowSummary) {
  const root = $("wordWatchStats");
  if (!root) return;

  const rows = [...windowSummary.wordWatchRows]
    .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label))
    .slice(0, 10);

  if (!rows.length) {
    root.innerHTML = `<div class="empty">No requested term hits in this window.</div>`;
    return;
  }

  const topSender = rows[0];
  const terms = windowSummary.wordWatchByTerm
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
    .map((item) => `<span>${escapeHtml(item.category)} <b>${fmt.format(item.count)}</b></span>`)
    .join("");

  root.innerHTML = `
    <div class="slur-total">
      <span>Term hits</span>
      <strong>${fmt.format(windowSummary.wordWatchTotal)}</strong>
    </div>
    <p class="note">Joke label only. Top sender by exact requested-term count: ${escapeHtml(topSender.label)}.</p>
    <div class="category-pills">${terms}</div>
    <div class="word-watch-list">
      ${rows
        .map((row, index) => {
          const breakdown = Object.entries(row.scores)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([category, count]) => `${category} ${fmt.format(count)}`)
            .join(" / ");
          return `
            <div class="detector-row">
              <span>${index + 1}. ${escapeHtml(row.label)} <em>${escapeHtml(breakdown)}</em></span>
              <b>${fmt.format(row.total)}</b>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderSwears(windowSummary) {
  const root = $("swearStats");
  if (!root) return;

  const rows = [...windowSummary.swearRows]
    .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label))
    .slice(0, 10);

  if (!rows.length) {
    root.innerHTML = `<div class="empty">No swear hits in this window.</div>`;
    return;
  }

  const topSender = rows[0];
  const terms = windowSummary.swearByTerm
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
    .slice(0, 8)
    .map((item) => `<span>${escapeHtml(item.category)} <b>${fmt.format(item.count)}</b></span>`)
    .join("");

  root.innerHTML = `
    <div class="slur-total">
      <span>Swear hits</span>
      <strong>${fmt.format(windowSummary.swearTotal)}</strong>
    </div>
    <p class="note">Top sender by exact swear-word count: ${escapeHtml(topSender.label)}.</p>
    <div class="category-pills">${terms}</div>
    <div class="word-watch-list">
      ${rows
        .map((row, index) => {
          const breakdown = Object.entries(row.scores)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .slice(0, 4)
            .map(([category, count]) => `${category} ${fmt.format(count)}`)
            .join(" / ");
          return `
            <div class="detector-row">
              <span>${index + 1}. ${escapeHtml(row.label)} <em>${escapeHtml(breakdown)}</em></span>
              <b>${fmt.format(row.total)}</b>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderSentiments() {
  const root = $("sentimentAwards");
  if (!root) return;

  const sentiments = state.sentiments;
  if (!sentiments?.rankings?.length) {
    root.innerHTML = `
      <span class="coming-soon">Coming soon</span>
      <p class="note">Run <code>npm run classify</code> with <code>OPENAI_API_KEY</code> to publish AI scores.</p>
    `;
    return;
  }

  root.innerHTML = sentiments.rankings
    .map((category) => {
      const winner = category.rows?.[0];
      if (!winner) {
        return `
          <div class="sentiment-card">
            <span>${escapeHtml(category.label)}</span>
            <strong>No signal</strong>
            <em>Minimum ${fmt.format(sentiments.minimumTurnsForRanking || 25)} turns</em>
          </div>
        `;
      }

      const examples = (winner.examples || [])
        .slice(0, 2)
        .map(
          (example) => `
            <blockquote>
              “${escapeHtml(example.quote)}”
              <cite>${escapeHtml(example.date)}</cite>
            </blockquote>
          `
        )
        .join("");

      return `
        <div class="sentiment-card">
          <span>${escapeHtml(category.label)}</span>
          <strong>${escapeHtml(winner.label)}</strong>
          <em>${fmt.format(winner.count)} / ${fmt.format(winner.turns)} turns · ${fmt.format(winner.rate)}%</em>
          <b>${fmt.format(winner.rate)}%</b>
          <div class="sentiment-quotes">${examples || "<blockquote>No quote kept</blockquote>"}</div>
        </div>
      `;
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
  renderMentions(windowSummary);
  renderReactionRanks(windowSummary);
  renderReactions(windowSummary);
  renderWordWatch(windowSummary);
  renderSwears(windowSummary);
  renderSentiments();
}

async function loadSummary() {
  const response = await fetch("./data/summary.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not load summary.json: ${response.status}`);
  }
  return response.json();
}

async function loadOptionalJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

async function init() {
  try {
    const summary = await loadSummary();
    const sentiments = await loadOptionalJson("./data/sentiments.json");
    state.summary = summary;
    state.sentiments = sentiments;
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
