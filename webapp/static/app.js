const $ = (sel) => document.querySelector(sel);

let uiShowCadence = true;
let uiShowExcelLookupImport = true;

function applyUiFeatureFlags() {
  const cadenceTab = $("#tab-cadence");
  const cadencePanel = $("#panel-cadence");
  if (cadenceTab) cadenceTab.classList.toggle("hidden", !uiShowCadence);
  if (cadencePanel) {
    cadencePanel.classList.toggle("hidden", !uiShowCadence);
    if (!uiShowCadence && cadencePanel.classList.contains("active")) {
      setTab("chat", { skipCadenceGuard: true });
    }
  }
  const excelCard = $("#settings-excel-import-card");
  if (excelCard) excelCard.classList.toggle("hidden", !uiShowExcelLookupImport);
  const cadenceRulesCard = $("#settings-cadence-rules-card");
  if (cadenceRulesCard) cadenceRulesCard.classList.toggle("hidden", !uiShowCadence);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function setTab(name, options = {}) {
  if (customRulesBusy) return;
  if (name === "cadence" && !uiShowCadence && !options.skipCadenceGuard) return;
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.classList.toggle("active", p.id === `panel-${name}`);
  });
  if (name === "review") loadReview();
  if (name === "settings") {
    loadSettings();
    loadCustomRules();
    if (uiShowCadence) loadCadenceRulesList();
  }
  if (name === "chat") loadChatHistory();
  if (name === "edit") loadTransactionEditor();
  if (name === "cadence" && uiShowCadence) loadCadencePanel();
  if (name === "taxonomy") loadTaxonomyPanel();
}

document.querySelectorAll(".tab-jump").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    if (tab) setTab(tab);
  });
});

function setWorkflowStepper(stepperEl, step) {
  if (!stepperEl) return;
  stepperEl.querySelectorAll(".workflow-step").forEach((el) => {
    const n = Number(el.dataset.step);
    el.classList.toggle("active", n === step);
    el.classList.toggle("done", n < step);
  });
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

async function loadStatus() {
  const s = await api("/api/status");
  uiShowCadence = s.ui_show_cadence !== false;
  uiShowExcelLookupImport = s.ui_show_excel_lookup_import !== false;
  applyUiFeatureFlags();
  const provider = s.llm_provider ? `${s.llm_provider} · ` : "";
  let modelLabel = s.chat_model || s.llm_model || "";
  if (s.pipeline_model && s.chat_model && s.pipeline_model !== s.chat_model) {
    modelLabel = `process ${s.pipeline_model} · chat ${s.chat_model}`;
  }
  $("#status-line").textContent =
    `${s.transaction_count} transactions · ${s.review_merchant_count} merchants to confirm · ${provider}${modelLabel}`;
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
      let line = `${x.tool}${args}`;
      const sql = x.result?.sql || x.args?.sql;
      if (x.tool === "query_sql" && sql) {
        line += `\n  SQL: ${sql}`;
      }
      if (x.result?.validation_rejected) {
        line += "\n  (validation rejected — retried)";
      }
      return line;
    })
    .join("\n\n");
  details.appendChild(pre);
  parent.appendChild(details);
}

function appendCadenceProposalAction(parent, proposal) {
  if (!uiShowCadence || !proposal || !proposal.insight) return;
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
let chatHistoryModalPage = 1;
const CHAT_SCREEN_CLEARED_KEY = "chatScreenCleared";

function isChatScreenCleared() {
  return sessionStorage.getItem(CHAT_SCREEN_CLEARED_KEY) === "1";
}

function formatChatTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 19);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function clearChatScreen() {
  const log = $("#chat-log");
  if (log) log.innerHTML = "";
  sessionStorage.setItem(CHAT_SCREEN_CLEARED_KEY, "1");
  chatHistoryLoaded = true;
}

async function loadChatHistory() {
  if (chatHistoryLoaded) return;
  if (isChatScreenCleared()) {
    chatHistoryLoaded = true;
    return;
  }
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

function renderChatHistoryModal(data) {
  const host = $("#chat-history-list");
  const pageInfo = $("#chat-history-page-info");
  const prevBtn = $("#btn-chat-history-prev");
  const nextBtn = $("#btn-chat-history-next");
  if (!host) return;

  const messages = data.messages || [];
  if (!messages.length) {
    host.innerHTML = '<p class="hint">No chat history yet.</p>';
  } else {
    host.innerHTML = messages
      .map((m) => {
        const role = m.role === "user" ? "user" : "assistant";
        const label = role === "user" ? "You" : "Assistant";
        return `
          <article class="chat-history-item ${role}">
            <div class="chat-history-item-head">
              <span class="chat-history-item-role">${escapeHtml(label)}</span>
              <time datetime="${escapeAttr(m.created_at || "")}">${escapeHtml(formatChatTimestamp(m.created_at))}</time>
            </div>
            <div class="chat-history-item-body">${escapeHtml(m.content || "")}</div>
          </article>`;
      })
      .join("");
  }

  const page = data.page || 1;
  const pages = data.pages || 1;
  const total = data.total ?? 0;
  chatHistoryModalPage = page;
  if (pageInfo) {
    pageInfo.textContent = `Page ${page} of ${pages} · ${total} message(s)`;
  }
  if (prevBtn) prevBtn.disabled = page <= 1;
  if (nextBtn) nextBtn.disabled = page >= pages;
}

async function openChatHistoryModal() {
  const overlay = $("#chat-history-overlay");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
  await loadChatHistoryModalPage(1);
}

function closeChatHistoryModal() {
  const overlay = $("#chat-history-overlay");
  if (!overlay) return;
  overlay.classList.add("hidden");
  overlay.setAttribute("aria-hidden", "true");
}

async function loadChatHistoryModalPage(page) {
  const host = $("#chat-history-list");
  if (host) host.innerHTML = '<p class="hint">Loading…</p>';
  try {
    const data = await api(
      `/api/chat/history?page=${encodeURIComponent(page)}&limit=10&order=desc`
    );
    renderChatHistoryModal(data);
  } catch (err) {
    if (host) host.innerHTML = `<p class="hint">Error: ${escapeHtml(err.message)}</p>`;
  }
}

async function downloadChatHistory() {
  const format = ($("#chat-download-format")?.value || "json").trim();
  const btn = $("#btn-chat-download");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(
      `/api/chat/history/export?format=${encodeURIComponent(format)}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }
    const blob = await res.blob();
    const disp = res.headers.get("Content-Disposition") || "";
    const match = disp.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : `chat-history.${format === "markdown" ? "md" : format}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message || "Download failed");
  } finally {
    if (btn) btn.disabled = false;
  }
}

(function initChatHistoryControls() {
  $("#btn-chat-clear")?.addEventListener("click", () => {
    if (chatBusy) return;
    clearChatScreen();
  });
  $("#btn-chat-history")?.addEventListener("click", () => {
    openChatHistoryModal().catch(() => {});
  });
  $("#btn-chat-history-close")?.addEventListener("click", closeChatHistoryModal);
  $("#chat-history-overlay")?.addEventListener("click", (e) => {
    if (e.target?.id === "chat-history-overlay") closeChatHistoryModal();
  });
  $("#btn-chat-history-prev")?.addEventListener("click", () => {
    if (chatHistoryModalPage > 1) {
      loadChatHistoryModalPage(chatHistoryModalPage - 1).catch(() => {});
    }
  });
  $("#btn-chat-history-next")?.addEventListener("click", () => {
    loadChatHistoryModalPage(chatHistoryModalPage + 1).catch(() => {});
  });
  $("#btn-chat-download")?.addEventListener("click", () => {
    downloadChatHistory().catch(() => {});
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeChatHistoryModal();
  });
})();

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
  sessionStorage.removeItem(CHAT_SCREEN_CLEARED_KEY);
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

function getRelevantSubCategories(category, options) {
  const byCat = options?.sub_categories_by_category || {};
  const cat = String(category || "").trim();
  if (!cat) return [];
  if (Array.isArray(byCat[cat])) {
    return [...byCat[cat]].sort((a, b) => a.localeCompare(b));
  }
  const key = Object.keys(byCat).find((k) => k.toLowerCase() === cat.toLowerCase());
  return key ? [...byCat[key]].sort((a, b) => a.localeCompare(b)) : [];
}

function orderedSubCategories(category, options) {
  const all = options?.sub_categories || [];
  const relevant = getRelevantSubCategories(category, options);
  const relevantSet = new Set(relevant.map((s) => s.toLowerCase()));
  const others = all
    .filter((s) => !relevantSet.has(String(s).toLowerCase()))
    .sort((a, b) => a.localeCompare(b));
  return [...relevant, ...others];
}

function splitOrderedSubCategories(category, options) {
  const cat = String(category || "").trim();
  const ordered = orderedSubCategories(cat, options);
  if (!cat) return { relevant: [], others: ordered };
  const relevant = getRelevantSubCategories(cat, options);
  const relevantSet = new Set(relevant.map((s) => s.toLowerCase()));
  const others = ordered.filter((s) => !relevantSet.has(String(s).toLowerCase()));
  return { relevant, others };
}

function renderDatalistOptions(datalistEl, items) {
  if (!datalistEl) return;
  datalistEl.innerHTML = (items || [])
    .map((c) => `<option value="${escapeAttr(c)}"></option>`)
    .join("");
}

function ensureReviewDatalists(options, category = "") {
  const host = $("#review-datalists");
  if (!host) return;
  const catId = "review-categories";
  const subId = "review-subcategories";
  host.innerHTML = `
    <datalist id="${catId}">${options.categories.map((c) => `<option value="${escapeAttr(c)}"></option>`).join("")}</datalist>
    <datalist id="${subId}"></datalist>
  `;
  renderDatalistOptions(document.getElementById(subId), orderedSubCategories(category, options));
}

function refreshReviewSubDatalist(category) {
  if (!reviewOptionsCache) return;
  renderDatalistOptions(
    document.getElementById("review-subcategories"),
    orderedSubCategories(category, reviewOptionsCache)
  );
}

function populateSubCategorySelect(sel, options, category, allLabel, current) {
  populateEditSelect(sel, orderedSubCategories(category, options), allLabel, current);
}

function reviewField(name, label, tooltip, controlHtml, { fullWidth = false } = {}) {
  const helpLabel = tooltip ? `Help: ${tooltip}` : "Help";
  return `
    <div class="review-field${fullWidth ? " review-field-full" : ""}">
      <div class="review-field-head">
        <label for="${name}">${escapeHtml(label)}</label>
        <button type="button" class="field-tip" title="${escapeAttr(tooltip)}" aria-label="${escapeAttr(helpLabel)}">?</button>
      </div>
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
let customRulesBusy = false;
let reviewSuggestBusy = false;

function setReviewPanelBusy(busy, { title, hint } = {}) {
  const overlay = $("#app-busy-overlay");
  document.querySelectorAll(".tab").forEach((t) => {
    t.disabled = busy;
  });
  if (overlay) {
    overlay.classList.toggle("hidden", !busy);
    overlay.setAttribute("aria-hidden", busy ? "false" : "true");
    const titleEl = overlay.querySelector(".review-busy-title");
    const hintEl = overlay.querySelector(".review-busy-hint");
    if (busy) {
      if (titleEl) titleEl.textContent = title || "Working…";
      if (hintEl) hintEl.textContent = hint || "Please wait. Do not leave this page.";
    }
  }
}

function setCustomRulesBusy(busy, { title, hint } = {}) {
  customRulesBusy = busy;
  setReviewPanelBusy(reviewSuggestBusy || customRulesBusy, busy ? { title, hint } : {});

  const saveBtn = $("#btn-custom-rule-save-apply");
  const reapplyBtn = $("#btn-custom-rules-reapply");
  const input = $("#custom-rule-input");
  const toggle = $("#btn-custom-rules-toggle");
  if (saveBtn) saveBtn.disabled = busy;
  if (reapplyBtn) reapplyBtn.disabled = busy;
  if (input) input.disabled = busy;
  if (toggle) toggle.disabled = busy;
}

function setReviewSuggestBusy(busy, { title, hint } = {}) {
  reviewSuggestBusy = busy;
  setReviewPanelBusy(reviewSuggestBusy || customRulesBusy, busy ? { title, hint } : {});

  const batchBtn = $("#btn-review-suggest-batch");
  const batchSelect = $("#review-suggest-batch");
  if (batchBtn) batchBtn.disabled = busy || customRulesBusy;
  if (batchSelect) batchSelect.disabled = busy || customRulesBusy;
}

function applyReviewSuggestionToCard(card, suggestion) {
  if (!card || !suggestion) return;
  const labels = suggestion.labels || {};
  const idx = card.dataset.reviewIdx;
  const setVal = (id, val) => {
    const el = card.querySelector(`#review-${idx}-${id}`);
    if (el && val != null && String(val).trim() !== "") el.value = val;
  };
  setVal("cat", labels.ai_category);
  setVal("sub", labels.ai_sub_category);
  setVal("flow", labels.flow_type);
  setVal("type", labels.expense_type);
  setVal("class", labels.classification);

  let note = card.querySelector(".review-suggest-note");
  if (!note) {
    note = document.createElement("div");
    note.className = "review-suggest-note";
    const meta = card.querySelector(".meta");
    if (meta) meta.after(note);
    else card.querySelector(".review-item-title")?.appendChild(note);
  }
  const conf = suggestion.confidence || "medium";
  const src = suggestion.source || "ai";
  const rationale = suggestion.rationale || "Suggested labels applied.";
  note.textContent = `✨ ${rationale} (${conf} · ${src})`;
  note.dataset.confidence = conf;
}

