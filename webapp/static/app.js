const $ = (sel) => document.querySelector(sel);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.classList.toggle("active", p.id === `panel-${name}`);
  });
  if (name === "review") {
    loadReview();
    loadCustomRules();
  }
  if (name === "settings") loadSettings();
  if (name === "chat") loadChatHistory();
  if (name === "edit") loadTransactionEditor();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

async function loadStatus() {
  const s = await api("/api/status");
  const provider = s.llm_provider ? `${s.llm_provider} · ` : "";
  let modelLabel = s.chat_model || s.llm_model || "";
  if (s.pipeline_model && s.chat_model && s.pipeline_model !== s.chat_model) {
    modelLabel = `process ${s.pipeline_model} · chat ${s.chat_model}`;
  }
  $("#status-line").textContent =
    `${s.transaction_count} transactions · ${s.review_merchant_count} merchants need review · ${provider}${modelLabel}`;
  $("#inbox-path").textContent = s.inbox_dir;
  const processedEl = $("#processed-path");
  if (processedEl && s.processed_dir) {
    processedEl.textContent = s.processed_dir;
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function renderMarkdown(text) {
  if (!text) return "";
  if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
    const raw = marked.parse(text, { breaks: true, gfm: true });
    return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function mountTableDisplay(container, display) {
  if (!display || display.type !== "table" || typeof Tabulator === "undefined") return;

  const toolbar = document.createElement("div");
  toolbar.className = "chat-display-toolbar";
  const csvBtn = document.createElement("button");
  csvBtn.type = "button";
  csvBtn.className = "btn-chat-export";
  csvBtn.textContent = "Download CSV";
  toolbar.appendChild(csvBtn);
  container.appendChild(toolbar);

  const wrap = document.createElement("div");
  wrap.className = "chat-table-wrap";
  container.appendChild(wrap);

  const columns = (display.columns || []).map((col) => {
    const def = { title: col.title || col.field, field: col.field, headerSort: true };
    if (col.formatter === "money" || col.field === "amount_display") {
      def.hozAlign = "right";
      def.sorter = "number";
    }
    return def;
  });

  const slug = (display.title || "export").replace(/[^\w-]+/g, "_").slice(0, 40);
  const wideTable = columns.length > 5;
  if (wideTable) wrap.classList.add("chat-table-wrap-wide");
  const table = new Tabulator(wrap, {
    data: display.rows || [],
    columns,
    layout: wideTable ? "fitData" : "fitColumns",
    height: Math.min(420, 42 + (display.rows || []).length * 32),
    pagination: (display.rows || []).length > 25 ? "local" : false,
    paginationSize: 25,
    placeholder: "No rows",
  });
  wrap._tabulator = table;
  csvBtn.addEventListener("click", () => {
    table.download("csv", `${slug || "data"}.csv`);
  });
}

function mountChartDisplay(container, display) {
  if (!display || display.type !== "chart" || typeof Chart === "undefined") return;
  const wrap = document.createElement("div");
  wrap.className = "chat-chart-wrap";
  const canvas = document.createElement("canvas");
  wrap.appendChild(canvas);
  container.appendChild(wrap);

  const accent = "rgba(61, 139, 253, 0.75)";
  const accentBorder = "rgba(61, 139, 253, 1)";
  const datasets = (display.datasets || []).map((ds, i) => ({
    label: ds.label || "Total",
    data: ds.data || [],
    backgroundColor: i === 0 ? accent : `rgba(120, 180, 255, 0.55)`,
    borderColor: i === 0 ? accentBorder : "rgba(120, 180, 255, 1)",
    borderWidth: 1,
  }));

  const chartType = display.chartType === "line" ? "line" : "bar";
  if (chartType === "line") {
    datasets.forEach((ds) => {
      ds.fill = false;
      ds.tension = 0.25;
    });
  }

  wrap._chart = new Chart(canvas, {
    type: chartType,
    data: {
      labels: display.labels || [],
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: "#e8eaed", boxWidth: 12 },
        },
      },
      scales: {
        x: {
          ticks: { color: "#9aa0a6", maxRotation: 45, minRotation: 0 },
          grid: { color: "rgba(255, 255, 255, 0.06)" },
        },
        y: {
          ticks: {
            color: "#9aa0a6",
            callback: (v) => (typeof v === "number" ? `$${v.toLocaleString()}` : v),
          },
          grid: { color: "rgba(255, 255, 255, 0.06)" },
        },
      },
    },
  });
}

function mountDisplay(container, display) {
  if (!display) return;
  if (display.type === "table") mountTableDisplay(container, display);
  else if (display.type === "chart") mountChartDisplay(container, display);
}

function appendToolTrace(parent, toolTrace) {
  if (!toolTrace || !toolTrace.length) return;
  const details = document.createElement("details");
  details.className = "tool-trace";
  const summary = document.createElement("summary");
  summary.textContent = `Tools used (${toolTrace.length})`;
  details.appendChild(summary);
  const pre = document.createElement("pre");
  pre.textContent = toolTrace
    .map((x) => {
      const args = x.args && Object.keys(x.args).length ? ` ${JSON.stringify(x.args)}` : "";
      return `${x.tool}${args}`;
    })
    .join("\n");
  details.appendChild(pre);
  parent.appendChild(details);
}

function appendCadenceProposalAction(parent, proposal) {
  if (!proposal || !proposal.insight) return;
  const wrap = document.createElement("div");
  wrap.className = "chat-cadence-proposal";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-cadence-proposal";
  btn.textContent = proposal.recommend_save_rule
    ? "Review & save cadence"
    : "View cadence suggestion";
  btn.addEventListener("click", () => openCadenceInsightModal(proposal));
  wrap.appendChild(btn);
  parent.appendChild(wrap);
}

function appendChat(role, text, toolTrace, display, cadenceProposal) {
  const log = $("#chat-log");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const label = role === "user" ? "You" : "Assistant";
  const strong = document.createElement("strong");
  strong.textContent = label;
  div.appendChild(strong);

  const body = document.createElement("div");
  body.className = "msg-body";
  if (role === "assistant") {
    body.classList.add("markdown-body");
    body.innerHTML = renderMarkdown(text);
    if (display) mountDisplay(body, display);
    if (cadenceProposal) appendCadenceProposalAction(body, cadenceProposal);
    appendToolTrace(body, toolTrace);
  } else {
    body.textContent = text;
  }
  div.appendChild(body);
  log.scrollTop = log.scrollHeight;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function appendChatPending() {
  const log = $("#chat-log");
  const div = document.createElement("div");
  div.className = "msg assistant pending";
  div.dataset.pending = "1";
  div.innerHTML = "<strong>Assistant</strong><div class=\"msg-body\">Thinking…</div>";
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function removeChatPending() {
  const el = $("#chat-log .msg.pending[data-pending='1']");
  if (el) el.remove();
}

let chatBusy = false;
let chatHistoryLoaded = false;
let chatHistoryPromise = null;

async function loadChatHistory() {
  if (chatHistoryLoaded) return;
  if (chatHistoryPromise) return chatHistoryPromise;
  const log = $("#chat-log");
  if (!log) return;

  chatHistoryPromise = (async () => {
    try {
      const res = await api("/api/chat/history");
      if (chatHistoryLoaded) return;
      const messages = res.messages || [];
      log.innerHTML = "";
      messages.forEach((m) => {
        appendChat(m.role, m.content, m.tool_trace, m.display, m.cadence_proposal);
      });
      log.scrollTop = log.scrollHeight;
    } catch (_) {
      /* history optional on first load */
    } finally {
      chatHistoryLoaded = true;
      chatHistoryPromise = null;
    }
  })();
  return chatHistoryPromise;
}

const CHAT_HELP_COMMANDS = [
  {
    label: "Months in DB",
    text: "What months do I have in the database?",
    description: "Lists every budget month in SQLite with transaction counts (full vs partial months).",
  },
  {
    label: "Last month spend",
    text: "How much did I spend last month?",
    description: "Total expenses for the latest full month in your data.",
  },
  {
    label: "Spending by month",
    text: "Show my spending by month",
    description: "Expense totals for each full month — good for trends over time.",
  },
  {
    label: "Income by month",
    text: "Show my income by month",
    description: "Income totals per month (respects paycheck spillover in budget_month).",
  },
  {
    label: "Top categories",
    text: "What are my top spending categories for April 2026?",
    description: "Ranks categories by spend for one month. Change the month in the inserted text.",
  },
  {
    label: "Category transactions",
    text: "Show all Insurance transactions for April 2026",
    description: "Lists individual transactions for a category and month. Change category/month as needed.",
  },
  {
    label: "Vs average",
    text: "How does April 2026 spending compare to my average?",
    description: "Compares one month's total expenses to your historical monthly average.",
  },
  {
    label: "Unusual spend",
    text: "What spending categories were unusually high in April 2026?",
    description: "Flags categories that spiked vs their own monthly average (outliers).",
  },
  {
    label: "Saved reports",
    text: "Show my custom reports",
    description: "Lists SQL reports you saved. Ask to run one by name after inserting.",
  },
];

function insertChatCommand(text) {
  const input = $("#chat-input");
  if (!input || chatBusy) return;
  input.value = text;
  input.focus();
  input.setSelectionRange(text.length, text.length);
}

function setChatHelpOpen(open) {
  const layout = $("#chat-layout");
  const panel = $("#chat-help-panel");
  const helpBtn = $("#btn-chat-help");
  if (!layout || !panel || !helpBtn) return;
  layout.classList.toggle("help-open", open);
  panel.classList.toggle("hidden", !open);
  panel.setAttribute("aria-hidden", open ? "false" : "true");
  helpBtn.classList.toggle("active", open);
  helpBtn.setAttribute("aria-expanded", open ? "true" : "false");
}

function renderChatHelpList() {
  const list = $("#chat-help-list");
  if (!list) return;
  list.innerHTML = "";
  CHAT_HELP_COMMANDS.forEach((cmd) => {
    const li = document.createElement("li");
    li.className = "chat-help-item";

    const row = document.createElement("div");
    row.className = "chat-help-row";

    const insertBtn = document.createElement("button");
    insertBtn.type = "button";
    insertBtn.className = "btn-chat-help-insert";
    insertBtn.title = "Insert into chat";
    insertBtn.setAttribute("aria-label", `Insert: ${cmd.label}`);
    insertBtn.textContent = "+";
    insertBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      insertChatCommand(cmd.text);
    });

    const labelBtn = document.createElement("button");
    labelBtn.type = "button";
    labelBtn.className = "btn-chat-help-label";
    labelBtn.textContent = cmd.label;
    labelBtn.addEventListener("click", () => {
      const wasOpen = li.classList.contains("open");
      list.querySelectorAll(".chat-help-item.open").forEach((el) => el.classList.remove("open"));
      if (!wasOpen) li.classList.add("open");
    });

    const desc = document.createElement("div");
    desc.className = "chat-help-desc";
    desc.innerHTML = `${escapeHtml(cmd.description)}<br><code>${escapeHtml(cmd.text)}</code>`;

    row.appendChild(insertBtn);
    row.appendChild(labelBtn);
    li.appendChild(row);
    li.appendChild(desc);
    list.appendChild(li);
  });
}

