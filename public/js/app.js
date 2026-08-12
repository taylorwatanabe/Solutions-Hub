const STATUSES = [
  "Received",
  "In Review",
  "In Progress / Pilots",
  "Completed",
  "NA / Archived",
];

const els = {
  tabs: document.querySelectorAll(".tab"),
  viewBoard: document.getElementById("view-board"),
  viewIntake: document.getElementById("view-intake"),
  kanban: document.getElementById("kanban"),
  deptFilter: document.getElementById("department-filter"),
  deptChips: document.getElementById("dept-chips"),
  deptSuggestions: document.getElementById("dept-suggestions"),
  refresh: document.getElementById("refresh-board"),
  boardError: document.getElementById("board-error"),
  intakeForm: document.getElementById("intake-form"),
  intakeStatus: document.getElementById("intake-status"),
  intakeSubmit: document.getElementById("intake-submit"),
  dialog: document.getElementById("card-dialog"),
  dialogTitle: document.getElementById("dialog-title"),
  dialogBody: document.getElementById("dialog-body"),
  dialogStatus: document.getElementById("dialog-status"),
  dialogDepartment: document.getElementById("dialog-department"),
  dialogNotes: document.getElementById("dialog-notes"),
  dialogLead: document.getElementById("dialog-lead"),
  dialogSave: document.getElementById("dialog-save"),
  dialogUpvote: document.getElementById("dialog-upvote"),
  drilldown: document.getElementById("drilldown"),
  drilldownTitle: document.getElementById("drilldown-title"),
  drilldownMeta: document.getElementById("drilldown-meta"),
  drilldownBody: document.getElementById("drilldown-body"),
  drilldownClose: document.getElementById("drilldown-close"),
};

let currentCard = null;
let lastBoard = null;
/** @type {Record<string, boolean>} */
const completedVisibleByMonth = {};

function truncate(text, n = 140) {
  const s = String(text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

function dateOnly(value) {
  const s = String(value || "").trim();
  if (!s) return "";
  const iso = s.match(/^(\d{4}-\d{2}-\d{2})/);
  if (iso) return iso[1];
  const us = s.match(/^(\d{1,2}\/\d{1,2}\/\d{4})/);
  if (us) return us[1];
  return s;
}

async function api(path, options = {}) {
  const {
    retry = true,
    headers: extraHeaders,
    timeoutMs = 45000,
    maxAttempts: maxAttemptsOpt,
    ...fetchOptions
  } = options;
  const maxAttempts = maxAttemptsOpt ?? (retry === false ? 1 : 2);
  let lastErr = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
        signal: controller.signal,
        ...fetchOptions,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data.error || `Request failed (${res.status})`;
        if ((res.status === 502 || res.status === 503 || res.status === 504) && attempt < maxAttempts) {
          await new Promise((r) => setTimeout(r, 500 * attempt));
          continue;
        }
        throw new Error(msg);
      }
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") {
        lastErr = new Error("Board request timed out waiting for Google Sheets. Click Refresh to try again.");
      } else {
        lastErr = err;
      }
      const isHttpErr = lastErr instanceof Error && /Request failed \(\d+\)/.test(lastErr.message);
      if (!isHttpErr && attempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, 500 * attempt));
        continue;
      }
      throw lastErr;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr || new Error("Request failed");
}

function setView(name) {
  els.tabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.view === name);
  });
  const board = name === "board";
  els.viewBoard.hidden = !board;
  els.viewBoard.classList.toggle("is-active", board);
  els.viewIntake.hidden = board;
  els.viewIntake.classList.toggle("is-active", !board);
}