function formatReviewGroupCount(count) {
  const n = Number(count) || 0;
  return `${n} group${n === 1 ? "" : "s"} to review`;
}

function updateReviewBulkCount(count) {
  const toolbar = $("#review-bulk-toolbar");
  const countEl = $("#review-bulk-count");
  const n =
    typeof count === "number"
      ? count
      : document.querySelectorAll("#review-list .review-item").length;
  if (countEl) countEl.textContent = formatReviewGroupCount(n);
  if (toolbar) toolbar.classList.toggle("hidden", n <= 0);
  updateReviewWorkflow(n);
  return n;
}

async function runReviewBulkSuggest() {
  if (reviewSuggestBusy || customRulesBusy) return;

  const limit = Number($("#review-suggest-batch")?.value || 10);
  const cards = [...document.querySelectorAll(".review-item")].slice(0, limit);
  const statusEl = $("#review-suggest-status");
  if (!cards.length) {
    if (statusEl) statusEl.textContent = "No groups to suggest.";
    return;
  }

  setReviewSuggestBusy(true, {
    title: "Suggesting labels…",
    hint: `0 / ${cards.length} — starting`,
  });
  if (statusEl) statusEl.textContent = `Suggesting up to ${cards.length} group(s)…`;

  let lookupCount = 0;
  let llmCount = 0;
  const errors = [];

  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    const merchantKey = card.dataset.merchantKey;
    const transactionId = card.dataset.transactionId || null;
    setReviewSuggestBusy(true, {
      title: "Suggesting labels…",
      hint: `${i + 1} / ${cards.length} — ${merchantKey}`,
    });
    if (statusEl) {
      statusEl.textContent = `Suggesting ${i + 1} / ${cards.length}: ${merchantKey}`;
    }
    try {
      const mk = encodeURIComponent(merchantKey);
      const suggestion = await api(`/api/review/${mk}/suggest-labels`, {
        method: "POST",
        body: JSON.stringify({ transaction_id: transactionId }),
      });
      applyReviewSuggestionToCard(card, suggestion);
      const src = suggestion.source || "";
      if (
        src === "MerchantCategories" ||
        src === "BusinessCategoryRules" ||
        src === "CustomRules" ||
        src === "merchant_labels"
      ) {
        lookupCount += 1;
      } else if (src === "llm") {
        llmCount += 1;
      }
    } catch (err) {
      errors.push(`${merchantKey}: ${err.message}`);
    }
  }

  setReviewSuggestBusy(false);
  if (statusEl) {
    const errPart = errors.length ? ` · ${errors.length} error(s)` : "";
    statusEl.textContent = `Done — ${lookupCount} from lookups, ${llmCount} from AI${errPart}. Review and confirm each group.`;
  }
  if (errors.length) {
    console.warn("Bulk suggest errors:", errors);
  }
}

function showCustomRulesResult(res) {
  const resultEl = $("#custom-rules-result");
  if (!resultEl) return;
  resultEl.dataset.sticky = "1";
  let msg = res.message || "Done.";
  if (res.compile_errors?.length && res.ok !== false) {
    msg += `\n\nCompile errors:\n${res.compile_errors
      .map((e) => `• ${e.rule}: ${e.error}`)
      .join("\n")}`;
  }
  resultEl.textContent = msg;
  resultEl.classList.remove("custom-rules-result-ok", "custom-rules-result-err");
  resultEl.classList.add(res.ok === false ? "custom-rules-result-err" : "custom-rules-result-ok");
}

async function runCustomRulesWorkflow(mode, { ruleText } = {}) {
  const isSave = mode === "save-apply";
  if (isSave && !(ruleText || "").trim()) {
    alert("Enter rule text first.");
    return;
  }

  setCustomRulesBusy(true, {
    title: isSave ? "Saving and applying rule…" : "Re-applying all rules…",
    hint: isSave
      ? "Compiling your rule and updating matching transactions. Please wait."
      : "Compiling pending rules and updating transactions. Please wait.",
  });

  const input = $("#custom-rule-input");
  const resultEl = $("#custom-rules-result");
  if (resultEl) {
    resultEl.classList.remove("custom-rules-result-ok", "custom-rules-result-err");
    resultEl.textContent = isSave ? "Saving and applying rule…" : "Applying rules…";
    resultEl.dataset.sticky = "1";
  }

  try {
    const res = isSave
      ? await api("/api/custom-rules/save-apply", {
          method: "POST",
          body: JSON.stringify({ rule: ruleText.trim() }),
        })
      : await api("/api/custom-rules/compile-apply", { method: "POST" });

    if (isSave && input) input.value = "";
    if (res.rules) renderCustomRulesList(res.rules);
    else await loadCustomRules();
    showCustomRulesResult(res);
    await loadReview();
    loadStatus();
    reviewOptionsCache = null;
  } catch (err) {
    if (resultEl) {
      resultEl.dataset.sticky = "1";
      resultEl.textContent = err.message;
      resultEl.classList.remove("custom-rules-result-ok");
      resultEl.classList.add("custom-rules-result-err");
    }
  } finally {
    setCustomRulesBusy(false);
  }
}

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
  if (customRulesBusy) return;
  customRulesExpanded = !customRulesExpanded;
  loadCustomRules();
});

$("#btn-custom-rule-save-apply")?.addEventListener("click", () => {
  const rule = ($("#custom-rule-input")?.value || "").trim();
  runCustomRulesWorkflow("save-apply", { ruleText: rule });
});

$("#btn-custom-rules-reapply")?.addEventListener("click", () => {
  runCustomRulesWorkflow("reapply");
});

$("#btn-review-suggest-batch")?.addEventListener("click", () => {
  runReviewBulkSuggest();
});

let editOptionsCache = null;
let editSourceTx = null;
let editLastResults = [];
const EDIT_PAGE_SIZE = 50;
const EDIT_CLASSIFICATIONS = ["Personal", "Business"];
const BUSINESS_AI_CATEGORIES = new Set(["Business Expenses", "Business"]);

function alignReviewClassificationFromCategory(formEl) {
  if (!formEl) return;
  const category = String(formEl.querySelector('[name="category"]')?.value || "").trim();
  const classEl = formEl.querySelector('[name="classification"]');
  if (!classEl || !BUSINESS_AI_CATEGORIES.has(category)) return;
  classEl.value = "Business";
}
let editPageOffset = 0;
let editSearchTotal = 0;
let editSortBy = "date";
let editSortDir = "desc";
let cadenceSuggestBusy = false;
let cadenceNeedingCount = 0;
let cadenceFilterLabels = [];
let cadenceQueue = [];
let pendingCadenceMerchant = null;
let cadenceWizardStep = 1;
let cadenceMerchantTotal = 0;
let cadenceMerchantOffset = 0;
let cadenceLastMerchants = [];
const CADENCE_MERCHANT_PAGE_SIZE = 50;
const CADENCE_SKIP_VARIABLE_NOTE = "Variable spending — no fixed cadence";

function renderCadencePreviewLine(amounts) {
  if (!amounts) return "";
  return `Cash: ${formatMoney(amounts.cash)}  |  Core: ${formatMoney(amounts.core)}  |  Normalized: ${formatMoney(amounts.normalized)}/mo`;
}

