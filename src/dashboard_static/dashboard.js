const content = document.getElementById("content");
const pageTitle = document.getElementById("page-title");
const tokenInput = document.getElementById("token-input");
const tokenSave = document.getElementById("token-save");

tokenInput.value = localStorage.getItem("dashboardAdminToken") || "";
tokenSave.addEventListener("click", () => {
  localStorage.setItem("dashboardAdminToken", tokenInput.value.trim());
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
        <h3>No sessions found</h3>
        <p class="muted">Sessions will appear here after voice calls are created.</p>
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
  setTitle("Voice Decision Support Platform");
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
        <h2>Proposed actions</h2>
        <p class="muted">Placeholder post-call actions appear here after a workflow is finalized.</p>
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

async function route() {
  try {
    const path = window.location.pathname.replace("/dashboard/calls", "/dashboard/sessions");
    if (path === "/dashboard/actions") {
      await renderActions();
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
