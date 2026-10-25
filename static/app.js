const $ = (id) => document.getElementById(id);
const AGENT_ORDER = ["Triage", "SourceScout", "Matcher", "Critic", "Tracer", "Reporter"];
const AGENT_LABEL = { SourceScout: "Scout" };

const state = { browse: { stock: {}, shipments: {}, agencies: {} }, packs: {}, running: {}, serverRuns: {}, loadingRuns: {}, openGroups: {}, config: null, recalls: [], selected: null, mode: "sample", runId: null, pack: null, trace: [], pulled: new Set(), open: new Set(), reading: null, screen: {}, cases: {}, caseInfo: null, busy: false };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
};

function apiKey() { try { return sessionStorage.getItem("lotline:key") || ""; } catch { return ""; } }

async function askKey() {
  const dlg = $("key-dialog");
  dlg.showModal();
  await new Promise((resolve) => dlg.addEventListener("close", resolve, { once: true }));
  try { sessionStorage.setItem("lotline:key", $("key-input").value); } catch { /* ignore */ }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(apiKey() ? { "X-API-Key": apiKey() } : {}) };
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) { await askKey(); return api(path, options); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

const fmtNum = (n) => (Number.isInteger(+n) ? String(+n) : (+n).toFixed(1));
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const shortModel = (m) => (m || "").replace(/^nvidia\//, "").replace(/-a\d+b$/, "");

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* header */

function renderHeader() {
  const { config } = state;
  const zetaris = config.data_backend === "zetaris";
  const meterless = config.power?.meterless || { state: "offline" };
  const rows = [
    ["Data layer", `${zetaris ? "Zetaris (federated)" : "Local copy of the CSVs"}${config.data_fallback ? " · fallback" : ""}`, config.data_fallback ? "warn" : "ok"],
    ...config.sources.filter((s) => s.table !== "recalls").map((s) => [s.table, `${s.rows.toLocaleString()} rows`, "ok"]),
    ["Model", config.live_available ? shortModel(config.models[0]) : "not configured", config.live_available ? "ok" : "warn"],
    ["Model route", config.model_route || "none", config.live_available ? "ok" : "warn"],
    ["Meterless Runtime", meterless.detail || meterless.state, meterless.state === "online" ? "ok" : "warn"],
  ];
  $("sys-rows").replaceChildren(...rows.map(([lbl, val, kind]) =>
    el("div", { class: "sys-row" }, el("span", { class: "lbl", text: lbl }), el("span", { class: `val ${kind}`, text: val }))));
  $("data-chip-text").textContent = config.data_fallback ? "Stock records: saved copy" : `Stock records: ${zetaris ? "Zetaris" : "local files"}`;
  $("data-chip").title = config.data_fallback ? "Zetaris couldn't be reached, so Lotline is using a saved copy of the records."
    : zetaris ? "Connected: every search and check reads the warehouse, shipment and recall databases through Zetaris."
    : "Reading the local copy of the data files. Set DATA_BACKEND=zetaris to read through Zetaris.";
  $("ai-toggle").disabled = !config.live_available;
  if (!config.live_available) $("ai-toggle").title = "Add NVIDIA_API_KEY to .env to turn on AI suggestions";
  const note = $("fallback-note");
  note.hidden = !config.data_fallback;
  note.textContent = config.data_fallback ? "Our main stock records couldn't be reached, so Lotline is using a saved copy. Double-check anything you pull against the shelf." : "";
  note.title = config.data_fallback || "";

  const saved = store.get("lotline:mode");
  state.mode = config.live_available ? (saved || config.default_mode) : "sample";
  for (const btn of document.querySelectorAll("button[data-mode]")) {
    if (btn.dataset.mode === "live" && !config.live_available) {
      btn.disabled = true;
      btn.title = "Add NVIDIA_API_KEY to .env to turn on AI suggestions";
    }
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  }
  setMode(state.mode);
}

function setAdmin(on) {
  document.body.classList.toggle("admin", on);
  store.set("lotline:admin", on ? "1" : "");
  $("admin-toggle").checked = on;
  if (state.recalls.length) renderRecalls();
}

async function testConnection() {
  toast("Testing model providers...");
  try {
    const { results } = await api("/api/power/test", { method: "POST" });
    toast(results.length ? results.map((r) => r.ok
      ? `${r.provider}: ok, ${r.served_by || r.model} in ${r.seconds}s`
      : `${r.provider}: failed (${r.error})`).join(" · ") : "No model provider configured; sample mode only.");
  } catch (err) {
    toast(`Test failed: ${err.message}`);
  }
}

async function resetDemo() {
  const ok = await confirmDialog({
    title: "Reset demo data?",
    body: [el("p", { text: "This clears every tick, package reading, confirmed spelling and sent notice on this shared link. It does not touch the recall data itself." })],
    ok: "Reset",
  });
  if (!ok) return;
  try {
    await api("/api/demo/reset", { method: "POST" });
    toast("Demo reset. Reloading...");
    setTimeout(() => location.reload(), 600);
  } catch (err) {
    toast(`Reset failed: ${err.message}`);
  }
}

function setMode(mode) {
  state.mode = mode;
  store.set("lotline:mode", mode);
  for (const btn of document.querySelectorAll("button[data-mode]")) {
    btn.setAttribute("aria-checked", String(btn.dataset.mode === mode));
  }
  $("ai-toggle").setAttribute("aria-checked", String(mode === "live"));
}

/* recall inbox */

const SCREEN_RANK = { affected: 0, check: 1, clear: 2 };

const CASE_LABEL = { pulling: "pulling items", checking: "reading packages", notifying: "messaging agencies", waiting: "waiting on agencies", ready: "ready to close", closed: "finished" };

function screenLine(r) {
  const cs = state.cases[r.recall_id];
  if (cs) return el("div", { class: "case-line", text: cs === "closed" ? "Finished" : `In progress: ${CASE_LABEL[cs] || cs}` });
  if (state.running[r.recall_id]) return el("div", { class: "recall-screen screen-pending", text: "Checking now..." });
  const agencySpan = (n) => (n ? el("span", { class: "flag-agency", text: `sent to ${n} ${n === 1 ? "agency" : "agencies"}` }) : null);
  const done = state.packs[r.recall_id]?.pack;
  if (done) {
    const pull = done.pull_list.length, check = done.human_checks.length;
    if (!pull && !check) return el("div", { class: "recall-screen", text: "Checked: none in our stock" });
    return el("div", { class: "recall-screen" },
      el("span", { class: "rs-src", text: "Checked:" }),
      pull ? el("span", { class: "flag", text: `${pull} to pull` }) : null,
      check ? el("span", { class: "flag-check", text: `${check} to check` }) : null,
      agencySpan(done.agency_notices.length));
  }
  const sc = state.screen[r.recall_id];
  if (!sc) return el("div", { class: "recall-screen screen-pending", text: "Looking through our stock..." });
  if (sc.status === "clear") return el("div", { class: "recall-screen", text: "Quick look: none in our stock" });
  const units = sc.units_on_hand;
  return el("div", { class: "recall-screen" },
    el("span", { class: "rs-src", text: "Quick look:" }),
    el("span", { class: sc.pull ? "flag" : "flag-check", text: units ? `${fmtNum(units)} units may be ours` : `${(sc.pull || 0) + (sc.check || 0)} items may be ours` }),
    agencySpan(sc.agencies));
}

function sevFor(r) {
  const cs = state.cases[r.recall_id];
  if (cs === "closed") return ["closed", "Done"];
  const done = state.packs[r.recall_id]?.pack;
  if (done) return done.pull_list.length ? ["pull", "Pull"] : done.human_checks.length ? ["check", "Needs a look"] : ["clear", "Not in stock"];
  const sc = state.screen[r.recall_id];
  if (!sc) return ["pending", "Checking"];
  if (sc.pull) return ["pull", "We may have it"];
  if (sc.check) return ["check", "Needs a look"];
  return ["clear", "Not in stock"];
}

const isTest = (r) => (r?.recall_id || "").startsWith("RC-");

function sortedRecalls() {
  const rank = (r) => SCREEN_RANK[state.screen[r.recall_id]?.status] ?? 3;
  return [...state.recalls].sort((a, b) => isTest(a) - isTest(b) || rank(a) - rank(b)
    || (b.announced_date || "").localeCompare(a.announced_date || ""));
}

async function loadScreen(refresh = false) {
  try {
    const open = await api("/api/cases");
    state.cases = Object.fromEntries(open.map((c) => [c.recall_id, c.status]));
  } catch { /* cases are optional */ }
  try {
    const rows = await api(`/api/recalls/screen${refresh ? "?refresh=true" : ""}`);
    state.screen = Object.fromEntries(rows.map((r) => [r.recall_id, r]));
    renderRecalls();
    if (state.selected && !isRunning() && !$("notice").hidden) renderNotice();
  } catch { /* the inbox still works without the screen */ }
}

const RECALL_GROUPS = [
  { key: "pull", title: "Needs action now", open: true },
  { key: "check", title: "Needs a closer look", open: true },
  { key: "pending", title: "Still checking", open: true },
  { key: "clear", title: "Not in our stock", open: false },
  { key: "closed", title: "Finished", open: false },
  { key: "test", title: "Practice recalls (made-up brands)", open: false },
];

function renderRecalls() {
  const buckets = Object.fromEntries(RECALL_GROUPS.map((g) => [g.key, []]));
  for (const r of sortedRecalls()) buckets[isTest(r) ? "test" : sevFor(r)[0]].push(r);
  $("recall-count").textContent = `${state.recalls.filter((r) => !isTest(r)).length} from the FDA`;
  const needs = buckets.pull.length + buckets.check.length;
  $("nav-inbox-count").hidden = !needs;
  $("nav-inbox-count").textContent = String(needs);
  const admin = document.body.classList.contains("admin");
  $("recall-list").replaceChildren(...RECALL_GROUPS.filter((g) => buckets[g.key].length && (g.key !== "test" || admin)).map((g) => {
    const list = buckets[g.key];
    const hasSelected = list.some((r) => r.recall_id === state.selected?.recall_id);
    const open = state.openGroups[g.key] ?? (g.open || hasSelected);
    const det = el("details", { class: `rgroup rg-${g.key}`, open: open || null },
      el("summary", {}, el("span", { class: "rg-title", text: g.title }), el("span", { class: "rg-count", text: String(list.length) })),
      el("ol", { class: "recall-list" }, list.map((r) => recallItem(r, g.key === "test"))));
    det.addEventListener("toggle", () => { state.openGroups[g.key] = det.open; });
    return det;
  }));
}

function recallItem(r, showBadge) {
  const [kind, label] = sevFor(r);
  return el("li", {}, el("button", {
    type: "button", class: `recall-item sev-${kind}`,
    "aria-current": String(r.recall_id === state.selected?.recall_id),
    onclick: () => selectRecall(r.recall_id),
  },
    el("div", { class: "recall-eyebrow" },
      el("span", { class: "src", text: r.source || "RECALL" }),
      el("span", { text: fmtDate(r.announced_date) }),
      showBadge ? el("span", { class: `sev sev-badge-${kind}`, text: label }) : null),
    el("div", { class: "recall-brand", text: r.brand }),
    el("div", { class: "recall-product", text: r.product }),
    el("div", { class: "recall-reason", text: r.reason }),
    screenLine(r)));
}

function selectRecall(id, { auto = true } = {}) {
  if (state.pack && state.packs[state.pack.recall_id]) state.packs[state.pack.recall_id].step = state.step;
  state.selected = state.recalls.find((r) => r.recall_id === id);
  const url = new URL(location.href);
  url.searchParams.set("recall", id);
  url.searchParams.delete("autorun");
  url.searchParams.delete("run");
  url.hash = "";
  history.replaceState(null, "", url);
  $("main").querySelector(".overlay-view")?.remove();
  setNavCurrent("inbox");
  setRail("shown");
  for (const sid of ["notice", "pipeline"]) $(sid).hidden = false;
  $("error").hidden = true;
  $("crumb-here").textContent = state.selected ? `${state.selected.brand} recall` : "Recalls";
  renderRecalls();
  if (state.packs[id]) {
    renderNotice();
    adopt(id);
  } else if (state.running[id]) {
    showRunning(id);
  } else if (state.serverRuns[id]) {
    renderNotice();
    renderSteps([]);
    $("pipeline-status").textContent = "Loading the last check...";
    $("results").hidden = true;
    loadSavedRun(id).then(() => { if (viewing(id)) selectRecall(id, { auto }); });
  } else {
    renderNotice();
    renderSteps([]);
    $("pipeline-status").textContent = "Not checked yet";
    setPipelineOpen(true);
    $("results").hidden = true;
    // The rules-only check costs nothing, so it always runs on open; a check with AI suggestions only on a click
    if (auto) startRun(id, { mode: "sample" });
  }
}

function runStatus() {
  const p = state.pack;
  return `run ${p.run_id} · ${p.mode === "live" ? "model assist" : "practice"} · ${p._elapsed}s`;
}

function lotChip(code) {
  return code ? el("span", { class: "lot", text: code }) : el("span", { class: "muted", text: "no lot" });
}

function codeList(text, empty) {
  if (!text) return el("span", { class: "muted", text: empty });
  if (text.toUpperCase() === "ALL") return el("b", { text: "all lots" });
  const parts = text.split(";").map((s) => s.trim()).filter(Boolean);
  return el("ul", { class: "code-lines" }, parts.map((p) => el("li", { class: /\d{4}-\d{2}-\d{2}/.test(p) ? "date" : null, text: plain(p) })));
}

const plural = (n, one, many) => (n === 1 ? one : many);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtDate(d) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d || ""));
  return m && +m[2] >= 1 && +m[2] <= 12 ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : d;
}