function openCadenceForMerchant(merchantKey) {
  if (!uiShowCadence || !merchantKey) return;
  closeEditPanel();
  pendingCadenceMerchant = merchantKey;
  setTab("cadence");
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
  const category = ($("#edit-search-category")?.value || "").trim();
  populateSubCategorySelect(
    $("#edit-search-sub"),
    options,
    category,
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
}

function selectDisplayValue(el) {
  if (!el || !el.value) return "";
  if (el.tagName === "SELECT") {
    const opt = el.selectedOptions?.[0];
    return (opt?.textContent || el.value).trim();
  }
  return String(el.value).trim();
}

function renderActiveFilterChips({ host, filters, onClearOne, onClearAll }) {
  if (!host) return;
  const active = filters.filter((f) => f.value);
  if (!active.length) {
    host.classList.add("hidden");
    host.innerHTML = "";
    return;
  }
  host.classList.remove("hidden");
  const chips = active
    .map(
      (f) =>
        `<button type="button" class="filter-chip" data-filter-key="${escapeAttr(f.key)}" aria-label="Remove ${escapeAttr(f.label)} filter">
          <span class="filter-chip-label">${escapeHtml(f.label)}:</span>
          <span class="filter-chip-value">${escapeHtml(f.display || f.value)}</span>
          <span class="filter-chip-remove" aria-hidden="true">×</span>
        </button>`
    )
    .join("");
  host.innerHTML = `
    <span class="active-filters-title">Active filters</span>
    ${chips}
    <button type="button" class="btn-link filter-clear-all">Clear all</button>
  `;
  host.querySelectorAll(".filter-chip").forEach((btn) => {
    btn.addEventListener("click", () => onClearOne(btn.dataset.filterKey));
  });
  host.querySelector(".filter-clear-all")?.addEventListener("click", onClearAll);
}

const EDIT_FILTER_CONFIG = [
  { key: "q", label: "Search", sel: "#edit-search-q" },
  { key: "month", label: "Month", sel: "#edit-search-month" },
  { key: "category", label: "Category", sel: "#edit-search-category" },
  { key: "sub_category", label: "Sub-category", sel: "#edit-search-sub" },
  { key: "expense_type", label: "Expense type", sel: "#edit-search-expense-type" },
  { key: "classification", label: "Classification", sel: "#edit-search-classification" },
];

const CADENCE_FILTER_CONFIG = [
  { key: "q", label: "Search", sel: "#cadence-search-q" },
  { key: "month", label: "Month", sel: "#cadence-search-month" },
  { key: "category", label: "Category", sel: "#cadence-search-category" },
  { key: "sub_category", label: "Sub-category", sel: "#cadence-search-sub" },
  { key: "expense_type", label: "Expense type", sel: "#cadence-search-expense-type" },
  { key: "classification", label: "Classification", sel: "#cadence-search-classification" },
];

function collectConfiguredFilters(config) {
  return config.map(({ key, label, sel }) => {
    const el = $(sel);
    const value = el ? String(el.value || "").trim() : "";
    return { key, label, value, display: selectDisplayValue(el) };
  });
}

function clearConfiguredFilter(config, key) {
  const entry = config.find((f) => f.key === key);
  if (!entry) return;
  const el = $(entry.sel);
  if (el) el.value = "";
}

function clearAllConfiguredFilters(config) {
  config.forEach(({ sel }) => {
    const el = $(sel);
    if (el) el.value = "";
  });
}

function renderEditActiveFilters() {
  renderActiveFilterChips({
    host: $("#edit-active-filters"),
    filters: collectConfiguredFilters(EDIT_FILTER_CONFIG),
    onClearOne: (key) => {
      clearConfiguredFilter(EDIT_FILTER_CONFIG, key);
      renderEditActiveFilters();
      runEditSearch({ resetPage: true }).catch(() => {});
    },
    onClearAll: () => {
      clearAllConfiguredFilters(EDIT_FILTER_CONFIG);
      renderEditActiveFilters();
      runEditSearch({ resetPage: true }).catch(() => {});
    },
  });
}

function renderCadenceActiveFilters() {
  renderActiveFilterChips({
    host: $("#cadence-active-filters"),
    filters: collectConfiguredFilters(CADENCE_FILTER_CONFIG),
    onClearOne: (key) => {
      clearConfiguredFilter(CADENCE_FILTER_CONFIG, key);
      renderCadenceActiveFilters();
      loadCadenceScope({ resetPage: true }).catch(() => {});
    },
    onClearAll: () => {
      clearAllConfiguredFilters(CADENCE_FILTER_CONFIG);
      renderCadenceActiveFilters();
      loadCadenceScope({ resetPage: true }).catch(() => {});
    },
  });
}

function editSortHeader(label, field) {
  const active = editSortBy === field;
  const arrow = active ? (editSortDir === "asc" ? " ▲" : " ▼") : "";
  return `<th class="edit-sort-th" data-sort="${field}" scope="col" tabindex="0" aria-sort="${active ? editSortDir + "ending" : "none"}">${escapeHtml(label)}${arrow}</th>`;
}

function attachComboField(wrapper, getOptions, { sectioned = false, getCategory = null, onSelect = null } = {}) {
  if (!wrapper || wrapper.dataset.comboReady === "1") return;
  const input = wrapper.querySelector("input");
  const btn = wrapper.querySelector(".combo-toggle");
  const menu = wrapper.querySelector(".combo-menu");
  if (!input || !btn || !menu) return;

  function getOptionsContext() {
    const raw = typeof getOptions === "function" ? getOptions() : getOptions;
    if (raw && !Array.isArray(raw) && raw.sub_categories) return raw;
    const subCategories = Array.isArray(raw) ? raw : [];
    return {
      sub_categories: subCategories,
      sub_categories_by_category:
        editOptionsCache?.sub_categories_by_category
        || reviewOptionsCache?.sub_categories_by_category
        || {},
    };
  }

  function renderMenu(filterText = "") {
    const q = filterText.trim().toLowerCase();
    if (!sectioned) {
      const raw = typeof getOptions === "function" ? getOptions() : getOptions;
      const allItems = Array.isArray(raw) ? raw : raw?.sub_categories || [];
      const items = q ? allItems.filter((o) => o.toLowerCase().includes(q)) : allItems;
      menu.innerHTML = items
        .map((o) => `<li role="option" tabindex="-1">${escapeHtml(o)}</li>`)
        .join("");
      return;
    }

    const category = getCategory ? String(getCategory() || "").trim() : "";
    const { relevant, others } = splitOrderedSubCategories(category, getOptionsContext());
    const ordered = [...relevant, ...others];
    const items = q ? ordered.filter((o) => o.toLowerCase().includes(q)) : ordered;

    if (!sectioned || !category || q || !relevant.length) {
      menu.innerHTML = items
        .map((o) => `<li role="option" tabindex="-1">${escapeHtml(o)}</li>`)
        .join("");
      return;
    }

    const relevantItems = relevant.filter((o) => !q || o.toLowerCase().includes(q));
    const otherItems = others.filter((o) => !q || o.toLowerCase().includes(q));
    let html = `<li class="combo-section" aria-hidden="true">Under ${escapeHtml(category)}</li>`;
    html += relevantItems
      .map((o) => `<li role="option" tabindex="-1">${escapeHtml(o)}</li>`)
      .join("");
    if (otherItems.length) {
      html += `<li class="combo-divider" role="separator"></li>`;
      html += `<li class="combo-section" aria-hidden="true">Other sub-categories</li>`;
      html += otherItems
        .map((o) => `<li role="option" tabindex="-1">${escapeHtml(o)}</li>`)
        .join("");
    }
    menu.innerHTML = html;
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

  input.addEventListener("focus", () => {
    if (sectioned) renderMenu(input.value);
  });

  menu.addEventListener("click", (e) => {
    const li = e.target.closest('li[role="option"]');
    if (!li) return;
    const value = li.textContent.trim();
    input.value = value;
    hideMenu();
    input.focus();
    if (onSelect) onSelect(value);
  });

  input.addEventListener("input", () => {
    if (!menu.classList.contains("hidden")) renderMenu(input.value);
  });

  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target)) hideMenu();
  });

  wrapper.dataset.comboReady = "1";
}

function wireSubCategoryFilterRerank(categorySelector, subSelector, getOptions) {
  const catEl = $(categorySelector);
  if (!catEl || catEl.dataset.subRerankReady === "1") return;
  catEl.addEventListener("change", () => {
    const options = typeof getOptions === "function" ? getOptions() : getOptions;
    populateSubCategorySelect(
      $(subSelector),
      options,
      catEl.value,
      "All sub-categories",
      $(subSelector)?.value
    );
  });
  catEl.dataset.subRerankReady = "1";
}

function setupEditCategoryControls(options) {
  populateEditSearchCategorySelect(options.categories || []);
  populateEditAdvancedFilters(options);
  wireSubCategoryFilterRerank("#edit-search-category", "#edit-search-sub", () => editOptionsCache);
  if (!editCombosInitialized) {
    attachComboField(
      document.querySelector('[data-combo="edit-merchant-key"]'),
      () => editOptionsCache?.merchant_keys || [],
      { onSelect: (value) => applyEditMerchantLabelPrefill(value) }
    );
    attachComboField(
      document.querySelector('[data-combo="edit-ai-category"]'),
      () => editOptionsCache?.categories || []
    );
    attachComboField(
      document.querySelector('[data-combo="edit-ai-sub"]'),
      () => editOptionsCache?.sub_categories || [],
      {
        sectioned: true,
        getCategory: () => ($("#edit-ai-category")?.value || "").trim(),
      }
    );
    editCombosInitialized = true;
  }
}

function getEditMerchantLabelMap() {
  const map = new Map();
  (editOptionsCache?.merchants || []).forEach((row) => {
    const key = String(row.merchant_key || "").trim();
    if (key) map.set(key, row);
  });
  return map;
}

function applyEditMerchantLabelPrefill(merchantKey) {
  const key = String(merchantKey || "").trim();
  if (!key) return;
  const row = getEditMerchantLabelMap().get(key);
  if (!row) return;
  const catInput = $("#edit-ai-category");
  const subInput = $("#edit-ai-sub");
  const flowSel = $("#edit-label-flow-type");
  const expenseSel = $("#edit-label-expense-type");
  const classSel = $("#edit-label-classification");
  if (catInput && row.ai_category) catInput.value = row.ai_category;
  if (subInput && row.ai_sub_category) subInput.value = row.ai_sub_category;
  if (flowSel && row.flow_type) {
    flowSel.value = row.flow_type === "Income" ? "Income" : "Expense";
  }
  if (expenseSel && row.expense_type) {
    expenseSel.value = row.expense_type === "Fixed" ? "Fixed" : "Variable";
  }
  if (classSel && row.classification && EDIT_CLASSIFICATIONS.includes(row.classification)) {
    classSel.value = row.classification;
  }
}