function fillStatusSelect(select, selected) {
  select.innerHTML = STATUSES.map(
    (s) => `<option value="${escapeAttr(s)}" ${s === selected ? "selected" : ""}>${escapeHtml(s)}</option>`
  ).join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

function renderDeptControls(departmentCounts) {
  const entries = Object.entries(departmentCounts || {});
  const selected = els.deptFilter.value;
  els.deptFilter.innerHTML =
    `<option value="">All departments</option>` +
    entries
      .map(
        ([name, count]) =>
          `<option value="${escapeAttr(name)}" ${name === selected ? "selected" : ""}>${escapeHtml(name)} (${count})</option>`
      )
      .join("");

  els.deptChips.innerHTML = entries
    .slice(0, 12)
    .map(
      ([name, count]) =>
        `<span class="chip"><strong>${escapeHtml(String(count))}</strong> ${escapeHtml(name)}</span>`
    )
    .join("");

  if (els.deptSuggestions) {
    els.deptSuggestions.innerHTML = entries
      .map(([name]) => `<option value="${escapeAttr(name)}"></option>`)
      .join("");
  }
}

function deptTileVariant(name) {
  const n = String(name || "")
    .trim()
    .toUpperCase()
    .replace(/\s+/g, " ");
  if (n === "D&E" || n === "D AND E" || n === "DE") return "dept-tile--de";
  if (n === "PE") return "dept-tile--pe";
  return "dept-tile--default";
}

function countByDepartment(cards) {
  const counts = {};
  for (const card of cards || []) {
    const name = String(card.department || "").trim() || "Unspecified";
    counts[name] = (counts[name] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

function cardHtml(card) {
  const statusLabel = card.status || "Received";
  const when = dateOnly(card.display_date || card.submitted_at || card.problem);
  return `
    <button type="button" class="card" data-id="${escapeAttr(card.id)}" role="listitem">
      <p class="card-dept">${escapeHtml(card.department || "Unspecified")}</p>
      <p class="card-problem">${escapeHtml(when || "—")}</p>
      <div class="card-meta">
        <span>Status: ${escapeHtml(statusLabel)}</span>
        <span>▲ ${escapeHtml(String(card.upvotes ?? 0))}</span>
      </div>
    </button>`;
}

function cellDeptTilesHtml(monthLabel, status, cards) {
  const tiles = countByDepartment(cards);
  if (!tiles.length) {
    return `<p class="card-meta">No cards</p>`;
  }
  return tiles
    .map(
      (t) => `
      <button
        type="button"
        class="dept-tile ${deptTileVariant(t.name)}"
        data-month="${escapeAttr(monthLabel)}"
        data-status="${escapeAttr(status)}"
        data-department="${escapeAttr(t.name)}"
      >
        ${escapeHtml(t.name)} <span class="dept-tile-count">[${escapeHtml(String(t.count))}]</span>
      </button>`
    )
    .join("");
}

function renderMonthRow(month, board) {
  const label = month.label || "Unknown date";
  const showCompleted = Boolean(completedVisibleByMonth[label]);
  const completedStatuses = board.completed_statuses || ["Completed"];
  const activeStatuses =
    board.active_statuses ||
    (board.statuses || STATUSES).filter((s) => !completedStatuses.includes(s));
  const visibleStatuses = showCompleted
    ? board.statuses || STATUSES
    : activeStatuses;

  const completedCount = Number(month.completed_count || 0);
  const visibleTotal = showCompleted
    ? Number(month.total || 0)
    : Math.max(0, Number(month.total || 0) - completedCount);
  const toggleLabel = showCompleted
    ? `Hide completed (${completedCount})`
    : `Show completed (${completedCount})`;

  const columns = month.columns || {};
  const columnsHtml = visibleStatuses
    .map((status) => {
      let cards = columns[status] || [];
      if (!showCompleted) {
        cards = cards.filter((c) => !c.is_completed && !completedStatuses.includes(c.status));
      }
      return `
        <section class="column" aria-label="${escapeAttr(status)}">
          <div class="column-header">
            <h2>${escapeHtml(status)}</h2>
            <span class="count">${cards.length}</span>
          </div>
          <div class="column-body column-body--tiles">${cellDeptTilesHtml(label, status, cards)}</div>
        </section>`;
    })
    .join("");

  return `
    <section class="month-row" data-month="${escapeAttr(label)}">
      <aside class="month-rail">
        <div class="month-row-title">
          <h2>${escapeHtml(label)}</h2>
          <span class="count">${escapeHtml(String(visibleTotal))}</span>
        </div>
        <button type="button" class="btn ghost completed-toggle" data-month="${escapeAttr(label)}" aria-pressed="${showCompleted ? "true" : "false"}">
          ${escapeHtml(toggleLabel)}
        </button>
      </aside>
      <div class="month-kanban">${columnsHtml}</div>
    </section>`;
}

function renderBoard(board) {
  lastBoard = board;
  const months = board.months || [];
  els.kanban.className = "board-months";
  els.kanban.innerHTML = months.length
    ? months.map((m) => renderMonthRow(m, board)).join("")
    : `<p class="board-loading">No submissions found.</p>`;
  renderDeptControls(board.department_counts || {});
}

async function loadBoard({ refresh = false } = {}) {
  closeDrilldown();
  els.boardError.hidden = true;
  els.kanban.className = "board-months";
  els.kanban.innerHTML = `<p class="board-loading">${refresh ? "Refreshing from Google Sheets…" : "Loading board…"}</p>`;
  const dept = els.deptFilter.value;
  const params = new URLSearchParams();
  if (dept) params.set("department", dept);
  if (refresh) params.set("refresh", "1");
  const qs = params.toString() ? `?${params.toString()}` : "";
  try {
    const board = await api(`/api/board${qs}`, { timeoutMs: 25000, maxAttempts: 2 });
    if (!board.months?.length) {
      els.kanban.innerHTML = "";
      els.boardError.hidden = false;
      els.boardError.textContent = "No submissions found for this filter.";
      return;
    }
    renderBoard(board);
  } catch (err) {
    els.kanban.innerHTML = "";
    els.boardError.hidden = false;
    els.boardError.textContent = err.message || String(err);
  }
}

function findCard(id) {
  if (!lastBoard?.months) return null;
  for (const month of lastBoard.months) {
    for (const cards of Object.values(month.columns || {})) {
      const hit = (cards || []).find((c) => c.id === id);
      if (hit) return hit;
    }
  }
  return null;
}

function cardsForCriteria({ month, status, department }) {
  if (!lastBoard?.months) return [];
  const row = lastBoard.months.find((m) => (m.label || "Unknown date") === month);
  if (!row) return [];
  const completedStatuses = lastBoard.completed_statuses || ["Completed"];
  const showCompleted = Boolean(completedVisibleByMonth[month]);
  let cards = row.columns?.[status] || [];
  if (!showCompleted) {
    cards = cards.filter((c) => !c.is_completed && !completedStatuses.includes(c.status));
  }
  const dept = String(department || "").trim();
  return cards.filter((c) => (String(c.department || "").trim() || "Unspecified") === dept);
}

function openDrilldown({ month, status, department }) {
  if (!els.drilldown) return;
  const cards = cardsForCriteria({ month, status, department });
  els.drilldownTitle.textContent = `${month} · ${status} · ${department}`;
  els.drilldownMeta.textContent = `${cards.length} card${cards.length === 1 ? "" : "s"}`;
  els.drilldownBody.innerHTML = cards.length
    ? `<div class="drilldown-grid">${cards.map(cardHtml).join("")}</div>`
    : `<p class="board-loading">No cards match this filter.</p>`;
  els.drilldown.hidden = false;
  document.body.classList.add("drilldown-open");
  els.drilldownClose?.focus();
}

function closeDrilldown() {
  if (!els.drilldown || els.drilldown.hidden) return;
  els.drilldown.hidden = true;
  document.body.classList.remove("drilldown-open");
  if (els.drilldownBody) els.drilldownBody.innerHTML = "";
}

function openCard(id, cardOverride = null) {
  const card = cardOverride || findCard(id);
  if (!card) return;
  currentCard = card;
  const title = dateOnly(card.display_date || card.submitted_at || card.problem) || truncate(card.problem, 80) || "Submission";
  els.dialogTitle.textContent = title;
  const fields = [
    ["Submitter", `${card.submitter_name || ""} ${card.submitter_email ? `<${card.submitter_email}>` : ""}`.trim()],
    ["Submitted", dateOnly(card.display_date || card.submitted_at || card.problem) || ""],
    ["Department", card.department || ""],
    ["Status", card.status || ""],
    ["Problem", card.problem || ""],
    ["Option A (Quick Win)", card.option_a || ""],
    ["Option B (Structural)", card.option_b || ""],
    ["Option C (Innovative)", card.option_c || ""],
    ["Recommendation", card.recommendation || ""],
    ["Resources", card.resources || ""],
    ["Estimated value", card.estimated_value || ""],
    ["Upvotes", String(card.upvotes ?? 0)],
  ];
  els.dialogBody.innerHTML = `<dl>${fields
    .map(
      ([k, v]) =>
        `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v || "—")}</dd></div>`
    )
    .join("")}</dl>`;
  fillStatusSelect(els.dialogStatus, card.status || "Received");
  els.dialogDepartment.value = card.department || "";
  els.dialogNotes.value = card.coaching_notes || "";
  els.dialogLead.value = card.sprint_lead || "";
  els.dialog.showModal();
}

els.tabs.forEach((tab) => {
  tab.addEventListener("click", () => setView(tab.dataset.view));
});

if (els.refresh) els.refresh.addEventListener("click", () => loadBoard({ refresh: true }));
if (els.deptFilter) els.deptFilter.addEventListener("change", () => loadBoard());

els.kanban.addEventListener("click", (event) => {
  const toggle = event.target.closest(".completed-toggle");
  if (toggle) {
    const month = toggle.dataset.month;
    completedVisibleByMonth[month] = !completedVisibleByMonth[month];
    if (lastBoard) renderBoard(lastBoard);
    return;
  }
  const tile = event.target.closest(".dept-tile");
  if (tile) {
    openDrilldown({
      month: tile.dataset.month,
      status: tile.dataset.status,
      department: tile.dataset.department,
    });
    return;
  }
});

if (els.drilldown) {
  els.drilldown.addEventListener("click", (event) => {
    if (event.target === els.drilldown || event.target.closest("#drilldown-close")) {
      closeDrilldown();
      return;
    }
    const card = event.target.closest(".card");
    if (card) openCard(card.dataset.id);
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && els.drilldown && !els.drilldown.hidden) {
    if (els.dialog?.open) return;
    closeDrilldown();
  }
});

els.dialogSave.addEventListener("click", async () => {
  if (!currentCard) return;
  els.dialogSave.disabled = true;
  try {
    const { submission } = await api(`/api/submissions/${encodeURIComponent(currentCard.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        status: els.dialogStatus.value,
        department: els.dialogDepartment.value,
        coaching_notes: els.dialogNotes.value,
        sprint_lead: els.dialogLead.value,
      }),
    });
    currentCard = submission;
    els.dialog.close();
    await loadBoard({ refresh: true });
  } catch (err) {
    alert(err.message || String(err));
  } finally {
    els.dialogSave.disabled = false;
  }
});

els.dialogUpvote.addEventListener("click", async () => {
  if (!currentCard) return;
  els.dialogUpvote.disabled = true;
  try {
    const { submission } = await api(
      `/api/submissions/${encodeURIComponent(currentCard.id)}/upvote`,
      { method: "POST", body: "{}" }
    );
    currentCard = submission;
    await loadBoard({ refresh: true });
    openCard(submission.id, submission);
  } catch (err) {
    alert(err.message || String(err));
  } finally {
    els.dialogUpvote.disabled = false;
  }
});

if (els.intakeForm) {
  els.intakeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    els.intakeStatus.textContent = "";
    els.intakeStatus.className = "form-status";
    const fd = new FormData(els.intakeForm);
    const payload = Object.fromEntries(fd.entries());
    els.intakeSubmit.disabled = true;
    try {
      const result = await api("/api/submissions", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      els.intakeForm.reset();
      els.intakeStatus.classList.add("ok");
      els.intakeStatus.textContent = `Submitted (${result.submission.id}). 30-day SLA receipt ${
        result.sla?.sent ? "sent" : "queued (stub for Current)"
      }.`;
      setView("board");
      await loadBoard({ refresh: true });
    } catch (err) {
      els.intakeStatus.classList.add("err");
      els.intakeStatus.textContent = err.message || String(err);
    } finally {
      els.intakeSubmit.disabled = false;
    }
  });
}

fillStatusSelect(els.dialogStatus, "Received");
loadBoard();