function plain(text) {
  if (text == null) return text;
  return String(text).replace(/\b\d{4}-\d{2}-\d{2}\b/g, (d) => fmtDate(d)).replace(/\s+-\s+/g, " to ");
}

function renderNotice() {
  const r = state.selected;
  if (!r) return;
  const sc = state.screen[r.recall_id];
  const p = state.pack?.recall_id === r.recall_id ? state.pack : null;
  const info = p ? state.caseInfo : null;
  let pull = 0, check = 0, units = 0, agencies = 0;
  if (p) {
    pull = p.pull_list.length; check = p.human_checks.length;
    units = (p.impact?.units_to_pull || 0) + (p.impact?.units_to_check || 0);
    agencies = p.agency_notices.length;
  } else if (sc) {
    pull = sc.pull || 0; check = sc.check || 0; units = sc.units_on_hand || 0; agencies = sc.agencies || 0;
  }
  const known = Boolean(p || sc);
  const kind = !known ? "unknown" : pull ? "pull" : check ? "check" : "clear";
  const n = pull + check;
  const em = (text) => el("em", { text });
  const agencyText = `${agencies} partner ${plural(agencies, "agency", "agencies")}`;

  let answer, detail;
  const buttons = [];
  if (isRunning(r.recall_id)) {
    answer = "Checking our stock now...";
    detail = "This takes a few seconds. Lotline is comparing every stock record with the recall.";
    buttons.push(el("button", { type: "button", class: "primary primary-big", id: "run-btn", disabled: true }, "Checking..."));
  } else if (!known) {
    answer = "Looking through our stock...";
    detail = "One moment.";
  } else if (!p) {
    if (kind === "clear") {
      answer = "It doesn't look like we have this.";
      detail = "A quick look found none of it in our stock. Check our stock to be sure and keep a record of what was compared.";
    } else {
      answer = ["We may have ", em(`${n} of these`), " on our shelves."];
      detail = `${units ? `That's about ${fmtNum(units)} ${plural(units, "unit", "units")}. ` : ""}${agencies ? `Some may already have gone to ${agencyText}. ` : ""}Check our stock to confirm before pulling anything from the shelves.`;
    }
    buttons.push(el("button", { type: "button", class: "primary primary-big", id: "run-btn", onclick: () => startRun() }, "Check our stock against this recall"));
  } else {
    const aiPass = state.mode === "live" && p.mode !== "live";
    const again = aiPass
      ? el("button", { type: "button", class: "ghost", id: "run-btn", title: "Adds AI spelling ideas and a second opinion; the rules still decide", onclick: () => startRun() }, "Re-check with AI suggestions")
      : el("button", { type: "button", class: "ghost", id: "run-btn", onclick: () => startRun() }, "Check again");
    if (info?.status.state === "closed") {
      answer = "This recall is finished.";
      detail = `Closed by ${info.case.closed_by}. The close-out record is below.`;
      buttons.push(again);
    } else if (kind === "pull") {
      const pullUnits = p.impact?.units_to_pull || 0;
      const pulledNow = p.pull_list.filter((v) => state.pulled.has(v.item_id)).length;
      const empty = p.pull_list.filter((v) => !(v.quantity > 0)).length;
      const told = Object.values(info?.case.notices || {}).filter((c) => c.sent_at).length;
      if (pulledNow >= pull) {
        answer = [em(pull === 1 ? "The item is" : `All ${pull} items are`), " off the shelves."];
        detail = check ? `Next: ${check} ${plural(check, "package needs", "packages need")} someone to read ${plural(check, "it", "them")} before we decide.`
          : agencies && told < agencies ? `Next: tell ${agencies - told} of the ${agencyText} that received this product.`
          : "Next: close the recall and print the close-out record.";
      } else {
        answer = ["Pull ", em(`${pull} ${plural(pull, "item", "items")}`), " from the shelves now."];
        detail = [
          `That's ${fmtNum(pullUnits)} ${plural(pullUnits, "unit", "units")} in total.`,
          empty ? ` ${empty === pull ? (pull === 1 ? "It has" : "They have") : `${empty} of them ${plural(empty, "has", "have")}`} nothing left on the shelf; check the shelf is empty.` : "",
          pulledNow ? ` ${pulledNow} of ${pull} done.` : "",
          check ? ` ${check} more ${plural(check, "needs", "need")} someone to read the package before we decide.` : "",
          agencies ? ` ${agencyText} received this product; a message for each one is ready below.` : " None of it was sent out to agencies.",
        ].join("");
      }
      buttons.push(again);
    } else if (kind === "check") {
      answer = ["Nothing to pull yet. ", em(`${check} ${plural(check, "item needs", "items need")}`), " a closer look."];
      detail = `Read each package and type what it says; Lotline will tell you whether to pull it.${agencies ? ` ${agencyText} received this product.` : ""}`;
      buttons.push(again);
    } else {
      answer = "None of this is in our stock.";
      detail = "We compared every stock record with the recall. The list at the bottom shows what was checked.";
      buttons.push(again);
    }
  }

  const lines = r.lines || [];
  const company = r.company && r.company !== r.brand ? r.company : r.brand;
  $("notice").replaceChildren(el("section", { class: `status-block k-${kind}`, "aria-label": "This recall" },
    el("div", { class: "st-recall" },
      el("span", { class: "st-tag", text: "Recall" }),
      el("span", { class: "st-reason", text: r.reason })),
    el("div", { class: "st-product" },
      el("div", { class: "st-brand", text: r.brand }),
      el("h1", { class: "st-name", text: r.product })),
    el("p", { class: "st-answer" }, answer),
    el("p", { class: "st-detail" }, detail),
    el("div", { class: "st-action" }, ...buttons,
      isRunning(r.recall_id) ? el("p", { class: "st-progress", id: "check-progress", text: "Starting..." }) : null),
    el("div", { class: "st-meta" },
      el("span", { text: `Recall issued ${fmtDate(r.announced_date)} by ${company}` }),
      el("span", { text: r.status === "active" ? "Still active" : "Closed by the FDA" }),
      el("button", { type: "button", class: "text-link st-stock", onclick: () => { history.replaceState(null, "", `${location.pathname}${location.search}#/stock`); openView("stock", r.brand); } }, `See all ${r.brand} stock`),
      r.notice_url ? el("a", { href: r.notice_url, target: "_blank", rel: "noopener noreferrer", text: "Read the FDA notice" }) : null,
      el("span", { class: "admin-only", text: `${r.recall_id} · ${r.recall_number || ""}` })),
    lines.length ? el("details", { class: "st-lines" },
      el("summary", { text: `Lot codes and barcodes on the recall notice (${lines.length} ${plural(lines.length, "product", "products")})` }),
      el("div", { class: "table-wrap" }, el("table", { class: "items" },
        el("thead", {}, el("tr", {}, ["Product", "Barcode (UPC)", "Lot or product codes", "Best by"].map((h) => el("th", { text: h })))),
        el("tbody", {}, lines.map((ln) => el("tr", {},
          el("td", {}, ln.brand !== r.brand ? el("b", { text: `${ln.brand} ` }) : null, `${ln.product}${ln.size ? `, ${ln.size}` : ""}`),
          el("td", {}, codeList(ln.upc, "not listed")),
          el("td", {}, codeList(ln.lot_codes, "none listed")),
          el("td", {}, codeList(ln.best_by, "none listed")))))))) : null));
}