function renderEditResults(transactions) {
  if (!transactions.length) {
    return '<p class="hint">No transactions match your search.</p>';
  }
  const rows = transactions
    .map((tx) => {
      const category = tx.ai_category || "—";
      const sub = tx.ai_sub_category || "—";
      const expenseType = tx.expense_type || "—";
      const classification = tx.classification || "—";
      const merchant = tx.merchant_key || "";
      const simpleDesc = (tx.simple_description || "").trim();
      const cadenceBtn = uiShowCadence
        ? `<button type="button" class="btn-link btn-edit-cadence-link" data-merchant-key="${escapeAttr(merchant)}" title="Open Cadence tab for this merchant">Cadence</button>`
        : "";
      return `
        <tr>
          <td>${escapeHtml(tx.date || "")}</td>
          <td class="amount">${escapeHtml(formatMoney(tx.amount))}</td>
          <td>${escapeHtml(simpleDesc || "—")}</td>
          <td>${escapeHtml(merchant)}</td>
          <td>${escapeHtml(category)}</td>
          <td>${escapeHtml(sub)}</td>
          <td>${escapeHtml(expenseType)}</td>
          <td>${escapeHtml(classification)}</td>
          <td class="edit-row-actions">
            <button type="button" class="btn-edit-row" data-tx-id="${escapeAttr(tx.transaction_id)}">Edit</button>
            ${cadenceBtn}
          </td>
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
          <th scope="col">Simple description</th>
          ${editSortHeader("Merchant", "merchant")}
          ${editSortHeader("Category", "ai_category")}
          <th scope="col">Sub-category</th>
          <th scope="col">Type</th>
          <th scope="col">Class</th>
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
          field === "merchant" || field === "ai_category" || field === "classification"
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
    const simpleDesc = (tx.simple_description || tx.description || "").trim();
    li.innerHTML = `
      <input type="checkbox" class="edit-match-cb" value="${escapeAttr(tx.transaction_id)}" checked />
      <div class="edit-match-item-body">
        <div class="edit-match-item-title">${escapeHtml(simpleDesc || tx.merchant_key || "")} · ${escapeHtml(formatMoney(tx.amount))}</div>
        <div class="edit-match-item-meta">${escapeHtml(tx.date || "")} · ${escapeHtml(tx.merchant_key || "")} · ${escapeHtml(labels)}</div>
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
  const merchantInput = $("#edit-merchant-key");
  const merchantSaveCb = $("#edit-update-merchant-label");
  const catInput = $("#edit-ai-category");
  const subInput = $("#edit-ai-sub");
  const flowSel = $("#edit-label-flow-type");
  const expenseSel = $("#edit-label-expense-type");
  const classSel = $("#edit-label-classification");
  const resultEl = $("#edit-apply-result");
  if (resultEl) resultEl.textContent = "";
  if (summary) {
    const simpleDesc = (tx.simple_description || "").trim();
    const originalDesc = (tx.original_description || "").trim();
    summary.innerHTML = `
      Bank: ${escapeHtml(simpleDesc || originalDesc || "—")}<br>
      ${originalDesc && simpleDesc && originalDesc !== simpleDesc
        ? `Original: ${escapeHtml(originalDesc)}<br>`
        : ""}
      ${escapeHtml(tx.date || "")} · ${escapeHtml(formatMoney(tx.amount))}<br>
      Current labels: ${escapeHtml(tx.ai_category || "—")}${tx.ai_sub_category ? ` / ${escapeHtml(tx.ai_sub_category)}` : ""}<br>
      Flow: ${escapeHtml(tx.flow_type || "Expense")} · Expense: ${escapeHtml(tx.expense_type || "—")} · Class: ${escapeHtml(tx.classification || "—")}
    `;
  }
  if (merchantInput) merchantInput.value = tx.merchant_key || "";
  if (merchantSaveCb) merchantSaveCb.checked = true;
  if (catInput) catInput.value = tx.ai_category || "";
  if (subInput) subInput.value = tx.ai_sub_category || "";
  if (flowSel) flowSel.value = tx.flow_type === "Income" ? "Income" : "Expense";
  if (expenseSel) expenseSel.value = tx.expense_type === "Fixed" ? "Fixed" : "Variable";
  if (classSel) {
    classSel.value = EDIT_CLASSIFICATIONS.includes(tx.classification) ? tx.classification : "Personal";
  }
  const scope = document.querySelector('input[name="edit-scope"]:checked')?.value || "single";
  loadEditMatches(scope).catch((err) => {
    const list = $("#edit-match-list");
    if (list) list.innerHTML = `<li class="edit-match-item">Error: ${escapeHtml(err.message)}</li>`;
  });
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
  const merchantInput = $("#edit-merchant-key");
  const merchantSaveCb = $("#edit-update-merchant-label");
  if (merchantInput) merchantInput.value = "";
  if (merchantSaveCb) merchantSaveCb.checked = true;
  const catInput = $("#edit-ai-category");
  const subInput = $("#edit-ai-sub");
  const flowSel = $("#edit-label-flow-type");
  const expenseSel = $("#edit-label-expense-type");
  const classSel = $("#edit-label-classification");
  if (catInput) catInput.value = "";
  if (subInput) subInput.value = "";
  if (flowSel) flowSel.value = "Expense";
  if (expenseSel) expenseSel.value = "Variable";
  if (classSel) classSel.value = "Personal";
  const singleScope = document.querySelector('input[name="edit-scope"][value="single"]');
  if (singleScope) singleScope.checked = true;
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
  host.querySelectorAll(".btn-edit-cadence-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      openCadenceForMerchant(btn.dataset.merchantKey || "");
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

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (month) params.set("month", month);
  if (category) params.set("category", category);
  if (subCategory) params.set("sub_category", subCategory);
  if (expenseType) params.set("expense_type", expenseType);
  if (classification) params.set("classification", classification);
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
  renderEditActiveFilters();
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

function getCadenceFilterPayload() {
  return {
    q: ($("#cadence-search-q")?.value || "").trim(),
    month: $("#cadence-search-month")?.value || "",
    category: ($("#cadence-search-category")?.value || "").trim(),
    sub_category: ($("#cadence-search-sub")?.value || "").trim(),
    expense_type: ($("#cadence-search-expense-type")?.value || "").trim(),
    classification: ($("#cadence-search-classification")?.value || "").trim(),
  };
}

function buildCadenceFilterQueryString() {
  const f = getCadenceFilterPayload();
  const params = new URLSearchParams();
  if (f.q) params.set("q", f.q);
  if (f.month) params.set("month", f.month);
  if (f.category) params.set("category", f.category);
  if (f.sub_category) params.set("sub_category", f.sub_category);
  if (f.expense_type) params.set("expense_type", f.expense_type);
  if (f.classification) params.set("classification", f.classification);
  return params.toString();
}

function formatCadenceMerchantCount(count, filterLabels) {
  const n = Number(count) || 0;
  let text = `${n} merchant${n === 1 ? "" : "s"} need cadence`;
  if (filterLabels?.length) {
    text += " (filtered)";
  }
  return text;
}

function setCadenceWizardStep(step) {
  cadenceWizardStep = Math.max(1, Math.min(3, step));
  setWorkflowStepper($("#cadence-stepper"), cadenceWizardStep);
  for (let i = 1; i <= 3; i++) {
    const panel = $(`#cadence-step-${i}`);
    if (panel) panel.classList.toggle("hidden", i !== cadenceWizardStep);
  }
  if (cadenceWizardStep === 2) {
    updateCadenceSuggestSummary();
  }
  updateCadenceQueueEmptyState();
}

function updateCadenceSuggestSummary() {
  const el = $("#cadence-suggest-summary");
  if (!el) return;
  const n = cadenceNeedingCount;
  el.textContent = n
    ? `${n} merchant(s) in scope. Choose a batch size and run AI suggest.`
    : "No merchants need cadence for this scope. Go back and adjust filters.";
}

function updateCadenceNextButton() {
  const btn = $("#btn-cadence-next-suggest");
  if (btn) btn.disabled = cadenceNeedingCount <= 0;
}

function updateCadenceQueueEmptyState() {
  const empty = $("#cadence-queue-empty");
  const host = $("#cadence-queue");
  if (!empty) return;
  const showEmpty = cadenceWizardStep === 3 && !cadenceQueue.length;
  empty.classList.toggle("hidden", !showEmpty);
  if (showEmpty && host) {
    host.classList.add("hidden");
  }
}

function renderCadenceMerchantList(merchants, total) {
  const host = $("#cadence-merchant-list");
  const meta = $("#cadence-merchant-meta");
  if (!host) return;
  cadenceMerchantTotal = Number(total) || 0;
  cadenceLastMerchants = merchants || [];
  updateCadenceNextButton();
  if (!cadenceMerchantTotal) {
    host.innerHTML = '<p class="hint">No merchants need cadence for these filters.</p>';
    if (meta) meta.textContent = "No merchants match your filters.";
    updateCadencePagination();
    return;
  }
  const shown = cadenceLastMerchants.length;
  if (meta) {
    const start = cadenceMerchantOffset + 1;
    const end = cadenceMerchantOffset + shown;
    meta.textContent = `Showing ${start}–${end} of ${cadenceMerchantTotal} merchant(s)`;
  }
  if (!shown) {
    host.innerHTML = '<p class="hint">No merchants on this page.</p>';
    updateCadencePagination();
    return;
  }
  const rows = cadenceLastMerchants
    .map((m) => {
      const merchant = m.merchant_key || "";
      const category = m.ai_category || "—";
      return `
        <tr>
          <td>${escapeHtml(merchant)}</td>
          <td>${escapeHtml(category)}</td>
          <td>${Number(m.tx_count) || 0}</td>
          <td>${Number(m.month_count) || 0}</td>
          <td class="edit-row-actions">
            <button type="button" class="btn-link btn-cadence-skip-variable" data-merchant-key="${escapeAttr(merchant)}" title="Mark as variable spending — no cadence rule">Skip</button>
          </td>
        </tr>`;
    })
    .join("");
  host.innerHTML = `
    <table class="edit-results-table cadence-merchant-table">
      <thead>
        <tr>
          <th scope="col">Merchant</th>
          <th scope="col">Category</th>
          <th scope="col"># of Transactions</th>
          <th scope="col"># of Months</th>
          <th scope="col"></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  host.querySelectorAll(".btn-cadence-skip-variable").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mk = btn.dataset.merchantKey || "";
      if (mk) saveCadenceSkipVariable(mk).catch((err) => alert(err.message));
    });
  });
  updateCadencePagination();
}

function updateCadencePagination() {
  const bar = $("#cadence-pagination");
  const info = $("#cadence-page-info");
  const prev = $("#btn-cadence-prev");
  const next = $("#btn-cadence-next");
  if (!bar || !info) return;

  const total = cadenceMerchantTotal;
  const pageCount = Math.max(1, Math.ceil(total / CADENCE_MERCHANT_PAGE_SIZE));
  const page = Math.floor(cadenceMerchantOffset / CADENCE_MERCHANT_PAGE_SIZE) + 1;

  if (total <= CADENCE_MERCHANT_PAGE_SIZE) {
    bar.classList.add("hidden");
    return;
  }

  bar.classList.remove("hidden");
  const start = cadenceMerchantOffset + 1;
  const end = Math.min(cadenceMerchantOffset + cadenceLastMerchants.length, total);
  info.textContent = `Page ${page} of ${pageCount} · rows ${start}–${end} of ${total}`;
  if (prev) prev.disabled = cadenceMerchantOffset <= 0;
  if (next) next.disabled = cadenceMerchantOffset + CADENCE_MERCHANT_PAGE_SIZE >= total;
}

async function loadCadenceMerchantsPreview({ resetPage = false } = {}) {
  const host = $("#cadence-merchant-list");
  if (resetPage) cadenceMerchantOffset = 0;
  if (host) host.innerHTML = '<p class="hint">Loading merchants…</p>';
  try {
    const qs = buildCadenceFilterQueryString();
    const params = new URLSearchParams(qs);
    params.set("limit", String(CADENCE_MERCHANT_PAGE_SIZE));
    params.set("offset", String(cadenceMerchantOffset));
    const path = `/api/transactions/cadence-suggest-merchants?${params.toString()}`;
    const res = await api(path);
    renderCadenceMerchantList(res.merchants || [], res.total ?? 0);
  } catch (err) {
    if (host) host.innerHTML = `<p class="hint">Error: ${escapeHtml(err.message)}</p>`;
    cadenceMerchantTotal = 0;
    cadenceLastMerchants = [];
    updateCadenceNextButton();
    updateCadencePagination();
  }
}

async function loadCadenceScope({ resetPage = false } = {}) {
  await Promise.all([
    loadCadenceBulkCount(),
    loadCadenceMerchantsPreview({ resetPage }),
  ]);
}

function updateReviewWorkflow(itemCount) {
  const empty = $("#review-empty-state");
  const stepper = $("#review-stepper");
  const n = Number(itemCount) || 0;
  if (empty) empty.classList.toggle("hidden", n > 0);
  if (n > 0) {
    setWorkflowStepper(stepper, 2);
  } else {
    setWorkflowStepper(stepper, 1);
  }
}

function updateCadenceBulkCount(count, filterLabels) {
  const countEl = $("#cadence-bulk-count");
  if (typeof count === "number") {
    cadenceNeedingCount = count;
  }
  if (filterLabels) {
    cadenceFilterLabels = filterLabels;
  }
  const n = cadenceNeedingCount;
  if (countEl) countEl.textContent = formatCadenceMerchantCount(n, cadenceFilterLabels);
  renderCadenceActiveFilters();
  updateCadenceNextButton();
  updateCadenceClearButton();
  return n;
}

async function loadCadenceBulkCount() {
  try {
    const qs = buildCadenceFilterQueryString();
    const path = qs
      ? `/api/transactions/cadence-suggest-count?${qs}`
      : "/api/transactions/cadence-suggest-count";
    const res = await api(path);
    return updateCadenceBulkCount(res.count, res.filter_labels || []);
  } catch {
    return updateCadenceBulkCount(0, []);
  }
}

async function loadCadenceRulesList() {
  const list = $("#cadence-rules-list");
  if (!list) return;
  try {
    const data = await api("/api/cadence-rules");
    const rules = (data.rules || []).filter((r) => r.enabled !== 0 && r.enabled !== false);
    if (!rules.length) {
      list.innerHTML = '<li class="custom-rules-empty">No saved cadence rules yet.</li>';
      return;
    }
    list.innerHTML = rules
      .map((rule) => {
        const kind = cadenceRuleKindDisplay(rule);
        const period =
          rule.period_count && rule.period_unit
            ? ` · every ${rule.period_count} ${rule.period_unit}`
            : "";
        const runRate =
          rule.include_in_run_rate === 0 || rule.include_in_run_rate === false
            ? " · excluded from core"
            : rule.include_in_run_rate === 1 || rule.include_in_run_rate === true
              ? " · in core"
              : "";
        const note = rule.notes ? ` — ${rule.notes}` : "";
        return `<li><span class="rule-text"><strong>${escapeHtml(rule.merchant_key || "")}</strong>: ${escapeHtml(kind)}${escapeHtml(period)}${escapeHtml(runRate)}${escapeHtml(note)}</span></li>`;
      })
      .join("");
  } catch (err) {
    list.innerHTML = `<li class="custom-rules-empty">Could not load rules: ${escapeHtml(err.message)}</li>`;
  }
}

function updateCadenceClearButton() {
  const btn = $("#btn-cadence-clear-queue");
  if (!btn) return;
  btn.disabled = cadenceSuggestBusy || cadenceQueue.length === 0;
}

function clearCadenceQueue() {
  cadenceQueue = [];
  renderCadenceQueue();
  const statusEl = $("#cadence-suggest-status");
  if (statusEl) statusEl.textContent = "Cleared cadence suggestions.";
  updateCadenceClearButton();
  updateCadenceQueueEmptyState();
}

function renderCadenceQueue() {
  const host = $("#cadence-queue");
  if (!host) return;
  if (!cadenceQueue.length) {
    host.innerHTML = "";
    host.classList.add("hidden");
    updateCadenceBulkCount();
    updateCadenceClearButton();
    updateCadenceQueueEmptyState();
    return;
  }
  host.classList.remove("hidden");
  updateCadenceQueueEmptyState();
  host.innerHTML = `
    <p class="hint" style="margin:0 0 0.25rem">Review each suggestion — save a rule, skip variable merchants, or dismiss.</p>
  `;
  cadenceQueue.forEach((proposal, idx) => {
    const el = document.createElement("div");
    el.className = "edit-cadence-queue-item";
    el.dataset.queueIdx = String(idx);
    el.dataset.merchantKey = proposal.merchant_key || "";
    const period =
      proposal.period_count && proposal.period_unit
        ? `Every ${proposal.period_count} ${proposal.period_unit}`
        : "—";
    const src = proposal.source || "ai";
    const conf = proposal.confidence || "medium";
    const insightText = (proposal.insight || "").replace(/\*\*/g, "");
    const preview = renderCadencePreviewLine(proposal.effective_amounts);
    el.innerHTML = `
      <h3>${escapeHtml(proposal.merchant_key || "Merchant")}</h3>
      <div class="edit-cadence-queue-meta">
        ${escapeHtml(cadenceKindLabel(proposal.cadence_kind))} · ${escapeHtml(period)} · ${escapeHtml(conf)} · ${escapeHtml(src)}
      </div>
      <p class="edit-cadence-queue-insight">${escapeHtml(insightText.slice(0, 280))}${insightText.length > 280 ? "…" : ""}</p>
      <p class="edit-cadence-preview">${escapeHtml(preview)}</p>
      <div class="edit-cadence-queue-actions">
        <button type="button" class="btn-primary btn-cadence-review" data-queue-idx="${idx}">Review &amp; save</button>
        <button type="button" class="btn-secondary btn-cadence-skip-variable" data-merchant-key="${escapeAttr(proposal.merchant_key || "")}" data-queue-idx="${idx}">Skip — variable</button>
        <button type="button" class="btn-secondary btn-cadence-dismiss" data-queue-idx="${idx}">Dismiss</button>
      </div>
    `;
    host.appendChild(el);
  });
  host.querySelectorAll(".btn-cadence-review").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.queueIdx);
      const proposal = cadenceQueue[i];
      if (proposal) openCadenceInsightModal(proposal);
    });
  });
  host.querySelectorAll(".btn-cadence-dismiss").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.queueIdx);
      cadenceQueue.splice(i, 1);
      renderCadenceQueue();
    });
  });
  host.querySelectorAll(".btn-cadence-skip-variable").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mk = btn.dataset.merchantKey || "";
      if (!mk) return;
      saveCadenceSkipVariable(mk).catch((err) => alert(err.message));
    });
  });
  updateCadenceBulkCount();
  updateCadenceClearButton();
  if (cadenceQueue.length) {
    setCadenceWizardStep(3);
  }
}

function removeMerchantFromCadenceQueue(merchantKey) {
  if (!merchantKey) return;
  cadenceQueue = cadenceQueue.filter((p) => p.merchant_key !== merchantKey);
  renderCadenceQueue();
  loadCadenceBulkCount().catch(() => {});
}

async function runCadenceBulkSuggest() {
  if (cadenceSuggestBusy) return;
  const limit = Number($("#cadence-suggest-batch")?.value || 10);
  const statusEl = $("#cadence-suggest-status");
  const batchBtn = $("#btn-cadence-suggest-batch");
  const batchSelect = $("#cadence-suggest-batch");

  cadenceSuggestBusy = true;
  if (batchBtn) batchBtn.disabled = true;
  if (batchSelect) batchSelect.disabled = true;
  updateCadenceClearButton();
  if (statusEl) statusEl.textContent = `Suggesting cadence for up to ${limit} merchant(s)…`;

  try {
    const res = await api("/api/transactions/cadence-suggest-batch", {
      method: "POST",
      body: JSON.stringify({
        limit,
        ...getCadenceFilterPayload(),
      }),
    });
    const incoming = res.suggestions || [];
    cadenceQueue = incoming.filter((p) => p.merchant_key);
    renderCadenceQueue();
    if (typeof res.queue_total === "number") {
      updateCadenceBulkCount(res.queue_total, res.filter_labels || cadenceFilterLabels);
    } else {
      await loadCadenceBulkCount();
    }
    if (statusEl) {
      const errPart = res.error_count ? ` · ${res.error_count} error(s)` : "";
      statusEl.textContent =
        (res.message || `Queued ${incoming.length} suggestion(s).`) + errPart;
    }
    if (res.error_count) {
      console.warn("Cadence bulk suggest errors:", res.errors);
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `Error: ${err.message}`;
  } finally {
    cadenceSuggestBusy = false;
    if (batchBtn) batchBtn.disabled = false;
    if (batchSelect) batchSelect.disabled = false;
    updateCadenceClearButton();
  }
}

function populateCadenceFilters(options, status) {
  const monthSel = $("#cadence-search-month");
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
  populateEditSelect(
    $("#cadence-search-category"),
    options.categories,
    "All categories",
    $("#cadence-search-category")?.value
  );
  populateSubCategorySelect(
    $("#cadence-search-sub"),
    options,
    ($("#cadence-search-category")?.value || "").trim(),
    "All sub-categories",
    $("#cadence-search-sub")?.value
  );
  populateEditSelect(
    $("#cadence-search-expense-type"),
    options.expense_types,
    "All expense types",
    $("#cadence-search-expense-type")?.value
  );
  populateEditSelect(
    $("#cadence-search-classification"),
    options.classifications?.length ? options.classifications : EDIT_CLASSIFICATIONS,
    "All classifications",
    $("#cadence-search-classification")?.value
  );
  wireSubCategoryFilterRerank("#cadence-search-category", "#cadence-search-sub", () => editOptionsCache);
}

async function loadCadencePanel() {
  try {
    const [status, options] = await Promise.all([
      api("/api/status"),
      editOptionsCache ? Promise.resolve(editOptionsCache) : api("/api/review/options"),
    ]);
    editOptionsCache = options;
    populateCadenceFilters(options, status);
    if (pendingCadenceMerchant) {
      const qEl = $("#cadence-search-q");
      if (qEl) qEl.value = pendingCadenceMerchant;
      pendingCadenceMerchant = null;
      renderCadenceActiveFilters();
    }
    await loadCadenceScope();
    renderCadenceQueue();
    setCadenceWizardStep(cadenceQueue.length ? 3 : 1);
  } catch (err) {
    const statusEl = $("#cadence-suggest-status");
    if (statusEl) statusEl.textContent = `Error: ${err.message}`;
  }
}

$("#btn-cadence-next-suggest")?.addEventListener("click", () => {
  setCadenceWizardStep(2);
});

$("#btn-cadence-back-scope")?.addEventListener("click", () => {
  setCadenceWizardStep(1);
});

$("#btn-cadence-back-suggest")?.addEventListener("click", () => {
  setCadenceWizardStep(2);
});

$("#btn-cadence-start-over")?.addEventListener("click", () => {
  clearCadenceQueue();
  cadenceMerchantOffset = 0;
  setCadenceWizardStep(1);
  loadCadenceScope({ resetPage: true }).catch(() => {});
});

$("#btn-cadence-suggest-batch")?.addEventListener("click", () => {
  runCadenceBulkSuggest().catch(() => {});
});

$("#btn-cadence-clear-queue")?.addEventListener("click", () => {
  clearCadenceQueue();
});

$("#cadence-search-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  renderCadenceActiveFilters();
  setCadenceWizardStep(1);
  loadCadenceScope({ resetPage: true }).catch(() => {});
});

$("#btn-cadence-prev")?.addEventListener("click", () => {
  if (cadenceMerchantOffset <= 0) return;
  cadenceMerchantOffset = Math.max(0, cadenceMerchantOffset - CADENCE_MERCHANT_PAGE_SIZE);
  loadCadenceMerchantsPreview().catch(() => {});
});

$("#btn-cadence-next")?.addEventListener("click", () => {
  if (cadenceMerchantOffset + CADENCE_MERCHANT_PAGE_SIZE >= cadenceMerchantTotal) return;
  cadenceMerchantOffset += CADENCE_MERCHANT_PAGE_SIZE;
  loadCadenceMerchantsPreview().catch(() => {});
});

[
  "#cadence-search-q",
  "#cadence-search-month",
  "#cadence-search-category",
  "#cadence-search-sub",
  "#cadence-search-expense-type",
  "#cadence-search-classification",
].forEach((sel) => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener("change", () => {
    renderCadenceActiveFilters();
    if (cadenceWizardStep === 1) loadCadenceScope({ resetPage: true }).catch(() => {});
    else loadCadenceBulkCount().catch(() => {});
  });
  if (el.tagName === "INPUT") {
    el.addEventListener("input", () => {
      clearTimeout(el._cadenceCountTimer);
      el._cadenceCountTimer = setTimeout(() => {
        renderCadenceActiveFilters();
        if (cadenceWizardStep === 1) loadCadenceScope({ resetPage: true }).catch(() => {});
        else loadCadenceBulkCount().catch(() => {});
      }, 400);
    });
  }
});

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

[
  "#edit-search-q",
  "#edit-search-month",
  "#edit-search-category",
  "#edit-search-sub",
  "#edit-search-expense-type",
  "#edit-search-classification",
].forEach((sel) => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener("change", () => {
    renderEditActiveFilters();
  });
  if (el.tagName === "INPUT") {
    el.addEventListener("input", () => {
      clearTimeout(el._editFilterChipTimer);
      el._editFilterChipTimer = setTimeout(() => {
        renderEditActiveFilters();
      }, 400);
    });
  }
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

function cadenceRuleKindDisplay(rule) {
  const note = (rule?.notes || "").trim();
  if (rule?.cadence_kind === "unknown" && note.startsWith("Variable spending")) {
    return "Variable (no cadence)";
  }
  return cadenceKindLabel(rule?.cadence_kind);
}

async function saveCadenceSkipVariable(merchantKey) {
  const mk = (merchantKey || "").trim();
  if (!mk) return;
  await api("/api/cadence-rules", {
    method: "POST",
    body: JSON.stringify({
      merchant_key: mk,
      cadence_kind: "unknown",
      period_count: null,
      period_unit: null,
      include_in_run_rate: true,
      notes: CADENCE_SKIP_VARIABLE_NOTE,
    }),
  });
  closeCadenceInsightModal();
  removeMerchantFromCadenceQueue(mk);
  await Promise.all([
    loadCadenceBulkCount(),
    loadCadenceMerchantsPreview(),
    loadCadenceRulesList(),
    loadStatus(),
  ]);
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

  const canSaveMerchant = Boolean(proposal.recommend_save_rule);
  const hasTx = Boolean(
    proposal.transaction_id || proposal.sample_transaction?.transaction_id
  );
  const hasMerchant = Boolean((proposal.merchant_key || "").trim());
  const skipVariable = $("#btn-cadence-insight-skip-variable");
  if (actions) {
    if (canSaveMerchant || hasTx || hasMerchant) {
      actions.classList.remove("hidden");
      if (saveMerchant) {
        saveMerchant.disabled = false;
        saveMerchant.classList.toggle("hidden", !canSaveMerchant);
      }
      if (saveTx) {
        saveTx.disabled = !hasTx;
        saveTx.classList.toggle("hidden", !hasTx);
      }
      if (skipVariable) {
        skipVariable.disabled = false;
        skipVariable.classList.toggle("hidden", !hasMerchant);
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
$("#btn-cadence-insight-skip-variable")?.addEventListener("click", () => {
  const mk = cadenceInsightState?.proposal?.merchant_key;
  if (!mk) return;
  const btn = $("#btn-cadence-insight-skip-variable");
  if (btn) btn.disabled = true;
  saveCadenceSkipVariable(mk)
    .catch((err) => {
      const body = $("#cadence-insight-body");
      if (body) {
        body.insertAdjacentHTML(
          "beforeend",
          `<p class="hint" style="margin-top:0.75rem">Skip failed: ${escapeHtml(err.message)}</p>`
        );
      }
    })
    .finally(() => {
      if (btn) btn.disabled = false;
    });
});
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
      removeMerchantFromCadenceQueue(proposal.merchant_key);
      loadStatus();
      loadCadenceRulesList().catch(() => {});
      loadCadenceBulkCount().catch(() => {});
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
    removeMerchantFromCadenceQueue(proposal.merchant_key);
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
  const newMerchantKey = ($("#edit-merchant-key")?.value || "").trim();
  const afterLabels = {
    ai_category: ($("#edit-ai-category")?.value || "").trim(),
    ai_sub_category: ($("#edit-ai-sub")?.value || "").trim(),
    flow_type: $("#edit-label-flow-type")?.value || "Expense",
    expense_type: $("#edit-label-expense-type")?.value || "Variable",
    classification: $("#edit-label-classification")?.value || "Personal",
  };
  const beforeLabels = {
    ai_category: editSourceTx.ai_category || "",
    ai_sub_category: editSourceTx.ai_sub_category || "",
    flow_type: editSourceTx.flow_type || "",
    expense_type: editSourceTx.expense_type || "",
    classification: editSourceTx.classification || "",
  };
  const payload = {
    transaction_ids: ids,
    new_merchant_key: newMerchantKey,
    ai_category: afterLabels.ai_category,
    ai_sub_category: afterLabels.ai_sub_category,
    flow_type: afterLabels.flow_type,
    expense_type: afterLabels.expense_type,
    classification: afterLabels.classification,
    update_merchant_label: Boolean($("#edit-update-merchant-label")?.checked),
  };
  if (!payload.ai_category) {
    if (resultEl) resultEl.textContent = "Category is required.";
    return;
  }
  if (!newMerchantKey) {
    if (resultEl) resultEl.textContent = "Merchant label is required.";
    return;
  }
  const insightContext = {
    merchant_key: newMerchantKey,
    scope,
    before: beforeLabels,
    after: afterLabels,
    amount: editSourceTx.amount != null ? Number(editSourceTx.amount) : null,
    update_merchant_label: payload.update_merchant_label,
    merchant_key_before: editSourceTx.merchant_key || "",
  };
  if (applyBtn) applyBtn.disabled = true;
  if (resultEl) resultEl.textContent = "Saving…";
  try {
    const res = await api("/api/transactions/bulk-label", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    try {
      const options = await api("/api/review/options");
      editOptionsCache = options;
    } catch (_) {
      /* options refresh optional */
    }
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

let reviewConfirmState = null;

function closeReviewConfirmModal() {
  reviewConfirmState = null;
  const overlay = $("#review-confirm-overlay");
  const errEl = $("#review-confirm-error");
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
  }
  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }
  const replaceWrap = $("#review-confirm-replace-wrap");
  const replaceCb = $("#review-confirm-replace");
  if (replaceWrap) replaceWrap.classList.add("hidden");
  if (replaceCb) replaceCb.checked = false;
}

function renderReviewConfirmBody(preview, merchantKey) {
  const parts = [];
  parts.push(
    `<p><strong>${escapeHtml(merchantKey)}</strong> — ${preview.pending_count} pending, ${preview.total_count} total transaction(s).</p>`
  );

  if (preview.differing_confirmed_count > 0) {
    parts.push(
      `<p><strong>${preview.differing_confirmed_count}</strong> already-confirmed transaction(s) have different labels than your choice.</p>`
    );
    if (preview.differing_breakdown?.length) {
      const items = preview.differing_breakdown
        .map(
          (row) =>
            `<li>${escapeHtml(row.ai_category || "—")}${row.ai_sub_category ? ` / ${escapeHtml(row.ai_sub_category)}` : ""} (${row.count})</li>`
        )
        .join("");
      parts.push(`<ul class="review-confirm-breakdown">${items}</ul>`);
    }
  } else if (preview.pending_count === 0) {
    parts.push("<p>No pending transactions; you can still update all rows to match this rule.</p>");
  }

  if (preview.suggest_custom_rule && preview.custom_rule_hint) {
    parts.push(`<div class="review-confirm-warn">${escapeHtml(preview.custom_rule_hint)}</div>`);
  }

  if (preview.excel_conflicts?.length) {
    const conflictLines = preview.excel_conflicts
      .map((c) => {
        const ex = c.existing || {};
        return `<li><strong>${escapeHtml(c.source)}</strong>: ${escapeHtml(ex.ai_category || "—")}${ex.ai_sub_category ? ` / ${escapeHtml(ex.ai_sub_category)}` : ""}</li>`;
      })
      .join("");
    parts.push(
      `<p>Conflicting rule in lookup workbook:</p><ul class="review-confirm-breakdown">${conflictLines}</ul>`
    );
  }

  parts.push(
    "<p class=\"hint\">Saving updates <code>transaction-lookups.xlsx</code> (MerchantCategories) and matching rows in the database.</p>"
  );
  return parts.join("");
}

function openReviewConfirmModal(preview, item, payload, cardEl) {
  reviewConfirmState = { item, payload, preview, cardEl };
  const overlay = $("#review-confirm-overlay");
  const body = $("#review-confirm-body");
  const title = $("#review-confirm-title");
  const replaceWrap = $("#review-confirm-replace-wrap");
  const replaceCb = $("#review-confirm-replace");
  if (title) title.textContent = `Confirm — ${item.merchant_key}`;
  if (body) body.innerHTML = renderReviewConfirmBody(preview, item.merchant_key);
  if (replaceWrap) {
    replaceWrap.classList.toggle("hidden", !(preview.excel_conflicts?.length));
  }
  if (replaceCb) replaceCb.checked = false;
  if (overlay) {
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
  }
}

async function submitReviewConfirm(scope) {
  if (!reviewConfirmState) return;
  const { item, payload, cardEl } = reviewConfirmState;
  const replaceExcel = $("#review-confirm-replace")?.checked ?? false;
  const errEl = $("#review-confirm-error");
  const pendingBtn = $("#btn-review-confirm-pending");
  const allBtn = $("#btn-review-confirm-all");
  if (pendingBtn) pendingBtn.disabled = true;
  if (allBtn) allBtn.disabled = true;
  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }
  try {
    const mk = encodeURIComponent(item.merchant_key);
    const res = await api(`/api/review/${mk}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        scope,
        replace_excel: replaceExcel,
      }),
    });
    if (cardEl) cardEl.remove();
    updateReviewBulkCount();
    closeReviewConfirmModal();
    loadStatus();
    if (res.suggest_custom_rule && res.custom_rule_hint) {
      const resultEl = $("#custom-rules-result");
      if (resultEl) {
        resultEl.textContent = `Confirmed. ${res.custom_rule_hint}`;
        resultEl.classList.add("custom-rules-result-ok");
        resultEl.dataset.sticky = "1";
      }
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    if (pendingBtn) pendingBtn.disabled = false;
    if (allBtn) allBtn.disabled = false;
  }
}

