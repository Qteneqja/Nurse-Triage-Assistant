// Birchwood Collision pitch dashboard (single vertical, hard-scoped).
// Reads the Birchwood-only endpoint; the lone write is the shared,
// audit-logged status change. External file — the app CSP is script-src
// 'self', so all logic lives here. Chart bar sizes are set via the CSSOM
// (el.style), never style attributes, to stay within style-src 'self'.

const content = document.getElementById("bw-content");
const valueLine = document.getElementById("bw-value-line");

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STATUS_LABELS = {
  new: "New",
  contacted: "Contacted",
  scheduled: "Scheduled",
  completed: "Completed",
  escalated: "Escalated",
};

let rows = [];
let statuses = [];
let sortKey = "created_at";
let sortDir = -1; // newest first

function tokenHeaders() {
  const token = localStorage.getItem("dashboardAdminToken");
  return token ? { "X-Dashboard-Token": token } : {};
}

async function api(path, options = {}) {
  const response = await fetch(`/api/v1/dashboard${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...tokenHeaders(),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const error = new Error(detail.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "n/a";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")} min`;
}

function badge(value, extra = "") {
  if (!value) return '<span class="badge">Unknown</span>';
  const cls = String(value).toLowerCase().replaceAll(" ", "_");
  return `<span class="badge ${cls} ${extra}">${escapeHtml(value)}</span>`;
}

function statusBadge(status) {
  return badge(STATUS_LABELS[status] || status).replace(
    'class="badge ',
    `class="badge ${escapeHtml(status)} `,
  );
}

function errorPanel(error) {
  const signIn =
    error.status === 401 || error.status === 403
      ? ' <a href="/dashboard/login">Sign in with the admin token</a>, then come back to this page.'
      : "";
  return `
    <section class="empty-state">
      <h2>Couldn't load Birchwood records</h2>
      <p class="muted">${escapeHtml(error.message)}.${signIn}</p>
    </section>
  `;
}

// --- Data shaping -----------------------------------------------------------

function damageArea(record) {
  const area = record.collision?.damage_area;
  if (area) return area;
  const text = (record.collision?.damage_type || "").toLowerCase();
  if (!text) return "unknown";
  if (record.collision?.glass_only || text.includes("windshield") || text.includes("glass")) {
    return "glass";
  }
  const hits = ["front", "rear", "side"].filter((k) => text.includes(k));
  if (hits.length > 1) return "multi";
  return hits[0] || "other";
}

function insurerLabel(record) {
  if (record.collision?.private_pay) return "Private pay";
  return record.collision?.insurance_provider || "Unknown";
}

function isToday(iso) {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

function withinDays(iso, days) {
  const d = new Date(iso);
  return Date.now() - d.getTime() <= days * 24 * 3600 * 1000;
}

// --- Top band -----------------------------------------------------------------

function statTile(label, value, sub, accent = false) {
  return `
    <div class="stat-card ${accent ? "bw-stat-accent" : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${sub ? `<small>${escapeHtml(sub)}</small>` : ""}
    </div>
  `;
}

function renderStats(list) {
  const today = list.filter((r) => isToday(r.created_at)).length;
  const week = list.filter((r) => withinDays(r.created_at, 7)).length;
  const awaiting = list.filter((r) => r.record_status === "new").length;
  const injuries = list.filter((r) => r.injury_flagged).length;
  const towed = list.filter((r) => r.collision?.is_drivable === false).length;
  return `
    <div class="bw-stats">
      ${statTile("Calls today", today, `${week} this week`)}
      ${statTile("Awaiting action", awaiting, "status: new")}
      ${statTile("Injury-flagged", injuries, "review before contact", true)}
      ${statTile("Towed / not drivable", towed, "need tow coordination")}
      ${statTile("Total intakes", list.length, "last 500 shown")}
    </div>
  `;
}

// --- Records table ---------------------------------------------------------------

const SORTS = {
  created_at: (r) => r.created_at || "",
  customer: (r) => (r.contact?.caller_name || "").toLowerCase(),
  vehicle: (r) => (r.vehicle || "").toLowerCase(),
  insurer: (r) => insurerLabel(r).toLowerCase(),
  record_status: (r) => r.record_status || "",
};

function sortedRows(list) {
  const key = SORTS[sortKey] || SORTS.created_at;
  return [...list].sort((a, b) => {
    // Injury/urgent records stay pinned regardless of the chosen sort.
    if (a.urgency_rank !== b.urgency_rank) return b.urgency_rank - a.urgency_rank;
    const av = key(a);
    const bv = key(b);
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return 0;
  });
}

function filteredRows() {
  const status = document.getElementById("f-status")?.value || "";
  const driv = document.getElementById("f-drivable")?.value || "";
  const injury = document.getElementById("f-injury")?.checked;
  const from = document.getElementById("f-from")?.value;
  const to = document.getElementById("f-to")?.value;
  return rows.filter((r) => {
    if (status && r.record_status !== status) return false;
    if (driv === "drivable" && r.collision?.is_drivable !== true) return false;
    if (driv === "towed" && r.collision?.is_drivable !== false) return false;
    if (injury && !r.injury_flagged) return false;
    if (from && new Date(r.created_at) < new Date(from)) return false;
    if (to && new Date(r.created_at) > new Date(`${to}T23:59:59`)) return false;
    return true;
  });
}

function sortHeader(key, label) {
  const arrow = sortKey === key ? (sortDir === 1 ? " ↑" : " ↓") : "";
  return `<th class="bw-sortable" data-sort="${key}">${label}${arrow}</th>`;
}

function recordsTable(list) {
  if (!list.length) {
    return `
      <div class="empty-state">
        <h3>No records match</h3>
        <p class="muted">Clear a filter, or wait for the next call to land.</p>
      </div>
    `;
  }
  const rowsHtml = sortedRows(list)
    .map((r) => {
      const flagged = r.urgency_rank > 0;
      const drivable =
        r.collision?.is_drivable === false
          ? badge("Towed")
          : r.collision?.is_drivable === true
            ? '<span class="muted">Drivable</span>'
            : '<span class="muted">Unknown</span>';
      return `
        <tr class="${flagged ? "bw-row-flagged" : ""}">
          <td>
            ${r.injury_flagged ? badge("Injury", "injury") : ""}
            ${r.urgent && !r.injury_flagged ? badge("Urgent", "urgent") : ""}
            ${statusBadge(r.record_status)}
          </td>
          <td>${escapeHtml(formatTime(r.created_at))}</td>
          <td>${escapeHtml(r.contact?.caller_name || "n/a")}<br />
              <span class="muted">${escapeHtml(r.contact?.phone || "")}</span></td>
          <td>${escapeHtml(r.vehicle || "unknown")}</td>
          <td>${drivable}</td>
          <td>${escapeHtml(r.collision?.damage_type || "unknown")}</td>
          <td>${escapeHtml(insurerLabel(r))}</td>
          <td>
            <a class="button secondary" href="/dashboard/birchwood/records/${encodeURIComponent(r.session_id)}">
              Open
            </a>
          </td>
        </tr>
      `;
    })
    .join("");
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Flags / status</th>
            ${sortHeader("created_at", "Time")}
            ${sortHeader("customer", "Customer")}
            ${sortHeader("vehicle", "Vehicle")}
            <th>Drivability</th>
            <th>Damage</th>
            ${sortHeader("insurer", "Insurance")}
            <th></th>
          </tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;
}

function filtersForm() {
  const options = statuses
    .map((s) => `<option value="${s}">${STATUS_LABELS[s] || s}</option>`)
    .join("");
  return `
    <form class="bw-filters" id="bw-filters">
      <label>Status
        <select id="f-status"><option value="">All</option>${options}</select>
      </label>
      <label>Drivability
        <select id="f-drivable">
          <option value="">All</option>
          <option value="drivable">Drivable</option>
          <option value="towed">Towed / not drivable</option>
        </select>
      </label>
      <label>From <input type="date" id="f-from" /></label>
      <label>To <input type="date" id="f-to" /></label>
      <label class="checkbox"><input type="checkbox" id="f-injury" /> Injury only</label>
      <button class="button secondary" type="reset" id="f-reset">Clear</button>
    </form>
  `;
}

// --- Charts (CSP-safe: sizes set via CSSOM, not style attributes) ---------------

function countBy(list, keyFn) {
  const counts = new Map();
  for (const item of list) {
    const key = keyFn(item);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return counts;
}

function verticalBars(container, labels, values) {
  const max = Math.max(...values, 1);
  container.replaceChildren();
  labels.forEach((label, i) => {
    const col = document.createElement("div");
    col.className = "bw-bar-v";
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = values[i] || "";
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.round((values[i] / max) * 100)}%`;
    const lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = label;
    col.append(val, bar, lbl);
    container.append(col);
  });
}

function horizontalBars(container, entries) {
  const max = Math.max(...entries.map(([, v]) => v), 1);
  container.replaceChildren();
  for (const [label, value] of entries) {
    const row = document.createElement("div");
    row.className = "bw-bar-h";
    const lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = label;
    const track = document.createElement("div");
    track.className = "track";
    const fill = document.createElement("div");
    fill.className = "fill";
    fill.style.width = `${Math.round((value / max) * 100)}%`;
    track.append(fill);
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = value;
    row.append(lbl, track, val);
    container.append(row);
  }
}

function splitBar(container, aCount, bCount) {
  container.replaceChildren();
  const total = aCount + bCount || 1;
  const segA = document.createElement("div");
  segA.className = "seg-a";
  segA.style.width = `${(aCount / total) * 100}%`;
  const segB = document.createElement("div");
  segB.className = "seg-b";
  segB.style.width = `${(bCount / total) * 100}%`;
  container.append(segA, segB);
}

function chartPanel(title, bodyHtml) {
  return `
    <section class="panel">
      <div class="panel-header"><h3>${escapeHtml(title)}</h3></div>
      <div class="panel-body">${bodyHtml}</div>
    </section>
  `;
}

function renderInsights(list) {
  const demo = list.some((r) => r.is_demo);
  const note = `Based on ${list.length} intakes${demo ? " — synthetic demo data" : ""}.`;

  const durations = list
    .map((r) => r.intake_duration_seconds)
    .filter((s) => s !== null && s !== undefined);
  const avgDuration = durations.length
    ? durations.reduce((a, b) => a + b, 0) / durations.length
    : null;
  const noCallback = list.filter((r) => !r.callback_needed).length;
  const noCallbackPct = list.length
    ? Math.round((noCallback / list.length) * 100)
    : 0;

  return `
    <div class="bw-section-head">
      <h2>Collision insights</h2>
      <span class="bw-sample-note">${escapeHtml(note)}</span>
    </div>
    <div class="bw-charts">
      ${chartPanel("Intakes by day of week", '<div class="bw-bars-v" id="c-weekday"></div>')}
      ${chartPanel("Intakes by hour of day", '<div class="bw-bars-v" id="c-hour"></div>')}
      ${chartPanel("Drivable vs towed", `
        <div class="bw-split" id="c-split"></div>
        <div class="bw-split-legend" id="c-split-legend"></div>
      `)}
      ${chartPanel("Damage areas", '<div class="bw-bars-h" id="c-damage"></div>')}
      ${chartPanel("Insurance providers", '<div class="bw-bars-h" id="c-insurer"></div>')}
      ${chartPanel("What ORCA handled", `
        <div class="bw-value-tiles">
          ${statTile("Average intake", avgDuration === null ? "n/a" : formatDuration(avgDuration), "from call start to file")}
          ${statTile("Complete without callback", `${noCallbackPct}%`, `${noCallback} of ${list.length} intakes`)}
        </div>
      `)}
    </div>
  `;
}

function mountCharts(list) {
  const dayCounts = new Array(7).fill(0);
  const hourCounts = new Map();
  for (const r of list) {
    const d = new Date(r.created_at);
    if (!Number.isNaN(d.getTime())) {
      dayCounts[(d.getDay() + 6) % 7] += 1; // Monday-first
      hourCounts.set(d.getHours(), (hourCounts.get(d.getHours()) || 0) + 1);
    }
  }
  verticalBars(document.getElementById("c-weekday"), DAY_LABELS, dayCounts);

  const hours = [...hourCounts.keys()].sort((a, b) => a - b);
  const hourLabels = hours.map((h) => `${h}:00`);
  verticalBars(
    document.getElementById("c-hour"),
    hourLabels,
    hours.map((h) => hourCounts.get(h)),
  );

  const drivable = list.filter((r) => r.collision?.is_drivable === true).length;
  const towed = list.filter((r) => r.collision?.is_drivable === false).length;
  const noDrivability = list.length - drivable - towed;
  splitBar(document.getElementById("c-split"), drivable, towed);
  document.getElementById("c-split-legend").innerHTML = `
    <span><span class="dot a"></span>Drivable — ${drivable}</span>
    <span><span class="dot b"></span>Towed / not drivable — ${towed}</span>
    ${noDrivability > 0 ? `<span class="muted">drivability not captured on ${noDrivability}</span>` : ""}
  `;

  const areaNames = { front: "Front", rear: "Rear", side: "Side", multi: "Multiple areas", glass: "Glass only", other: "Other", unknown: "Unknown" };
  const damage = [...countBy(list, (r) => damageArea(r)).entries()]
    .map(([k, v]) => [areaNames[k] || k, v]);
  mountCategoryBars("c-damage", damage, "Damage area");

  const insurers = [...countBy(list, insurerLabel).entries()];
  mountCategoryBars("c-insurer", insurers, "Insurer");
}

// Placeholder categories never headline a chart: known values sort first and
// uncaptured ones collapse into an honest footnote instead of a giant
// "Unknown" bar.
const UNCAPTURED_LABELS = new Set(["Unknown", "unknown", "n/a", ""]);

function mountCategoryBars(containerId, entries, noun) {
  const container = document.getElementById(containerId);
  const known = entries
    .filter(([label]) => !UNCAPTURED_LABELS.has(label))
    .sort((a, b) => b[1] - a[1]);
  const uncaptured = entries
    .filter(([label]) => UNCAPTURED_LABELS.has(label))
    .reduce((sum, [, value]) => sum + value, 0);
  horizontalBars(container, known);
  if (uncaptured > 0) {
    const note = document.createElement("p");
    note.className = "bw-chart-footnote";
    note.textContent = `${noun} not captured on ${uncaptured} ${uncaptured === 1 ? "call" : "calls"}.`;
    container.append(note);
  }
}

function renderValueLine(list) {
  if (!list.length) {
    valueLine.textContent = "";
    return;
  }
  const month = list.filter((r) => withinDays(r.created_at, 30)).length;
  const durations = list
    .map((r) => r.intake_duration_seconds)
    .filter((s) => s !== null && s !== undefined);
  const avg = durations.length
    ? (durations.reduce((a, b) => a + b, 0) / durations.length / 60).toFixed(1)
    : null;
  const injuries = list.filter((r) => r.injury_flagged).length;
  const demo = list.some((r) => r.is_demo) ? " (demo data)" : "";
  valueLine.textContent =
    `${month} intakes handled in the last 30 days` +
    (avg ? ` · ${avg} min average intake` : "") +
    ` · ${injuries} injury advisor${injuries === 1 ? "y" : "ies"} surfaced${demo}`;
}

// --- Main view --------------------------------------------------------------------

async function renderMain() {
  document.title = "Birchwood Collision · Intake Dashboard · ORCA";
  content.innerHTML =
    '<div class="panel"><div class="panel-body">Loading Birchwood intake records...</div></div>';
  let data;
  try {
    data = await api("/birchwood/records?limit=500");
  } catch (error) {
    content.innerHTML = errorPanel(error);
    return;
  }
  rows = data.records || [];
  statuses = data.statuses || [];

  content.innerHTML = `
    ${renderStats(rows)}
    <section class="panel">
      <div class="panel-header">
        <h2 id="bw-records-title">${rows.length} intake ${rows.length === 1 ? "record" : "records"}</h2>
      </div>
      <div class="panel-body">${filtersForm()}</div>
      <div id="bw-table">${recordsTable(rows)}</div>
    </section>
    <div id="bw-insights">${renderInsights(rows)}</div>
  `;
  mountCharts(rows);
  renderValueLine(rows);
  wireMain();
}

function refreshTable() {
  const list = filteredRows();
  document.getElementById("bw-table").innerHTML = recordsTable(list);
  document.getElementById("bw-records-title").textContent =
    `${list.length} intake ${list.length === 1 ? "record" : "records"}`;
  wireSortHeaders();
}

function wireSortHeaders() {
  document.querySelectorAll("th.bw-sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (sortKey === key) {
        sortDir = -sortDir;
      } else {
        sortKey = key;
        sortDir = key === "created_at" ? -1 : 1;
      }
      refreshTable();
    });
  });
}

function wireMain() {
  const form = document.getElementById("bw-filters");
  form.addEventListener("input", refreshTable);
  form.addEventListener("reset", () => setTimeout(refreshTable, 0));
  form.addEventListener("submit", (event) => event.preventDefault());
  wireSortHeaders();
}

// --- Detail view ---------------------------------------------------------------------

function yesNo(value) {
  if (value === true || value === "yes") return "Yes";
  if (value === false || value === "no") return "No";
  return value ? String(value) : "Not stated";
}

function kv(label, value) {
  return `
    <div class="kv">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value ?? "n/a")}</strong>
    </div>
  `;
}

function panel(title, body) {
  return `
    <section class="panel">
      <div class="panel-header"><h2>${escapeHtml(title)}</h2></div>
      <div class="panel-body">${body}</div>
    </section>
  `;
}

// --- Safety events, in plain language ------------------------------------------
// The deterministic rules are unchanged — this only relabels the display.
// Unknown flags fall back to a clean generic line; raw JSON lives behind the
// "Technical details" expander, never as the default view.

const SAFETY_COPY = {
  injuries_reported:
    "Caller mentioned possible injuries — the safety advisory was given and the record is flagged for priority review.",
  injury_advisory:
    "Caller mentioned possible injuries — the safety advisory was given and the record is flagged for priority review.",
  non_drivable_transfer:
    "Vehicle reported not drivable — recommended transfer to the collision center.",
  glass_only_transfer:
    "Glass-only damage — routed to the glass department.",
  caller_requested_transfer:
    "Caller asked to speak with a person — a transfer was offered.",
  missing_claim_number:
    "Claim number not captured — a callback is needed to complete the file.",
  callback_needed:
    "A callback is needed to complete this intake.",
  possible_duplicate:
    "Looks like a possible duplicate of an earlier call — worth checking before contact.",
  rebuilt_salvage_declined:
    "Caller declined the rebuilt/salvage question — flagged for staff review.",
  staff_review_rebuilt_status:
    "Rebuilt or salvage title reported — staff review suggested before the estimate.",
  luxury_auto_assigned:
    "Luxury brand — routed to the luxury collision location.",
  vehicle_year_declined:
    "Caller declined to share the vehicle year.",
  multiple_vehicles:
    "More than one vehicle was involved.",
  readback_correction:
    "Caller corrected a detail during read-back — the file reflects the correction.",
  vw_location_choice:
    "Volkswagen — the VW-certified location was offered.",
  private_pay:
    "Customer is paying privately — no insurance claim on this file.",
};

function humanizeSafetyEvent(event) {
  if (typeof event === "string") {
    for (const key of Object.keys(SAFETY_COPY)) {
      if (event.includes(key)) return SAFETY_COPY[key];
    }
    return event;
  }
  if (event && typeof event === "object") {
    const candidates = [
      event.flag,
      event.type,
      String(event.rule_id || "").split(":").pop(),
    ];
    for (const candidate of candidates) {
      if (candidate && SAFETY_COPY[candidate]) return SAFETY_COPY[candidate];
    }
    if (typeof event.message === "string" && event.message) return event.message;
  }
  return "A safety rule was triggered on this call — open the technical details for specifics.";
}

function safetyEventsBody(events) {
  if (!events.length) {
    return '<p class="muted">None recorded on this call.</p>';
  }
  const lines = events
    .map((event) => `<p class="bw-safety-line">${escapeHtml(humanizeSafetyEvent(event))}</p>`)
    .join("");
  return `
    ${lines}
    <details class="bw-tech-details">
      <summary>Technical details</summary>
      <pre class="json-block">${escapeHtml(JSON.stringify(events, null, 2))}</pre>
    </details>
  `;
}

function statusHistory(history) {
  if (!history.length) {
    return '<p class="muted">No status changes yet.</p>';
  }
  return `
    <div class="turn-list">
      ${history
        .map(
          (event) => `
            <div class="turn">
              <div class="turn-meta">
                ${statusBadge(event.status)}
                <span>${escapeHtml(formatTime(event.created_at))}</span>
                <span>by ${escapeHtml(event.actor)}</span>
              </div>
              ${event.note ? `<p>${escapeHtml(event.note)}</p>` : ""}
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function turnsList(turns) {
  if (!turns.length) {
    return '<p class="muted">No transcript available for this call.</p>';
  }
  return `
    <div class="turn-list">
      ${turns
        .map((turn) => {
          const caller = turn.caller_text || (turn.role === "caller" ? turn.text : "");
          const assistant =
            turn.assistant_text || (turn.role === "assistant" ? turn.text : "");
          return `
            <div class="turn">
              ${caller ? `<p><strong>Caller:</strong> ${escapeHtml(caller)}</p>` : ""}
              ${assistant ? `<p><strong>ORCA:</strong> ${escapeHtml(assistant)}</p>` : ""}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

async function renderDetail(sessionId) {
  content.innerHTML =
    '<div class="panel"><div class="panel-body">Loading the intake file...</div></div>';
  let detail;
  try {
    detail = await api(`/records/${encodeURIComponent(sessionId)}`);
  } catch (error) {
    content.innerHTML = errorPanel(error);
    return;
  }
  const record = detail.record || {};
  const intake = detail.intake_record || {};
  if (record.vertical_key && record.vertical_key !== "automotive_collision") {
    content.innerHTML = `
      <section class="empty-state">
        <h2>Not a collision record</h2>
        <p class="muted">This file belongs to another intake line.
          <a href="/dashboard/birchwood">Back to Birchwood records</a>.</p>
      </section>
    `;
    return;
  }
  document.title = `${record.vehicle || "Intake record"} · Birchwood Collision · ORCA`;

  const advisory = (detail.safety_events || []).find(
    (e) => e && typeof e === "object" && e.type === "injury_advisory",
  );
  const banner = record.injury_flagged
    ? `<div class="alert-banner">
         <span class="alert-kicker">Injury reported</span>
         <p>${escapeHtml(
           advisory?.message ||
             "The caller was advised to seek medical attention or call 9-1-1. Review this file before you make contact.",
         )}</p>
       </div>`
    : record.urgent
      ? `<div class="alert-banner">
           <span class="alert-kicker">Urgent</span>
           <p>Review this file and act promptly.</p>
         </div>`
      : "";

  const statusButtons = (detail.statuses || [])
    .map(
      (status) => `
        <button class="button ${detail.record_status === status ? "secondary" : ""}"
                data-status="${status}" ${detail.record_status === status ? "disabled" : ""}>
          ${STATUS_LABELS[status] || status}
        </button>
      `,
    )
    .join("");

  content.innerHTML = `
    ${banner}
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>${escapeHtml(record.vehicle || "Vehicle unknown")}</h2>
          <p class="muted">
            ${escapeHtml(formatTime(record.created_at))}
            ${record.injury_flagged ? badge("Injury", "injury") : ""}
            ${record.urgent && !record.injury_flagged ? badge("Urgent", "urgent") : ""}
            ${statusBadge(detail.record_status)}
          </p>
        </div>
        <a class="button secondary" href="/dashboard/birchwood">Back to records</a>
      </div>
      <div class="panel-body kv-grid">
        ${kv("Customer", record.contact?.caller_name)}
        ${kv("Callback", record.contact?.phone)}
        ${kv("Preferred timing", intake.preferred_timing)}
        ${kv("Drivability", record.collision?.is_drivable === false || intake.is_drivable === false ? "Towed / not drivable" : intake.is_drivable === true ? "Drivable" : "Unknown")}
        ${kv("Damage", intake.damage_type)}
        ${kv("Damage description", intake.incident_description ? "see narrative below" : "n/a")}
        ${kv("Injuries", intake.injuries_state === "reported" ? "REPORTED — advisory issued" : intake.injuries_state === "denied" ? "None reported" : "Not stated")}
        ${kv("Other parties", intake.other_parties)}
        ${kv("Police report", yesNo(intake.police_report_filed))}
        ${kv("Photos available", yesNo(intake.photos_available))}
        ${kv("Insurance", intake.private_pay ? "Private pay" : intake.insurance_provider)}
        ${kv("Claim number", intake.claim_number || (intake.private_pay ? "n/a" : "pending"))}
        ${kv("Vehicle year", intake.vehicle_year)}
        ${kv("Preferred location", intake.preferred_collision_center)}
        ${kv("Missing required info", (record.missing_information || []).length ? (record.missing_information || []).join(", ") : "None — all required fields captured")}
      </div>
      <div class="panel-body">
        <div class="status-row">
          <label>Your name
            <input id="actor-input" value="${escapeHtml(localStorage.getItem("dashboardActorName") || "")}" placeholder="e.g. front-desk" />
          </label>
          ${statusButtons}
        </div>
      </div>
    </section>
    <div class="bw-detail-grid">
      <div class="bw-content">
        ${panel("Shop summary", `<pre class="json-block">${escapeHtml(detail.shop_summary || "n/a")}</pre>`)}
        ${panel("Caller narrative", `<p>${escapeHtml(detail.narrative || "No narrative captured.")}</p>`)}
        ${panel("Call transcript", turnsList(detail.turns || []))}
      </div>
      <div class="bw-content">
        ${panel("Status history (audit log)", statusHistory(detail.status_history || []))}
        ${panel("Safety events", safetyEventsBody(detail.safety_events || []))}
      </div>
    </div>
  `;

  document.getElementById("actor-input").addEventListener("change", (event) => {
    localStorage.setItem("dashboardActorName", event.target.value.trim());
  });
  document.querySelectorAll("[data-status]").forEach((button) => {
    button.addEventListener("click", async () => {
      const actor = (document.getElementById("actor-input").value || "").trim();
      if (!actor) {
        alert("Enter your name first — every status change is audit-logged.");
        return;
      }
      localStorage.setItem("dashboardActorName", actor);
      button.disabled = true;
      await api(`/records/${encodeURIComponent(sessionId)}/status`, {
        method: "POST",
        body: JSON.stringify({ status: button.dataset.status, actor }),
      });
      await renderDetail(sessionId);
    });
  });
}

// --- Router ------------------------------------------------------------------------

async function route() {
  const path = window.location.pathname;
  try {
    if (path.startsWith("/dashboard/birchwood/records/")) {
      await renderDetail(decodeURIComponent(path.split("/").pop()));
    } else {
      await renderMain();
    }
  } catch (error) {
    content.innerHTML = errorPanel(error);
  }
}

route();