function daysAgo(date) {
  if (!date) return "";
  const then = new Date(date + "T00:00:00");
  if (isNaN(then)) return date;
  const days = Math.round((Date.now() - then.getTime()) / 86_400_000);
  if (days < 0) return date;
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.round(days / 7)} week${days < 14 ? "" : "s"} ago`;
  return date;
}

/* pipeline */

function renderSteps(events = []) {
  const byAgent = Object.fromEntries(AGENT_ORDER.map((a) => [a, { state: "idle", text: "Waiting", round: 0 }]));
  const roles = Object.fromEntries((state.config?.agents || []).map((a) => [a.name, a.role]));
  for (const a of AGENT_ORDER) byAgent[a].text = roles[a] || "Waiting";
  for (const e of events) {
    const st = byAgent[e.agent_name];
    if (!st) {
      if (e.status === "error") {
        const running = AGENT_ORDER.find((a) => byAgent[a].state === "running");
        if (running) Object.assign(byAgent[running], { state: "error", text: e.output_summary });
      }
      continue;
    }
    if (e.event === "start") { st.state = "running"; st.round += 1; st.text = e.input_summary || st.text; }
    if (e.event === "done") {
      st.state = e.status === "revision_requested" ? "revision" : "done";
      st.text = e.output_summary || st.text;
    }
  }
  const loops = events.filter((e) => e.agent_name === "Critic" && e.status === "revision_requested");
  $("loop-note").hidden = !loops.length;
  $("loop-note").textContent = loops.length
    ? `The Critic sent the Matcher back ${loops.length === 1 ? "once" : `${loops.length} times`} to search for ${loops.map((e) => (e.output_summary.split("re-search with ")[1] || "")).filter(Boolean).join("; then ")}`
    : "";
  $("steps").replaceChildren(...AGENT_ORDER.map((a, i) => {
    const st = byAgent[a];
    const label = { idle: "waiting", running: "working", done: "done", revision: "sent back", error: "failed" }[st.state];
    return el("li", { class: "step", "data-state": st.state },
      el("span", { class: "step-dot", "aria-hidden": true }, st.state === "done" ? icon("ok") : st.state === "error" ? icon("no") : String(i + 1)),
      el("div", { class: "step-name", text: STEP_TITLE[a] }),
      el("span", { class: "step-state", text: st.round > 1 ? `${label} · round ${st.round}` : label }),
      el("span", { class: "step-agent", text: `${a} agent` }),
      el("div", { class: "step-text", text: st.text }));
  }));
  const prog = $("check-progress");
  if (prog && events.length) {
    const running = AGENT_ORDER.findIndex((a) => byAgent[a].state === "running");
    const at = running >= 0 ? running : Math.min(AGENT_ORDER.filter((a) => byAgent[a].state === "done").length, AGENT_ORDER.length - 1);
    prog.replaceChildren(el("b", { text: `Step ${at + 1} of ${AGENT_ORDER.length}: ` }), STEP_TITLE[AGENT_ORDER[at]]);
  }
}

const STEP_TITLE = {
  Triage: "Read the notice",
  SourceScout: "Found our records",
  Matcher: "Looked for matches",
  Critic: "Double-checked",
  Tracer: "Followed shipments",
  Reporter: "Wrote it up",
};

const viewing = (rid) => state.selected?.recall_id === rid && !$("main").querySelector(".overlay-view");
const isRunning = (rid = state.selected?.recall_id) => Boolean(state.running[rid]);
const recallName = (rid) => state.recalls.find((r) => r.recall_id === rid)?.brand || rid;

function showRunning(rid) {
  setPipelineOpen(true);
  renderNotice();
  $("results").hidden = true;
  $("error").hidden = true;
  $("pipeline").hidden = false;
  renderSteps(state.running[rid]?.trace || []);
}

function adopt(rid) {
  const c = state.packs[rid];
  Object.assign(state, { pack: c.pack, trace: c.trace, runId: c.runId });
  state.step = c.step || firstStep(c.pack);
  state.open.clear();
  loadPulled();
  renderSteps(state.trace);
  setPipelineOpen(Boolean(state.pipelineOpen));
  $("pipeline-status").textContent = runStatus();
  const url = new URL(location.href);
  url.searchParams.set("run", c.runId);
  history.replaceState(null, "", url);
  renderResults({ scroll: false });
}

function loadSavedRun(rid) {
  if (state.loadingRuns[rid]) return state.loadingRuns[rid];
  const runId = state.serverRuns[rid];
  state.loadingRuns[rid] = Promise.all([api(`/api/runs/${runId}`), api(`/api/runs/${runId}/trace`)])
    .then(([run, trace]) => {
      if (run.status !== "success") throw new Error(run.status);
      const pack = { ...run.output, _elapsed: run.execution_time_seconds };
      state.packs[rid] = { pack, trace, runId, step: firstStep(pack) };
    })
    .catch(() => { delete state.serverRuns[rid]; })
    .finally(() => { delete state.loadingRuns[rid]; renderRecalls(); });
  return state.loadingRuns[rid];
}

async function startRun(rid = state.selected?.recall_id, { mode = state.mode } = {}) {
  if (!rid || state.running[rid]) return;
  const job = { trace: [], t0: performance.now() };
  state.running[rid] = job;
  delete state.packs[rid];
  if (viewing(rid)) showRunning(rid);
  renderRecalls();
  const tick = setInterval(() => {
    if (viewing(rid)) $("pipeline-status").textContent = `${mode === "live" ? "with AI suggestions" : "rules only"} · running ${((performance.now() - job.t0) / 1000).toFixed(1)}s`;
  }, 100);
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ recall_id: rid, mode }) });
    let run;
    for (;;) {
      const [r, trace] = await Promise.all([api(`/api/runs/${run_id}`), api(`/api/runs/${run_id}/trace`)]);
      run = r;
      job.trace = trace;
      if (viewing(rid)) renderSteps(trace);
      if (run.status !== "running") break;
      await new Promise((ok) => setTimeout(ok, 400));
    }
    if (run.status === "error") throw new Error(run.error);
    const pack = { ...run.output, _elapsed: run.execution_time_seconds };
    state.packs[rid] = { pack, trace: job.trace, runId: run_id, step: firstStep(pack) };
    state.serverRuns[rid] = run_id;
    if (viewing(rid)) adopt(rid);
  } catch (err) {
    if (viewing(rid)) {
      showError(err.message);
      $("pipeline-status").textContent = "stopped";
    } else {
      toast(`The check for ${recallName(rid)} didn't finish. Open it to try again.`);
    }
  } finally {
    clearInterval(tick);
    delete state.running[rid];
    renderRecalls();
    if (viewing(rid)) renderNotice();
  }
}

function showError(message) {
  const box = $("error");
  box.hidden = false;
  box.replaceChildren(
    el("strong", { text: "The check didn't finish." }),
    el("p", { class: "err-plain", text: "Nothing was changed. Try again; if it keeps failing, an administrator can see the reason in Settings under technical details." }),
    el("p", { class: "admin-only", text: message }),
    el("button", { type: "button", class: "ghost", onclick: () => { if (state.mode === "live") setMode("sample"); startRun(); } },
      state.mode === "live" ? "Try again without AI suggestions" : "Try again"));
}

/* results */

const me = () => (store.get("lotline:reader") || "").trim();

function applyCase(info) {
  state.caseInfo = info;
  state.pulled = new Set(Object.keys(info.case.pulled || {}));
  if (info.case.updated_at) state.cases[info.case.recall_id] = info.status.state;
  if (state.pack) { renderCaseBar(); renderPullTable(); renderAgencies(); renderRecalls(); renderAudit(); renderStepper(); if (!isRunning()) renderNotice(); }
}

async function loadPulled() {
  state.pulled = new Set();
  state.caseInfo = null;
  try { applyCase(await api(`/api/cases/${state.pack.recall_id}?run_id=${state.runId}`)); } catch { /* case features need a live run */ }
}

async function caseAction(action, body) {
  try {
    applyCase(await api(`/api/cases/${state.pack.recall_id}/${action}`, { method: "POST", body: JSON.stringify({ run_id: state.runId, by: me(), ...body }) }));
  } catch (err) {
    toast(err.message);
    renderPullTable();
  }
}

function togglePulled(id, on) {
  caseAction("pulled", { item_id: id, pulled: on });
}

async function openRecord() {
  // Fetched rather than linked so an API key header still applies
  try {
    const headers = apiKey() ? { "X-API-Key": apiKey() } : {};
    const res = await fetch(`/api/cases/${state.pack.recall_id}/record?run_id=${state.runId}`, { headers });
    const html = await res.text();
    if (!res.ok) throw new Error(`the server answered ${res.status}`);
    const doc = new DOMParser().parseFromString(html, "text/html");
    $("print-sheet").replaceChildren(el("div", { class: "record-print" }, [...doc.body.childNodes].map((n) => document.importNode(n, true))));
    window.print();
  } catch (err) {
    toast(`Could not open the record: ${err.message}`);
  }
}

const CASE_STEPS = [["pulling", "Pull"], ["checking", "Read packages"], ["notifying", "Message agencies"], ["waiting", "Agency replies"], ["ready", "Close"]];

function renderCaseBar() {
  const bar = $("casebar");
  const info = state.caseInfo;
  if (!info) {
    bar.replaceChildren(el("p", { class: "review-empty", text: "Progress is tracked once this check is saved. Run the check again if this message stays." }));
    renderStepper();
    return;
  }
  const st = info.status;
  const rows = [
    ["Items taken off the shelf", st.pull_total ? `${st.pulled} of ${st.pull_total}` : "None to pull", st.pulled >= st.pull_total],
    ["Packages checked", st.to_check.length ? `${st.to_check.length} still to check` : "All done", !st.to_check.length],
    ["Agencies told", st.agencies ? `${st.agencies - st.to_send.length} of ${st.agencies}` : "None to tell", !st.to_send.length],
    ["Agencies replied", st.agencies ? `${st.replied} of ${st.agencies}` : "None to hear from", st.replied >= st.agencies],
  ];
  bar.replaceChildren(
    el("ul", { class: "finish-list" }, rows.map(([k, val, ok]) => el("li", { class: ok ? "ok" : "todo" },
      icon(ok ? "ok" : "warn"), el("span", { class: "fl-k", text: k }), el("span", { class: "fl-v", text: val })))),
    el("div", { class: "panel-foot" },
      el("button", { type: "button", class: "primary big-btn", disabled: st.state !== "ready", onclick: () => caseAction("close", {}) },
        st.state === "closed" ? `Closed by ${info.case.closed_by}` : "Close this recall"),
      el("button", { type: "button", class: "ghost big-btn", onclick: openRecord }, "Print the close-out record"),
      st.state !== "ready" && st.state !== "closed" ? el("span", { class: "muted", text: "You can close it once everything above is ticked." }) : null));
  renderStepper();
}

function ruleChips(checks = []) {
  const names = { lot: "lot", upc_match: "barcode", brand: "brand", product: "name", best_by: "date", size: "size", source_recheck: "re-checked" };
  return el("div", { class: "rules" }, checks.map((c) => {
    const kind = checkWord(c);
    const word = c.rule === "source_recheck" ? (kind === "pass" ? "" : " ?") : { pass: " matches", fail: " differs", unknown: " unknown" }[kind];
    return el("span", { class: `rule rule-${kind}`, title: c.note || "" }, `${names[c.rule] || c.rule}${word}`);
  }));
}

function table(id, headers, rows, emptyText) {
  const t = $(id);
  if (!rows.length) {
    t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "none", colspan: headers.length, text: emptyText }))));
    return;
  }
  t.replaceChildren(
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { class: h.num ? "num" : null, text: h.label })))),
    el("tbody", {}, rows));
}

const METHOD_TEXT = {
  learned: "spelling confirmed by staff",
  alias: "spelling guessed by the computer",
  notice: "name on the recall notice",
  in_name: "brand written in the product name",
  fuzzy: "spelled differently",
  missing: "no brand written down",
  different: "a different brand",
};

function brandBadge(v) {
  const m = v.brand_match;
  if (!m || !METHOD_TEXT[m.method] || m.method === "different") return null;
  const label = ["missing", "in_name"].includes(m.method) ? METHOD_TEXT[m.method] : `as "${m.recorded_as}"`;
  const badge = el("span", { class: `brand-badge brand-${m.method}`, title: METHOD_TEXT[m.method] },
    label, ["missing", "in_name"].includes(m.method) ? null
      : el("small", { text: ` · ${METHOD_TEXT[m.method]}` }));
  if (m.method !== "fuzzy" && m.method !== "alias") return badge;
  return el("span", { class: "brand-wrap" }, badge,
    el("button", { type: "button", class: "link-btn", onclick: (e) => approveAlias(v, e.currentTarget) }, "Confirm spelling"));
}

function setMethod(alias, method) {
  const p = state.pack;
  for (const item of [...p.pull_list, ...p.human_checks, ...p.near_misses]) {
    if (item.brand_match?.recorded_as === alias) item.brand_match = { ...item.brand_match, method };
  }
  renderResults({ scroll: false });
}

async function approveAlias(v, btn) {
  const brand = v.line_brand || state.pack.recall.brand;
  const alias = v.brand_match.recorded_as;
  btn.disabled = true;
  try {
    await api("/api/aliases", { method: "POST", body: JSON.stringify({ brand, alias, run_id: state.runId }) });
    setMethod(alias, "learned");
    toast(`Saved. From now on Lotline treats "${alias}" as ${brand}.`, async () => {
      await api(`/api/aliases?brand=${encodeURIComponent(brand)}&alias=${encodeURIComponent(alias)}`, { method: "DELETE" });
      setMethod(alias, "fuzzy");
    });
  } catch (err) {
    btn.disabled = false;
    toast(`Could not save: ${err.message}`);
  }
}

let toastTimer;
function toast(message, undo) {
  const box = $("toast");
  box.replaceChildren(...[el("span", { text: message }),
    undo ? el("button", { type: "button", class: "link-btn", onclick: async () => { box.hidden = true; await undo(); } }, "Undo") : null].filter(Boolean));
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 7000);
}

function productCell(v) {
  return el("td", {}, v.product, brandBadge(v),
    v.reading ? el("span", { class: "read-badge", text: `Package read by ${v.reading.by}` }) : null);
}

function evidenceToggle(v) {
  return el("button", { type: "button", class: "why-btn", "aria-label": `Why ${v.product} is on this list`,
    onclick: (e) => { e.stopPropagation(); openDrawer(v); } }, "Evidence");
}

const rowOpen = (v) => (e) => { if (!e.target.closest("input, button, a, label")) openDrawer(v); };


function checkWord(c) {
  return c.passed === true ? "pass" : c.passed === false ? "fail" : "unknown";
}