function syncChatHelpInsertButtons() {
  document.querySelectorAll(".btn-chat-help-insert").forEach((btn) => {
    btn.disabled = chatBusy;
  });
}

(function initChatHelp() {
  const helpBtn = $("#btn-chat-help");
  const closeBtn = $("#btn-chat-help-close");
  if (!helpBtn) return;
  renderChatHelpList();
  helpBtn.addEventListener("click", () => {
    const panel = $("#chat-help-panel");
    setChatHelpOpen(panel?.classList.contains("hidden"));
  });
  closeBtn?.addEventListener("click", () => setChatHelpOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setChatHelpOpen(false);
  });
})();

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (chatBusy) return;
  await loadChatHistory();
  const input = $("#chat-input");
  const sendBtn = $("#btn-chat-send");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  chatBusy = true;
  if (sendBtn) sendBtn.disabled = true;
  syncChatHelpInsertButtons();
  appendChat("user", msg);
  appendChatPending();
  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: msg }),
    });
    removeChatPending();
    appendChat("assistant", res.answer, res.tool_trace, res.display, res.cadence_proposal);
  } catch (err) {
    removeChatPending();
    appendChat("assistant", `Error: ${err.message}`);
  } finally {
    chatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
    syncChatHelpInsertButtons();
    input.focus();
  }
});

(function initChatMic() {
  const micBtn = $("#btn-chat-mic");
  const input = $("#chat-input");
  if (!micBtn || !input) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    micBtn.disabled = true;
    micBtn.title = "Voice input not supported in this browser";
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = "en-US";
  let listening = false;

  recognition.onresult = (event) => {
    let transcript = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      transcript += event.results[i][0].transcript;
    }
    input.value = transcript.trim();
  };

  recognition.onend = () => {
    listening = false;
    micBtn.classList.remove("listening");
    micBtn.textContent = "Mic";
  };

  recognition.onerror = () => {
    listening = false;
    micBtn.classList.remove("listening");
    micBtn.textContent = "Mic";
  };

  micBtn.addEventListener("click", () => {
    if (chatBusy) return;
    if (listening) {
      recognition.stop();
      return;
    }
    try {
      recognition.start();
      listening = true;
      micBtn.classList.add("listening");
      micBtn.textContent = "Stop";
    } catch (_) {
      /* already started */
    }
  });
})();

let reviewOptionsCache = null;

function ensureReviewDatalists(options) {
  const host = $("#review-datalists");
  if (!host) return;
  const catId = "review-categories";
  const subId = "review-subcategories";
  host.innerHTML = `
    <datalist id="${catId}">${options.categories.map((c) => `<option value="${escapeAttr(c)}"></option>`).join("")}</datalist>
    <datalist id="${subId}">${options.sub_categories.map((c) => `<option value="${escapeAttr(c)}"></option>`).join("")}</datalist>
  `;
}

function reviewField(name, label, tooltip, controlHtml) {
  return `
    <div class="review-field">
      <label for="${name}" title="${escapeAttr(tooltip)}">
        <span>${escapeHtml(label)}</span>
        <span class="field-tip" title="${escapeAttr(tooltip)}" aria-hidden="true">?</span>
      </label>
      ${controlHtml}
    </div>
  `;
}

function formatMoney(amount) {
  const n = Number(amount);
  if (Number.isNaN(n)) return String(amount ?? "");
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return n < 0 ? `-$${abs}` : `$${abs}`;
}