$("#btn-review-confirm-close")?.addEventListener("click", closeReviewConfirmModal);
$("#btn-review-confirm-cancel")?.addEventListener("click", closeReviewConfirmModal);
$("#review-confirm-overlay")?.addEventListener("click", (e) => {
  if (e.target?.id === "review-confirm-overlay") closeReviewConfirmModal();
});
$("#btn-review-confirm-pending")?.addEventListener("click", () => submitReviewConfirm("pending"));
$("#btn-review-confirm-all")?.addEventListener("click", () => submitReviewConfirm("all"));

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
    if (!list.dataset.subRerankReady) {
      list.addEventListener("focusin", (e) => {
        if (!e.target.matches('input[name="sub"]')) return;
        const form = e.target.closest("form");
        const cat = form?.querySelector('[name="category"]')?.value?.trim() || "";
        refreshReviewSubDatalist(cat);
      });
      list.dataset.subRerankReady = "1";
    }

    const tips = options.tooltips || {};
    const suggestStatus = $("#review-suggest-status");
    if (!items.length) {
      updateReviewBulkCount(0);
      if (suggestStatus) suggestStatus.textContent = "";
      list.innerHTML = "";
      return;
    }
    updateReviewBulkCount(items.length);
    if (suggestStatus && !reviewSuggestBusy) suggestStatus.textContent = "";
    list.innerHTML = "";
    items.forEach((item, idx) => {
      const flow = item.flow_type || "Expense";
      const expType = item.expense_type || "Variable";
      const classification =
        EDIT_CLASSIFICATIONS.includes(item.classification) ? item.classification : "Personal";
      const classOptions = (options.classifications || EDIT_CLASSIFICATIONS).filter((c) =>
        EDIT_CLASSIFICATIONS.includes(c)
      );
      const classValues = classOptions.length ? classOptions : [...EDIT_CLASSIFICATIONS];
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
      el.dataset.merchantKey = item.merchant_key;
      el.dataset.reviewIdx = String(idx);
      if (item.transaction_id) el.dataset.transactionId = item.transaction_id;
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
            "Category",
            tips.category || "Top-level category",
            `<input id="${uid}-cat" name="category" list="review-categories" value="${escapeAttr(cat)}" placeholder="Select or type…" required title="${escapeAttr(tips.category || "")}" />`
          )}
          ${reviewField(
            `${uid}-sub`,
            "Sub-category",
            tips.sub_category || "Specific label",
            `<input id="${uid}-sub" name="sub" list="review-subcategories" value="${escapeAttr(sub)}" placeholder="Select or type…" title="${escapeAttr(tips.sub_category || "")}" />`
          )}
          <div class="review-form-row">
            ${reviewField(
              `${uid}-flow`,
              "Transaction kind",
              tips.flow_type || "How this row is counted in summaries",
              `<select id="${uid}-flow" name="flow" title="${escapeAttr(tips.flow_type || "")}">${buildSelectOptions(options.flow_types, flow)}</select>`
            )}
            ${reviewField(
              `${uid}-type`,
              "Expense Type",
              tips.expense_type || "Expense type",
              `<select id="${uid}-type" name="type" title="${escapeAttr(tips.expense_type || "")}">${buildSelectOptions(options.expense_types, expType)}</select>`
            )}
            ${reviewField(
              `${uid}-class`,
              "Classification",
              tips.classification || "Personal or Business",
              `<select id="${uid}-class" name="classification" title="${escapeAttr(tips.classification || "")}">${buildSelectOptions(classValues, classification)}</select>`
            )}
          </div>
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

      const reviewForm = el.querySelector("form");
      const categoryInput = reviewForm?.querySelector('[name="category"]');
      if (categoryInput) {
        categoryInput.addEventListener("change", () => {
          alignReviewClassificationFromCategory(reviewForm);
          refreshReviewSubDatalist(categoryInput.value.trim());
        });
        categoryInput.addEventListener("input", () => {
          alignReviewClassificationFromCategory(reviewForm);
        });
        alignReviewClassificationFromCategory(reviewForm);
      }

      const subInput = reviewForm?.querySelector('[name="sub"]');
      if (subInput) {
        subInput.addEventListener("focus", () => {
          refreshReviewSubDatalist(categoryInput?.value?.trim() || "");
        });
      }

      reviewForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const payload = {
          ai_category: String(fd.get("category") || "").trim(),
          ai_sub_category: String(fd.get("sub") || "").trim(),
          flow_type: fd.get("flow"),
          expense_type: fd.get("type"),
          classification: fd.get("classification") || "Personal",
        };
        if (isSingleTx) {
          payload.transaction_id = item.transaction_id;
          try {
            const mk = encodeURIComponent(item.merchant_key);
            await api(`/api/review/${mk}/confirm`, {
              method: "POST",
              body: JSON.stringify(payload),
            });
            el.remove();
            updateReviewBulkCount();
            loadStatus();
          } catch (err) {
            alert(err.message);
          }
          return;
        }
        try {
          const mk = encodeURIComponent(item.merchant_key);
          const preview = await api(`/api/review/${mk}/confirm-preview`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          openReviewConfirmModal(preview, item, payload, el);
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

function formatUploadResultLines(uploads) {
  return (uploads || []).map((u) => {
    if (!u.ok) return `${u.original_name}: upload failed — ${u.error}`;
    if (u.original_name && u.original_name !== u.saved_as) {
      return `${u.original_name} → saved as ${u.saved_as}`;
    }
    return `${u.saved_as}: ready in inbox`;
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

async function uploadCsvFiles(files, { runAfterUpload = true } = {}) {
  const resultEl = $("#upload-result");
  const chooseBtn = $("#btn-choose-upload");
  const processBtn = $("#btn-categorize");
  let startedProcess = false;
  if (!files?.length) {
    if (resultEl) resultEl.textContent = "No files selected.";
    return false;
  }
  if (resultEl) resultEl.textContent = "Uploading…";
  if (chooseBtn) chooseBtn.disabled = true;
  if (processBtn) processBtn.disabled = true;
  try {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    const res = await fetch("/api/ingest/upload", {
      method: "POST",
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    const lines = formatUploadResultLines(data.uploads);
    const okCount = (data.uploads || []).filter((u) => u.ok).length;
    if (resultEl) {
      resultEl.textContent = lines.length ? lines.join("\n") : JSON.stringify(data, null, 2);
    }
    loadStatus();
    if (okCount === 0) return false;
    if (runAfterUpload) {
      startedProcess = true;
      await runCategorizeStream();
    }
    return true;
  } catch (err) {
    if (resultEl) resultEl.textContent = err.message;
    return false;
  } finally {
    if (chooseBtn) chooseBtn.disabled = false;
    if (processBtn && !startedProcess) processBtn.disabled = false;
    setInboxSelectedFiles(null);
    const input = $("#inbox-file-input");
    if (input) input.value = "";
  }
}

$("#btn-choose-upload")?.addEventListener("click", () => {
  $("#inbox-file-input")?.click();
});

$("#inbox-file-input")?.addEventListener("change", (e) => {
  const files = e.target.files;
  if (!files?.length) return;
  setInboxSelectedFiles(files);
  uploadCsvFiles(files).catch(() => {});
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
        "No CSV in input/. Choose CSV files to upload, or copy an export from processed/ back into input/.";
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

  setCategorizeProgress(0, "Starting processing…");
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

$("#btn-categorize")?.addEventListener("click", () => {
  runCategorizeStream().catch(() => {});
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

/* --- AI Rules (taxonomy) --- */
let taxonomyProposals = [];
const taxonomySelected = new Set();
let taxonomyApplyMode = "preview";
let taxonomyOptionsCache = null;
let taxonomyPreviewData = null;
const taxonomyGroupPages = new Map();
const TAXONOMY_PREVIEW_PAGE_SIZE = 25;

async function ensureTaxonomyOptions() {
  if (!taxonomyOptionsCache) {
    taxonomyOptionsCache = await api("/api/review/options");
    const catDl = $("#taxonomy-category-options");
    const subDl = $("#taxonomy-sub-options");
    if (catDl) {
      catDl.innerHTML = (taxonomyOptionsCache.categories || [])
        .map((c) => `<option value="${escapeAttr(c)}"></option>`)
        .join("");
    }
    if (subDl) {
      subDl.innerHTML = (taxonomyOptionsCache.sub_categories || [])
        .map((c) => `<option value="${escapeAttr(c)}"></option>`)
        .join("");
    }
  }
  return taxonomyOptionsCache;
}

function taxonomyRuleTypeLabel(ruleType) {
  if (ruleType === "category_merge") return "Category";
  if (ruleType === "sub_category_merge") return "Sub-category";
  if (ruleType === "label_unify") return "Unify";
  if (ruleType === "merchant_alias") return "Merchant";
  return ruleType || "Rule";
}

function taxonomyCustomRuleText(p) {
  const toSub = p.to_label || "";
  const toCat = p.target_category || p.scope_category || "";
  if (p.rule_type === "label_unify") {
    const variants = (p.from_sub_categories || [p.from_label]).join('", "');
    return (
      `When AI Sub-Category is one of "${variants}", ` +
      `set AI Category to ${toCat || "(unchanged)"} and AI Sub-Category to ${toSub}.`
    );
  }
  if (p.rule_type === "category_merge") {
    return `When AI Category is ${p.from_label}, set AI Category to ${p.to_label}.`;
  }
  if (p.rule_type === "sub_category_merge") {
    return (
      `When AI Category is ${p.scope_category || "…"} and AI Sub-Category is ${p.from_label}, ` +
      `set AI Category to ${toCat || p.scope_category} and AI Sub-Category to ${toSub}.`
    );
  }
  if (p.rule_type === "merchant_alias") {
    return `Treat merchant "${p.from_label}" the same as "${p.to_label}".`;
  }
  return "";
}

function taxonomyProposalTargetFields(p) {
  const showCat =
    p.rule_type === "label_unify" || p.rule_type === "sub_category_merge";
  const showSub =
    p.rule_type === "label_unify" || p.rule_type === "sub_category_merge";
  const showCatOnly = p.rule_type === "category_merge";
  if (!showCat && !showSub && !showCatOnly) return "";

  const variants =
    p.rule_type === "label_unify" && p.from_sub_categories?.length
      ? `<p class="taxonomy-variant-list"><span class="hint">Variants:</span> ${p.from_sub_categories
          .map((v) => escapeHtml(v))
          .join(", ")}</p>`
      : "";

  const catSources =
    p.source_categories?.length && p.rule_type === "label_unify"
      ? `<p class="taxonomy-variant-list hint">Currently under: ${escapeHtml(p.source_categories.join(", "))}</p>`
      : "";

  return `
    ${variants}
    ${catSources}
    <div class="taxonomy-target-fields">
      ${
        showSub
          ? `<label class="taxonomy-target-label">Merge to sub-category
               <input type="text" class="taxonomy-edit-to-sub" list="taxonomy-sub-options" value="${escapeAttr(p.to_label || "")}" />
             </label>`
          : ""
      }
      ${
        showCatOnly
          ? `<label class="taxonomy-target-label">Merge to category
               <input type="text" class="taxonomy-edit-to-sub" list="taxonomy-category-options" value="${escapeAttr(p.to_label || "")}" />
             </label>`
          : ""
      }
      ${
        showCat
          ? `<label class="taxonomy-target-label">Target category
               <input type="text" class="taxonomy-edit-to-cat" list="taxonomy-category-options" value="${escapeAttr(p.target_category || p.scope_category || "")}" />
             </label>`
          : ""
      }
      <button type="button" class="btn-link taxonomy-save-custom-rule" data-id="${escapeAttr(p.id)}">Save as Custom Rule…</button>
    </div>`;
}

function readTaxonomyProposalFromCard(base) {
  const card = document.querySelector(`.taxonomy-proposal[data-id="${CSS.escape(base.id)}"]`);
  if (!card) return { ...base };
  const toSub = card.querySelector(".taxonomy-edit-to-sub")?.value?.trim();
  const toCat = card.querySelector(".taxonomy-edit-to-cat")?.value?.trim();
  const updated = { ...base };
  if (toSub) updated.to_label = toSub;
  if (toCat) {
    updated.target_category = toCat;
    if (updated.rule_type === "sub_category_merge") {
      updated.scope_category = updated.scope_category || toCat;
    }
  }
  return updated;
}

function taxonomyConfidenceClass(confidence) {
  const c = Number(confidence) || 0;
  if (c >= 0.9) return "high";
  if (c >= 0.75) return "medium";
  return "low";
}

function renderTaxonomySummary(summary) {
  const el = $("#taxonomy-summary");
  if (!el || !summary) return;
  const items = [
    ["Categories", summary.category_count],
    ["Category pairs", summary.pair_count],
    ["Ambiguous subs", summary.ambiguous_sub_category_count],
    ["Merchant alias groups", summary.alias_group_count],
    ["Label drift rows", summary.drift_rows],
    ["Proposals", summary.heuristic_proposal_count ?? summary.proposal_count ?? 0],
  ];
  el.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="taxonomy-stat"><div class="taxonomy-stat-value">${escapeHtml(String(value ?? 0))}</div><div class="taxonomy-stat-label">${escapeHtml(label)}</div></div>`
    )
    .join("");
}

function updateTaxonomyActionButtons() {
  const n = taxonomySelected.size;
  const previewBtn = $("#btn-taxonomy-preview");
  const applyBtn = $("#btn-taxonomy-apply");
  if (previewBtn) previewBtn.disabled = n === 0;
  if (applyBtn) applyBtn.disabled = n === 0;
}

function renderTaxonomyProposals(proposals) {
  taxonomyProposals = proposals || [];
  const host = $("#taxonomy-proposals");
  const empty = $("#taxonomy-empty");
  if (!host) return;

  taxonomySelected.clear();
  const selectAll = $("#taxonomy-select-all");
  if (selectAll) selectAll.checked = false;
  updateTaxonomyActionButtons();

  if (!taxonomyProposals.length) {
    host.innerHTML = "";
    empty?.classList.remove("hidden");
    return;
  }
  empty?.classList.add("hidden");

  host.innerHTML = taxonomyProposals
    .map((p) => {
      const confClass = taxonomyConfidenceClass(p.confidence);
      const fromDisplay =
        p.rule_type === "label_unify" && p.from_sub_categories?.length
          ? p.from_sub_categories.join(", ")
          : p.from_label;
      const samples =
        p.sample_merchants?.length > 0
          ? ` · e.g. ${escapeHtml(p.sample_merchants.slice(0, 3).join(", "))}`
          : "";
      return `
        <article class="taxonomy-proposal" data-id="${escapeAttr(p.id)}">
          <div class="taxonomy-proposal-head">
            <input type="checkbox" class="taxonomy-proposal-check" data-id="${escapeAttr(p.id)}" aria-label="Select rule" />
            <div class="taxonomy-proposal-main">
              <p class="taxonomy-proposal-merge">
                <span class="from-label">${escapeHtml(fromDisplay)}</span>
                → <span class="to-label taxonomy-display-to">${escapeHtml(p.to_label)}</span>
              </p>
              <div class="taxonomy-proposal-meta">
                <span class="taxonomy-badge taxonomy-badge-type-${escapeAttr(p.rule_type)}">${escapeHtml(taxonomyRuleTypeLabel(p.rule_type))}</span>
                <span class="taxonomy-badge taxonomy-badge-source-${escapeAttr(p.source)}">${escapeHtml(p.source === "llm" ? "AI" : "Heuristic")}</span>
                <span class="taxonomy-badge taxonomy-badge-conf-${confClass}">${Math.round((p.confidence || 0) * 100)}% conf</span>
                ${p.automation_ready ? '<span class="taxonomy-badge taxonomy-badge-auto-ready">Future auto</span>' : ""}
              </div>
              <p class="taxonomy-proposal-rationale">${escapeHtml(p.rationale || "")}</p>
              ${taxonomyProposalTargetFields(p)}
              <p class="taxonomy-proposal-foot">${p.affected_transactions || 0} transaction(s) · $${Number(p.affected_spend || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} expense${samples}</p>
            </div>
          </div>
        </article>`;
    })
    .join("");

  host.querySelectorAll(".taxonomy-proposal-check").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = cb.dataset.id;
      const card = cb.closest(".taxonomy-proposal");
      if (cb.checked) {
        taxonomySelected.add(id);
        card?.classList.add("selected");
      } else {
        taxonomySelected.delete(id);
        card?.classList.remove("selected");
      }
      updateTaxonomyActionButtons();
      if (selectAll) {
        selectAll.checked =
          taxonomySelected.size > 0 && taxonomySelected.size === taxonomyProposals.length;
      }
    });
  });

  host.querySelectorAll(".taxonomy-edit-to-sub").forEach((input) => {
    input.addEventListener("input", () => {
      const card = input.closest(".taxonomy-proposal");
      const display = card?.querySelector(".taxonomy-display-to");
      if (display) display.textContent = input.value;
    });
  });

  host.querySelectorAll(".taxonomy-save-custom-rule").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const p = taxonomyProposals.find((x) => x.id === id);
      if (!p) return;
      const edited = readTaxonomyProposalFromCard(p);
      const text = taxonomyCustomRuleText(edited);
      const area = $("#custom-rule-input");
      if (area) area.value = text;
      setTab("settings");
      area?.focus();
    });
  });
}

function getSelectedTaxonomyProposals() {
  return taxonomyProposals
    .filter((p) => taxonomySelected.has(p.id))
    .map((p) => readTaxonomyProposalFromCard(p));
}

function taxonomyPreviewFieldLabel(ruleType) {
  if (ruleType === "category_merge") return "Category";
  if (ruleType === "sub_category_merge" || ruleType === "label_unify") return "Labels";
  if (ruleType === "merchant_alias") return "Merchant";
  return "Label";
}

function taxonomyPreviewFieldValue(state, ruleType, scopeCategory, targetCategory) {
  if (!state) return "—";
  if (ruleType === "category_merge") return state.ai_category || "—";
  if (ruleType === "sub_category_merge" || ruleType === "label_unify") {
    const cat = state.ai_category || "—";
    const sub = state.ai_sub_category || "—";
    return `${cat} / ${sub}`;
  }
  if (ruleType === "merchant_alias") return state.merchant_key || "—";
  return "—";
}

function taxonomyPreviewAfterValue(state, ruleType, group) {
  if (!state) return "—";
  if (ruleType === "category_merge") return group.to_label || state.ai_category || "—";
  if (ruleType === "sub_category_merge" || ruleType === "label_unify") {
    const cat = group.target_category || group.scope_category || state.ai_category || "—";
    const sub = group.to_label || state.ai_sub_category || "—";
    return `${cat} / ${sub}`;
  }
  if (ruleType === "merchant_alias") return group.to_label || state.merchant_key || "—";
  return "—";
}

function taxonomyPreviewGroupKey(group, index) {
  return String(group.proposal_id || `group-${index}`);
}

function buildTaxonomyPreviewHtml(data) {
  const stats = data.preview || {};
  const lines = [
    ["Category rows updated", stats.category_merges],
    ["Sub-category rows updated", stats.sub_category_merges],
    ["Unified label rows", stats.label_unify],
    ["Merchant alias transactions", stats.merchant_alias_transactions],
    ["Merchant labels renamed", stats.merchant_labels_renamed],
    ["Merchant labels deleted", stats.merchant_labels_deleted],
    ["Cadence rules updated", stats.cadence_rules_updated],
  ].filter(([, v]) => v != null && Number(v) > 0);

  let html = `<p>Preview for <strong>${data.proposal_count}</strong> selected rule(s)${stats.dry_run ? " (dry run — nothing saved yet)" : ""}:</p>`;
  if (lines.length) {
    html += `<ul class="taxonomy-preview-stats">${lines
      .map(([label, val]) => `<li>${escapeHtml(label)}: <strong>${val}</strong></li>`)
      .join("")}</ul>`;
  } else {
    html += `<p class="hint">No row changes detected — rules may already be applied or labels do not match.</p>`;
  }

  const groups = data.sample_groups || [];
  if (groups.length) {
    html += `<p class="hint" style="margin-top:0.75rem">Matching transactions — <span class="taxonomy-preview-before">before</span> vs <span class="taxonomy-preview-after">after</span> (${TAXONOMY_PREVIEW_PAGE_SIZE} per page):</p>`;
    groups.forEach((group, groupIdx) => {
      const groupKey = taxonomyPreviewGroupKey(group, groupIdx);
      const field = taxonomyPreviewFieldLabel(group.rule_type);
      const scope =
        group.rule_type === "sub_category_merge" && group.scope_category
          ? ` under ${escapeHtml(group.scope_category)}`
          : "";
      const samples = group.samples || [];
      const pageSize = TAXONOMY_PREVIEW_PAGE_SIZE;
      const pageCount = Math.max(1, Math.ceil(samples.length / pageSize));
      let page = taxonomyGroupPages.get(groupKey) || 0;
      if (page >= pageCount) page = pageCount - 1;
      taxonomyGroupPages.set(groupKey, page);
      const start = page * pageSize;
      const pageSamples = samples.slice(start, start + pageSize);

      html += `<div class="taxonomy-preview-group" data-group-key="${escapeAttr(groupKey)}">`;
      html += `<p class="taxonomy-preview-group-title">${escapeHtml(String(group.from_label))} → ${escapeHtml(String(group.to_label))}${scope}</p>`;
      html += `<p class="taxonomy-preview-group-meta">${escapeHtml(field)} · ${group.total_matches || 0} matching transaction(s)</p>`;

      if (!samples.length) {
        html += `<p class="hint">No matching rows in database.</p></div>`;
        return;
      }

      if (group.truncated) {
        const cap = data.max_preview_rows_per_proposal || 2000;
        html += `<p class="taxonomy-preview-truncated hint">Showing ${samples.length} of ${group.total_matches} transactions (server cap ${cap}).</p>`;
      }

      html += `<div class="taxonomy-preview-table-wrap"><table class="taxonomy-preview-table"><thead><tr>`;
      html += `<th>Date</th><th>Merchant</th><th>Amount</th><th>Before</th><th>After</th>`;
      html += `</tr></thead><tbody>`;
      for (const row of pageSamples) {
        const beforeVal = taxonomyPreviewFieldValue(
          row.before,
          group.rule_type,
          group.scope_category,
          group.target_category
        );
        const afterVal = taxonomyPreviewAfterValue(row.after, group.rule_type, group);
        html += `<tr>`;
        html += `<td>${escapeHtml(row.date || "")}</td>`;
        html += `<td>${escapeHtml(row.before?.merchant_key || "—")}</td>`;
        html += `<td class="amount">${escapeHtml(formatMoney(row.amount))}</td>`;
        html += `<td class="taxonomy-preview-before">${escapeHtml(beforeVal)}</td>`;
        html += `<td class="taxonomy-preview-after">${escapeHtml(afterVal)}</td>`;
        html += `</tr>`;
      }
      html += `</tbody></table></div>`;

      if (pageCount > 1) {
        html += `<div class="taxonomy-preview-pagination">`;
        html += `<button type="button" class="btn-secondary taxonomy-preview-page-btn" data-group-key="${escapeAttr(groupKey)}" data-dir="prev" ${page <= 0 ? "disabled" : ""}>Previous</button>`;
        html += `<span class="taxonomy-preview-page-info">Page ${page + 1} of ${pageCount} · ${samples.length} row(s)</span>`;
        html += `<button type="button" class="btn-secondary taxonomy-preview-page-btn" data-group-key="${escapeAttr(groupKey)}" data-dir="next" ${page >= pageCount - 1 ? "disabled" : ""}>Next</button>`;
        html += `</div>`;
      } else {
        html += `<p class="taxonomy-preview-more">${samples.length} transaction(s) shown.</p>`;
      }
      html += `</div>`;
    });
  }

  const warnings = stats.warnings || [];
  if (warnings.length) {
    html += `<div class="taxonomy-preview-warnings"><strong>Warnings</strong><ul>${warnings
      .map((w) => `<li>${escapeHtml(w)}</li>`)
      .join("")}</ul></div>`;
  }
  return html;
}

function refreshTaxonomyPreviewBody() {
  const body = $("#taxonomy-apply-body");
  if (body && taxonomyPreviewData) {
    body.innerHTML = buildTaxonomyPreviewHtml(taxonomyPreviewData);
  }
}

function openTaxonomyApplyModal(html, mode) {
  taxonomyApplyMode = mode;
  const overlay = $("#taxonomy-apply-overlay");
  const body = $("#taxonomy-apply-body");
  const title = $("#taxonomy-apply-title");
  const confirmBtn = $("#btn-taxonomy-apply-confirm");
  const errEl = $("#taxonomy-apply-error");
  taxonomyGroupPages.clear();
  if (title) {
    title.textContent =
      mode === "preview" ? "Preview taxonomy rules" : "Apply taxonomy rules";
  }
  if (body) body.innerHTML = html;
  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }
  if (confirmBtn) {
    confirmBtn.classList.toggle("hidden", mode === "preview");
    confirmBtn.disabled = false;
  }
  overlay?.classList.remove("hidden");
  overlay?.setAttribute("aria-hidden", "false");
}

function closeTaxonomyApplyModal() {
  const overlay = $("#taxonomy-apply-overlay");
  overlay?.classList.add("hidden");
  overlay?.setAttribute("aria-hidden", "true");
  taxonomyPreviewData = null;
  taxonomyGroupPages.clear();
}

async function runTaxonomyPreview(mode) {
  const selected = getSelectedTaxonomyProposals();
  if (!selected.length) return;
  const status = $("#taxonomy-status");
  if (status) status.textContent = "Running preview…";
  try {
    const data = await api("/api/taxonomy-rules/preview", {
      method: "POST",
      body: JSON.stringify({ proposals: selected, reconcile: false }),
    });
    taxonomyPreviewData = data;
    openTaxonomyApplyModal(buildTaxonomyPreviewHtml(data), mode);
    if (status) status.textContent = "";
  } catch (err) {
    if (status) status.textContent = err.message;
  }
}

async function loadTaxonomyPanel({ analyzeOnly = true } = {}) {
  const status = $("#taxonomy-status");
  if (status) status.textContent = analyzeOnly ? "Analyzing…" : "";
  try {
    await ensureTaxonomyOptions();
    const data = analyzeOnly
      ? await api("/api/taxonomy-rules/analyze")
      : await api("/api/taxonomy-rules/suggest", { method: "POST", body: "{}" });
    renderTaxonomySummary(data.summary);
    renderTaxonomyProposals(data.proposals);
    if (status) {
      status.textContent = analyzeOnly
        ? `Found ${(data.proposals || []).length} heuristic proposal(s).`
        : `AI review: ${(data.proposals || []).length} proposal(s) (${data.llm_proposal_count ?? 0} from AI).`;
    }
  } catch (err) {
    if (status) status.textContent = err.message;
  }
}

$("#btn-taxonomy-analyze")?.addEventListener("click", () => {
  loadTaxonomyPanel({ analyzeOnly: true }).catch(() => {});
});

$("#btn-taxonomy-suggest")?.addEventListener("click", async () => {
  const btn = $("#btn-taxonomy-suggest");
  const analyzeBtn = $("#btn-taxonomy-analyze");
  const status = $("#taxonomy-status");
  if (btn) btn.disabled = true;
  if (analyzeBtn) analyzeBtn.disabled = true;
  if (status) status.textContent = "AI is reviewing your labels (may take a minute)…";
  try {
    await loadTaxonomyPanel({ analyzeOnly: false });
  } finally {
    if (btn) btn.disabled = false;
    if (analyzeBtn) analyzeBtn.disabled = false;
  }
});

$("#taxonomy-select-all")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  taxonomySelected.clear();
  document.querySelectorAll(".taxonomy-proposal-check").forEach((cb) => {
    cb.checked = checked;
    const card = cb.closest(".taxonomy-proposal");
    if (checked) {
      taxonomySelected.add(cb.dataset.id);
      card?.classList.add("selected");
    } else {
      card?.classList.remove("selected");
    }
  });
  updateTaxonomyActionButtons();
});

$("#btn-taxonomy-preview")?.addEventListener("click", () => {
  runTaxonomyPreview("preview").catch(() => {});
});

$("#btn-taxonomy-apply")?.addEventListener("click", () => {
  runTaxonomyPreview("apply").catch(() => {});
});

$("#btn-taxonomy-apply-close")?.addEventListener("click", closeTaxonomyApplyModal);
$("#btn-taxonomy-apply-cancel")?.addEventListener("click", closeTaxonomyApplyModal);

$("#btn-taxonomy-apply-confirm")?.addEventListener("click", async () => {
  const selected = getSelectedTaxonomyProposals();
  const confirmBtn = $("#btn-taxonomy-apply-confirm");
  const errEl = $("#taxonomy-apply-error");
  if (!selected.length) return;
  if (confirmBtn) confirmBtn.disabled = true;
  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }
  try {
    const res = await api("/api/taxonomy-rules/apply", {
      method: "POST",
      body: JSON.stringify({
        proposals: selected,
        reconcile: false,
        confirm: "APPLY",
      }),
    });
    closeTaxonomyApplyModal();
    taxonomySelected.clear();
    const status = $("#taxonomy-status");
    if (status) {
      const s = res.stats || {};
      status.textContent = `Applied ${res.proposal_count} rule(s) — ${s.category_merges || 0} category, ${s.sub_category_merges || 0} sub-category, ${s.merchant_alias_transactions || 0} merchant row(s) updated.`;
    }
    await loadTaxonomyPanel({ analyzeOnly: true });
    loadStatus();
    reviewOptionsCache = null;
    editOptionsCache = null;
    taxonomyOptionsCache = null;
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
    if (confirmBtn) confirmBtn.disabled = false;
  }
});

$("#taxonomy-apply-overlay")?.addEventListener("click", (e) => {
  if (e.target.id === "taxonomy-apply-overlay") closeTaxonomyApplyModal();
});

$("#taxonomy-apply-body")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".taxonomy-preview-page-btn");
  if (!btn || btn.disabled) return;
  const groupKey = btn.dataset.groupKey;
  const dir = btn.dataset.dir;
  if (!groupKey || !dir) return;
  const page = taxonomyGroupPages.get(groupKey) || 0;
  if (dir === "prev" && page > 0) {
    taxonomyGroupPages.set(groupKey, page - 1);
    refreshTaxonomyPreviewBody();
  } else if (dir === "next") {
    taxonomyGroupPages.set(groupKey, page + 1);
    refreshTaxonomyPreviewBody();
  }
});

loadChatHistory();
