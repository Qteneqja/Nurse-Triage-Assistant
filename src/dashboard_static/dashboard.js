const content = document.getElementById("content");
const pageTitle = document.getElementById("page-title");
const tokenInput = document.getElementById("token-input");
const tokenSave = document.getElementById("token-save");

tokenInput.value = localStorage.getItem("dashboardAdminToken") || "";
tokenSave.addEventListener("click", () => {
  const token = tokenInput.value.trim();
  localStorage.setItem("dashboardAdminToken", token);
  // The shell page gate accepts this cookie on full page loads (filters,
  // direct links); data endpoints still require the header.
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `dashboard_token=${encodeURIComponent(token)}; path=/; SameSite=Strict${secure}`;
  route();
});

function tokenHeaders() {
  const token = localStorage.getItem("dashboardAdminToken");
  return token ? { "X-Dashboard-Token": token } : {};
}

async function api(path, options = {}) {
  const response = await fetch(`/admin${path}`, {
    ...options,
    headers: { ...tokenHeaders(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Admin request failed (${response.status})`);
  }
  return response.json();
}

function setTitle(title) {
  pageTitle.textContent = title;
  document.querySelectorAll(".nav a").forEach((link) => {
    const routePath = link.dataset.route;
    const active =
      window.location.pathname === routePath ||
      (routePath !== "/dashboard" && window.location.pathname.startsWith(routePath));
    link.classList.toggle("active", active);
  });
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

function badge(value, extra = "") {
  if (!value) return '<span class="badge">Unknown</span>';
  const cls = String(value).toLowerCase().replaceAll(" ", "_");
  return `<span class="badge ${cls} ${extra}">${escapeHtml(value)}</span>`;
}

function jsonBlock(value) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) {
    return '<p class="muted">No structured data available.</p>';
  }
  return `<pre class="json-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
}

function stat(label, value) {
  return `
    <div class="stat-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value ?? 0)}</strong>
    </div>
  `;
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

function sessionTable(sessions) {
  if (!sessions.length) {
    return `
      <div class="empty-state">
        <h3>No sessions yet</h3>
        <p class="muted">Sessions appear here as soon as a call comes in.</p>
      </div>
    `;
  }

  const rows = sessions
    .map(
      (session) => `
        <tr>
          <td>${escapeHtml(formatTime(session.created_at))}</td>
          <td>${escapeHtml(session.organization_name || session.organization_id || "Unknown")}</td>
          <td>${badge(session.vertical || "unknown")}</td>
          <td><span class="muted">${escapeHtml(session.workflow_id || "unknown")}</span></td>
          <td>${badge(session.disposition || session.status || "active")}</td>
          <td>${
            session.confidence_score === null || session.confidence_score === undefined
              ? "n/a"
              : `${Math.round(session.confidence_score * 100)}%`
          }</td>
          <td>${session.escalation_required ? badge("Yes", "urgent") : '<span class="muted">No</span>'}</td>
          <td>${badge(session.status || "active")}</td>
          <td>
            <a class="button secondary" href="/dashboard/sessions/${encodeURIComponent(session.session_id)}">
              View
            </a>
          </td>
        </tr>
      `,
    )
    .join("");

  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Organization</th>
            <th>Vertical</th>
            <th>Workflow</th>
            <th>Disposition</th>
            <th>Confidence</th>
            <th>Escalation</th>
            <th>Status</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function renderOverview() {
  setTitle("Overview");
  content.innerHTML = '<div class="panel"><div class="panel-body">Loading overview...</div></div>';
  const summary = await api("/summary");
  const healthcare = summary.sessions_by_vertical.healthcare || 0;
  const property = summary.sessions_by_vertical.property_management || 0;
  content.innerHTML = `
    <div class="stats-grid">
      ${stat("Total sessions", summary.total_sessions)}
      ${stat("Escalations", summary.escalations)}
      ${stat("Human reviews", summary.human_reviews)}
      ${stat("Healthcare", healthcare)}
      ${stat("Property management", property)}
      ${stat("Pending actions", summary.pending_actions)}
    </div>
    <section class="panel">
      <div class="panel-header">
        <h2>Recent sessions</h2>
        <a class="button secondary" href="/dashboard/sessions">All sessions</a>
      </div>
      ${sessionTable(summary.recent_sessions || [])}
    </section>
  `;
}

async function renderSessions() {
  setTitle("Sessions");
  const params = new URLSearchParams(window.location.search);
  const vertical = params.get("vertical") || "";
  const workflow = params.get("workflow_id") || "";
  const status = params.get("status") || "";
  const query = new URLSearchParams();
  if (vertical) query.set("vertical", vertical);
  if (workflow) query.set("workflow_id", workflow);
  if (status) query.set("status", status);

  content.innerHTML = '<div class="panel"><div class="panel-body">Loading sessions...</div></div>';
  const sessions = await api(`/sessions?${query.toString()}`);
  content.innerHTML = `
    <section class="panel">
      <div class="panel-header"><h2>Sessions</h2></div>
      <div class="panel-body">
        <form class="filters" id="filters">
          <label>Vertical
            <select name="vertical">
              <option value="">All</option>
              <option value="healthcare" ${vertical === "healthcare" ? "selected" : ""}>Healthcare</option>
              <option value="property_management" ${vertical === "property_management" ? "selected" : ""}>Property management</option>
            </select>
          </label>
          <label>Workflow
            <input name="workflow_id" value="${escapeHtml(workflow)}" />
          </label>
          <label>Status
            <select name="status">
              <option value="">All</option>
              <option value="active" ${status === "active" ? "selected" : ""}>Active</option>
              <option value="ended" ${status === "ended" ? "selected" : ""}>Ended</option>
              <option value="escalated" ${status === "escalated" ? "selected" : ""}>Escalated</option>
            </select>
          </label>
          <button class="button" type="submit">Apply</button>
        </form>
      </div>
      ${sessionTable(sessions || [])}
    </section>
  `;
  document.getElementById("filters").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const [key, value] of form.entries()) {
      if (String(value).trim()) next.set(key, String(value).trim());
    }
    window.location.href = `/dashboard/sessions?${next.toString()}`;
  });
}

async function renderDetail() {
  const sessionId = decodeURIComponent(window.location.pathname.split("/").pop());
  setTitle("Session detail");
  content.innerHTML = '<div class="panel"><div class="panel-body">Loading session detail...</div></div>';
  const detail = await api(`/sessions/${encodeURIComponent(sessionId)}`);
  const session = detail.session || {};
  const verticalPanel =
    session.vertical === "property_management"
      ? propertyPanel(detail.property_management_metadata)
      : session.vertical === "healthcare"
        ? healthcarePanel(detail.healthcare_metadata)
        : panel("Workflow result", jsonBlock(detail.final_output));

  content.innerHTML = `
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>${escapeHtml(session.session_id || sessionId)}</h2>
          <p class="muted">
            ${escapeHtml(formatTime(session.created_at))}
            ${badge(session.vertical || "unknown")}
            ${badge(session.disposition || session.status || "active")}
          </p>
        </div>
        <a class="button secondary" href="/dashboard/sessions">Back to sessions</a>
      </div>
      <div class="panel-body kv-grid">
        ${kv("Organization", session.organization_name || session.organization_id || "Unknown")}
        ${kv("Workflow", session.workflow_id || "unknown")}
        ${kv("Status", session.status || "unknown")}
        ${kv(
          "Confidence",
          session.confidence_score === null || session.confidence_score === undefined
            ? "n/a"
            : `${Math.round(session.confidence_score * 100)}%`,
        )}
      </div>
    </section>
    <div class="detail-grid">
      <div class="content">
        ${verticalPanel}
        ${panel("Final output", jsonBlock(detail.final_output))}
        ${panel("Transcript and turns", turnsList(detail.turns || []))}
      </div>
      <div class="content">
        ${panel("Audit metadata", jsonBlock(detail.audit_metadata))}
        ${panel("Safety events", jsonBlock(detail.safety_events))}
        ${panel("Proposed actions", actionsList(session.session_id, detail.proposed_actions || []))}
      </div>
    </div>
  `;
  wireActionButtons();
}

function healthcarePanel(data) {
  if (!data) return "";
  return panel(
    "Healthcare audit trail",
    `<div class="kv-grid">
      ${kv("Disposition", data.disposition || "unknown")}
      ${kv("Finalization reason", data.finalization_reason || "n/a")}
      ${kv("Blocked reason", data.healthcare_finalization_blocked_reason || "none")}
      ${kv("SBAR available", data.sbar_available ? "yes" : "no")}
    </div>
    <h3>SBAR</h3>
    ${jsonBlock(data.sbar)}
    <h3>Completeness</h3>
    ${jsonBlock(data.healthcare_intake_completeness || {})}`,
  );
}

function propertyPanel(data) {
  if (!data) return "";
  return panel(
    "Property maintenance output",
    `<div class="kv-grid">
      ${kv("Disposition", data.disposition || "unknown")}
      ${kv("Issue type", data.issue_type || "unknown")}
      ${kv("Property", data.property_address || "masked")}
      ${kv("Unit", data.unit_number || "n/a")}
      ${kv("Vendor", data.vendor_type || "n/a")}
      ${kv("Completeness", data.required_fields_completeness?.is_complete ? "complete" : "incomplete")}
    </div>
    <h3>Work order</h3>
    ${jsonBlock(data.work_order_output || {})}`,
  );
}

function turnsList(turns) {
  if (!turns.length) {
    return '<p class="muted">No transcript or turn records are available.</p>';
  }
  return `
    <div class="turn-list">
      ${turns
        .map(
          (turn) => `
            <div class="turn">
              <div class="turn-meta">
                <span>Turn ${escapeHtml(turn.turn_index ?? "")}</span>
                <span>${escapeHtml(formatTime(turn.timestamp))}</span>
                ${turn.disposition ? badge(turn.disposition) : ""}
              </div>
              ${turn.caller_text ? `<p><strong>Caller:</strong> ${escapeHtml(turn.caller_text)}</p>` : ""}
              ${turn.assistant_text ? `<p><strong>Assistant:</strong> ${escapeHtml(turn.assistant_text)}</p>` : ""}
              ${turn.text ? `<p>${escapeHtml(turn.text)}</p>` : ""}
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function actionsList(sessionId, actions) {
  if (!actions.length) {
    return '<p class="muted">No proposed actions are available for this session.</p>';
  }
  return `
    <div class="actions-grid compact">
      ${actions
        .map(
          (action) => `
            <article class="action-card">
              <h3>${escapeHtml(action.title)}</h3>
              <p>${escapeHtml(action.description)}</p>
              ${badge(action.status)}
              <div class="action-buttons">
                <button data-session="${escapeHtml(sessionId)}" data-action="${escapeHtml(action.action_id)}" data-transition="approve">
                  Approve
                </button>
                <button data-session="${escapeHtml(sessionId)}" data-action="${escapeHtml(action.action_id)}" data-transition="reject">
                  Reject
                </button>
                <button data-session="${escapeHtml(sessionId)}" data-action="${escapeHtml(action.action_id)}" data-transition="complete">
                  Mark complete
                </button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function wireActionButtons() {
  document.querySelectorAll("[data-transition]").forEach((button) => {
    button.addEventListener("click", async () => {
      const sessionId = button.dataset.session;
      const actionId = button.dataset.action;
      const transition = button.dataset.transition;
      button.disabled = true;
      await api(
        `/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(actionId)}/${transition}`,
        { method: "POST" },
      );
      await renderDetail();
    });
  });
}

async function renderActions() {
  setTitle("Actions");
  content.innerHTML = '<div class="panel"><div class="panel-body">Loading actions...</div></div>';
  const sessions = await api("/sessions?limit=200");
  const finalized = sessions.filter((session) => session.proposed_action_count > 0);
  if (!finalized.length) {
    content.innerHTML = `
      <section class="empty-state">
        <h2>No proposed actions yet</h2>
        <p class="muted">Follow-up actions appear here after a call is finalized. Nothing runs without your approval.</p>
      </section>
    `;
    return;
  }

  const actionGroups = await Promise.all(
    finalized.map(async (session) => ({
      session,
      actions: await api(`/sessions/${encodeURIComponent(session.session_id)}/actions`),
    })),
  );
  content.innerHTML = `
    <div class="content">
      ${actionGroups
        .map(
          (group) => panel(
            `${group.session.vertical || "Session"} - ${group.session.session_id}`,
            actionsList(group.session.session_id, group.actions),
          ),
        )
        .join("")}
    </div>
  `;
  wireActionButtons();
}

// ---------------------------------------------------------------------------
// Intake records (PR 4) — the collision shop's working queue
// ---------------------------------------------------------------------------

async function dashApi(path, options = {}, base = "/api/v1/dashboard") {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...tokenHeaders(),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const error = new Error(detail.detail || `Dashboard request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function actorName() {
  return localStorage.getItem("dashboardActorName") || "";
}

function recordBadges(record) {
  const parts = [];
  if (record.injury_flagged) parts.push(badge("INJURY", "urgent"));
  if (record.urgent && !record.injury_flagged) parts.push(badge("URGENT", "urgent"));
  parts.push(badge(record.record_status));
  return parts.join(" ");
}

function recordsTable(records) {
  if (!records.length) {
    return `
      <div class="empty-state">
        <h3>No records yet</h3>
        <p class="muted">Records appear here after each call completes.</p>
      </div>
    `;
  }
  const rows = records
    .map(
      (record) => `
        <tr class="${record.urgency_rank > 0 ? "record-flagged" : ""}">
          <td>${recordBadges(record)}</td>
          <td>${escapeHtml(formatTime(record.created_at))}</td>
          <td>${escapeHtml(record.vehicle || record.vertical_key || "unknown")}</td>
          <td>${escapeHtml(record.contact?.caller_name ?? "n/a")}<br />
              <span class="muted">${escapeHtml(record.contact?.phone ?? "")}</span></td>
          <td>${badge(record.disposition || "unknown")}</td>
          <td>${escapeHtml(record.recommended_action || "")}</td>
          <td>
            <a class="button secondary" href="/dashboard/records/${encodeURIComponent(record.session_id)}">
              Open
            </a>
          </td>
        </tr>
      `,
    )
    .join("");
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Flags / status</th>
            <th>Time</th>
            <th>Vehicle</th>
            <th>Customer</th>
            <th>Disposition</th>
            <th>Recommended action</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function renderRecords() {
  setTitle("Intake records");
  const params = new URLSearchParams(window.location.search);
  const query = new URLSearchParams();
  for (const key of ["record_status", "vertical_key", "date_from", "date_to"]) {
    const value = params.get(key);
    if (value) query.set(key, value);
  }
  if (params.get("injury_flagged")) query.set("injury_flagged", "true");
  if (params.get("urgent_only")) query.set("urgent_only", "true");

  content.innerHTML = '<div class="panel"><div class="panel-body">Loading records...</div></div>';
  const data = await dashApi(`/records?${query.toString()}`);
  const statusValue = params.get("record_status") || "";
  content.innerHTML = `
    <section class="panel">
      <div class="panel-header"><h2>${escapeHtml(data.total_matched)} intake ${data.total_matched === 1 ? "record" : "records"}</h2></div>
      <div class="panel-body">
        <form class="filters" id="record-filters">
          <label>Status
            <select name="record_status">
              <option value="">All</option>
              ${data.statuses
                .map(
                  (status) =>
                    `<option value="${status}" ${statusValue === status ? "selected" : ""}>${status}</option>`,
                )
                .join("")}
            </select>
          </label>
          <label>Vertical
            <select name="vertical_key">
              <option value="">All</option>
              <option value="automotive_collision" ${params.get("vertical_key") === "automotive_collision" ? "selected" : ""}>Collision (Birchwood)</option>
              <option value="insurance" ${params.get("vertical_key") === "insurance" ? "selected" : ""}>Insurance</option>
              <option value="healthcare" ${params.get("vertical_key") === "healthcare" ? "selected" : ""}>Healthcare</option>
            </select>
          </label>
          <label>From <input type="date" name="date_from" value="${escapeHtml(params.get("date_from") || "")}" /></label>
          <label>To <input type="date" name="date_to" value="${escapeHtml(params.get("date_to") || "")}" /></label>
          <label class="checkbox"><input type="checkbox" name="injury_flagged" ${params.get("injury_flagged") ? "checked" : ""}/> Injury only</label>
          <label class="checkbox"><input type="checkbox" name="urgent_only" ${params.get("urgent_only") ? "checked" : ""}/> Urgent only</label>
          <button class="button" type="submit">Apply</button>
        </form>
      </div>
      ${recordsTable(data.records || [])}
    </section>
  `;
  document.getElementById("record-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const [key, value] of form.entries()) {
      if (String(value).trim() && value !== "on") next.set(key, String(value).trim());
      if (value === "on") next.set(key, "true");
    }
    window.location.href = `/dashboard/records?${next.toString()}`;
  });
}

function statusButtons(detail) {
  return `
    <div class="status-buttons">
      <label>Your name
        <input id="actor-input" value="${escapeHtml(actorName())}" placeholder="e.g. front-desk" />
      </label>
      ${detail.statuses
        .map(
          (status) => `
            <button class="button ${detail.record_status === status ? "secondary" : ""}"
                    data-status="${status}" ${detail.record_status === status ? "disabled" : ""}>
              ${status}
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function statusHistory(history) {
  if (!history.length) {
    return '<p class="muted">No status changes yet (derived status shown).</p>';
  }
  return `
    <div class="turn-list">
      ${history
        .map(
          (event) => `
            <div class="turn">
              <div class="turn-meta">
                ${badge(event.status)}
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

async function renderRecordDetail() {
  const sessionId = decodeURIComponent(window.location.pathname.split("/").pop());
  setTitle("Intake record");
  content.innerHTML = '<div class="panel"><div class="panel-body">Loading record...</div></div>';
  const detail = await dashApi(`/records/${encodeURIComponent(sessionId)}`);
  const record = detail.record || {};
  const intake = detail.intake_record || {};
  const banner = record.injury_flagged
    ? `<div class="alert-banner injury-banner">
         <span class="alert-kicker">Injury reported</span>
         <p>The caller was advised to seek medical attention or call 9-1-1. Review this record before you make contact.</p>
       </div>`
    : record.urgent
      ? `<div class="alert-banner urgent-banner">
           <span class="alert-kicker">Urgent</span>
           <p>Review this record and act promptly.</p>
         </div>`
      : "";

  content.innerHTML = `
    ${banner}
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>${escapeHtml(record.vehicle || record.vertical_key || sessionId)}</h2>
          <p class="muted">
            ${escapeHtml(formatTime(record.created_at))}
            ${recordBadges(record)}
          </p>
        </div>
        <a class="button secondary" href="/dashboard/records">Back to records</a>
      </div>
      <div class="panel-body kv-grid">
        ${kv("Customer", record.contact?.caller_name)}
        ${kv("Callback", record.contact?.phone)}
        ${kv("Disposition", record.disposition)}
        ${kv("Recommended action", record.recommended_action)}
        ${kv("Missing info", (record.missing_information || []).join(", ") || "none")}
        ${kv("Flags", (record.flags || []).join(", ") || "none")}
      </div>
      <div class="panel-body">${statusButtons(detail)}</div>
    </section>
    <div class="detail-grid">
      <div class="content">
        ${panel("Shop summary", `<pre class="json-block">${escapeHtml(detail.shop_summary || "n/a")}</pre>`)}
        ${panel("Caller narrative", `<p>${escapeHtml(detail.narrative || "No narrative captured.")}</p>`)}
        ${panel("Transcript", turnsList(detail.turns || []))}
      </div>
      <div class="content">
        ${panel("Status history (audit log)", statusHistory(detail.status_history || []))}
        ${panel("Full intake record", jsonBlock(intake))}
        ${panel("Safety events", jsonBlock(detail.safety_events))}
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
      await dashApi(`/records/${encodeURIComponent(sessionId)}/status`, {
        method: "POST",
        body: JSON.stringify({ status: button.dataset.status, actor }),
      });
      await renderRecordDetail();
    });
  });
}

// ---------------------------------------------------------------------------
// Enrichment preview (shadow mode) — internal upsell demo view
// ---------------------------------------------------------------------------

function enrichmentCard(row) {
  const payload = row.rendered_drafts
    ? { ...row.payload, ...row.rendered_drafts }
    : row.payload;
  return `
    <div class="turn">
      <div class="turn-meta">
        ${badge(row.feature)}
        ${badge(row.status, row.status === "completed" ? "" : "urgent")}
        <span>${escapeHtml(formatTime(row.created_at))}</span>
        <span class="muted">session ${escapeHtml(row.session_id)}</span>
        <span class="muted">pii: ${escapeHtml(row.pii_mode)}</span>
      </div>
      ${jsonBlock(payload)}
    </div>
  `;
}

async function renderEnrichment() {
  setTitle("Enrichment (preview)");
  content.innerHTML = '<div class="panel"><div class="panel-body">Loading enrichment preview...</div></div>';
  let recent;
  try {
    recent = await dashApi("/recent?limit=50", {}, "/api/v1/enrichment");
  } catch (error) {
    const explanation = error.status === 404
      ? "Enrichment is disabled on this server (ENRICHMENT_ENABLED=false)."
      : `The enrichment API failed (HTTP ${escapeHtml(error.status ?? "?")}): ${escapeHtml(error.message)}. Check the server logs.`;
    content.innerHTML = `
      <section class="empty-state">
        <h2>Enrichment preview</h2>
        <p class="muted">${explanation}</p>
      </section>
    `;
    return;
  }
  // Insights failing must not hide the per-call results.
  let insightsBody;
  let sampleLabel = "?";
  try {
    const insights = await dashApi("/insights", {}, "/api/v1/enrichment");
    const agg = insights.insights || {};
    sampleLabel = agg.sample_size ?? 0;
    insightsBody = agg.status === "ok"
      ? jsonBlock(agg)
      : `<p class="muted">${escapeHtml(agg.sample_size)} ${agg.sample_size === 1 ? "call" : "calls"} in the sample — not enough yet for reliable insights. ${escapeHtml(agg.detail || "")}</p>`;
  } catch (error) {
    insightsBody = `<p class="muted">Insights unavailable (HTTP ${escapeHtml(error.status ?? "?")}): ${escapeHtml(error.message)}</p>`;
  }
  content.innerHTML = `
    <div class="preview-banner">
      <span class="alert-kicker">Preview — shadow mode</span>
      <p>Nothing on this page is part of the core pilot record. Drafts need your approval and are never sent automatically.</p>
    </div>
    ${panel(`${escapeHtml(sampleLabel)} ${sampleLabel === 1 ? "call" : "calls"} analyzed — aggregate insights`, insightsBody)}
    ${panel(`${escapeHtml(recent.count)} recent enrichment ${recent.count === 1 ? "output" : "outputs"}`, `
      <div class="turn-list">
        ${(recent.results || []).map(enrichmentCard).join("") || '<p class="muted">No enrichment outputs yet.</p>'}
      </div>
    `)}
  `;
}

async function route() {
  try {
    const path = window.location.pathname.replace("/dashboard/calls", "/dashboard/sessions");
    if (path === "/dashboard/actions") {
      await renderActions();
    } else if (path === "/dashboard/enrichment") {
      await renderEnrichment();
    } else if (path.startsWith("/dashboard/records/")) {
      await renderRecordDetail();
    } else if (path === "/dashboard/records") {
      await renderRecords();
    } else if (path.startsWith("/dashboard/sessions/")) {
      await renderDetail();
    } else if (path === "/dashboard/sessions") {
      await renderSessions();
    } else {
      await renderOverview();
    }
  } catch (error) {
    content.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

route();