function renderTransactionRows(transactions) {
  if (!transactions.length) {
    return '<p class="review-tx-empty">No transactions found.</p>';
  }
  const rows = transactions
    .map((tx) => {
      const desc =
        tx.simple_description ||
        tx.user_description ||
        tx.original_description ||
        "—";
      const extra = [
        tx.ai_category ? `AI: ${tx.ai_category}${tx.ai_sub_category ? ` / ${tx.ai_sub_category}` : ""}` : "",
        tx.source_category ? `Bank: ${tx.source_category}` : "",
        tx.account_name ? `Account: ${tx.account_name}` : "",
        tx.classification ? `Class: ${tx.classification}` : "",
        tx.budget_month ? `Month: ${tx.budget_month}` : "",
        tx.label_status ? `Status: ${tx.label_status}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      return `
        <tr>
          <td>${escapeHtml(tx.date || "")}</td>
          <td class="amount">${escapeHtml(formatMoney(tx.amount))}</td>
          <td>${escapeHtml(desc)}</td>
          <td class="tx-extra">${escapeHtml(extra || "—")}</td>
        </tr>
      `;
    })
    .join("");
  return `
    <div class="review-tx-scroll">
      <table class="review-tx-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Amount</th>
            <th>Description</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function toggleReviewTransactions(btn, merchantKey, panel) {
  const expanded = btn.getAttribute("aria-expanded") === "true";
  if (expanded) {
    btn.setAttribute("aria-expanded", "false");
    btn.title = "Show transactions in this group";
    panel.classList.add("hidden");
    return;
  }
  btn.setAttribute("aria-expanded", "true");
  btn.title = "Hide transactions";
  panel.classList.remove("hidden");
  if (panel.dataset.loaded === "1") return;

  panel.innerHTML = '<p class="review-tx-loading">Loading transactions…</p>';
  try {
    const txs = await api(`/api/review/${encodeURIComponent(merchantKey)}/transactions`);
    panel.innerHTML = renderTransactionRows(txs);
    panel.dataset.loaded = "1";
  } catch (err) {
    panel.innerHTML = `<p class="review-tx-empty">Error: ${escapeHtml(err.message)}</p>`;
  }
}

function buildSelectOptions(values, selected, { allowEmpty = false } = {}) {
  const opts = allowEmpty ? [`<option value="">— choose —</option>`] : [];
  const seen = new Set();
  if (selected && !values.includes(selected)) {
    opts.push(`<option value="${escapeAttr(selected)}" selected>${escapeHtml(selected)}</option>`);
    seen.add(selected);
  }
  for (const v of values) {
    if (seen.has(v)) continue;
    seen.add(v);
    const sel = v === selected ? " selected" : "";
    opts.push(`<option value="${escapeAttr(v)}"${sel}>${escapeHtml(v)}</option>`);
  }
  return opts.join("");
}

let customRulesExpanded = false;

function renderCustomRulesList(rules) {
  const list = $("#custom-rules-list");
  const wrap = $("#custom-rules-list-wrap");
  const toggle = $("#btn-custom-rules-toggle");
  if (!list) return;

  if (!rules.length) {
    list.innerHTML = '<li class="custom-rules-empty">No custom rules yet.</li>';
    toggle?.classList.add("hidden");
    return;
  }

  const visible = customRulesExpanded ? rules : rules.slice(0, 5);

  list.innerHTML = visible
    .map((r) => {
      const status = (r.status || "Pending").toLowerCase();
      const err = r.last_error
        ? `<span class="rule-err">${escapeHtml(r.last_error)}</span>`
        : "";
      return `<li>
        <span class="rule-status ${escapeAttr(status)}">${escapeHtml(r.status || "Pending")}</span>
        <span class="rule-text">${escapeHtml(r.rule)}</span>
        ${err}
      </li>`;
    })
    .join("");

  if (rules.length > 5 && toggle) {
    toggle.classList.remove("hidden");
    toggle.textContent = customRulesExpanded ? "Show fewer" : `Show all (${rules.length})`;
    wrap?.classList.toggle("expanded", customRulesExpanded);
  } else {
    toggle?.classList.add("hidden");
    wrap?.classList.remove("expanded");
  }
}

async function loadCustomRules() {
  const resultEl = $("#custom-rules-result");
  try {
    const data = await api("/api/custom-rules");
    const rules = data.rules || [];
    renderCustomRulesList(rules);
    if (resultEl && !resultEl.dataset.sticky) {
      resultEl.textContent = data.lookup_file_exists
        ? `${rules.length} rule(s) in workbook`
        : "Lookup workbook will be created on first add.";
    }
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  }
}

$("#btn-custom-rules-toggle")?.addEventListener("click", () => {
  customRulesExpanded = !customRulesExpanded;
  loadCustomRules();
});

$("#btn-custom-rule-add")?.addEventListener("click", async () => {
  const input = $("#custom-rule-input");
  const resultEl = $("#custom-rules-result");
  const rule = (input?.value || "").trim();
  if (!rule) {
    alert("Enter rule text first.");
    return;
  }
  try {
    const res = await api("/api/custom-rules", {
      method: "POST",
      body: JSON.stringify({ rule }),
    });
    if (input) input.value = "";
    renderCustomRulesList(res.rules || []);
    if (resultEl) {
      resultEl.textContent = res.message || "Rule added.";
      delete resultEl.dataset.sticky;
    }
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  }
});

$("#btn-custom-rules-apply")?.addEventListener("click", async () => {
  const btn = $("#btn-custom-rules-apply");
  const resultEl = $("#custom-rules-result");
  if (btn) btn.disabled = true;
  if (resultEl) {
    resultEl.textContent = "Compiling rules and applying to database…";
    resultEl.dataset.sticky = "1";
  }
  try {
    const res = await api("/api/custom-rules/compile-apply", { method: "POST" });
    let msg = res.message || "Done.";
    if (res.compile_errors?.length) {
      msg += `\n\nCompile errors:\n${res.compile_errors
        .map((e) => `• ${e.rule}: ${e.error}`)
        .join("\n")}`;
    }
    if (resultEl) resultEl.textContent = msg;
    await loadCustomRules();
    await loadReview();
    loadStatus();
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  } finally {
    if (btn) btn.disabled = false;
  }
});

let editOptionsCache = null;
let editSourceTx = null;
let editLastResults = [];
const EDIT_PAGE_SIZE = 50;
const EDIT_CLASSIFICATIONS = ["Personal", "Business"];
let editPageOffset = 0;
let editSearchTotal = 0;
let editSortBy = "date";
let editSortDir = "desc";
let cadencePreviewTimer = null;

const CADENCE_DEFAULT_RUNRATE = {
  recurring: true,
  lump: false,
  one_time: false,
  exclude: false,
};

function setEditCadenceRunrateDefault(kind) {
  const cb = $("#edit-cadence-runrate");
  if (!cb || !(kind in CADENCE_DEFAULT_RUNRATE)) return;
  cb.checked = CADENCE_DEFAULT_RUNRATE[kind];
}

function updateCadencePeriodVisibility() {
  const kind = $("#edit-cadence-kind")?.value || "unknown";
  const row = $("#edit-cadence-period-row");
  const runrateWrap = document.querySelector(".edit-cadence-runrate");
  const needsPeriod = kind === "recurring" || kind === "lump";
  if (row) row.classList.toggle("hidden", !needsPeriod);
  if (runrateWrap) runrateWrap.classList.toggle("hidden", kind === "unknown");
}

function resetEditCadenceForm() {
  const kindSel = $("#edit-cadence-kind");
  const countInput = $("#edit-cadence-period-count");
  const unitSel = $("#edit-cadence-period-unit");
  const runrate = $("#edit-cadence-runrate");
  const note = $("#edit-cadence-note");
  const preview = $("#edit-cadence-preview");
  if (kindSel) kindSel.value = "unknown";
  if (countInput) countInput.value = "";
  if (unitSel) unitSel.value = "months";
  if (runrate) runrate.checked = true;
  if (note) note.value = "";
  if (preview) preview.textContent = "";
  const txScope = document.querySelector('input[name="edit-cadence-scope"][value="transaction"]');
  if (txScope) txScope.checked = true;
  updateCadencePeriodVisibility();
}

function loadEditCadenceForm(tx, cadenceRes) {
  const effective = cadenceRes?.effective_cadence || {};
  const txKind = (tx?.cadence_kind || "").trim().toLowerCase();
  const useTx = txKind && txKind !== "unknown";
  const source = useTx ? tx : effective;
  const kind = (source?.cadence_kind || "unknown").trim().toLowerCase();
  const kindSel = $("#edit-cadence-kind");
  const countInput = $("#edit-cadence-period-count");
  const unitSel = $("#edit-cadence-period-unit");
  const runrate = $("#edit-cadence-runrate");
  const note = $("#edit-cadence-note");
  if (kindSel) {
    kindSel.value = [...kindSel.options].some((o) => o.value === kind) ? kind : "unknown";
  }
  if (countInput) {
    countInput.value =
      source?.period_count != null && source.period_count !== "" ? String(source.period_count) : "";
  }
  if (unitSel && source?.period_unit) {
    unitSel.value = source.period_unit;
  }
  if (runrate) {
    const explicit = source?.include_in_run_rate;
    if (explicit === true || explicit === 1 || explicit === "1") runrate.checked = true;
    else if (explicit === false || explicit === 0 || explicit === "0") runrate.checked = false;
    else setEditCadenceRunrateDefault(kindSel?.value || kind);
  }
  if (note) note.value = source?.cadence_note || tx?.cadence_note || "";
  updateCadencePeriodVisibility();
}

function getEditCadencePayload() {
  const kind = ($("#edit-cadence-kind")?.value || "unknown").trim();
  if (!kind || kind === "unknown") return null;
  const countRaw = ($("#edit-cadence-period-count")?.value || "").trim();
  const period_count = countRaw ? parseInt(countRaw, 10) : null;
  const period_unit = ($("#edit-cadence-period-unit")?.value || "").trim() || null;
  const runrate = $("#edit-cadence-runrate");
  const include_in_run_rate = runrate && !runrate.closest(".hidden") ? runrate.checked : null;
  return {
    cadence_kind: kind,
    period_count: Number.isFinite(period_count) ? period_count : null,
    period_unit,
    include_in_run_rate,
    cadence_note: ($("#edit-cadence-note")?.value || "").trim(),
  };
}

function renderCadencePreviewLine(amounts) {
  if (!amounts) return "";
  return `Cash: ${formatMoney(amounts.cash)}  |  Core: ${formatMoney(amounts.core)}  |  Normalized: ${formatMoney(amounts.normalized)}/mo`;
}

async function refreshCadencePreview() {
  if (!editSourceTx?.transaction_id) return;
  const previewEl = $("#edit-cadence-preview");
  if (!previewEl) return;
  const kind = ($("#edit-cadence-kind")?.value || "unknown").trim();
  const txId = encodeURIComponent(editSourceTx.transaction_id);
  try {
    let res;
    if (kind === "unknown") {
      res = await api(`/api/transactions/${txId}/cadence`);
    } else {
      const params = new URLSearchParams({ cadence_kind: kind });
      const countRaw = ($("#edit-cadence-period-count")?.value || "").trim();
      const unit = ($("#edit-cadence-period-unit")?.value || "").trim();
      if (countRaw) params.set("period_count", countRaw);
      if (unit) params.set("period_unit", unit);
      const runrate = $("#edit-cadence-runrate");
      if (runrate && !runrate.closest(".hidden")) {
        params.set("include_in_run_rate", runrate.checked ? "true" : "false");
      }
      res = await api(`/api/transactions/${txId}/cadence?${params}`);
    }
    previewEl.textContent = renderCadencePreviewLine(res.effective_amounts);
  } catch (err) {
    previewEl.textContent = `Preview error: ${err.message}`;
  }
}

function scheduleCadencePreview() {
  clearTimeout(cadencePreviewTimer);
  cadencePreviewTimer = setTimeout(() => {
    refreshCadencePreview().catch(() => {});
  }, 300);
}

function setEditPanelOpen(open) {
  const layout = document.querySelector(".edit-layout");
  const panel = $("#edit-side-panel");
  if (layout) layout.classList.toggle("panel-open", open);
  if (panel) panel.classList.toggle("hidden", !open);
}

let editCombosInitialized = false;

function populateEditSelect(sel, items, allLabel, current) {
  if (!sel) return;
  sel.innerHTML =
    `<option value="">${escapeHtml(allLabel)}</option>` +
    (items || []).map((c) => `<option value="${escapeAttr(c)}">${escapeHtml(c)}</option>`).join("");
  if (current && [...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  }
}

function populateEditValueLabelSelect(sel, items, allLabel, current) {
  if (!sel) return;
  sel.innerHTML =
    `<option value="">${escapeHtml(allLabel)}</option>` +
    (items || [])
      .map((item) => {
        const value = item?.value ?? "";
        const label = item?.label ?? value;
        return `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`;
      })
      .join("");
  if (current && [...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  }
}

function populateEditSearchCategorySelect(categories) {
  populateEditSelect($("#edit-search-category"), categories, "All categories", $("#edit-search-category")?.value);
}

function populateEditAdvancedFilters(options) {
  populateEditSelect(
    $("#edit-search-sub"),
    options.sub_categories,
    "All sub-categories",
    $("#edit-search-sub")?.value
  );
  populateEditSelect(
    $("#edit-search-expense-type"),
    options.expense_types,
    "All expense types",
    $("#edit-search-expense-type")?.value
  );
  populateEditSelect(
    $("#edit-search-classification"),
    options.classifications?.length ? options.classifications : EDIT_CLASSIFICATIONS,
    "All classifications",
    $("#edit-search-classification")?.value
  );
  populateEditValueLabelSelect(
    $("#edit-search-cadence-kind"),
    options.cadence_kinds || [],
    "All cadence kinds",
    $("#edit-search-cadence-kind")?.value
  );
  populateEditValueLabelSelect(
    $("#edit-search-cadence-period"),
    options.cadence_periods || [],
    "All expense cadences",
    $("#edit-search-cadence-period")?.value
  );
  populateEditValueLabelSelect(
    $("#edit-search-include-runrate"),
    options.run_rate_filters || [],
    "All",
    $("#edit-search-include-runrate")?.value
  );
}

function editSortHeader(label, field) {
  const active = editSortBy === field;
  const arrow = active ? (editSortDir === "asc" ? " ▲" : " ▼") : "";
  return `<th class="edit-sort-th" data-sort="${field}" scope="col" tabindex="0" aria-sort="${active ? editSortDir + "ending" : "none"}">${escapeHtml(label)}${arrow}</th>`;
}

function attachComboField(wrapper, options) {
  if (!wrapper || wrapper.dataset.comboReady === "1") return;
  const input = wrapper.querySelector("input");
  const btn = wrapper.querySelector(".combo-toggle");
  const menu = wrapper.querySelector(".combo-menu");
  if (!input || !btn || !menu) return;

  const allOptions = [...options];

  function renderMenu(filterText = "") {
    const q = filterText.trim().toLowerCase();
    const items = q
      ? allOptions.filter((o) => o.toLowerCase().includes(q))
      : allOptions;
    menu.innerHTML = items
      .map((o) => `<li role="option" tabindex="-1">${escapeHtml(o)}</li>`)
      .join("");
  }

  function showAllOptions() {
    renderMenu("");
    menu.classList.remove("hidden");
  }

  function hideMenu() {
    menu.classList.add("hidden");
  }

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    if (menu.classList.contains("hidden")) showAllOptions();
    else hideMenu();
  });

  menu.addEventListener("click", (e) => {
    const li = e.target.closest('li[role="option"]');
    if (!li) return;
    input.value = li.textContent;
    hideMenu();
    input.focus();
  });

  input.addEventListener("input", () => {
    if (!menu.classList.contains("hidden")) renderMenu(input.value);
  });

  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target)) hideMenu();
  });

  wrapper.dataset.comboReady = "1";
}

