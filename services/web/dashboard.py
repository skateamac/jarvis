from __future__ import annotations

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Jarvis</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a222c;
      --text: #e8eef4;
      --muted: #8b9aab;
      --accent: #3d9a78;
      --danger: #c45c5c;
      --border: #2a3542;
      --focus: #5eb89a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(1200px 600px at 10% -10%, #1c3a32 0%, transparent 55%),
        radial-gradient(900px 500px at 100% 0%, #243044 0%, transparent 50%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    header {
      padding: 1.25rem 1.25rem 0.5rem;
      max-width: 720px;
      margin: 0 auto;
    }
    header h1 {
      font-family: "IBM Plex Serif", Georgia, serif;
      font-weight: 600;
      font-size: 1.75rem;
      letter-spacing: -0.02em;
      margin: 0;
    }
    header p {
      margin: 0.35rem 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }
    main {
      max-width: 720px;
      margin: 0 auto;
      padding: 0.75rem 1.25rem 2.5rem;
      display: grid;
      gap: 1.25rem;
    }
    section h2 {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 0 0 0.65rem;
      font-weight: 600;
    }
    .panel {
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0.9rem 1rem;
    }
    .empty { color: var(--muted); font-size: 0.95rem; }
    .item {
      padding: 0.75rem 0;
      border-top: 1px solid var(--border);
    }
    .item:first-child { border-top: 0; padding-top: 0; }
    .item:last-child { padding-bottom: 0; }
    .title { font-weight: 560; margin: 0 0 0.25rem; }
    .meta { color: var(--muted); font-size: 0.85rem; margin: 0; }
    .summary-body {
      white-space: pre-wrap;
      font-size: 0.9rem;
      line-height: 1.45;
      margin: 0.35rem 0 0;
    }
    .actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-top: 0.65rem;
      align-items: center;
    }
    input[type="password"] {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 8px;
      padding: 0.45rem 0.6rem;
      width: 7rem;
      font-size: 1rem;
    }
    input:focus, button:focus-visible {
      outline: 2px solid var(--focus);
      outline-offset: 1px;
    }
    button {
      border: 0;
      border-radius: 8px;
      padding: 0.5rem 0.85rem;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
    }
    .approve { background: var(--accent); color: #04140f; }
    .reject { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
    .refresh {
      background: transparent;
      color: var(--text);
      border: 1px solid var(--border);
      margin-left: auto;
    }
    .toolbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
    .status-pill {
      display: inline-block;
      font-size: 0.75rem;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .toast {
      position: fixed;
      left: 50%;
      bottom: 1.25rem;
      transform: translateX(-50%);
      background: var(--panel);
      border: 1px solid var(--border);
      padding: 0.65rem 1rem;
      border-radius: 999px;
      font-size: 0.9rem;
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease;
      z-index: 10;
    }
    .toast.show { opacity: 1; }
  </style>
</head>
<body>
  <header>
    <h1>Jarvis</h1>
    <p>Household timeline, approvals, email, and today’s schedule</p>
  </header>
  <main>
    <div class="toolbar">
      <span id="sync-state" class="status-pill">loading</span>
      <button class="refresh" type="button" id="refresh-btn">Refresh</button>
    </div>

    <section>
      <h2>Pending approvals</h2>
      <div class="panel" id="approvals"><p class="empty">Loading…</p></div>
    </section>

    <section>
      <h2>School events</h2>
      <div class="panel" id="extractions"><p class="empty">Loading…</p></div>
    </section>

    <section>
      <h2>Email summary</h2>
      <div class="panel" id="email-summary"><p class="empty">Loading…</p></div>
    </section>

    <section>
      <h2>Today</h2>
      <div class="panel" id="schedule"><p class="empty">Loading…</p></div>
    </section>

    <section>
      <h2>Timeline</h2>
      <div class="panel" id="timeline"><p class="empty">Loading…</p></div>
    </section>
  </main>
  <div class="toast" id="toast" role="status"></div>
  <script>
    const toastEl = document.getElementById("toast");
    function toast(message) {
      toastEl.textContent = message;
      toastEl.classList.add("show");
      setTimeout(() => toastEl.classList.remove("show"), 2200);
    }

    function todayIso() {
      const now = new Date();
      const offset = now.getTimezoneOffset();
      const local = new Date(now.getTime() - offset * 60000);
      return local.toISOString().slice(0, 10);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    async function loadApprovals() {
      const res = await fetch("/v1/approvals/pending");
      const data = await res.json();
      const root = document.getElementById("approvals");
      const approvals = data.approvals || [];
      if (!approvals.length) {
        root.innerHTML = '<p class="empty">No pending approvals.</p>';
        return;
      }
      root.innerHTML = approvals.map((item) => {
        const params = Object.entries(item.parameters || {})
          .map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v)}`)
          .join(" · ");
        return `
          <div class="item" data-approval="${escapeHtml(item.approval_id)}">
            <p class="title">${escapeHtml(item.intent)}</p>
            <p class="meta">${params || "No details"} · ${escapeHtml(item.created_at)}</p>
            <div class="actions">
              <input type="password" inputmode="numeric" maxlength="8" placeholder="PIN" aria-label="Confirmation PIN" />
              <button class="approve" type="button" data-action="confirm">Approve</button>
              <button class="reject" type="button" data-action="reject">Reject</button>
            </div>
          </div>`;
      }).join("");
    }

    async function loadExtractions() {
      const [pendingRes, statsRes] = await Promise.all([
        fetch("/v1/extractions/pending"),
        fetch("/v1/extractions/stats"),
      ]);
      const pending = await pendingRes.json();
      const stats = await statsRes.json();
      const root = document.getElementById("extractions");
      const rate = stats.acceptance_rate == null
        ? "n/a"
        : `${Math.round(stats.acceptance_rate * 100)}%`;
      const statsLine = `<p class="meta">Accepted ${stats.approved || 0} · Rejected ${stats.rejected || 0} · Rate ${rate}</p>`;
      if (!pending.length) {
        root.innerHTML = `${statsLine}<p class="empty">No school events awaiting review.</p>`;
        return;
      }
      root.innerHTML = statsLine + pending.map((item) => `
        <div class="item" data-extraction="${escapeHtml(item.id)}">
          <p class="title">${escapeHtml(item.title)}</p>
          <p class="meta">${escapeHtml(item.start_time || "time TBD")} · confidence ${escapeHtml(item.confidence)} · ${escapeHtml(item.raw_excerpt || "")}</p>
          <div class="actions">
            <input type="password" inputmode="numeric" maxlength="8" placeholder="PIN" aria-label="Confirmation PIN" />
            <button class="approve" type="button" data-action="accept">Accept</button>
            <button class="reject" type="button" data-action="reject">Reject</button>
          </div>
        </div>
      `).join("");
    }

    async function loadEmailSummary() {
      const res = await fetch("/v1/email/summaries?limit=1");
      const summaries = await res.json();
      const root = document.getElementById("email-summary");
      if (!summaries.length) {
        root.innerHTML = '<p class="empty">No email summaries yet.</p>';
        return;
      }
      const item = summaries[0];
      root.innerHTML = `
        <div class="item">
          <p class="title">${escapeHtml(item.cadence)} · ${escapeHtml(item.message_count)} messages</p>
          <p class="meta">${escapeHtml(item.created_at)}</p>
          <p class="summary-body">${escapeHtml(item.summary_text)}</p>
        </div>`;
    }

    async function loadSchedule() {
      const res = await fetch(`/v1/calendar/day?date=${todayIso()}`);
      const data = await res.json();
      const root = document.getElementById("schedule");
      const events = data.events || [];
      if (!events.length) {
        root.innerHTML = `<p class="empty">Nothing on ${escapeHtml(data.date || "today")}.</p>`;
        return;
      }
      root.innerHTML = events.map((event) => `
        <div class="item">
          <p class="title">${escapeHtml(event.title || "Event")}</p>
          <p class="meta">${escapeHtml(event.start_time || "")} · ${escapeHtml(event.calendar_scope || event.source || "")}</p>
        </div>
      `).join("");
    }

    async function loadTimeline() {
      const res = await fetch("/v1/timeline?limit=30");
      const events = await res.json();
      const root = document.getElementById("timeline");
      if (!events.length) {
        root.innerHTML = '<p class="empty">No recent activity.</p>';
        return;
      }
      root.innerHTML = events.map((event) => `
        <div class="item">
          <p class="title">${escapeHtml(event.action)} → ${escapeHtml(event.outcome)}</p>
          <p class="meta">${escapeHtml(event.source)} · ${escapeHtml(event.actor_id)} · ${escapeHtml(event.created_at)}</p>
        </div>
      `).join("");
    }

    async function refresh() {
      const state = document.getElementById("sync-state");
      state.textContent = "syncing";
      try {
        await Promise.all([
          loadApprovals(),
          loadExtractions(),
          loadEmailSummary(),
          loadSchedule(),
          loadTimeline(),
        ]);
        state.textContent = "live";
      } catch (err) {
        state.textContent = "error";
        toast("Could not refresh dashboard");
      }
    }

    document.getElementById("refresh-btn").addEventListener("click", refresh);

    document.getElementById("approvals").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const item = button.closest("[data-approval]");
      const approvalId = item.dataset.approval;
      const action = button.dataset.action;
      try {
        if (action === "confirm") {
          const pin = item.querySelector('input[type="password"]').value;
          const res = await fetch(`/v1/approvals/${approvalId}/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmation_type: "pin", confirmation_value: pin }),
          });
          if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            throw new Error(detail.detail || "Approval failed");
          }
          toast("Approved");
        } else {
          const res = await fetch(`/v1/approvals/${approvalId}/reject`, { method: "POST" });
          if (!res.ok) throw new Error("Reject failed");
          toast("Rejected");
        }
        await refresh();
      } catch (err) {
        toast(err.message || "Action failed");
      }
    });

    document.getElementById("extractions").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const item = button.closest("[data-extraction]");
      const extractionId = item.dataset.extraction;
      const action = button.dataset.action;
      try {
        if (action === "accept") {
          const pin = item.querySelector('input[type="password"]').value;
          const res = await fetch(`/v1/extractions/${extractionId}/accept`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmation_type: "pin", confirmation_value: pin }),
          });
          if (!res.ok) {
            const detail = await res.json().catch(() => ({}));
            throw new Error(detail.detail || "Accept failed");
          }
          toast("School event accepted");
        } else {
          const res = await fetch(`/v1/extractions/${extractionId}/reject`, { method: "POST" });
          if (!res.ok) throw new Error("Reject failed");
          toast("School event rejected");
        }
        await refresh();
      } catch (err) {
        toast(err.message || "Action failed");
      }
    });

    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""