function readingForm(v) {
  const needs = (v.rule_checks || []).filter((c) => c.passed !== true).map((c) => c.rule);
  const line = noticeLine(v);
  const recallSays = (text, empty) => (text ? `The recall lists ${text}.` : empty);
  // Empty fields: only what the person types is recorded as read; the stored value is shown as a hint
  const field = (name, label, hint, compare, attrs = {}) => {
    const need = needs.includes(name === "lot_code" ? "lot" : name === "upc" ? "upc_match" : name);
    return el("label", { class: need ? "need" : null },
      el("span", { text: label }),
      el("input", { name, value: "", autocomplete: "off", ...attrs,
        oninput: (e) => { e.target.setCustomValidity(""); if (attrs.type === "date") e.target.classList.toggle("empty", !e.target.value); } }),
      el("small", { class: "field-hint", text: hint }),
      compare ? el("small", { class: "field-compare", text: compare }) : null);
  };
  const form = el("form", { class: "reading-form", onsubmit: (e) => {
    e.preventDefault();
    const lot = e.currentTarget.elements.lot_code;
    if (/^\d{11,14}$/.test(lot.value.replace(/\s/g, ""))) {
      lot.setCustomValidity("That looks like a barcode number. Put it in the Barcode box; the lot code is the short code stamped near the date.");
      lot.reportValidity();
      return;
    }
    saveReading(v, e.currentTarget);
  } },
    el("p", { class: "reading-help", text: `Pick up the package on shelf ${v.location || "(no location)"} and type only what's printed on it. Leave a box empty if you can't read it.` }),
    el("div", { class: "reading-fields" },
      field("upc", "Barcode (UPC)", "The long number under the barcode stripes, usually 12 digits.",
        recallSays(line?.upc, "The recall lists no barcode."),
        { inputmode: "numeric", pattern: "[0-9 ]{8,16}", title: "Digits only, as printed under the barcode", placeholder: v.upc ? `recorded: ${v.upc}` : "e.g. 012345678905" }),
      field("lot_code", "Lot or product code", "The short code stamped near the date, like L26-241. Not the barcode.",
        line?.lot_codes?.toUpperCase() === "ALL" ? "The recall covers every lot, so the barcode decides." : recallSays(allCodes(line?.lot_codes), "The recall lists no lot codes."),
        { placeholder: v.lot_code ? `recorded: ${v.lot_code}` : "e.g. L26-241" }),
      field("best_by", "Best by", "The \u201cbest by\u201d or \u201cuse by\u201d date.",
        recallSays(allCodes(line?.best_by), "The recall lists no dates."),
        { type: "date", class: "empty", title: v.best_by ? `Recorded as ${fmtDate(v.best_by)}` : null }),
      el("label", {}, el("span", { text: "Read by" }), el("input", { name: "read_by", value: me(), placeholder: "your name", autocomplete: "name", required: true }),
        el("small", { class: "field-hint", text: "Saved with this reading." }))),
    el("div", { class: "actions" },
      el("button", { type: "submit", class: "primary" }, "Save and decide"),
      el("button", { type: "button", class: "ghost", onclick: () => { state.reading = null; renderReviewQueue(); } }, "Cancel")));
  return form;
}

async function saveReading(v, form) {
  const data = Object.fromEntries(new FormData(form));
  store.set("lotline:reader", data.read_by || "");
  renderUser();
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const res = await api(`/api/runs/${state.runId}/items/${encodeURIComponent(v.item_id)}/reading`, { method: "POST", body: JSON.stringify(data) });
    state.pack = { ...res.output, _elapsed: state.pack._elapsed };
    state.reading = null;
    const where = { pull_list: "Pull it: it's part of the recall.", human_checks: "Still not sure. It needs another look.", near_misses: "Not part of the recall. It can stay." }[res.list];
    toast(`${v.product} (shelf ${v.location}): ${where}`);
    renderResults({ scroll: false });
    loadPulled();
    loadScreen(true);
  } catch (err) {
    btn.disabled = false;
    toast(`Could not record the reading: ${err.message}`);
  }
}

function qty(v) { return el("td", { class: "num" }, `${fmtNum(v.quantity || 0)} ${v.unit || ""}`); }

const STEP_ORDER = ["pull", "check", "notify", "finish"];

function firstStep(p = state.pack) {
  if (p.pull_list.length) return "pull";
  if (p.human_checks.length) return "check";
  if (p.agency_notices.length) return "notify";
  return "finish";
}

function stepInfo() {
  const p = state.pack;
  const st = state.caseInfo?.status;
  const pullTotal = p.pull_list.length;
  const pulled = st ? st.pulled : state.pulled.size;
  const toCheck = p.human_checks.length;
  const agencies = p.agency_notices.length;
  const sent = st ? st.agencies - st.to_send.length : 0;
  return {
    pull: { label: "Pull from shelves", sub: pullTotal ? `${pulled} of ${pullTotal} done` : "Nothing to pull", done: pulled >= pullTotal, todo: pulled < pullTotal ? "todo-pull" : "" },
    check: { label: "Check packages", sub: toCheck ? `${toCheck} to check` : "Nothing to check", done: toCheck === 0, todo: toCheck ? "todo-check" : "" },
    notify: { label: "Tell agencies", sub: agencies ? `${sent} of ${agencies} told` : "No one to tell", done: sent >= agencies, todo: "" },
    finish: { label: "Finish", sub: st?.state === "closed" ? "Closed" : st?.state === "ready" ? "Ready to close" : "Close when all done", done: st?.state === "closed", todo: "" },
  };
}

function renderStepper() {
  if (!state.pack || $("results").hidden) return;
  const info = stepInfo();
  const at = STEP_ORDER.indexOf(state.step);
  $("stepper").replaceChildren(
    el("p", { class: "stepper-hint", text: `Work through these ${STEP_ORDER.length} steps in order. Tap a step to go back to it.` }),
    el("ol", { class: "wizard" }, STEP_ORDER.map((k, i) => {
      const st = info[k];
      const here = i === at;
      return el("li", { class: `wz-step${st.done ? " done" : ""}${here ? " here" : ""} ${st.todo}` },
        el("button", { type: "button", class: "wz-btn", "aria-current": here ? "step" : null, onclick: () => showStep(k) },
          el("span", { class: "wz-dot" }, st.done && !here ? icon("ok") : String(i + 1)),
          el("span", { class: "wz-text" },
            el("span", { class: "wz-label", text: st.label }),
            el("span", { class: "wz-sub", text: here ? "You are here" : st.sub }))));
    })));
}

function showStep(step) {
  state.step = STEP_ORDER.includes(step) ? step : firstStep();
  const info = stepInfo();
  for (const panel of document.querySelectorAll(".step-panel")) {
    const k = panel.dataset.step;
    panel.hidden = k !== state.step;
    const head = panel.querySelector(".panel-head");
    let kicker = head.querySelector(".panel-step");
    if (!kicker) { kicker = el("p", { class: "panel-step" }); head.prepend(kicker); }
    kicker.textContent = `Step ${STEP_ORDER.indexOf(k) + 1} of ${STEP_ORDER.length}`;
    const next = STEP_ORDER[STEP_ORDER.indexOf(k) + 1];
    const btn = panel.querySelector("[data-next]");
    if (btn && next) btn.textContent = `Next: ${info[next].label}`;
  }
  renderStepper();
}