function setupEditCategoryControls(options) {
  populateEditSearchCategorySelect(options.categories || []);
  populateEditAdvancedFilters(options);
  if (!editCombosInitialized) {
    attachComboField(document.querySelector('[data-combo="edit-ai-category"]'), options.categories || []);
    attachComboField(document.querySelector('[data-combo="edit-ai-sub"]'), options.sub_categories || []);
    editCombosInitialized = true;
  }
}

function renderEditResults(transactions) {
  if (!transactions.length) {
    return '<p class="hint">No transactions match your search.</p>';
  }
  const rows = transactions
    .map((tx) => {
      const category = tx.ai_category || "—";
      const expenseCadence = tx.expense_cadence && tx.expense_cadence !== "—" ? tx.expense_cadence : "—";
      const cadenceKind = tx.cadence_kind_label || tx.effective_cadence_kind || "—";
      const runRate = tx.include_in_run_rate_label || "—";
      return `
        <tr>
          <td>${escapeHtml(tx.date || "")}</td>
          <td class="amount">${escapeHtml(formatMoney(tx.amount))}</td>
          <td>${escapeHtml(tx.merchant_key || "")}</td>
          <td>${escapeHtml(category)}</td>
          <td>${escapeHtml(tx.expense_type || "—")}</td>
          <td>${escapeHtml(tx.classification || "—")}</td>
          <td class="cadence-col">${escapeHtml(expenseCadence)}</td>
          <td class="cadence-col">${escapeHtml(cadenceKind)}</td>
          <td class="cadence-col">${escapeHtml(runRate)}</td>
          <td><button type="button" class="btn-edit-row" data-tx-id="${escapeAttr(tx.transaction_id)}">Edit</button></td>
        </tr>
      `;
    })
    .join("");
  return `
    <table class="edit-results-table">
      <thead>
        <tr>
          ${editSortHeader("Date", "date")}
          ${editSortHeader("Amount", "amount")}
          ${editSortHeader("Merchant", "merchant")}
          ${editSortHeader("AI Category", "ai_category")}
          <th scope="col">Expense Type</th>
          ${editSortHeader("Classification", "classification")}
          <th scope="col">Expense Cadence</th>
          ${editSortHeader("Cadence Kind", "cadence_kind")}
          ${editSortHeader("In run-rate", "include_in_run_rate")}
          <th scope="col"></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function bindEditSortHeaders() {
  document.querySelectorAll(".edit-sort-th").forEach((th) => {
    const activate = () => {
      const field = th.dataset.sort;
      if (!field) return;
      if (editSortBy === field) {
        editSortDir = editSortDir === "asc" ? "desc" : "asc";
      } else {
        editSortBy = field;
        editSortDir =
          field === "merchant" ||
          field === "ai_category" ||
          field === "classification" ||
          field === "cadence_kind" ||
          field === "include_in_run_rate"
            ? "asc"
            : "desc";
      }
      runEditSearch({ resetPage: true }).catch(() => {});
    };
    th.addEventListener("click", activate);
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate();
      }
    });
  });
}

function renderEditMatchList(matches) {
  const list = $("#edit-match-list");
  const countEl = $("#edit-match-count");
  if (!list) return;
  list.innerHTML = "";
  matches.forEach((tx) => {
    const li = document.createElement("li");
    li.className = "edit-match-item";
    const labels = [tx.ai_category, tx.ai_sub_category].filter(Boolean).join(" / ") || "—";
    li.innerHTML = `
      <input type="checkbox" class="edit-match-cb" value="${escapeAttr(tx.transaction_id)}" checked />
      <div class="edit-match-item-body">
        <div class="edit-match-item-title">${escapeHtml(tx.merchant_key || "")} · ${escapeHtml(formatMoney(tx.amount))}</div>
        <div class="edit-match-item-meta">${escapeHtml(tx.date || "")} · ${escapeHtml(labels)}</div>
      </div>
    `;
    list.appendChild(li);
  });
  if (countEl) countEl.textContent = `${matches.length} transaction(s)`;
}

async function loadEditMatches(scope) {
  if (!editSourceTx?.transaction_id) return;
  const res = await api(
    `/api/transactions/${encodeURIComponent(editSourceTx.transaction_id)}/matches?scope=${encodeURIComponent(scope)}`
  );
  renderEditMatchList(res.matches || []);
}

async function openEditPanel(tx) {
  editSourceTx = tx;
  setEditPanelOpen(true);
  const singleScope = document.querySelector('input[name="edit-scope"][value="single"]');
  if (singleScope) singleScope.checked = true;
  const summary = $("#edit-source-summary");
  const catInput = $("#edit-ai-category");
  const subInput = $("#edit-ai-sub");
  const expenseSel = $("#edit-label-expense-type");
  const classSel = $("#edit-label-classification");
  const resultEl = $("#edit-apply-result");
  if (resultEl) resultEl.textContent = "";
  resetEditCadenceForm();
  if (summary) {
    summary.innerHTML = `
      <strong>${escapeHtml(tx.merchant_key || "")}</strong><br>
      ${escapeHtml(tx.date || "")} · ${escapeHtml(formatMoney(tx.amount))}<br>
      Category: ${escapeHtml(tx.ai_category || "—")}${tx.ai_sub_category ? ` / ${escapeHtml(tx.ai_sub_category)}` : ""}<br>
      Expense: ${escapeHtml(tx.expense_type || "—")} · Class: ${escapeHtml(tx.classification || "—")}
    `;
  }
  if (catInput) catInput.value = tx.ai_category || "";
  if (subInput) subInput.value = tx.ai_sub_category || "";
  if (expenseSel) expenseSel.value = tx.expense_type === "Fixed" ? "Fixed" : "Variable";
  if (classSel) {
    classSel.value = EDIT_CLASSIFICATIONS.includes(tx.classification) ? tx.classification : "Personal";
  }
  const scope = document.querySelector('input[name="edit-scope"]:checked')?.value || "single";
  loadEditMatches(scope).catch((err) => {
    const list = $("#edit-match-list");
    if (list) list.innerHTML = `<li class="edit-match-item">Error: ${escapeHtml(err.message)}</li>`;
  });
  try {
    const txId = encodeURIComponent(tx.transaction_id);
    const [full, cadenceRes] = await Promise.all([
      api(`/api/transactions/${txId}`),
      api(`/api/transactions/${txId}/cadence`),
    ]);
    loadEditCadenceForm(full, cadenceRes);
    const previewEl = $("#edit-cadence-preview");
    if (previewEl) previewEl.textContent = renderCadencePreviewLine(cadenceRes.effective_amounts);
  } catch (err) {
    const previewEl = $("#edit-cadence-preview");
    if (previewEl) previewEl.textContent = `Could not load cadence: ${err.message}`;
  }
}

function closeEditPanel() {
  editSourceTx = null;
  setEditPanelOpen(false);
  const resultEl = $("#edit-apply-result");
  const summary = $("#edit-source-summary");
  const list = $("#edit-match-list");
  const countEl = $("#edit-match-count");
  if (resultEl) resultEl.textContent = "";
  if (summary) summary.innerHTML = "";
  if (list) list.innerHTML = "";
  if (countEl) countEl.textContent = "";
  const catInput = $("#edit-ai-category");
  const subInput = $("#edit-ai-sub");
  const expenseSel = $("#edit-label-expense-type");
  const classSel = $("#edit-label-classification");
  if (catInput) catInput.value = "";
  if (subInput) subInput.value = "";
  if (expenseSel) expenseSel.value = "Variable";
  if (classSel) classSel.value = "Personal";
  const singleScope = document.querySelector('input[name="edit-scope"][value="single"]');
  if (singleScope) singleScope.checked = true;
  resetEditCadenceForm();
  clearTimeout(cadencePreviewTimer);
}

function updateEditPagination() {
  const bar = $("#edit-pagination");
  const info = $("#edit-page-info");
  const prev = $("#btn-edit-prev");
  const next = $("#btn-edit-next");
  if (!bar || !info) return;

  const total = editSearchTotal;
  const pageCount = Math.max(1, Math.ceil(total / EDIT_PAGE_SIZE));
  const page = Math.floor(editPageOffset / EDIT_PAGE_SIZE) + 1;

  if (total <= EDIT_PAGE_SIZE) {
    bar.classList.add("hidden");
    return;
  }

  bar.classList.remove("hidden");
  const start = editPageOffset + 1;
  const end = Math.min(editPageOffset + editLastResults.length, total);
  info.textContent = `Page ${page} of ${pageCount} · rows ${start}–${end} of ${total}`;
  if (prev) prev.disabled = editPageOffset <= 0;
  if (next) next.disabled = editPageOffset + EDIT_PAGE_SIZE >= total;
}

function bindEditResultRows() {
  const host = $("#edit-results");
  if (!host) return;
  host.querySelectorAll(".btn-edit-row").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.txId;
      const tx = editLastResults.find((t) => t.transaction_id === id);
      if (tx) openEditPanel(tx);
    });
  });
}

async function runEditSearch({ resetPage = false } = {}) {
  closeEditPanel();
  if (resetPage) editPageOffset = 0;

  const q = ($("#edit-search-q")?.value || "").trim();
  const month = $("#edit-search-month")?.value || "";
  const category = ($("#edit-search-category")?.value || "").trim();
  const subCategory = ($("#edit-search-sub")?.value || "").trim();
  const expenseType = ($("#edit-search-expense-type")?.value || "").trim();
  const classification = ($("#edit-search-classification")?.value || "").trim();
  const cadenceKind = ($("#edit-search-cadence-kind")?.value || "").trim();
  const cadencePeriod = ($("#edit-search-cadence-period")?.value || "").trim();
  const includeRunrate = ($("#edit-search-include-runrate")?.value || "").trim();

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (month) params.set("month", month);
  if (category) params.set("category", category);
  if (subCategory) params.set("sub_category", subCategory);
  if (expenseType) params.set("expense_type", expenseType);
  if (classification) params.set("classification", classification);
  if (cadenceKind) params.set("cadence_kind", cadenceKind);
  if (cadencePeriod) params.set("cadence_period", cadencePeriod);
  if (includeRunrate) params.set("include_in_run_rate", includeRunrate);
  params.set("limit", String(EDIT_PAGE_SIZE));
  params.set("offset", String(editPageOffset));
  params.set("sort_by", editSortBy);
  params.set("sort_dir", editSortDir);

  const res = await api(`/api/transactions/search?${params.toString()}`);
  editLastResults = res.transactions || [];
  editSearchTotal = res.total ?? editLastResults.length;

  const host = $("#edit-results");
  const meta = $("#edit-search-meta");
  if (host) {
    host.innerHTML = renderEditResults(editLastResults);
    bindEditResultRows();
    bindEditSortHeaders();
  }
  if (meta) {
    const shown = editLastResults.length;
    if (shown) {
      const start = editPageOffset + 1;
      const end = editPageOffset + shown;
      meta.textContent = `Showing ${start}–${end} of ${editSearchTotal} transaction(s)`;
    } else {
      meta.textContent = "No transactions match your filters.";
    }
  }
  updateEditPagination();
}

async function loadTransactionEditor() {
  const monthSel = $("#edit-search-month");
  try {
    const [status, options] = await Promise.all([
      api("/api/status"),
      editOptionsCache ? Promise.resolve(editOptionsCache) : api("/api/review/options"),
    ]);
    editOptionsCache = options;
    setupEditCategoryControls(options);
    if (monthSel) {
      const current = monthSel.value;
      monthSel.innerHTML = '<option value="">All months</option>';
      (status.months || []).forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        monthSel.appendChild(opt);
      });
      if (current) monthSel.value = current;
    }
    await runEditSearch();
  } catch (err) {
    const host = $("#edit-results");
    if (host) host.innerHTML = `<p class="hint">Error: ${escapeHtml(err.message)}</p>`;
  }
}

$("#edit-search-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  runEditSearch({ resetPage: true }).catch((err) => {
    const host = $("#edit-results");
    if (host) host.innerHTML = `<p class="hint">Error: ${escapeHtml(err.message)}</p>`;
  });
});

$("#btn-edit-prev")?.addEventListener("click", () => {
  if (editPageOffset <= 0) return;
  editPageOffset = Math.max(0, editPageOffset - EDIT_PAGE_SIZE);
  runEditSearch({ resetPage: false }).catch(() => {});
});

$("#btn-edit-next")?.addEventListener("click", () => {
  if (editPageOffset + EDIT_PAGE_SIZE >= editSearchTotal) return;
  editPageOffset += EDIT_PAGE_SIZE;
  runEditSearch({ resetPage: false }).catch(() => {});
});

document.querySelectorAll('input[name="edit-scope"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    if (!editSourceTx) return;
    loadEditMatches(radio.value).catch(() => {});
  });
});

$("#edit-cadence-kind")?.addEventListener("change", () => {
  setEditCadenceRunrateDefault($("#edit-cadence-kind")?.value || "unknown");
  updateCadencePeriodVisibility();
  scheduleCadencePreview();
});

["edit-cadence-period-count", "edit-cadence-period-unit", "edit-cadence-runrate", "edit-cadence-note"].forEach(
  (id) => {
    const el = document.getElementById(id);
    el?.addEventListener("input", scheduleCadencePreview);
    el?.addEventListener("change", scheduleCadencePreview);
  }
);

document.querySelectorAll(".edit-cadence-preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    const kindSel = $("#edit-cadence-kind");
    const countInput = $("#edit-cadence-period-count");
    const unitSel = $("#edit-cadence-period-unit");
    if (kindSel) kindSel.value = btn.dataset.kind || "unknown";
    if (countInput) countInput.value = btn.dataset.count || "";
    if (unitSel && btn.dataset.unit) unitSel.value = btn.dataset.unit;
    setEditCadenceRunrateDefault(kindSel?.value || "unknown");
    updateCadencePeriodVisibility();
    scheduleCadencePreview();
  });
});

$("#btn-edit-select-all")?.addEventListener("click", () => {
  document.querySelectorAll(".edit-match-cb").forEach((cb) => {
    cb.checked = true;
  });
});

$("#btn-edit-select-none")?.addEventListener("click", () => {
  document.querySelectorAll(".edit-match-cb").forEach((cb) => {
    cb.checked = false;
  });
});

$("#btn-edit-close")?.addEventListener("click", closeEditPanel);
$("#btn-edit-cancel")?.addEventListener("click", closeEditPanel);

let editInsightState = null;
let cadenceInsightState = null;

function cadenceKindLabel(kind) {
  const labels = {
    recurring: "Recurring",
    lump: "Lump sum",
    one_time: "One-time",
    exclude: "Exclude",
    unknown: "Unknown",
  };
  return labels[kind] || kind || "Unknown";
}

function closeCadenceInsightModal() {
  const overlay = $("#cadence-insight-overlay");
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
  }
  cadenceInsightState = null;
}

function renderCadenceInsight(proposal) {
  const body = $("#cadence-insight-body");
  const actions = $("#cadence-insight-actions");
  const saveMerchant = $("#btn-cadence-insight-save-merchant");
  const saveTx = $("#btn-cadence-insight-save-tx");
  if (!body) return;

  const notes =
    (proposal.data_notes || []).length > 0
      ? `<ul class="edit-insight-notes">${proposal.data_notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
      : "";

  const existingRuleBlock = proposal.existing_similar_rule
    ? `
        <p class="edit-insight-existing-rule">
          <strong>Existing cadence rule</strong>
          ${proposal.existing_rule_source ? ` (${escapeHtml(proposal.existing_rule_source)})` : ""}:
          <span class="edit-insight-existing-rule-text">${escapeHtml(proposal.existing_similar_rule)}</span>
        </p>
      `
    : "";

  const period =
    proposal.period_count && proposal.period_unit
      ? `Every ${proposal.period_count} ${proposal.period_unit}`
      : "—";
  const runRate =
    proposal.include_in_run_rate === true
      ? "Included in core"
      : proposal.include_in_run_rate === false
        ? "Excluded from core"
        : "Default by kind";

  body.innerHTML = `
    <p class="edit-insight-pattern"><strong>${escapeHtml(proposal.merchant_key || "")}</strong> · ${escapeHtml(cadenceKindLabel(proposal.cadence_kind))} · ${escapeHtml(period)} · ${escapeHtml(runRate)}</p>
    <div class="edit-insight-prose">${renderMarkdown(proposal.insight || "")}</div>
    <p class="edit-cadence-preview">${escapeHtml(renderCadencePreviewLine(proposal.effective_amounts))}</p>
    ${proposal.cadence_note ? `<p class="edit-insight-future">Note: ${escapeHtml(proposal.cadence_note)}</p>` : ""}
    ${notes}
    ${existingRuleBlock}
    ${proposal.confidence ? `<span class="edit-insight-confidence">Confidence: ${escapeHtml(proposal.confidence)}</span>` : ""}
  `;

  const canSave = Boolean(proposal.recommend_save_rule);
  const hasTx = Boolean(
    proposal.transaction_id || proposal.sample_transaction?.transaction_id
  );
  if (actions) {
    if (canSave) {
      actions.classList.remove("hidden");
      if (saveMerchant) saveMerchant.disabled = false;
      if (saveTx) {
        saveTx.disabled = !hasTx;
        saveTx.classList.toggle("hidden", !hasTx);
      }
    } else {
      actions.classList.add("hidden");
    }
  }
}

function openCadenceInsightModal(proposal) {
  const overlay = $("#cadence-insight-overlay");
  const body = $("#cadence-insight-body");
  const actions = $("#cadence-insight-actions");
  if (!overlay || !body) return;
  cadenceInsightState = { proposal };
  body.innerHTML = `<p class="hint">Loading…</p>`;
  if (actions) actions.classList.add("hidden");
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
  renderCadenceInsight(proposal);
}

$("#btn-cadence-insight-close")?.addEventListener("click", closeCadenceInsightModal);
$("#btn-cadence-insight-dismiss")?.addEventListener("click", closeCadenceInsightModal);
$("#cadence-insight-overlay")?.addEventListener("click", (e) => {
  if (e.target?.id === "cadence-insight-overlay") closeCadenceInsightModal();
});

async function saveCadenceProposal(scope) {
  const proposal = cadenceInsightState?.proposal;
  if (!proposal) return;
  const saveMerchant = $("#btn-cadence-insight-save-merchant");
  const saveTx = $("#btn-cadence-insight-save-tx");
  const cadencePayload = {
    cadence_kind: proposal.cadence_kind,
    period_count: proposal.period_count ?? null,
    period_unit: proposal.period_unit ?? null,
    include_in_run_rate: proposal.include_in_run_rate ?? null,
    cadence_note: proposal.cadence_note || "",
  };

  if (scope === "merchant") {
    if (saveMerchant) saveMerchant.disabled = true;
    try {
      await api("/api/cadence-rules", {
        method: "POST",
        body: JSON.stringify({
          merchant_key: proposal.merchant_key,
          cadence_kind: cadencePayload.cadence_kind,
          period_count: cadencePayload.period_count,
          period_unit: cadencePayload.period_unit,
          include_in_run_rate: cadencePayload.include_in_run_rate,
          notes: cadencePayload.cadence_note,
        }),
      });
      closeCadenceInsightModal();
      loadStatus();
    } catch (err) {
      const body = $("#cadence-insight-body");
      if (body) {
        body.insertAdjacentHTML(
          "beforeend",
          `<p class="hint" style="margin-top:0.75rem">Save failed: ${escapeHtml(err.message)}</p>`
        );
      }
      if (saveMerchant) saveMerchant.disabled = false;
    }
    return;
  }

  const sample = proposal.sample_transaction || {};
  const txId = proposal.transaction_id || sample.transaction_id;
  if (!txId) return;
  if (saveTx) saveTx.disabled = true;
  try {
    await api("/api/transactions/bulk-label", {
      method: "POST",
      body: JSON.stringify({
        transaction_ids: [txId],
        ai_category: sample.ai_category || "Uncategorized",
        ai_sub_category: sample.ai_sub_category || "",
        cadence: cadencePayload,
        cadence_scope: "transaction",
      }),
    });
    closeCadenceInsightModal();
    loadStatus();
  } catch (err) {
    const body = $("#cadence-insight-body");
    if (body) {
      body.insertAdjacentHTML(
        "beforeend",
        `<p class="hint" style="margin-top:0.75rem">Save failed: ${escapeHtml(err.message)}</p>`
      );
    }
    if (saveTx) saveTx.disabled = false;
  }
}

$("#btn-cadence-insight-save-merchant")?.addEventListener("click", () => saveCadenceProposal("merchant"));
$("#btn-cadence-insight-save-tx")?.addEventListener("click", () => saveCadenceProposal("transaction"));

function closeEditInsightModal() {
  const overlay = $("#edit-insight-overlay");
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
  }
  editInsightState = null;
}

