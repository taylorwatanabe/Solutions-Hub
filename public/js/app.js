const STATUSES = [
  "Received",
  "In Review",
  "In Progress / Pilots",
  "Implemented / Wins",
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
};

let currentCard = null;
let lastBoard = null;

function truncate(text, n = 140) {
  const s = String(text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

async function api(path, options = {}) {
  const { retry = true, headers: extraHeaders, ...fetchOptions } = options;
  const maxAttempts = retry === false ? 1 : 3;
  let lastErr = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
        ...fetchOptions,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data.error || `Request failed (${res.status})`;
        if ((res.status === 502 || res.status === 503 || res.status === 504) && attempt < maxAttempts) {
          await new Promise((r) => setTimeout(r, 700 * attempt));
          continue;
        }
        throw new Error(msg);
      }
      return data;
    } catch (err) {
      lastErr = err;
      const isHttpErr = err instanceof Error && /Request failed \(\d+\)/.test(err.message);
      if (!isHttpErr && attempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, 700 * attempt));
        continue;
      }
      throw err;
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

  els.deptSuggestions.innerHTML = entries
    .map(([name]) => `<option value="${escapeAttr(name)}"></option>`)
    .join("");
}

function renderBoard(board) {
  lastBoard = board;
  const columns = board.columns || {};
  const monthKeys =
    board.months && board.months.length
      ? board.months
      : Object.keys(columns);
  els.kanban.innerHTML = monthKeys
    .map((month) => {
      const cards = columns[month] || [];
      const cardsHtml = cards
        .map((card) => {
          const statusLabel = card.status || "Received";
          return `
            <button type="button" class="card" data-id="${escapeAttr(card.id)}" role="listitem">
              <p class="card-dept">${escapeHtml(card.department || "Unspecified")}</p>
              <p class="card-problem">${escapeHtml(truncate(card.problem, 160))}</p>
              <div class="card-meta">
                <span>Status: ${escapeHtml(statusLabel)}</span>
                <span>▲ ${escapeHtml(String(card.upvotes ?? 0))}</span>
              </div>
            </button>`;
        })
        .join("");
      return `
        <section class="column" aria-label="${escapeAttr(month)}">
          <div class="column-header">
            <h2>${escapeHtml(month)}</h2>
            <span class="count">${cards.length}</span>
          </div>
          <div class="column-body">${cardsHtml || `<p class="card-meta">No cards</p>`}</div>
        </section>`;
    })
    .join("");

  renderDeptControls(board.department_counts || {});
}

async function loadBoard() {
  els.boardError.hidden = true;
  const dept = els.deptFilter.value;
  const qs = dept ? `?department=${encodeURIComponent(dept)}` : "";
  try {
    const board = await api(`/api/board${qs}`);
    renderBoard(board);
  } catch (err) {
    els.boardError.hidden = false;
    els.boardError.textContent = err.message || String(err);
  }
}

function findCard(id) {
  if (!lastBoard?.columns) return null;
  for (const cards of Object.values(lastBoard.columns)) {
    const hit = (cards || []).find((c) => c.id === id);
    if (hit) return hit;
  }
  return null;
}

function openCard(id, cardOverride = null) {
  const card = cardOverride || findCard(id);
  if (!card) return;
  currentCard = card;
  els.dialogTitle.textContent = truncate(card.problem, 80) || "Submission";
  const fields = [
    ["Submitter", `${card.submitter_name || ""} ${card.submitter_email ? `<${card.submitter_email}>` : ""}`.trim()],
    ["Submitted", card.submitted_at || ""],
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

if (els.refresh) els.refresh.addEventListener("click", () => loadBoard());
if (els.deptFilter) els.deptFilter.addEventListener("change", () => loadBoard());

els.kanban.addEventListener("click", (event) => {
  const card = event.target.closest(".card");
  if (!card) return;
  openCard(card.dataset.id);
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
    await loadBoard();
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
    await loadBoard();
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
      await loadBoard();
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