function nextStep() {
  const i = STEP_ORDER.indexOf(state.step);
  showStep(STEP_ORDER[Math.min(i + 1, STEP_ORDER.length - 1)]);
  $("stepper").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function prevStep() {
  const i = STEP_ORDER.indexOf(state.step);
  showStep(STEP_ORDER[Math.max(i - 1, 0)]);
  $("stepper").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function setPipelineOpen(open) {
  $("pipeline").classList.toggle("collapsed", !open);
  $("pipeline-toggle").textContent = open ? "Hide the steps" : "Show the steps";
  $("pipeline-toggle").setAttribute("aria-expanded", String(open));
}

function icon(kind) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "rb-icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", { ok: "M3 8.5l3 3 7-7", warn: "M8 2.5l6 11H2zM8 7v2.5M8 11.6v.4", no: "M4 4l8 8M12 4l-8 8" }[kind]);
  for (const [k, v] of Object.entries({ fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" })) path.setAttribute(k, v);
  svg.append(path);
  return svg;
}

function reasonBadges(v) {
  const c = Object.fromEntries((v.rule_checks || []).map((x) => [x.rule, x]));
  const m = v.brand_match || {};
  const out = [];
  const add = (kind, text) => out.push(el("span", { class: `reason-badge rb-${kind}` }, icon(kind), text));
  if (c.upc_match?.passed === true) add("ok", "Exact barcode match");
  else if (c.upc_match?.passed === false) add("no", "Different barcode");
  else if (!v.upc) add("warn", "No barcode written down");
  if (m.method === "fuzzy") add("warn", `Found under a different spelling (“${m.recorded_as}”)`);
  else if (m.method === "alias") add("warn", `Spelling guessed by the computer (“${m.recorded_as}”)`);
  else if (m.method === "learned") add("ok", `Spelling confirmed by staff (“${m.recorded_as}”)`);
  else if (m.method === "missing") add("warn", "No brand written down");
  else if (m.method === "different") add("no", "A different brand");
  if (c.lot?.passed === true) add("ok", "Lot code is in the recall");
  else if (c.lot?.passed === false) add("no", "Lot code isn't in the recall");
  else if (c.lot) add("warn", v.lot_code ? "Lot code unclear" : "No lot code written down");
  if (c.best_by?.passed === true && c.lot?.passed !== true) add("ok", "Best-by date is in the recall");
  else if (c.best_by?.passed === false) add("no", "Best-by date isn't in the recall");
  if (c.source_recheck?.passed === true) add("ok", "Record re-checked before pulling");
  if (v.reading) add("ok", `Package read by ${v.reading.by}`);
  return el("div", { class: "reason-badges" }, out);
}

function renderPullTable() {
  const items = state.pack.pull_list;
  $("pulled-note").textContent = items.length ? `${state.pulled.size} of ${items.length} done.` : "";
  if (!items.length) {
    $("pull-cards").replaceChildren(el("p", { class: "review-empty", text: "Nothing on our shelves matches this recall exactly, so there's nothing to pull." }));
  } else {
    $("pull-cards").replaceChildren(...items.map((v) => {
      const done = state.pulled.has(v.item_id);
      const by = state.caseInfo?.case.pulled[v.item_id]?.by;
      const none = !(v.quantity > 0);
      return el("article", { class: `pull-card${done ? " done" : ""}`, "aria-label": `${v.product}, shelf ${v.location}` },
        el("div", { class: "pc-shelf" }, el("span", { class: "pc-lbl", text: "Shelf" }), el("span", { class: "pc-loc", text: v.location || "?" })),
        el("div", { class: "pc-main" },
          el("div", { class: "pc-qty", text: none ? "Nothing left on this shelf" : `Take ${fmtNum(v.quantity)} ${v.unit || ""} off the shelf` }),
          none ? el("div", { class: "pc-note", text: "All of it was sent to agencies (step 3). Check the shelf is empty, then tick." }) : null,
          el("div", { class: "pc-name", text: v.product }),
          el("div", { class: "pc-lot" }, "Lot", lotChip(v.lot_code)),
          reasonBadges(v),
          el("button", { type: "button", class: "text-link", onclick: () => openDrawer(v) }, "Show the evidence")),
        el("label", { class: "pc-check" },
          el("input", { type: "checkbox", checked: done, onchange: (e) => togglePulled(v.item_id, e.target.checked) }),
          el("span", { text: none ? (done ? `Shelf checked${by ? ` by ${by}` : ""}` : "I've checked the shelf")
            : done ? `Taken off the shelf${by ? ` by ${by}` : ""}` : "I've taken these off the shelf" })));
    }));
  }
  renderStepper();
}

function whyCell(v) {
  return el("td", { class: "why" }, plain(v.rationale), v.model_note ? el("span", { class: "model-note", text: `Reviewer: ${v.model_note}` }) : null);
}

function renderImpact() {
  const p = state.pack;
  const im = p.impact;
  if (!im || !im.brand_units_found) { $("impact").hidden = true; return; }
  $("impact").hidden = false;
  const parts = [["pull", "to pull now", im.units_to_pull], ["check", "to check by hand", im.units_to_check], ["clear", "stay in use", im.units_kept_in_use]]
    .filter(([, , n]) => n > 0);
  const spared = im.agencies_total - im.agencies_to_notify;
  $("impact").replaceChildren(
    el("p", { class: "impact-head" },
      "Pulling every case of the brand would take ", el("b", { text: `${fmtNum(im.brand_units_found)} units` }),
      " off the shelves and alert ", el("b", { text: `all ${im.agencies_total} agencies` }), ". Lotline narrows it to:"),
    el("div", { class: "impact-bar", role: "img", "aria-label": parts.map(([, label, n]) => `${fmtNum(n)} units ${label}`).join(", ") },
      parts.map(([k, , n]) => el("span", { class: `seg seg-${k}`, style: `flex-grow:${n}` }))),
    el("ul", { class: "impact-legend" },
      parts.map(([k, label, n]) => el("li", {}, el("span", { class: `dot dot-${k}` }), el("b", { text: fmtNum(n) }), ` units ${label}`)),
      el("li", {}, el("span", { class: "dot dot-notify" }), el("b", { text: String(im.agencies_to_notify) }),
        ` of ${im.agencies_total} agencies alerted${spared > 0 ? `, ${spared} left alone` : ""}`)));
}

function renderResults({ scroll = true } = {}) {
  const p = state.pack;
  $("results").hidden = false;
  $("briefing").textContent = p.verdict_summary;
  const bs = p.briefing_source || "template";
  const rejected = bs.includes("rejected");
  $("briefing-source").textContent = rejected
    ? `Written from the counts: the model's draft was rejected (${bs.split("rejected: ")[1].replace(/\)$/, "")})`
    : bs === "template" ? "Written from the counts below" : `Written by ${shortModel(bs.replace(" (numbers checked)", ""))} · every number checked against the data`;
  $("briefing-source").dataset.kind = rejected ? "warn" : "ok";
  const ups = p.updates || [];
  if (ups.length) {
    $("briefing-source").textContent += ` · written before ${ups.length} package reading${ups.length === 1 ? "" : "s"}; the counts below are current`;
  }
  const gaps = p.data_gaps || [];
  $("data-gaps").hidden = !gaps.length;
  $("data-gaps").textContent = gaps.length ? `Ran in a degraded mode: ${gaps.join("; ")}` : "";
  if (gaps.length) {
    $("fallback-note").hidden = false;
    $("fallback-note").textContent = "Part of this check couldn't run as usual. The pull list still comes from the rules, but double-check items against the shelf.";
    $("fallback-note").title = gaps.join("; ");
  }
  renderImpact();
  const stats = [
    ["pull", "Pull now", p.pull_list.length, `${fmtNum(p.total_units_to_pull)} units`],
    ["check", "Check by hand", p.human_checks.length, ""],
    ["notify", "Agencies to notify", p.agency_notices.length, `${p.distributed_items.length} shipments`],
    ["clear", "Ruled out", p.near_misses.length, ""],
    ["time", "Traced in", `${p._elapsed}`, "sec"],
  ];
  $("stats").replaceChildren(...stats.map(([k, label, n, sub]) =>
    el("div", { class: `stat stat-${k}` }, el("dt", { text: label }), el("dd", {}, String(n), sub ? el("small", { text: sub }) : null))));

  renderPullTable();

  renderReviewQueue();

  renderAgencies();

  $("clear-count").textContent = `(${p.near_misses.length})`;
  $("clear-details").open = p.pull_list.length === 0 && p.human_checks.length === 0;
  table("clear-table",
    [{ label: "Shelf" }, { label: "Product" }, { label: "Lot" }, { label: "Why it's not part of the recall" }, { label: "" }],
    p.near_misses.map((v) => el("tr", { class: "clickable", onclick: rowOpen(v) },
      el("td", {}, el("span", { class: "loc", text: v.location || "?" })),
      productCell(v),
      el("td", {}, lotChip(v.lot_code)),
      whyCell(v),
      el("td", { class: "num" }, evidenceToggle(v)))),
    "Nothing else from this brand is in stock.");

  renderAudit();
  showStep(state.step);
  if (!isRunning()) renderNotice();
  if (scroll) $("results").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function renderAgencies() {
  const notices = state.pack.agency_notices;
  if (!notices.length) {
    $("agencies").replaceChildren(el("p", { class: "none", text: "None of the flagged stock has been shipped out." }));
    return;
  }
  $("agencies").replaceChildren(...notices.map((n) => {
    const [subjectLine, ...rest] = n.draft_message.split("\n");
    const subject = subjectLine.replace(/^Subject:\s*/, "");
    const body = rest.join("\n").trim();
    const mailto = `mailto:${encodeURIComponent(n.contact_email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    const status = el("span", { class: "sent" });
    return el("article", { class: "agency" },
      el("div", {}, el("div", { class: "agency-name", text: n.agency_name }),
        el("div", { class: "agency-contact", text: `${n.contact_name} · ${n.contact_email}` })),
      el("ul", {}, n.items.map((i) => el("li", {},
        el("span", { class: `tag tag-${i.item_verdict === "CONFIRMED_MATCH" ? "remove" : "hold"}`, text: i.item_verdict === "CONFIRMED_MATCH" ? "Remove" : "Hold" }),
        el("span", {}, `${fmtNum(i.quantity)} ${i.unit} `, lotChip(i.lot_code), ` sent ${fmtDate(i.distributed_date)}`)))),
      el("details", {}, el("summary", { text: "Read the message" }), el("pre", { text: n.draft_message })),
      el("div", { class: "actions" },
        el("button", { type: "button", class: "ghost big-btn", onclick: async () => {
          try { await navigator.clipboard.writeText(n.draft_message); status.textContent = "Copied"; } catch { status.textContent = "Copy blocked by browser"; }
        } }, "Copy message"),
        el("a", { class: "ghost big-btn", href: mailto }, "Open in email"),
        status),
      noticeTracking(n));
  }));
}

function noticeTracking(n) {
  const c = state.caseInfo?.case.notices?.[n.agency_id] || {};
  if (!state.caseInfo) return null;
  if (!c.sent_at) {
    return el("div", { class: "notice-track" }, el("span", { class: "draft-flag", text: "Not sent yet" }),
      el("button", { type: "button", class: "primary sent-btn", onclick: () => caseAction("notice", { agency_id: n.agency_id, event: "sent" }) }, "I've sent it"));
  }
  const sent = `Sent ${fmtDate(c.sent_at)}, ${c.sent_at.slice(11, 16)} by ${c.sent_by}`;
  if (c.replied_at) {
    return el("div", { class: "notice-track done" }, el("span", { text: `${sent} · replied${c.remaining != null ? `, ${fmtNum(c.remaining)} still on hand` : ""}` }));
  }
  const input = el("input", { type: "number", min: "0", step: "1", placeholder: "units left", "aria-label": "Units the agency still has" });
  return el("div", { class: "notice-track" }, el("span", { class: "muted", text: sent }), input,
    el("button", { type: "button", class: "ghost", onclick: () => caseAction("notice", {
      agency_id: n.agency_id, event: "replied", remaining: input.value === "" ? null : Number(input.value) }) }, "Save their reply"));
}

function renderAudit() {
  const p = state.pack;
  const crit = p.match_criteria || {};
  const wm = Object.entries(p.world_model || {}).map(([k, v]) => `${v} ${k}`).join(", ");
  const opened = state.caseInfo?.case.opened_at;
  $("audit-run").replaceChildren(...[
    el("div", { text: `Checked ${fmtTime(p.generated_at)}, ${p.mode === "live" ? "with AI suggestions" : "by the rules only"}.` }),
    opened ? el("div", { text: `Recall work started ${fmtTime(opened)}.` }) : null,
    el("div", { text: `Check reference: ${p.run_id}` }),
    el("div", { text: "Every item's evidence (stock record, comparison, second look, shipments) opens from “Show the evidence”." }),
  ].filter(Boolean));
  $("audit-meta").replaceChildren(...[
    el("span", {}, el("b", { text: "Mode " }), `${p.mode} (${p.model})`),
    el("span", {}, el("b", { text: "Spellings suggested " }), (crit.brand_aliases || []).join(", ")),
    el("span", {}, el("b", { text: "Spellings found in data " }), (p.searched_brands || []).join(", ") || "n/a"),
    (crit.learned_aliases || []).length ? el("span", {}, el("b", { text: "Learned from past runs " }), crit.learned_aliases.join(", ")) : null,
    el("span", {}, el("b", { text: "Sources " }), (p.sources || []).map((s) => (s.role ? `${s.table_name} as ${s.role}` : s.table_name)).join(", ")),
    el("span", {}, el("b", { text: "Columns mapped by " }), p.mapping_method || "column names"),
    el("span", {}, el("b", { text: "Briefing " }), p.briefing_source || "template"),
    el("span", {}, el("b", { text: "Critic revisions " }), String(p.revision_rounds)),
    el("span", {}, el("b", { text: "World model " }), wm || "n/a"),
    el("span", {}, el("b", { text: "Run " }), p.run_id),
  ].filter(Boolean));
  $("timeline").replaceChildren(...state.trace.map((e) => el("li", {},
    el("time", { text: (e.timestamp || "").slice(11, 19) }),
    el("span", { class: "agent", text: `${AGENT_LABEL[e.agent_name] || e.agent_name} ${e.event}` }),
    el("span", { class: e.status === "revision_requested" ? "tl-revision" : e.status === "error" ? "tl-error" : null,
      text: e.output_summary || e.input_summary || e.action }))));
}

function buildPrintSheet() {
  const p = state.pack;
  const r = state.recalls.find((x) => x.recall_id === p.recall_id) || p.recall || {};
  const now = new Date();
  const printed = `${now.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })} ${now.toTimeString().slice(0, 5)}`;
  const box = () => el("span", { class: "ps-box", "aria-hidden": true });
  const blank = () => el("span", { class: "ps-blank" });
  $("print-sheet").replaceChildren(
    el("header", { class: "ps-head" },
      el("div", {},
        el("p", { class: "ps-kicker", text: "Recall pull list" }),
        el("h1", { text: `${r.brand || ""} ${r.product || ""}`.trim() }),
        el("p", { class: "ps-reason", text: `Recalled for: ${r.reason || ""}` })),
      el("dl", { class: "ps-meta" },
        el("dt", { text: "FDA notice" }), el("dd", { text: `${r.recall_number || p.recall_id}, announced ${fmtDate(r.announced_date)}` }),
        el("dt", { text: "Food bank" }), el("dd", { text: myFoodBank() }),
        el("dt", { text: "Printed" }), el("dd", { text: `${printed} by ${me() || "________________"}` }),
        el("dt", { text: "Lotline check" }), el("dd", { text: p.run_id }))),
    el("p", { class: "ps-instr", text: "Take every item below off the shelf and move it to the hold area. Do not give any of it out. Tick the box and initial each line when done." }),
    el("table", { class: "ps-table" },
      el("thead", {}, el("tr", {}, ["Done", "Shelf", "Product as recorded", "Lot", "Best by", "Quantity", "Initials"].map((h) => el("th", { text: h })))),
      el("tbody", {}, p.pull_list.length ? p.pull_list.map((v) => el("tr", {},
        el("td", {}, box()), el("td", { class: "ps-shelf", text: v.location || "?" }), el("td", { text: v.product }),
        el("td", { class: "ps-mono", text: v.lot_code || "not recorded" }), el("td", { text: v.best_by ? fmtDate(v.best_by) : "" }),
        el("td", { text: `${fmtNum(v.quantity || 0)} ${v.unit || ""}` }), el("td", {}, blank())))
        : [el("tr", {}, el("td", { colspan: "7", text: "Nothing to pull." }))])),
    p.human_checks.length ? el("section", {},
      el("h2", { text: `Set aside and read the package (${p.human_checks.length})` }),
      el("p", { class: "ps-instr", text: "Lotline could not confirm these. Hold them, read the lot code, best-by date and barcode on the package, and enter what you read in Lotline." }),
      el("table", { class: "ps-table" },
        el("thead", {}, el("tr", {}, ["Shelf", "Product as recorded", "Lot read on package", "Best by read", "Barcode read", "Initials"].map((h) => el("th", { text: h })))),
        el("tbody", {}, p.human_checks.map((v) => el("tr", {},
          el("td", { class: "ps-shelf", text: v.location || "?" }), el("td", { text: v.product }),
          el("td", {}, blank()), el("td", {}, blank()), el("td", {}, blank()), el("td", {}, blank())))))) : null,
    el("footer", { class: "ps-sign" },
      el("p", {}, "Pulled by ", blank(), "  Date ", blank()),
      el("p", {}, "Checked by ", blank(), "  Date ", blank()),
      el("p", { class: "ps-note", text: "Evidence for every item is in Lotline under “Show the evidence”." })));
}

function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+\-@]/.test(s)) s = `'${s}`;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function exportCsv() {
  const p = state.pack;
  const rows = [["task", "item_id", "brand_recorded", "product", "lot_code", "quantity", "unit", "location", "verdict", "reason", "source_record", "pulled"]];
  for (const [task, list] of [["pull", p.pull_list], ["check", p.human_checks]]) {
    for (const v of list) rows.push([task, v.item_id, v.brand, v.product, v.lot_code, v.quantity, v.unit, v.location, v.verdict, v.rationale, v.source_ref, state.pulled.has(v.item_id) ? "yes" : ""]);
  }
  download(`lotline-${p.recall_id}-${p.run_id}.csv`, rows.map((r) => r.map(csvCell).join(",")).join("\n"), "text/csv");
}

/* boot */

async function boot() {
  setAdmin(store.get("lotline:admin") === "1");
  $("admin-toggle").addEventListener("change", (e) => setAdmin(e.target.checked));
  $("drawer-close").addEventListener("click", () => $("drawer").close());
  $("settings-close").addEventListener("click", () => $("settings").close());
  for (const id of ["drawer", "settings"]) $(id).addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  $("coord-input").addEventListener("input", (e) => { store.set("lotline:reader", e.target.value.replace(/\s+/g, " ").trimStart()); renderUser(); });
  $("role-input").addEventListener("input", (e) => { store.set("lotline:role", e.target.value.replace(/\s+/g, " ").trimStart()); renderUser(); });
  $("foodbank-input").addEventListener("input", (e) => { store.set("lotline:foodbank", e.target.value.replace(/\s+/g, " ").trimStart()); renderUser(); });
  $("user-btn").addEventListener("click", openAccount);
  $("account-close").addEventListener("click", () => $("account").close());
  $("account").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  $("test-btn").addEventListener("click", testConnection);
  $("demo-reset-btn").addEventListener("click", resetDemo);
  $("ai-toggle").addEventListener("click", () => {
    if (!state.config?.live_available) return;
    setMode(state.mode === "live" ? "sample" : "live");
    toast(state.mode === "live" ? "AI suggestions on. Use \u201cCheck again\u201d to re-check a recall with them." : "AI suggestions off. New checks use the rules only.");
  });
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("search-input").value.trim();
    history.replaceState(null, "", `${location.pathname}${location.search}#/stock`);
    openView("stock", q);
    $("search-input").value = "";
  });
  renderUser();
  $("print-btn").addEventListener("click", () => { buildPrintSheet(); window.print(); });
  for (const b of document.querySelectorAll("[data-next]")) b.addEventListener("click", nextStep);
  for (const b of document.querySelectorAll("[data-prev]")) b.addEventListener("click", prevStep);
  wireRailResizer();
  $("rail-collapse").addEventListener("click", () => { store.set("lotline:rail-collapsed", "1"); setRail("shown"); });
  $("rail-open").addEventListener("click", () => { store.set("lotline:rail-collapsed", ""); setRail("shown"); });
  $("pipeline-toggle").addEventListener("click", () => {
    state.pipelineOpen = $("pipeline").classList.contains("collapsed");
    setPipelineOpen(state.pipelineOpen);
  });
  $("csv-btn").addEventListener("click", exportCsv);
  $("json-btn").addEventListener("click", () => download(`lotline-${state.pack.run_id}.json`, JSON.stringify(state.pack, null, 2), "application/json"));
  $("trace-btn").addEventListener("click", () => download(`trace-${state.pack.run_id}.jsonl`, state.trace.map((e) => JSON.stringify(e)).join("\n"), "application/x-ndjson"));

  try {
    [state.config, state.recalls] = await Promise.all([api("/api/config"), api("/api/recalls")]);
  } catch (err) {
    $("notice").replaceChildren(el("p", { class: "empty", text: `Could not reach the Lotline API: ${err.message}` }));
    return;
  }
  renderHeader();
  $("demo-group").hidden = !state.config.demo_mode;
  try {
    for (const r of await api("/api/runs")) {
      if (r.status === "success" && r.recall_id && !state.serverRuns[r.recall_id]) state.serverRuns[r.recall_id] = r.run_id;
    }
  } catch { /* no saved checks */ }
  wireNav();
  loadScreen();
  const params = new URLSearchParams(location.search);
  const hashView = location.hash.replace(/^#\//, "");
  if (params.get("mode") && (params.get("mode") === "sample" || state.config.live_available)) setMode(params.get("mode"));
  const wanted = state.recalls.find((r) => r.recall_id === params.get("recall"));
  const runParam = params.get("run");
  selectRecall((wanted || state.recalls.find((r) => r.status === "active") || state.recalls[0]).recall_id, { auto: !runParam });
  if (runParam) openRun(runParam);
  for (const rid of Object.keys(state.serverRuns)) if (!state.packs[rid]) loadSavedRun(rid);
  if (VIEWS[hashView]) {
    history.replaceState(null, "", `${location.pathname}${location.search}#/${hashView}`);
    openView(hashView);
  }
}

async function openRun(runId) {
  try {
    const [run, trace] = await Promise.all([api(`/api/runs/${encodeURIComponent(runId)}`), api(`/api/runs/${encodeURIComponent(runId)}/trace`)]);
    if (run.status !== "success") throw new Error(run.error || `Run is ${run.status}`);
    Object.assign(state, { runId, trace, pack: { ...run.output, _elapsed: run.execution_time_seconds } });
    state.step = firstStep();
    state.packs[state.pack.recall_id] = { pack: state.pack, trace, runId };
    state.selected = state.recalls.find((r) => r.recall_id === run.output.recall_id) || state.selected;
    renderRecalls();
    renderNotice();
    $("pipeline").hidden = false;
    renderSteps(trace);
    $("pipeline-status").textContent = runStatus();
    loadPulled();
    renderResults({ scroll: false });
  } catch (err) {
    showError(`Could not open run ${runId}: ${err.message}`);
  }
}

const VIEWS = {
  stock: { title: "Stock", lead: "Everything in the warehouse, read from our stock records. On hand is what was received minus what was shipped." },
  shipments: { title: "Shipments", lead: "Everything sent to partner agencies, newest first, from the shipment log." },
  agencies: { title: "Partner agencies", lead: "The pantries, shelters and programs we supply: who to call, how much they receive, and which open recalls involve them." },
  audit: { title: "History", lead: "Every stock check since Lotline was started. Open one to see its results again." },
  spellings: { title: "Spellings", lead: "Brand spellings someone on staff confirmed, like \u201cMezzeta\u201d for Mezzetta. Lotline treats them as the same brand from now on. A computer guess never goes on this list by itself." },
};

function wireNav() {
  for (const link of document.querySelectorAll(".nav-link")) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const view = link.dataset.view;
      if (view === "settings") { openSettings(); return; }
      history.replaceState(null, "", view === "inbox" ? location.pathname + location.search : `#/${view}`);
      if (view === "inbox") showInbox();
      else openView(view);
    });
  }
}

function setNavCurrent(view) {
  for (const link of document.querySelectorAll(".nav-link")) {
    link.setAttribute("aria-current", String(link.dataset.view === view));
  }
}

function showInbox() {
  if (state.selected) return selectRecall(state.selected.recall_id, { auto: false });
  $("main").querySelector(".overlay-view")?.remove();
  setNavCurrent("inbox");
  $("crumb-here").textContent = "Recalls";
}

function setRail(mode) {
  const wrap = document.querySelector(".main-wrap");
  wrap.classList.toggle("no-rail", mode === "hidden");
  const collapsed = store.get("lotline:rail-collapsed") === "1";
  wrap.classList.toggle("rail-collapsed", mode !== "hidden" && collapsed);
  $("rail-open").hidden = mode === "hidden" || !collapsed;
}

async function openView(view, q = "") {
  const meta = VIEWS[view];
  if (!meta) return showInbox();
  setNavCurrent(view);
  setRail("hidden");
  $("crumb-here").textContent = meta.title;
  for (const id of ["notice", "pipeline", "results", "error"]) $(id).hidden = true;
  $("main").querySelector(".overlay-view")?.remove();

  const body = el("div", { class: "view-body" }, el("p", { class: "muted", text: "Loading..." }));
  const panel = el("section", { class: "overlay-view", "aria-labelledby": "view-title" },
    el("h1", { class: "view-title", id: "view-title", text: meta.title }),
    el("p", { class: "view-lead", text: meta.lead }),
    body);
  $("main").append(panel);

  try {
    if (["stock", "shipments", "agencies"].includes(view)) body.replaceChildren(await browseView(view, q));
    if (view === "audit") body.replaceChildren(renderRunList(await api("/api/runs")));
    if (view === "spellings") body.replaceChildren(renderAliasList(await api("/api/aliases")));
  } catch (err) {
    body.replaceChildren(el("p", { class: "error", text: /not found/i.test(err.message)
      ? "This page needs the latest Lotline server. Restart it (Ctrl+C in its window, then python run.py) and reload."
      : `Could not load this page: ${err.message}` }));
  }
}

function emptyState(text, actionLabel = "Go to the recall inbox") {
  return el("div", { class: "view-empty" },
    el("p", { text }),
    el("button", { type: "button", class: "ghost", onclick: () => { history.replaceState(null, "", location.pathname + location.search); showInbox(); } }, actionLabel));
}

function recallFlags() {
  const flags = {};
  for (const { pack } of Object.values(state.packs)) {
    for (const v of pack.pull_list) flags[v.item_id] = { kind: "pull", recall: pack.recall_id, text: `On the ${pack.recall?.brand || pack.recall_id} pull list` };
    for (const v of pack.human_checks) flags[v.item_id] ??= { kind: "check", recall: pack.recall_id, text: `Needs a closer look (${pack.recall?.brand || pack.recall_id})` };
  }
  return flags;
}

function matchingRecalls(q) {
  const t = q.trim().toLowerCase();
  if (!t) return [];
  return state.recalls.filter((r) => [r.recall_id, r.recall_number, r.brand, r.product, r.company,
    ...(r.lines || []).flatMap((l) => [l.upc, l.lot_codes, l.product])].some((x) => (x || "").toLowerCase().includes(t)));
}

async function browseView(view, q) {
  const f = state.browse[view];
  const flags = recallFlags();
  const res = await api(view === "agencies" ? "/api/agencies" : `/api/${view}?q=${encodeURIComponent(q)}&limit=500`);
  const rows = res.rows;
  const results = el("div", { class: "browse-results" });
  const check = (key, label) => el("label", { class: "check-row" },
    el("input", { type: "checkbox", checked: f[key] || null, onchange: (e) => { f[key] = e.target.checked; draw(); } }), label);
  const select = (key, label, values) => el("label", { class: "filter-field" }, el("span", { text: label }),
    el("select", { class: "text-input", onchange: (e) => { f[key] = e.target.value; draw(); } },
      el("option", { value: "", text: "All" }), values.map((v) => el("option", { value: v, selected: f[key] === v || null, text: v }))));
  const dateField = (key, label) => el("label", { class: "filter-field" }, el("span", { text: label }),
    el("input", { type: "date", class: "text-input", value: f[key] || "", onchange: (e) => { f[key] = e.target.value; draw(); } }));
  const uniq = (key) => [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort();

  let searchBar = null;
  let filters;
  if (view === "agencies") {
    const input = el("input", { type: "search", class: "text-input browse-input", value: f.text || "", placeholder: "Agency or contact name", "aria-label": "Filter agencies",
      oninput: (e) => { f.text = e.target.value; draw(); } });
    filters = el("div", { class: "filter-bar" }, input, check("affected", "Only agencies that received recalled food"));
  } else {
    const input = el("input", { type: "search", class: "text-input browse-input", value: q, maxlength: "80",
      placeholder: view === "stock" ? "Brand, product, barcode, lot code or shelf" : "Agency, brand or product", "aria-label": `Search ${view}` });
    searchBar = el("form", { class: "browse-search", role: "search", onsubmit: (e) => { e.preventDefault(); openView(view, input.value); } },
      input, el("button", { type: "submit", class: "primary big-btn" }, "Search"),
      q ? el("button", { type: "button", class: "ghost big-btn", onclick: () => openView(view, "") }, "Clear") : null);
    filters = view === "stock"
      ? el("div", { class: "filter-bar" }, select("shelf", "Shelf", uniq("location")), check("onHand", "Only items still on the shelf"), check("recalled", "Only items on a recall list"))
      : el("div", { class: "filter-bar" }, select("agency", "Agency", uniq("agency_name")), dateField("from", "From"), dateField("to", "To"), check("recalled", "Only recalled products"));
  }

  const agencyRecalls = {};
  for (const { pack } of Object.values(state.packs)) {
    for (const n of pack.agency_notices) (agencyRecalls[n.agency_id] ||= []).push({ rid: pack.recall_id, brand: pack.recall?.brand || pack.recall_id });
  }

  const flagCell = (id) => {
    const fl = flags[id];
    return fl ? el("button", { type: "button", class: `reason-badge rb-${fl.kind === "pull" ? "no" : "warn"} flag-btn`, onclick: () => selectRecall(fl.recall, { auto: false }) }, icon(fl.kind === "pull" ? "no" : "warn"), fl.text) : null;
  };

  function draw() {
    let list = rows;
    if (view === "stock") {
      if (f.shelf) list = list.filter((r) => r.location === f.shelf);
      if (f.onHand) list = list.filter((r) => (r.quantity_on_hand || 0) > 0);
      if (f.recalled) list = list.filter((r) => flags[r.item_id]);
    } else if (view === "shipments") {
      if (f.agency) list = list.filter((r) => r.agency_name === f.agency);
      if (f.from) list = list.filter((r) => (r.ship_date || "") >= f.from);
      if (f.to) list = list.filter((r) => (r.ship_date || "") <= f.to);
      if (f.recalled) list = list.filter((r) => flags[r.item_id]);
    } else {
      const t = (f.text || "").trim().toLowerCase();
      if (t) list = list.filter((r) => [r.name, r.contact_name, r.contact_email].some((x) => (x || "").toLowerCase().includes(t)));
      if (f.affected) list = list.filter((r) => agencyRecalls[r.agency_id]);
    }
    const shown = `${list.length} of ${rows.length} ${rows.length === 1 ? "record" : "records"}`;
    const where = res.backend === "zetaris" ? "through Zetaris" : "from the local data files";
    const summary = el("p", { class: "browse-source", text: `${shown}${q ? ` matching “${q}”` : ""}, read ${where}.` });
    let tableEl;
    if (!list.length) tableEl = el("p", { class: "review-empty", text: "Nothing matches these filters." });
    else if (view === "stock") {
      tableEl = el("table", { class: "items browse-table" },
        el("thead", {}, el("tr", {}, ["Shelf", "Product", "Barcode", "Lot", "Best by", "On hand", "Received from", ""].map((h, i) => el("th", { class: i === 5 ? "num" : null, text: h })))),
        el("tbody", {}, list.map((r) => el("tr", {},
          el("td", {}, el("span", { class: "loc", text: r.location || "?" })),
          el("td", {}, r.brand ? el("b", { text: `${r.brand} ` }) : el("span", { class: "muted", text: "(no brand) " }), r.product, el("span", { class: "id sub-id", text: r.source_ref || r.item_id })),
          el("td", {}, r.upc ? el("span", { class: "lot", text: r.upc }) : el("span", { class: "muted", text: "none" })),
          el("td", {}, lotChip(r.lot_code)),
          el("td", { text: r.best_by ? fmtDate(r.best_by) : "" }),
          el("td", { class: "num", text: r.quantity_on_hand == null ? "?" : `${fmtNum(r.quantity_on_hand)} ${r.unit || ""}` }),
          el("td", { class: "muted", text: r.source || "" }),
          el("td", {}, flagCell(r.item_id))))));
    } else if (view === "shipments") {
      tableEl = el("table", { class: "items browse-table" },
        el("thead", {}, el("tr", {}, ["Date", "Agency", "Product", "Lot", "Quantity", ""].map((h, i) => el("th", { class: i === 4 ? "num" : null, text: h })))),
        el("tbody", {}, list.map((r) => el("tr", {},
          el("td", { text: fmtDate(r.ship_date) }),
          el("td", {}, el("b", { text: r.agency_name || r.agency_id })),
          el("td", {}, r.product || r.item_id, el("span", { class: "id sub-id", text: r.source_ref || r.ship_id })),
          el("td", {}, lotChip(r.lot_code)),
          el("td", { class: "num", text: `${fmtNum(r.quantity || 0)} ${r.unit || ""}` }),
          el("td", {}, flagCell(r.item_id))))));
    } else {
      tableEl = el("table", { class: "items browse-table" },
        el("thead", {}, el("tr", {}, ["Agency", "Contact", "Shipments", "Last shipment", "Open recalls involving them"].map((h, i) => el("th", { class: i === 2 ? "num" : null, text: h })))),
        el("tbody", {}, list.map((r) => el("tr", {},
          el("td", {}, el("b", { text: r.name }), String(r.active).toLowerCase() === "false" ? el("span", { class: "muted", text: " (inactive)" }) : null),
          el("td", {}, r.contact_name || "", el("span", { class: "sub-id muted", text: [r.contact_email, r.contact_phone].filter(Boolean).join(" · ") })),
          el("td", { class: "num", text: String(r.shipments) }),
          el("td", { text: r.last_shipment ? fmtDate(r.last_shipment) : "none" }),
          el("td", {}, (agencyRecalls[r.agency_id] || []).map((x) => el("button", { type: "button", class: "reason-badge rb-no flag-btn", onclick: () => selectRecall(x.rid, { auto: false }) }, icon("no"), x.brand)))))));
    }
    results.replaceChildren(summary, el("div", { class: "table-wrap" }, tableEl));
  }

  const recalls = view === "stock" ? matchingRecalls(q) : [];
  const recallBlock = recalls.length ? el("div", { class: "browse-recalls" },
    el("h3", { text: `Recalls matching “${q}”` }),
    el("ul", {}, recalls.map((r) => el("li", {}, el("button", { type: "button", class: "text-link", onclick: () => selectRecall(r.recall_id) },
      `${r.brand}: ${r.product}`), el("span", { class: "muted", text: ` · ${r.reason}` }))))) : null;
  const note = Object.keys(state.packs).length ? null
    : el("p", { class: "browse-note", text: "Recall flags appear here for every recall you have checked." });
  draw();
  return el("div", {}, view === "agencies" ? null : sourcesPanel(res.backend), searchBar, filters, recallBlock, results, note);
}

const SYSTEMS = [
  { name: "Warehouse", what: "What we received and where it sits", tables: { inventory: "Stock" } },
  { name: "Distribution", what: "What went out, and to whom", tables: { distributions: "Shipments", agencies: "Partner agencies" } },
  { name: "Recall notices", what: "What the FDA recalled", tables: { recalls: "Notices", recall_products: "Recalled product lines" } },
];

function sourcesPanel(backend) {
  const byTable = Object.fromEntries((state.config?.sources || []).map((s) => [s.table, s]));
  const zetaris = backend === "zetaris";
  return el("section", { class: "sources-panel", "aria-label": "Where our records come from" },
    el("div", { class: "sp-head" },
      el("h3", { text: "Where our records come from" }),
      el("span", { class: "sp-via", text: zetaris ? "Read live through Zetaris" : "Read from the local data files" })),
    el("div", { class: "sp-systems" }, SYSTEMS.map((sys) => el("div", { class: "sp-system" },
      el("p", { class: "sp-name", text: sys.name }),
      el("p", { class: "sp-what", text: sys.what }),
      el("ul", {}, Object.entries(sys.tables).map(([table, label]) => {
        const src = byTable[table];
        return el("li", {},
          el("span", { class: "sp-label", text: label }),
          el("span", { class: "sp-rows", text: src ? `${src.rows.toLocaleString()} ${src.rows === 1 ? "row" : "rows"}` : "not found" }),
          src?.location ? el("span", { class: "sp-loc", text: src.location }) : null);
      }))))),
    el("p", { class: "sp-note", text: "Three separate systems that don't share IDs or spellings. Lotline's agents map and join them for every check." }));
}

function renderRunList(runs) {
  if (!runs.length) return emptyState("No checks yet since Lotline was started. History starts fresh whenever the server restarts.");
  return el("table", { class: "items" },
    el("thead", {}, el("tr", {}, ["Recall", "Result", "Took", ""].map((h) => el("th", { text: h })))),
    el("tbody", {}, runs.map((r) => {
      const recall = state.recalls.find((x) => x.recall_id === r.recall_id);
      const result = r.status === "success" ? `${r.pull_count ?? 0} to pull` : r.status === "running" ? "Still checking" : "Didn't finish";
      return el("tr", {},
        el("td", {}, recall ? el("b", { text: `${recall.brand} ` }) : null, recall ? recall.product : r.recall_id || "",
          el("span", { class: "id sub-id admin-only", text: `${r.run_id} · ${r.mode || ""}` })),
        el("td", { text: result }),
        el("td", { class: "num", text: r.execution_time_seconds ? `${r.execution_time_seconds}s` : "" }),
        el("td", { class: "num" }, r.status === "success"
          ? el("button", { type: "button", class: "ghost", onclick: () => { showInbox(); openRun(r.run_id); } }, "Open")
          : null));
    })));
}

function renderAliasList(aliases) {
  if (!aliases.length) return emptyState("No spellings confirmed yet. When a volunteer's spelling is close to a recalled brand (for example “Mezzeta” for Mezzetta), you can confirm it from the “Needs a closer look” list.");
  return el("table", { class: "items" },
    el("thead", {}, el("tr", {}, ["Brand on the recall", "Spelling we accept", "Confirmed", ""].map((h) => el("th", { text: h })))),
    el("tbody", {}, aliases.map((a) => el("tr", {},
      el("td", {}, el("b", { text: a.brand })),
      el("td", {}, el("span", { class: "lot", text: a.alias })),
      el("td", { class: "id", text: (a.approved_at || "").slice(0, 16).replace("T", " ") }),
      el("td", { class: "num" }, el("button", { type: "button", class: "ghost", onclick: async () => {
        try {
          await api(`/api/aliases?brand=${encodeURIComponent(a.brand)}&alias=${encodeURIComponent(a.alias)}`, { method: "DELETE" });
          toast(`Removed “${a.alias}”`);
          openView("spellings");
        } catch (err) { toast(`Couldn't remove it: ${err.message}`); }
      } }, "Remove"))))));
}

const myRole = () => (store.get("lotline:role") || "").trim() || "Food Safety Coordinator";
const myFoodBank = () => (store.get("lotline:foodbank") || "").trim() || "Your food bank";

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return parts.length ? (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() : "?";
}

function renderUser() {
  const name = me();
  const role = myRole();
  $("user-name").textContent = name || "Set up your profile";
  $("user-role").textContent = name ? role : "";
  $("user-btn").classList.toggle("unset", !name);
  for (const id of ["user-avatar", "account-avatar"]) $(id).textContent = initials(name);
  $("account-name").textContent = name || "Set up your profile";
  $("account-sub").textContent = `${role} · ${myFoodBank()}`;
}

function openAccount() {
  renderUser();
  $("coord-input").value = me();
  $("role-input").value = store.get("lotline:role") || "";
  $("foodbank-input").value = store.get("lotline:foodbank") || "";
  $("account").showModal();
}

function openSettings() {
  renderUser();
  $("settings").showModal();
}

function confirmDialog({ title, body, ok = "Confirm" }) {
  const dlg = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-body").replaceChildren(...body);
  $("confirm-ok").textContent = ok;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

function noticeLine(v) {
  const recall = state.recalls.find((r) => r.recall_id === state.pack.recall_id);
  return (recall?.lines || []).find((l) => String(l.line_id) === String(v.line_id));
}

function allCodes(text) {
  if (!text) return null;
  if (text.toUpperCase() === "ALL") return "all lots";
  return text.split(";").map((x) => plain(x.trim())).filter(Boolean).join("; ");
}

function renderReviewQueue() {
  const items = state.pack.human_checks;
  $("review-queue").replaceChildren(...(items.length ? items.map(reviewCard)
    : [el("p", { class: "review-empty", text: "Nothing needs a closer look. Every item was settled by its lot code, date or barcode." })]));
}

const PLAIN_VERDICT = {
  CONFIRMED_MATCH: "Part of the recall",
  PROBABLE_MATCH: "Probably part of the recall",
  HUMAN_CHECK: "Needs a person to check",
  NEAR_MISS: "Close, but not part of the recall",
  NO_MATCH: "Not part of the recall",
};

function reviewCard(v) {
  const m = v.brand_match || {};
  const line = noticeLine(v);
  const spelling = ["fuzzy", "alias"].includes(m.method);
  const brand = v.line_brand || state.pack.recall?.brand || "";
  const fieldRow = (label, value, missText = "not written down") => [el("dt", { text: label }), value ? el("dd", {}, value) : el("dd", { class: "miss", text: missText })];
  const here = v.quantity ? `${fmtNum(v.quantity)} ${v.unit || ""} on the shelf` : "none left on the shelf, all sent out";
  const open = state.reading === v.item_id;
  return el("article", { class: "xcard", "aria-label": `${v.product}, shelf ${v.location}` },
    el("div", { class: "xc-head" },
      el("span", { class: "xc-tag", text: "Check this package" }),
      el("span", { class: "xc-big", text: `Shelf ${v.location || "?"}` }),
      el("span", { class: "xc-where", text: here })),
    el("div", { class: "xc-compare" },
      el("div", { class: "xc-side" },
        el("div", { class: "xc-label", text: "What a volunteer wrote down" }),
        el("div", { class: "xc-raw" }, el("div", { text: m.recorded_as || v.brand || "(no brand written down)" }), el("div", { text: v.product })),
        el("dl", { class: "xc-fields" }, ...fieldRow("Barcode", v.upc), ...fieldRow("Lot", v.lot_code), ...fieldRow("Best by", v.best_by && el("span", { class: "plain-date", text: fmtDate(v.best_by) })))),
      el("div", { class: "xc-side" },
        el("div", { class: "xc-label", text: "What the recall says" }),
        el("div", { class: "xc-notice-name", text: line ? `${line.brand} ${line.product}${line.size ? `, ${line.size}` : ""}` : (v.line_label || "") }),
        el("dl", { class: "xc-fields" },
          ...fieldRow("Barcode", line?.upc && codeList(line.upc), "not listed"), ...fieldRow("Lots", line?.lot_codes && codeList(line.lot_codes), "none listed"),
          ...fieldRow("Best by", line?.best_by && codeList(line.best_by), "none listed")))),
    el("div", { class: "xc-look" },
      reasonBadges(v),
      el("p", {}, el("b", { text: "What to look for: " }), plain(v.rationale)),
      spelling ? el("p", { text: m.method === "alias"
        ? `The computer guessed “${m.recorded_as}” could mean ${brand}. A guess isn't proof, so this stays on the shelf until someone checks.`
        : `“${m.recorded_as}” is spelled close to ${brand}, but a similar spelling isn't proof and there's no barcode to settle it.` }) : null),
    el("div", { class: "xc-actions" },
      spelling ? el("button", { type: "button", class: "primary big-btn", onclick: () => confirmApprove(v) }, `Same brand: pull it if the codes match`) : null,
      el("button", { type: "button", class: spelling ? "ghost big-btn" : "primary big-btn", "aria-expanded": String(open),
        onclick: () => { state.reading = open ? null : v.item_id; renderReviewQueue(); } }, open ? "Close" : "Type what the package says"),
      el("a", { class: "ghost big-btn", href: escalateHref(v) }, "Ask a supervisor"),
      el("button", { type: "button", class: "text-link", onclick: () => openDrawer(v) }, "Show the evidence")),
    open ? el("div", { class: "xc-form" }, readingForm(v)) : null);
}

async function confirmApprove(v) {
  const brand = v.line_brand || state.pack.recall?.brand;
  const alias = v.brand_match.recorded_as;
  const ok = await confirmDialog({
    title: `Is “${alias}” the same brand as ${brand}?`,
    ok: "Yes, same brand: check again",
    body: [
      el("p", {}, "Only confirm this if you're sure the volunteer meant ", el("b", { text: brand }), " when they wrote ", el("span", { class: "lot", text: alias }), "."),
      el("p", { text: "Lotline will remember this spelling and check the recall again. Each item is still judged on its lot code, date and barcode, so confirming a spelling doesn't pull anything by itself." }),
      el("p", { text: "You can undo this later under Spellings." }),
    ],
  });
  if (!ok) return;
  try {
    await api("/api/aliases", { method: "POST", body: JSON.stringify({ brand, alias, run_id: state.runId }) });
    toast(`Saved: “${alias}” means ${brand}. Checking again.`);
    startRun();
  } catch (err) {
    toast(`Couldn't save the spelling: ${err.message}`);
  }
}

function escalateHref(v) {
  const p = state.pack;
  const r = p.recall || {};
  const subject = `Recall review needed: ${v.item_id} (${p.recall_id})`;
  const body = [
    `Recall: ${p.recall_id} ${r.brand || ""} ${r.product || ""}`.trim(),
    `Item: ${v.item_id} on shelf ${v.location || "?"}, ${fmtNum(v.quantity || 0)} ${v.unit || ""} on hand`,
    `Entered as: ${v.brand_match?.recorded_as || v.brand || "(no brand)"} / ${v.product}`,
    `UPC ${v.upc || "not recorded"} · lot ${v.lot_code || "not recorded"} · best by ${v.best_by ? fmtDate(v.best_by) : "not recorded"}`,
    `Notice line: ${v.line_label || ""}`,
    `Why it is uncertain: ${plain(v.rationale)}`,
    `Source record: ${v.source_ref || ""}`,
    `Lotline check: ${p.run_id}`,
    "",
    "Lotline has not pulled or cleared this item. It is waiting for a decision.",
  ].join("\n");
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

const fmtTime = (ts) => (ts ? `${ts.slice(0, 10)} ${ts.slice(11, 19)} UTC` : "");

function openDrawer(v) {
  const p = state.pack;
  const list = p.pull_list.includes(v) ? "pull" : p.human_checks.includes(v) ? "check" : "clear";
  const kicker = { pull: "Why it's on the pull list", check: "Why it needs a closer look", clear: "Why it's not part of the recall" }[list];
  const find = (agent, ev) => state.trace.find((e) => e.agent_name === agent && e.event === ev);
  const scoutStart = find("SourceScout", "start");
  const matcher = find("Matcher", "done");
  const critic = [...state.trace].reverse().find((e) => e.agent_name === "Critic" && e.event === "done");
  const tracer = find("Tracer", "done");
  const recheck = (v.rule_checks || []).find((c) => c.rule === "source_recheck");
  const checks = (v.rule_checks || []).filter((c) => c.rule !== "source_recheck");
  const ships = p.distributed_items.filter((d) => d.item_id === v.item_id);
  const m = v.brand_match || {};
  const pulled = state.caseInfo?.case.pulled?.[v.item_id];
  const kv = (pairs) => el("dl", { class: "kv" }, pairs.flatMap(([k, val]) => [el("dt", { text: k }), el("dd", { text: val == null || val === "" ? "not written down" : String(val) })]));
  const step = (cls, title, who, ts, ...content) => el("li", { class: cls },
    el("div", { class: "trail-head" }, el("h4", { text: title }), el("span", { class: "trail-who", text: who }), ts ? el("time", { text: fmtTime(ts) }) : null),
    ...content);
  const brandText = m.method === "exact" ? "same as the recall" : METHOD_TEXT[m.method] || "unknown";
  const word = { pass: "matches", fail: "differs", unknown: "unclear" };

  const trail = [
    step("t-data", "Our stock record", "Inventory", scoutStart?.timestamp,
      el("p", { text: `From ${v.source_ref || "the inventory list"}.` }),
      el("p", { class: "admin-only", text: scoutStart?.input_summary || "" }),
      kv([["Brand written as", m.recorded_as || v.brand], ["Product written as", v.product], ["Barcode", v.upc], ["Lot", v.lot_code], ["Best by", v.best_by && fmtDate(v.best_by)],
        ["Stock", v.quantity_received != null ? `${fmtNum(v.quantity_received)} received, ${fmtNum(v.shipped || 0)} sent out, ${fmtNum(v.quantity)} ${v.unit || ""} left` : `${fmtNum(v.quantity || 0)} ${v.unit || ""}`],
        ["Shelf", v.location], ["Received", [v.received_date, v.source_type].filter(Boolean).join(" · ")], ["Item number", v.item_id]])),
    step("t-rules", "Compared with the recall", "Lotline", matcher?.timestamp,
      kv([["Recalled product", v.line_label], ["Brand", brandText]]),
      el("ul", {}, checks.map((c) => el("li", {}, el("span", { class: `rule rule-${checkWord(c)}`, text: word[checkWord(c)] }), el("span", { text: c.note })))),
      el("p", {}, el("b", { text: `${PLAIN_VERDICT[v.verdict] || v.verdict}. ` }), plain(v.rationale))),
    p.mode === "live" ? step("t-model", "AI review", "Can flag items, never decides", null,
      el("p", { text: v.model_note || "The AI didn't flag anything on this item." })) : null,
    step("t-data", "Second look before pulling", "Lotline", recheck ? critic?.timestamp : null,
      el("p", { text: recheck ? recheck.note : "Not needed. Lotline re-reads the record only before telling you to pull something." })),
    v.reading ? step("t-human", "Package read", v.reading.by, v.reading.at,
      kv(Object.entries(v.reading).filter(([k]) => !["by", "at"].includes(k)).map(([k, val]) => [k.replace("_", " "), val])),
      el("p", { text: `Before this, Lotline's answer was: ${(PLAIN_VERDICT[v.previous_verdict] || "").toLowerCase()}.` }))
      : list === "check" ? step("t-human", "Waiting for someone to read the package", "You", null, el("p", { text: "Nobody has read it yet." })) : null,
    pulled ? step("t-human", "Pulled from the shelf", pulled.by || "a volunteer", pulled.at, el("p", { text: "Marked as done." })) : null,
    step("t-ship", "Where it went", "Shipment log", tracer?.timestamp,
      ships.length ? el("ul", {}, ships.map((d) => el("li", {}, el("span", { class: "id", text: d.distributed_date }),
        el("span", { text: `${fmtNum(d.quantity)} ${d.unit} to ${d.agency_name}` }))))
        : el("p", { text: "None of this item was sent out." })),
  ].filter(Boolean);

  $("drawer-kicker").textContent = kicker;
  $("drawer-title").textContent = `${v.product} · shelf ${v.location || "?"}`;
  $("drawer-body").replaceChildren(el("ol", { class: "trail" }, trail));
  if (!$("drawer").open) $("drawer").showModal();
}

function wireRailResizer() {
  const wrap = document.querySelector(".main-wrap");
  const handle = $("rail-resizer");
  const setWidth = (w) => {
    const width = Math.round(Math.min(560, Math.max(240, w)));
    wrap.style.setProperty("--rail-w", `${width}px`);
    handle.setAttribute("aria-valuenow", String(width));
    return width;
  };
  const saved = Number(store.get("lotline:rail-w"));
  if (saved) setWidth(saved);
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const left = wrap.getBoundingClientRect().left;
    const move = (ev) => setWidth(ev.clientX - left);
    const up = (ev) => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      document.body.classList.remove("resizing");
      store.set("lotline:rail-w", String(setWidth(ev.clientX - left)));
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
  handle.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    const now = $("rail").getBoundingClientRect().width;
    store.set("lotline:rail-w", String(setWidth(now + (e.key === "ArrowRight" ? 16 : -16))));
  });
}

boot();