function openEditInsightModal() {
  const overlay = $("#edit-insight-overlay");
  const body = $("#edit-insight-body");
  const actions = $("#edit-insight-actions");
  if (!overlay || !body) return;
  body.innerHTML = `<p class="hint">Analyzing your edit…</p>`;
  if (actions) actions.classList.add("hidden");
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
}

function renderEditInsight(insight) {
  const body = $("#edit-insight-body");
  const actions = $("#edit-insight-actions");
  const saveBtn = $("#btn-edit-insight-save");
  if (!body) return;

  const notes =
    (insight.data_notes || []).length > 0
      ? `<ul class="edit-insight-notes">${insight.data_notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
      : "";

  const existingRuleBlock = insight.existing_similar_rule
    ? `
        <p class="edit-insight-existing-rule">
          <strong>Similar rule already exists</strong>
          ${insight.existing_rule_status ? ` (${escapeHtml(insight.existing_rule_status)})` : ""}:
          <span class="edit-insight-existing-rule-text">${escapeHtml(insight.existing_similar_rule)}</span>
        </p>
      `
    : "";

  const ruleBlock =
    insight.recommend_save_rule && insight.suggested_rule
      ? `
        <label class="edit-insight-rule-label" for="edit-insight-rule-text">Suggested custom rule</label>
        <textarea id="edit-insight-rule-text" class="edit-insight-rule-input" rows="3">${escapeHtml(insight.suggested_rule)}</textarea>
      `
      : existingRuleBlock;

  body.innerHTML = `
    ${insight.pattern_summary ? `<p class="edit-insight-pattern">${escapeHtml(insight.pattern_summary)}</p>` : ""}
    <div class="edit-insight-prose">${renderMarkdown(insight.insight || "")}</div>
    ${insight.future_note ? `<p class="edit-insight-future">${escapeHtml(insight.future_note)}</p>` : ""}
    ${notes}
    ${ruleBlock}
    ${insight.confidence ? `<span class="edit-insight-confidence">Confidence: ${escapeHtml(insight.confidence)}</span>` : ""}
  `;

  if (actions) {
    if (insight.recommend_save_rule && insight.suggested_rule) {
      actions.classList.remove("hidden");
      if (saveBtn) saveBtn.disabled = false;
    } else {
      actions.classList.add("hidden");
    }
  }
}

async function fetchEditInsight(context) {
  openEditInsightModal();
  try {
    const insight = await api("/api/transactions/edit-insight", {
      method: "POST",
      body: JSON.stringify(context),
    });
    editInsightState = { context, insight };
    renderEditInsight(insight);
  } catch (err) {
    const body = $("#edit-insight-body");
    if (body) body.innerHTML = `<p class="hint">Could not load AI insight: ${escapeHtml(err.message)}</p>`;
  }
}

$("#btn-edit-insight-close")?.addEventListener("click", closeEditInsightModal);
$("#btn-edit-insight-dismiss")?.addEventListener("click", closeEditInsightModal);
$("#edit-insight-overlay")?.addEventListener("click", (e) => {
  if (e.target?.id === "edit-insight-overlay") closeEditInsightModal();
});

$("#btn-edit-insight-save")?.addEventListener("click", async () => {
  const ruleEl = $("#edit-insight-rule-text");
  const saveBtn = $("#btn-edit-insight-save");
  const rule = (ruleEl?.value || editInsightState?.insight?.suggested_rule || "").trim();
  if (!rule) return;
  const compile = $("#edit-insight-compile")?.checked ?? true;
  if (saveBtn) saveBtn.disabled = true;
  try {
    await api("/api/custom-rules", {
      method: "POST",
      body: JSON.stringify({ rule }),
    });
    let compileMsg = "";
    if (compile) {
      const compiled = await api("/api/custom-rules/compile-apply", { method: "POST" });
      compileMsg = ` Compiled ${compiled.rules_compiled || 0} rule(s); ${compiled.rows_updated || 0} row(s) updated.`;
    }
    closeEditInsightModal();
    const meta = $("#edit-search-meta");
    if (meta) meta.textContent = `Custom rule saved.${compileMsg}`;
    loadStatus();
  } catch (err) {
    const body = $("#edit-insight-body");
    if (body) {
      body.insertAdjacentHTML(
        "beforeend",
        `<p class="hint" style="margin-top:0.75rem">Save failed: ${escapeHtml(err.message)}</p>`
      );
    }
    if (saveBtn) saveBtn.disabled = false;
  }
});

$("#edit-label-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!editSourceTx) return;
  const resultEl = $("#edit-apply-result");
  const applyBtn = $("#btn-edit-apply");
  const ids = [...document.querySelectorAll(".edit-match-cb:checked")].map((cb) => cb.value);
  if (!ids.length) {
    if (resultEl) resultEl.textContent = "Select at least one transaction.";
    return;
  }
  const scope = document.querySelector('input[name="edit-scope"]:checked')?.value || "single";
  const afterLabels = {
    ai_category: ($("#edit-ai-category")?.value || "").trim(),
    ai_sub_category: ($("#edit-ai-sub")?.value || "").trim(),
    expense_type: $("#edit-label-expense-type")?.value || "Variable",
    classification: $("#edit-label-classification")?.value || "Personal",
  };
  const beforeLabels = {
    ai_category: editSourceTx.ai_category || "",
    ai_sub_category: editSourceTx.ai_sub_category || "",
    expense_type: editSourceTx.expense_type || "",
    classification: editSourceTx.classification || "",
  };
  const payload = {
    transaction_ids: ids,
    ai_category: afterLabels.ai_category,
    ai_sub_category: afterLabels.ai_sub_category,
    expense_type: afterLabels.expense_type,
    classification: afterLabels.classification,
    update_merchant_label: scope === "merchant",
    merchant_key: scope === "merchant" ? editSourceTx.merchant_key : null,
  };
  const cadence = getEditCadencePayload();
  if (cadence) {
    payload.cadence = cadence;
    const cadenceScope =
      document.querySelector('input[name="edit-cadence-scope"]:checked')?.value || "transaction";
    payload.cadence_scope = cadenceScope;
    if (cadenceScope === "merchant") {
      if (!editSourceTx.merchant_key) {
        if (resultEl) resultEl.textContent = "Merchant key is required for merchant cadence rule.";
        return;
      }
      payload.merchant_key = editSourceTx.merchant_key;
    }
    if ((cadence.cadence_kind === "recurring" || cadence.cadence_kind === "lump") && !cadence.period_count) {
      if (resultEl) resultEl.textContent = "Period count is required for recurring or lump cadence.";
      return;
    }
  }
  if (!payload.ai_category) {
    if (resultEl) resultEl.textContent = "AI Category is required.";
    return;
  }
  const insightContext = {
    merchant_key: editSourceTx.merchant_key,
    scope,
    before: beforeLabels,
    after: afterLabels,
    amount: editSourceTx.amount != null ? Number(editSourceTx.amount) : null,
    update_merchant_label: scope === "merchant",
  };
  if (applyBtn) applyBtn.disabled = true;
  if (resultEl) resultEl.textContent = "Saving…";
  try {
    const res = await api("/api/transactions/bulk-label", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeEditPanel();
    await runEditSearch({ resetPage: false });
    loadStatus();
    const meta = $("#edit-search-meta");
    const note = `Updated ${res.rows_updated} transaction(s).`;
    if (meta) meta.textContent = note;
    fetchEditInsight({ ...insightContext, rows_updated: res.rows_updated || ids.length });
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  } finally {
    if (applyBtn) applyBtn.disabled = false;
  }
});

async function loadReview() {
  const list = $("#review-list");
  list.innerHTML = "<p>Loading…</p>";
  try {
    const [items, options] = await Promise.all([
      api("/api/review"),
      reviewOptionsCache ? Promise.resolve(reviewOptionsCache) : api("/api/review/options"),
    ]);
    reviewOptionsCache = options;
    ensureReviewDatalists(options);

    const tips = options.tooltips || {};
    if (!items.length) {
      list.innerHTML = "<p>No merchants pending review.</p>";
      return;
    }
    list.innerHTML = "";
    items.forEach((item, idx) => {
      const flow = item.flow_type || "Expense";
      const expType = item.expense_type || "Variable";
      const cat = item.ai_category || "";
      const sub = item.ai_sub_category || "";
      const uid = `review-${idx}`;
      const isSingleTx = item.review_mode === "transaction" && item.transaction_id;
      const title = isSingleTx
        ? `${item.merchant_key} · ${formatMoney(item.amount)}`
        : item.merchant_key;
      const meta = isSingleTx
        ? `${item.date || "—"} · ${item.sample_description || ""} · confidence ${(item.confidence || 0).toFixed(2)}`
        : `${item.transaction_count} tx · confidence ${(item.confidence || 0).toFixed(2)} · ${item.sample_description || ""}`;
      const confirmLabel = isSingleTx ? "Confirm this transaction" : "Confirm pending in this group";

      const el = document.createElement("div");
      el.className = `review-item${isSingleTx ? " review-item-single" : ""}`;
      el.innerHTML = `
        <div class="review-item-header${isSingleTx ? " review-item-header-single" : ""}">
          <div class="review-item-title">
            <h3>${escapeHtml(title)}</h3>
            <div class="meta" title="${escapeAttr(tips.confidence || "")}">
              ${meta}
            </div>
          </div>
          ${isSingleTx ? "" : '<button type="button" class="review-expand" aria-expanded="false" aria-label="Show transactions" title="Show transactions in this group">▶</button>'}
        </div>
        ${isSingleTx ? "" : `<div class="review-tx-panel hidden" data-merchant="${escapeAttr(item.merchant_key)}"></div>`}
        <form class="review-form">
          ${reviewField(
            `${uid}-cat`,
            "AI Category",
            tips.category || "Top-level category",
            `<input id="${uid}-cat" name="category" list="review-categories" value="${escapeAttr(cat)}" placeholder="Select or type…" required title="${escapeAttr(tips.category || "")}" />`
          )}
          ${reviewField(
            `${uid}-sub`,
            "Sub-category",
            tips.sub_category || "Specific label",
            `<input id="${uid}-sub" name="sub" list="review-subcategories" value="${escapeAttr(sub)}" placeholder="Select or type…" title="${escapeAttr(tips.sub_category || "")}" />`
          )}
          ${reviewField(
            `${uid}-flow`,
            "Income / Expense",
            tips.flow_type || "Flow type",
            `<select id="${uid}-flow" name="flow" title="${escapeAttr(tips.flow_type || "")}">${buildSelectOptions(options.flow_types, flow)}</select>`
          )}
          ${reviewField(
            `${uid}-type`,
            "Fixed / Variable",
            tips.expense_type || "Expense type",
            `<select id="${uid}-type" name="type" title="${escapeAttr(tips.expense_type || "")}">${buildSelectOptions(options.expense_types, expType)}</select>`
          )}
          <button type="submit">${confirmLabel}</button>
        </form>
      `;
      const expandBtn = el.querySelector(".review-expand");
      const txPanel = el.querySelector(".review-tx-panel");
      if (expandBtn && txPanel) {
        expandBtn.addEventListener("click", () => {
          toggleReviewTransactions(expandBtn, item.merchant_key, txPanel);
        });
      }

      el.querySelector("form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const mk = encodeURIComponent(item.merchant_key);
        const payload = {
          ai_category: String(fd.get("category") || "").trim(),
          ai_sub_category: String(fd.get("sub") || "").trim(),
          flow_type: fd.get("flow"),
          expense_type: fd.get("type"),
        };
        if (isSingleTx) payload.transaction_id = item.transaction_id;
        try {
          await api(`/api/review/${mk}/confirm`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          el.remove();
          loadStatus();
        } catch (err) {
          alert(err.message);
        }
      });
      list.appendChild(el);
    });
  } catch (err) {
    list.innerHTML = `<p>Error: ${escapeHtml(err.message)}</p>`;
  }
}

function escapeAttr(s) {
  return String(s).replace(/"/g, "&quot;");
}

function formatScanResultLines(results) {
  return (results || []).map((r) => {
    if (!r.ok) return `${r.file}: ERROR — ${r.error}`;
    if (r.file_unchanged) return `${r.file}: skipped (unchanged file)`;
    return `${r.file}: ${r.message || `${r.inserted} new, ${r.updated} updated, ${r.skipped} unchanged`}`;
  });
}

function formatUploadResultLines(uploads) {
  return (uploads || []).map((u) => {
    if (!u.ok) return `${u.original_name}: upload failed — ${u.error}`;
    if (u.original_name && u.original_name !== u.saved_as) {
      return `${u.original_name} → saved as ${u.saved_as}`;
    }
    return `${u.saved_as}: copied to inbox`;
  });
}

function setInboxSelectedFiles(files) {
  const el = $("#inbox-selected-files");
  if (!el) return;
  if (!files?.length) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  const names = [...files].map((f) => f.name).join(", ");
  el.textContent = `Selected: ${names}`;
  el.classList.remove("hidden");
}

async function uploadAndScan(files) {
  const resultEl = $("#scan-result");
  const chooseBtn = $("#btn-choose-scan");
  const scanBtn = $("#btn-scan-existing");
  if (!files?.length) {
    if (resultEl) resultEl.textContent = "No files selected.";
    return;
  }
  if (resultEl) resultEl.textContent = "Uploading and scanning…";
  if (chooseBtn) chooseBtn.disabled = true;
  if (scanBtn) scanBtn.disabled = true;
  try {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    const res = await fetch("/api/ingest/upload-and-scan", {
      method: "POST",
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    const lines = [
      ...formatUploadResultLines(data.uploads),
      ...formatScanResultLines(data.results),
    ];
    if (resultEl) {
      resultEl.textContent = lines.length ? lines.join("\n") : JSON.stringify(data, null, 2);
    }
    loadStatus();
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  } finally {
    if (chooseBtn) chooseBtn.disabled = false;
    if (scanBtn) scanBtn.disabled = false;
    setInboxSelectedFiles(null);
    const input = $("#inbox-file-input");
    if (input) input.value = "";
  }
}

$("#btn-choose-scan")?.addEventListener("click", () => {
  $("#inbox-file-input")?.click();
});

$("#inbox-file-input")?.addEventListener("change", (e) => {
  const files = e.target.files;
  if (!files?.length) return;
  setInboxSelectedFiles(files);
  uploadAndScan(files).catch(() => {});
});

$("#btn-scan-existing")?.addEventListener("click", async () => {
  const resultEl = $("#scan-result");
  const chooseBtn = $("#btn-choose-scan");
  const scanBtn = $("#btn-scan-existing");
  if (resultEl) resultEl.textContent = "Scanning inbox…";
  if (chooseBtn) chooseBtn.disabled = true;
  if (scanBtn) scanBtn.disabled = true;
  try {
    const res = await api("/api/ingest/scan", { method: "POST" });
    const lines = formatScanResultLines(res.results);
    if (resultEl) {
      resultEl.textContent = lines.length ? lines.join("\n") : JSON.stringify(res, null, 2);
    }
    loadStatus();
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
  } finally {
    if (chooseBtn) chooseBtn.disabled = false;
    if (scanBtn) scanBtn.disabled = false;
  }
});

function setCategorizeProgress(percent, message) {
  const fill = $("#categorize-progress-fill");
  const text = $("#categorize-progress-text");
  if (fill) fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  if (text) text.textContent = message;
}

function appendCategorizeLog(message) {
  const log = $("#categorize-progress-log");
  if (!log) return;
  const li = document.createElement("li");
  li.textContent = message;
  log.appendChild(li);
  while (log.children.length > 12) {
    log.removeChild(log.firstChild);
  }
  log.scrollTop = log.scrollHeight;
}

async function runCategorizeStream() {
  const btn = $("#btn-categorize");
  const panel = $("#categorize-progress");
  const result = $("#categorize-result");
  const log = $("#categorize-progress-log");

  btn.disabled = true;
  panel?.classList.remove("hidden");
  if (log) log.innerHTML = "";
  setCategorizeProgress(0, "Checking inbox…");
  result.textContent = "";

  try {
    const st = await api("/api/status");
    const files = st.inbox_csv_files || [];
    if (files.length === 0) {
      const msg =
        "No CSV in input/. Use “Choose CSV files & scan”, or copy one export from processed/ back into input/.";
      setCategorizeProgress(0, msg);
      result.textContent = msg;
      btn.disabled = false;
      return;
    }
    if (files.length > 1) {
      appendCategorizeLog(`Processing ${files.length} file(s): ${files.join(", ")}`);
    }
  } catch (err) {
    setCategorizeProgress(0, err.message);
    result.textContent = err.message;
    btn.disabled = false;
    return;
  }

  setCategorizeProgress(0, "Starting processing (CLI pipeline)…");
  const es = new EventSource("/api/process/stream");

  es.onmessage = (ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return;
    }

    if (data.type === "phase" && data.message) {
      if (typeof data.percent === "number") {
        setCategorizeProgress(data.percent, data.message);
      } else {
        const text = $("#categorize-progress-text");
        if (text) text.textContent = data.message;
      }
      appendCategorizeLog(data.message);
    } else if (data.type === "progress" && data.message) {
      setCategorizeProgress(data.percent ?? 0, data.message);
      appendCategorizeLog(data.message);
    } else if (data.type === "start") {
      setCategorizeProgress(0, data.message);
      appendCategorizeLog(data.message);
    } else if (data.type === "batch_start") {
      const pct = data.total_merchants
        ? Math.round((data.merchants_done / data.total_merchants) * 100)
        : 0;
      setCategorizeProgress(pct, data.message);
      if (data.preview?.length) {
        appendCategorizeLog(`  → ${data.preview.join(", ")}${data.merchants_in_batch > 4 ? "…" : ""}`);
      }
    } else if (data.type === "batch_done") {
      setCategorizeProgress(data.percent, data.message);
    } else if (data.type === "batch_error") {
      appendCategorizeLog(data.message);
    } else if (data.type === "file_start" || data.type === "file_done") {
      if (typeof data.percent === "number") {
        setCategorizeProgress(data.percent, data.message);
      } else if (data.message) {
        const text = $("#categorize-progress-text");
        if (text) text.textContent = data.message;
      }
      if (data.message) appendCategorizeLog(data.message);
    } else if (data.type === "done") {
      const fileCount = data.file_count ?? (data.results?.length || (data.result ? 1 : 0));
      const doneMsg =
        data.message ||
        (fileCount > 1
          ? `Complete — processed ${fileCount} file(s)`
          : data.result?.archived_to
            ? `Complete — archived to ${data.result.archived_to}`
            : "Complete");
      setCategorizeProgress(100, doneMsg);
      if (data.message) appendCategorizeLog(data.message);
      if (Array.isArray(data.results)) {
        for (const item of data.results) {
          if (item.archived_to) {
            appendCategorizeLog(`Archived ${item.file} → ${item.archived_to}`);
          }
        }
        result.textContent = JSON.stringify(
          { file_count: fileCount, results: data.results },
          null,
          2
        );
      } else if (data.result) {
        if (data.result.archived_to) {
          appendCategorizeLog(`Archived CSV → ${data.result.archived_to}`);
        }
        result.textContent = JSON.stringify(data.result, null, 2);
      } else {
        result.textContent = JSON.stringify(
          {
            labeled: data.labeled,
            rows_updated: data.rows_updated,
            errors: data.errors,
          },
          null,
          2
        );
      }
      es.close();
      btn.disabled = false;
      loadStatus();
      reviewOptionsCache = null;
    } else if (data.type === "error") {
      setCategorizeProgress(0, data.message);
      result.textContent = data.message;
      es.close();
      btn.disabled = false;
    }
  };

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    es.close();
    btn.disabled = false;
    const msg = "Connection lost during processing. Check server logs.";
    setCategorizeProgress(0, msg);
    result.textContent = msg;
  };
}

$("#btn-categorize").addEventListener("click", () => {
  runCategorizeStream();
});

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    $("#settings-db-path").textContent = s.db_path;
    $("#settings-lookup-path").textContent = s.lookup_file;
    const status = $("#settings-lookup-status");
    if (status) {
      status.textContent = s.lookup_file_exists ? "(found)" : "(not found)";
      status.className = s.lookup_file_exists ? "" : "lookup-missing";
    }
    const lines = Object.entries(s.table_counts || {}).map(
      ([k, v]) => `${k}: ${v}`
    );
    $("#settings-counts").textContent = lines.join("\n") || "Empty";
  } catch (err) {
    $("#settings-counts").textContent = `Error: ${err.message}`;
  }
}

$("#btn-clear-data").addEventListener("click", async () => {
  const ok = window.confirm(
    "Delete ALL web app data (transactions, labels, chat, ingest history)?\n\nThis cannot be undone."
  );
  if (!ok) return;
  const typed = window.prompt('Type CLEAR to confirm:');
  if (typed !== "CLEAR") return;
  $("#clear-result").textContent = "Clearing…";
  try {
    const res = await api("/api/settings/clear", {
      method: "POST",
      body: JSON.stringify({ confirm: "CLEAR" }),
    });
    $("#clear-result").textContent = res.message || JSON.stringify(res, null, 2);
    loadStatus();
    loadSettings();
    reviewOptionsCache = null;
  } catch (err) {
    $("#clear-result").textContent = err.message;
  }
});

$("#btn-import-lookups").addEventListener("click", async () => {
  $("#import-lookups-result").textContent = "Importing…";
  try {
    const res = await api("/api/settings/import-lookups", {
      method: "POST",
      body: JSON.stringify({}),
    });
    $("#import-lookups-result").textContent = res.message || JSON.stringify(res, null, 2);
    loadStatus();
    loadSettings();
    reviewOptionsCache = null;
  } catch (err) {
    $("#import-lookups-result").textContent = err.message;
  }
});

loadStatus().catch((e) => {
  $("#status-line").textContent = `Offline: ${e.message}`;
});

loadChatHistory();
