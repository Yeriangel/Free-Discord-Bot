"use strict";

/* ===== session token (passed once in the URL fragment, never sent to the server in a URL) ===== */
const TOKEN = (() => {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  if (fromHash) {
    sessionStorage.setItem("botd-token", fromHash);
    history.replaceState(null, "", location.pathname);
    return fromHash;
  }
  return sessionStorage.getItem("botd-token") || "";
})();

const STATE_LABELS = {
  stopped: "כבוי", connecting: "מתחבר…", connected: "מחובר", reconnecting: "מתחבר מחדש…",
  stopping: "נעצר…", error: "שגיאה", offline: "השירות לא זמין",
};

const GENERAL_NAV = [
  { page: "dashboard", icon: "◉", label: "לוח בקרה" },
  { page: "connection", icon: "🔑", label: "חיבור והגדרות" },
  { page: "profile", icon: "🖼", label: "פרופיל הבוט", sub: "Bot Personalizer" },
  { page: "permissions", icon: "🛡", label: "הרשאות והזמנה" },
  { page: "updates", icon: "⟳", label: "עדכונים" },
  { page: "customize", icon: "🎨", label: "התאמה אישית", sub: "Customize" },
];
const CAT_ICONS = { essentials: "⭐", server_management: "🛠", automations: "⚡", utilities: "🧰", social: "📣", games: "🎮", ai: "✦" };
const NONE_IMG = "logo.png";  // the app logo stands in wherever the bot has no picture yet

const S = {
  page: "dashboard",
  status: { state: "offline" },
  settings: null,
  catalog: null,
  guilds: [],
  guildId: localStorage.getItem("botd-guild") || "",
  guild: null,
  logs: [],
  logFilter: "ALL",
  profile: null,
  avatarDraft: null,
  bannerDraft: null,
  presenceDraft: null,
  invite: null,
  permSel: null,
  updates: null,
  dirty: false,
  autos: null,
  autoOpen: null,
};

/* ===== helpers ===== */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mod = (key) => S.catalog && S.catalog.modules.find((m) => m.key === key);
const permLabel = (k) => ((S.catalog && S.catalog.permissions.find((p) => p.key === k)) || { label: k }).label;

class ApiError extends Error {
  constructor(message, status, extra) { super(message); this.status = status; this.extra = extra || {}; }
}

/* Applies an installed update without closing the app: the service reloads with the new code (the bot reconnects
   for a few seconds), then this page reloads itself with the new screens. */
async function applyLiveUpdate(tag) {
  const box = document.createElement("div");
  box.className = "modal live-update";
  box.innerHTML = `<div class="modal-card"><h2></h2><p class="muted"></p><div class="live-update-bar"><span></span></div></div>`;
  box.querySelector("h2").textContent = t("מחיל את העדכון…");
  box.querySelector("p").textContent = t("הבוט מתנתק לכמה שניות וחוזר. אין צורך לסגור את האפליקציה.");
  document.body.appendChild(box);
  let before = "";
  try { before = (await api("GET", "/ping")).build || ""; } catch { /* the service may already be reloading */ }
  await api("POST", "/app/reload");
  const want = String(tag || "").replace(/^v/i, "");
  const started = Date.now();
  while (Date.now() - started < 60000) {
    await new Promise(r => setTimeout(r, 700));
    try {
      const ping = await api("GET", "/ping");
      if (ping.build && ping.build !== before && (!want || ping.version === want)) {
        sessionStorage.setItem("liveUpdated", ping.version);
        location.reload();
        return;
      }
    } catch { /* still restarting */ }
  }
  box.querySelector("h2").textContent = t("העדכון הותקן");
  box.querySelector("p").textContent = t("השירות לא חזר בזמן. סגור את האפליקציה ופתח אותה שוב.");
}

async function api(method, path, body) {
  let resp;
  try {
    resp = await fetch("/api" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-Session-Token": TOKEN },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("אין חיבור לשירות הבוט", 0);
  }
  let data = null;
  try { data = await resp.json(); } catch { /* empty */ }
  if (!resp.ok) {
    const d = data && data.detail;
    const msg = (d && (d.message || (typeof d === "string" ? d : ""))) || t("שגיאה {status}", { status: resp.status });
    throw new ApiError(msg, resp.status, d && typeof d === "object" ? d : {});
  }
  return data;
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

function confirmDialog(title, body, okLabel = "אישור", danger = true, checkLabel = "") {
  return new Promise((resolve) => {
    const modal = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").textContent = body;
    const checkRow = $("#modal-check-row"), check = $("#modal-check");
    check.checked = false;
    checkRow.classList.toggle("hidden", !checkLabel);
    $("#modal-check-label").textContent = checkLabel;
    const ok = $("#modal-ok");
    ok.textContent = okLabel;
    ok.className = "btn " + (danger ? "btn-danger" : "btn-primary");
    modal.classList.remove("hidden");
    const done = (v) => {
      modal.classList.add("hidden");
      ok.onclick = null; $("#modal-cancel").onclick = null; modal.onclick = null;
      resolve(checkLabel ? { ok: v, checked: check.checked } : v);
    };
    ok.onclick = () => done(true);
    $("#modal-cancel").onclick = () => done(false);
    modal.onclick = (e) => { if (e.target === modal) done(false); };
  });
}

function fmtRetry(seconds) {
  if (!seconds) return "";
  const s = Math.ceil(seconds);
  if (s < 60) return t("{n} שניות", { n: s });
  const m = Math.ceil(s / 60);
  return m < 60 ? t("{n} דקות", { n: m }) : t("{h} שעות ו-{m} דקות", { h: Math.floor(m / 60), m: m % 60 });
}

function errMessage(e) {
  if (e instanceof ApiError && e.status === 429 && e.extra.retry_after) {
    return t("{msg} אפשר לנסות שוב בעוד {time}.", { msg: e.message, time: fmtRetry(e.extra.retry_after) });
  }
  return e.message || String(e);
}

async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); }
  catch (e) { toast(errMessage(e), "error"); }
  finally { if (btn) btn.disabled = false; }
}

/* ===== translations (UI language; source strings are Hebrew) ===== */
let I18N = { lang: "he", dir: "rtl", strings: {}, patterns: [], languages: [] };
const HEB_RE = /[֐-׿]/;
const N_ = (s) => s;

function t(key, vars) {
  let s = (I18N.lang !== "he" && I18N.strings[key]) || key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return s;
}

function buildPatterns(strings) {
  const esc = (p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return Object.keys(strings)
    .filter((k) => /\{\w+\}/.test(k) && k.replace(/\{\w+\}/g, "").trim().length > 1)
    .map((k) => {
      const names = [];
      const body = k.split(/(\{\w+\})/).map((part) => {
        const m = part.match(/^\{(\w+)\}$/);
        if (m) { names.push(m[1]); return "([\\s\\S]+?)"; }
        return esc(part);
      }).join("");
      return { re: new RegExp("^" + body + "$"), names, out: strings[k], len: k.length };
    })
    .sort((a, b) => b.len - a.len);
}

function trText(s) {
  const core = s.trim();
  if (!core || !HEB_RE.test(core)) return s;
  let out = I18N.strings[core];
  if (!out) {
    for (const p of I18N.patterns) {
      const m = core.match(p.re);
      if (m) { out = p.out.replace(/\{(\w+)\}/g, (x, k) => { const i = p.names.indexOf(k); return i >= 0 ? m[i + 1] : x; }); break; }
    }
  }
  return out ? s.replace(core, out) : s;
}

const SKIP_SEL = ".no-translate, script, style, textarea, .logs";
function translateTextNode(node) {
  const parent = node.parentElement;
  if (!parent || parent.closest(SKIP_SEL)) return;
  const next = trText(node.nodeValue);
  if (next !== node.nodeValue) node.nodeValue = next;
}

function translateTree(root) {
  if (I18N.lang === "he" || !root) return;
  if (root.nodeType === 3) { translateTextNode(root); return; }
  if (root.nodeType !== 1 || root.closest(".no-translate, script, style, .logs")) return;  // text inside textareas is skipped per node
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(translateTextNode);
  const els = [root, ...root.querySelectorAll("[placeholder], [title]")];
  for (const el of els) {
    // text inside a textarea is the user's own; its placeholder and tooltip are app text and do get translated
    if (!el.getAttribute || el.closest(".no-translate, script, style, .logs")) continue;
    for (const attr of ["placeholder", "title"]) {
      const v = el.getAttribute(attr);
      if (v && HEB_RE.test(v)) { const nv = trText(v); if (nv !== v) el.setAttribute(attr, nv); }
    }
  }
}

const i18nObserver = new MutationObserver((muts) => {
  if (I18N.lang === "he") return;
  for (const m of muts) {
    if (m.type === "characterData") translateTextNode(m.target);
    else if (m.type === "attributes") translateTree(m.target);
    else m.addedNodes.forEach((n) => translateTree(n));
  }
});

async function loadI18n() {
  const d = await api("GET", "/i18n");
  I18N = { ...d, patterns: buildPatterns(d.strings) };
  document.documentElement.lang = d.lang;
  document.documentElement.dir = d.dir;
  renderLangSelect();
  if (d.lang !== "he") {
    translateTree(document.body);
    i18nObserver.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title"] });
  }
}

function langOptions(selected) {
  return (I18N.languages || []).map((l) => `<option value="${l.code}" ${l.code === selected ? "selected" : ""}>${esc(l.name)}</option>`).join("");
}

function renderLangSelect() {
  const sel = $("#ui-lang");
  if (sel) sel.innerHTML = langOptions(I18N.lang);
}

async function setUiLang(code) {
  try {
    await api("PUT", "/settings", { ui_lang: code });
    location.reload();
  } catch (e) { toast(errMessage(e), "error"); }
}

function guildLangSelect() {
  if (!S.guild) return "";
  return `<label class="row guild-lang no-translate-host"><span class="label">${esc(t("שפת הבוט בשרת הזה"))}</span>
    <select id="guild-lang" class="no-translate" style="width:auto">${langOptions(S.guild.language || "he")}</select></label>`;
}

/* ===== live events ===== */
function connectWS() {
  const updated = sessionStorage.getItem("liveUpdated");
  if (updated) { sessionStorage.removeItem("liveUpdated"); setTimeout(() => toast(t("עודכן לגרסה {tag}", { tag: updated }), "ok"), 400); }
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => ws.send(JSON.stringify({ token: TOKEN }));
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "backlog") { S.logs = msg.logs; renderLogs(); }
    else if (msg.type === "log") { S.logs.push(msg); if (S.logs.length > 1000) S.logs.shift(); appendLog(msg); }
    else if (msg.type === "status") { onStatus(msg.status); }
  };
  ws.onclose = () => { onStatus({ state: "offline" }); setTimeout(connectWS, 2000); };
}

function onStatus(status) {
  const prev = S.status.state;
  S.status = status;
  renderStatus();
  if (S.page === "dashboard") renderDashboardStats();
  if (status.state === "connected" && prev !== "connected") refreshGuilds();
  else if (status.state !== prev && prev === "connected" && !S.dirty) loadGuild();
  if (status.state === "error" && prev !== "error" && status.error) toast(status.error, "error");
}

function renderStatus() {
  const st = S.status.state || "offline";
  $("#status-pill").className = "pill pill-" + st;
  $("#status-text").textContent = t(STATE_LABELS[st] || st);
  const btn = $("#power-btn");
  const running = ["connecting", "connected", "reconnecting"].includes(st);
  btn.textContent = running ? "כבה בוט" : "הפעל בוט";
  btn.className = "btn " + (running ? "btn-danger" : "btn-primary");
  btn.disabled = st === "stopping" || st === "offline";
  if (S.status.user) {
    $("#brand-avatar").src = S.status.user.has_avatar === false ? "logo.svg" : (S.status.user.avatar || "logo.svg");
    $("#brand-name").textContent = S.status.user.name;
  }
}

/* ===== data loading ===== */
async function refreshGuilds() {
  try { S.guilds = await api("GET", "/guilds"); } catch (e) { toast(errMessage(e), "error"); return; }
  if (!S.guilds.find((g) => g.id === S.guildId)) S.guildId = S.guilds[0] ? S.guilds[0].id : "";
  renderGuildSelect();
  await loadGuild();
}

async function loadGuild() {
  S.guild = null;
  if (S.guildId) {
    localStorage.setItem("botd-guild", S.guildId);
    try { S.guild = await api("GET", "/guilds/" + S.guildId); } catch (e) { toast(errMessage(e), "error"); }
  }
  if (S.page.startsWith("cat:") || S.page.startsWith("mod:")) render();
}

function renderGuildSelect() {
  const sel = $("#guild-select");
  const add = `<option value="__add">➕ ${t("הוסף שרת חדש…")}</option>`;
  if (!S.guilds.length) {
    sel.innerHTML = `<option value="">${t("אין שרתים עדיין")}</option>${add}`;
    return;
  }
  sel.classList.add("no-translate");
  sel.innerHTML = S.guilds.map((g) =>
    `<option value="${esc(g.id)}" ${g.id === S.guildId ? "selected" : ""}>${esc(g.name)}${g.present === false ? " " + t("(הבוט לא בשרת)") : ""}</option>`
  ).join("") + add;
}

/* ===== navigation ===== */
function navItems() {
  const cats = S.catalog ? S.catalog.categories.map((c) => ({ page: "cat:" + c.key, icon: CAT_ICONS[c.key] || "•", label: c.label, sub: c.en })) : [];
  return [{ group: "כללי" }, ...GENERAL_NAV, { group: "מודולים" }, ...cats];
}

function currentNavPage() {
  if (S.page.startsWith("mod:")) { const m = mod(S.page.slice(4)); return m ? "cat:" + m.category : S.page; }
  return S.page;
}

function renderNav() {
  const active = currentNavPage();
  $("#nav").innerHTML = navItems().map((n) => n.group
    ? `<div class="nav-group">${esc(n.group)}</div>`
    : `<button class="nav-item ${active === n.page ? "active" : ""}" data-nav="${n.page}">
         <span class="icon">${n.icon}</span>
         <span>${esc(n.label)}${n.sub ? `<span class="sub">${esc(n.sub)}</span>` : ""}</span>
       </button>`).join("");
}

const SINGLE_MODULE_CATS = { automations: "automations" };
const NAV_HISTORY = { back: [], forward: [] };
async function go(page, fromHistory = false) {
  if (page.startsWith("cat:") && SINGLE_MODULE_CATS[page.slice(4)]) page = "mod:" + SINGLE_MODULE_CATS[page.slice(4)];
  if (S.dirty && page !== S.page) {
    if (!(await confirmDialog("יש שינויים שלא נשמרו", "לצאת מהדף בלי לשמור? השינויים יאבדו.", "צא בלי לשמור"))) return;
  }
  if (!fromHistory && page !== S.page) {
    NAV_HISTORY.back.push(S.page);
    if (NAV_HISTORY.back.length > 100) NAV_HISTORY.back.shift();
    NAV_HISTORY.forward = [];
  }
  setDirty(false);
  S.page = page;
  renderNav();
  render();
  $("#main").scrollTop = 0;
}

function setDirty(v) {
  S.dirty = v;
  $("#savebar").classList.toggle("hidden", !v);
}

function pageTitle() {
  if (S.page.startsWith("mod:")) { const m = mod(S.page.slice(4)); return m ? m.label : ""; }
  const item = navItems().find((n) => n.page === S.page);
  return item ? item.label : "";
}

function render() {
  $("#page-title").textContent = pageTitle();
  const main = $("#main");
  const p = S.page;
  if (p === "dashboard") { main.innerHTML = viewDashboard(); renderDashboardStats(); afterConsoleRender(); }
  else if (p === "connection") main.innerHTML = viewConnection();
  else if (p === "profile") {
    main.innerHTML = viewProfile();
    if (!S.profile) loadProfile();
    if (!S.serverProfile || S.serverProfile.gid !== S.guildId) loadServerProfile();
    if (profileScope() === "character" && (!S.persona || S.persona.gid !== S.guildId)) loadPersona();
  }
  else if (p === "permissions") main.innerHTML = viewPermissions();
  else if (p === "updates") { main.innerHTML = viewUpdates(); loadUpdates(); }
  else if (p === "console") { S.page = "dashboard"; render(); return; }  // the console is part of the dashboard now
  else if (p === "customize") { main.innerHTML = viewCustomize(); afterCustomizeRender(); }
  else if (p.startsWith("cat:")) main.innerHTML = viewCategory(p.slice(4));
  else if (p.startsWith("mod:")) { main.innerHTML = viewModulePage(p.slice(4)); afterModuleRender(p.slice(4)); }
}

/* ===== dashboard ===== */
function viewDashboard() {
  return `
    <div class="section"><div id="dash-stats" class="grid grid-4"></div></div>
    <div id="dash-notice"></div>
    ${viewConsole()}`;
}

function renderDashboardStats() {
  const box = $("#dash-stats");
  if (!box) return;
  const st = S.status;
  const stat = (label, value) => `<div class="card"><div class="stat-label">${esc(label)}</div><div class="stat-value">${value}</div></div>`;
  box.innerHTML =
    stat("מצב", esc(t(STATE_LABELS[st.state] || st.state))) +
    stat("בוט", st.user ? esc(st.user.name) : "—") +
    stat("שרתים", st.guild_count ?? "—") +
    stat("השהיה", st.latency_ms != null ? `${st.latency_ms}ms` : "—");
  const notice = $("#dash-notice");
  if (st.state === "error" && st.error) notice.innerHTML = `<div class="notice error">${esc(st.error)}</div>`;
  else if (S.settings && !S.settings.secrets.bot_token.set) notice.innerHTML = `<div class="notice warn">${t('עוד לא הוגדר Bot Token. עבור ל<a href="#" data-nav="connection">חיבור והגדרות</a> כדי להוסיף אותו.')}</div>`;
  else notice.innerHTML = "";
}

function logLineHTML(l) {
  const ts = new Date(l.ts * 1000).toLocaleTimeString("he-IL", { hour12: false });
  return `<div class="log-line log-${esc(l.level)}"><span class="ts">${ts}</span> <span class="lvl">${esc(l.level)}</span> <span class="msg">${esc(l.msg)}</span></div>`;
}
const logVisible = (l) => S.logFilter === "ALL" || l.level === S.logFilter || (S.logFilter === "ERROR" && l.level === "CRITICAL");

function renderLogs() {
  const box = $("#logs");
  if (!box) return;
  box.innerHTML = S.logs.filter(logVisible).map(logLineHTML).join("") || `<div class="muted">אין לוגים עדיין</div>`;
  box.scrollTop = box.scrollHeight;
}

function appendLog(l) {
  const box = $("#logs");
  if (!box || !logVisible(l)) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  if (box.firstElementChild && box.firstElementChild.classList.contains("muted")) box.innerHTML = "";
  box.insertAdjacentHTML("beforeend", logLineHTML(l));
  while (box.childElementCount > 1000) box.firstElementChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}

/* ===== connection & settings ===== */
function secretField(name, label, hint) {
  const info = S.settings.secrets[name];
  return `
    <div class="card section">
      <div class="row" style="margin-bottom:10px">
        <h3>${esc(label)}</h3>
        ${info.set ? `<span class="badge badge-ok">שמור ${esc(info.hint)}</span>` : `<span class="badge">לא הוגדר</span>`}
      </div>
      <p class="desc" style="margin-bottom:12px">${hint}</p>
      <div class="input-group">
        <input type="password" id="secret-${name}" autocomplete="off" spellcheck="false" placeholder="${info.set ? "הדבק ערך חדש כדי להחליף" : "הדבק כאן"}">
        <button class="btn" data-action="toggle-show" data-target="secret-${name}">הצג</button>
        <button class="btn btn-primary" data-action="save-secret" data-name="${name}">שמור</button>
      </div>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-sm" data-action="test-secret" data-name="${name}" ${info.set ? "" : "disabled"}>בדוק חיבור</button>
        <button class="btn btn-sm btn-ghost" data-action="delete-secret" data-name="${name}" data-label="${esc(label)}" ${info.set ? "" : "disabled"}>מחק</button>
        <span class="muted small">נשמר ב-Windows Credential Manager. הערך המלא לא מוצג אחרי השמירה.</span>
      </div>
    </div>`;
}

function viewAccounts() {
  const accs = (S.status.accounts && S.status.accounts.length ? S.status.accounts : null);
  const rows = (accs || []).filter((a) => !a.main).map((a) => `
      <div class="row acc-row">
        ${a.user ? `<img class="chat-avatar" src="${esc(a.user.avatar)}" alt="">` : `<span class="chat-avatar"></span>`}
        <div class="grow"><b class="no-translate">${esc(a.label)}</b>${a.user ? ` <span class="muted small no-translate">${esc(a.user.name)}</span>` : ""}
          <div class="muted small">${esc(t(STATE_LABELS[a.state] || a.state))}${a.guild_count != null ? " · " + esc(t("{n} שרתים", { n: a.guild_count })) : ""}${a.error ? " · " + esc(a.error) : ""}</div></div>
        <button class="btn btn-sm btn-ghost" data-action="account-remove" data-id="${esc(a.id)}" data-label="${esc(a.label)}">${esc(t("הסר"))}</button>
      </div>`).join("");
  return `
    <div class="card section">
      <h3>${esc(t("חשבונות בוט נוספים"))}</h3>
      <p class="desc" style="margin:6px 0 12px">${esc(t("רוצה בוט אחר (שם, תמונה וטוקן משלו) לשרת אחר? הוסף כאן את הטוקן שלו. כל הבוטים רצים יחד מהאפליקציה, ולכל שרת בוחרים איזה בוט מפעיל אותו. הבוט הראשי (הטוקן למעלה) ממשיך לעבוד כרגיל בשרתים שלו."))}</p>
      ${rows || `<div class="muted small" style="margin-bottom:10px">${esc(t("אין עדיין חשבונות נוספים. הבוט הראשי מפעיל את כל השרתים."))}</div>`}
      <div class="row" style="flex-wrap:wrap;margin-top:10px">
        <input type="text" id="acc-label" placeholder="${esc(t("שם לחשבון (לא חובה)"))}" style="max-width:200px">
        <input type="password" id="acc-token" class="ltr grow" placeholder="${esc(t("Bot Token של הבוט הנוסף"))}" autocomplete="off" spellcheck="false">
        <button class="btn btn-primary" data-action="account-add">${esc(t("הוסף בוט"))}</button>
      </div>
      <span class="hint">${esc(t("הטוקן נשמר ב-Windows Credential Manager. אחרי ההוספה, הזמן את הבוט החדש לשרת שלו (בעמוד 'הרשאות והזמנה' בוחרים את הבוט החדש ויוצרים לו קישור הזמנה)."))}</span>
    </div>`;
}

function guildBotSelect(g) {
  const accs = S.status.accounts || [];
  if (accs.length < 2) return "";
  const present = (g.objects && g.objects.bots_present) || [];
  const chosen = ((g.config && g.config._server) || {}).bot || "";
  return `<label class="row guild-lang"><span class="label">${esc(t("הבוט בשרת הזה"))}</span>
    <select id="guild-bot" class="no-translate" style="width:auto">
      <option value="" ${!chosen ? "selected" : ""}>${esc(t("אוטומטי (הראשי, אם הוא בשרת)"))}</option>
      ${accs.map((a) => `<option value="${esc(a.id)}" ${chosen === a.id ? "selected" : ""}>${esc(a.label)}${present.includes(a.id) ? " ✓" : ` (${esc(t("לא בשרת"))})`}</option>`).join("")}
    </select></label>`;
}

function toggleRow(key, label, checked) {
  return `<div class="row" style="padding:8px 0">
      <label class="switch"><input type="checkbox" data-setting="${key}" ${checked ? "checked" : ""}><span class="slider"></span></label>
      <span>${esc(label)}</span></div>`;
}

function viewConnection() {
  if (!S.settings) return `<div class="empty">טוען…</div>`;
  const s = S.settings.settings;
  return `
    <div class="card section">
      <h3>שפת הממשק</h3>
      <p class="desc" style="margin-bottom:10px">שפת האפליקציה. את שפת ההודעות של הבוט בוחרים לכל שרת בנפרד, בראש עמודי המודולים.</p>
      <select id="ui-lang-2" class="no-translate" style="width:auto">${langOptions(I18N.lang)}</select>
    </div>
    ${secretField("bot_token", "Bot Token", t('מ-<a href="https://discord.com/developers/applications" target="_blank">Developer Portal</a>, בלשונית Bot, לוחצים Reset Token.'))}
    ${secretField("anthropic_key", "Anthropic API Key", t('מ-<a href="https://console.anthropic.com/settings/keys" target="_blank">Anthropic Console</a>. משמש לצ\'אט עם Claude.'))}
    <details class="card section"><summary><b>${esc(t("ChatGPT ו-Grok (לא חובה, במקום Claude)"))}</b></summary>
      <p class="desc" style="margin:10px 0">${esc(t("בוחרים את הספק בעמוד 'צ'אט AI'. המפתחות נשמרים ב-Windows Credential Manager, כמו כל השאר."))}</p>
      ${secretField("openai_key", "OpenAI API Key (ChatGPT)", t('מ-<a href="https://platform.openai.com/api-keys" target="_blank">OpenAI Platform</a>. החיוב נפרד ממנוי ChatGPT Plus.'))}
      ${secretField("xai_key", "xAI API Key (Grok)", t('מ-<a href="https://console.x.ai" target="_blank">xAI Console</a>. החיוב נפרד ממנוי X Premium.'))}
    </details>
    <details class="card section"><summary><b>${esc(t("Twitch (לא חובה, להתראות רשתות)"))}</b></summary>
      <p class="desc" style="margin:10px 0">${t('יוצרים אפליקציה חינמית ב-<a href="https://dev.twitch.tv/console/apps" target="_blank">Twitch Developer Console</a> (OAuth Redirect: http://localhost) ומעתיקים את ה-Client ID וה-Client Secret.')}</p>
      ${secretField("twitch_client_id", "Twitch Client ID", "")}
      ${secretField("twitch_client_secret", "Twitch Client Secret", "")}
    </details>
    ${viewAccounts()}
    <div class="card section">
      <h3>מי מורשה לדבר עם הבוט</h3>
      <p class="desc" style="margin-bottom:14px">לחיצה ימנית על המשתמש או על הערוץ בדיסקורד, ואז "העתק מזהה". צריך להפעיל קודם מצב מפתחים בהגדרות דיסקורד, תחת מתקדם.</p>
      <div class="grid" style="grid-template-columns:1fr 1fr">
        <div class="field"><span class="label">User ID מורשה</span><input type="text" class="ltr" id="allowed_user_id" value="${esc(s.allowed_user_id)}" placeholder="123456789012345678"></div>
        <div class="field"><span class="label">ערוץ מורשה (לא חובה)</span><input type="text" class="ltr" id="allowed_channel_id" value="${esc(s.allowed_channel_id)}" placeholder="ריק = כל ערוץ ו-DM"></div>
      </div>
      <button class="btn btn-primary" data-action="save-ids">שמור</button>
    </div>
    <div class="card section">
      <h3>התנהגות</h3>
      ${toggleRow("autostart_bot", "להפעיל את הבוט אוטומטית כשהשירות עולה", s.autostart_bot)}
      ${toggleRow("keep_running_on_close", "להשאיר את הבוט רץ ברקע כשסוגרים את החלון", s.keep_running_on_close)}
      ${toggleRow("start_with_windows", "להפעיל את הבוט ברקע כשנכנסים ל-Windows (בלי חלון)", S.settings.start_with_windows)}
      <span class="hint">${esc(t("כשהמחשב כבוי, הבוט לא יכול לעבוד. כדי שיעבוד 24/7, אפשר להריץ אותו על מחשב שתמיד דלוק (שרת ענן קטן או Raspberry Pi)."))}</span>
    </div>
    <div class="notice">
      ${t("<b>Privileged Intents:</b> מודולים כמו ברוכים הבאים ואוטומוד צריכים את Server Members Intent ואת Message Content Intent. אפשר להפעיל אותם בלחיצה על 'הפעל אוטומטית' בעמודי המודולים, או ידנית ב-Developer Portal, בלשונית Bot.")}
    </div>`;
}

/* ===== bot profile (Bot Personalizer) ===== */
const STATUS_LABELS = { online: N_("מחובר"), idle: N_("לא פעיל"), dnd: N_("לא להפריע"), invisible: N_("בלתי נראה") };
const ACTIVITY_LABELS = {
  none: N_("ללא"), playing: N_("משחק ב"), streaming: N_("משדר"), listening: N_("מאזין ל"),
  watching: N_("צופה ב"), competing: N_("מתחרה ב"), custom: N_("מותאם אישית"),
};

function presenceDraft() {
  if (!S.presenceDraft) S.presenceDraft = { ...((S.profile && S.profile.presence) || { status: "online", activity: "none", text: "", url: "" }) };
  return S.presenceDraft;
}

function profileScope() {
  if (S.guild && S.profileScope === "character") return "character";
  return S.guild && S.profileScope !== "default" ? "server" : "default";
}

function profileView() {
  /* what the page and the preview show: the server's own profile, or the default one */
  const p = S.profile || {};
  const sp = S.serverProfile && S.serverProfile.gid === S.guildId ? S.serverProfile : null;
  if (profileScope() === "server") {
    const nickEl = $("#sp-nick");
    return {
      server: true, ready: !!(sp && sp.account), loading: !sp,
      name: (nickEl ? nickEl.value : sp && sp.nick) || p.username || "Bot",
      avatar: S.spAvatar || (sp && sp.avatar_url) || p.avatar_url || NONE_IMG,
      banner: S.spBanner || (sp && sp.banner_url) || p.banner_url || "",
      ownAvatar: !!(sp && sp.avatar_url), ownBanner: !!(sp && sp.banner_url),
    };
  }
  const userEl = $("#bot-username");
  return { server: false, ready: !!p.username, name: (userEl && userEl.value) || p.username || "Bot",
           avatar: S.avatarDraft || p.avatar_url || NONE_IMG, banner: S.bannerDraft || p.banner_url || "" };
}

function viewProfilePreview() {
  const v = profileView();
  const pr = presenceDraft();
  const name = esc(v.name);
  const avatar = esc(v.avatar);
  const act = pr.activity !== "none" && pr.text
    ? `<div class="pv-activity"><div class="muted small">${esc(t(ACTIVITY_LABELS[pr.activity]))}</div><b class="no-translate">${esc(pr.text)}</b></div>` : "";
  return `
    <div class="field"><span class="label">${esc(t("תצוגה ברשימת החברים"))}</span>
      <div class="pv-member">
        <div class="pv-avatar-wrap"><img src="${avatar}" alt=""><span class="pv-dot ${pr.status}"></span></div>
        <div><div class="row" style="gap:6px"><b class="no-translate">${name}</b><span class="pv-app">APP</span></div>
          <div class="muted small no-translate">${pr.activity !== "none" ? esc(pr.text) : ""}</div></div>
      </div>
    </div>
    <div class="field"><span class="label">${esc(t("תצוגת פרופיל"))}</span>
      <div class="pv-profile">
        <div class="pv-banner" style="${v.banner ? `background-image:url('${esc(v.banner)}')` : ""}"></div>
        <div class="pv-avatar-wrap big"><img src="${avatar}" alt=""><span class="pv-dot ${pr.status}"></span></div>
        <div class="pv-body"><div class="row" style="gap:6px"><b class="no-translate" style="font-size:17px">${name}</b><span class="pv-app">APP</span></div>
          ${act}</div>
      </div>
    </div>`;
}

function viewProfile() {
  const p = S.profile;
  const v = profileView();
  const sp = S.serverProfile && S.serverProfile.gid === S.guildId ? S.serverProfile : null;
  const pr = presenceDraft();
  const statusOpts = Object.entries(STATUS_LABELS).map(([k, val]) => `<option value="${k}" ${k === pr.status ? "selected" : ""}>${esc(t(val))}</option>`).join("");
  const actOpts = Object.entries(ACTIVITY_LABELS).map(([k, val]) => `<option value="${k}" ${k === pr.activity ? "selected" : ""}>${esc(t(val))}</option>`).join("");
  const server = v.server;
  const scopeBar = S.guild ? `
    <div class="tabs" style="margin-bottom:14px">
      <button class="tab ${server ? "active" : ""}" data-action="profile-scope" data-scope="server">${esc(t("השרת הזה"))}: <span class="no-translate">${esc(S.guild.name)}</span></button>
      <button class="tab ${!server && profileScope() === "default" ? "active" : ""}" data-action="profile-scope" data-scope="default">${esc(t("פרופיל ברירת מחדל (כל השרתים)"))}</button>
      <button class="tab ${profileScope() === "character" ? "active" : ""}" data-action="profile-scope" data-scope="character">🎭 ${esc(t("דמות (AI)"))}</button>
    </div>
    <p class="desc" style="margin:-4px 0 14px">${esc(server
      ? t("התמונה, הבאנר, השם והביו כאן נראים רק בשרת הזה. מחליפים שרת למעלה כדי לערוך שרת אחר.")
      : t("הפרופיל שכל השרתים רואים, חוץ משרתים שקבעת להם פרופיל משלהם."))}</p>` : "";
  if (profileScope() === "character") return scopeBar.replace(/<p class="desc"[\s\S]*?<\/p>/, "") + viewCharacter();
  if (server && v.loading) return scopeBar + `<div class="card section empty">${esc(t("טוען…"))}</div>`;
  if (server && !v.ready) return scopeBar + `<div class="notice warn">${esc(t("הבוט צריך להיות מחובר ובשרת הזה כדי לערוך את הפרופיל שלו כאן."))}</div>`;
  const ids = server ? { avatar: "sp-avatar-file", banner: "sp-banner-file" } : { avatar: "avatar-file", banner: "banner-file" };
  const avatarUndo = server ? (S.spAvatar ? `<button class="btn btn-sm btn-ghost" data-action="sp-discard" data-field="avatar">${esc(t("בטל תמונה"))}</button>`
      : v.ownAvatar ? `<button class="btn btn-sm btn-ghost" data-action="sp-reset" data-field="avatar">${esc(t("חזרה לתמונה הרגילה"))}</button>` : "")
    : (S.avatarDraft ? `<button class="btn btn-sm btn-ghost" data-action="discard-avatar">בטל תמונה</button>` : "");
  const useLogo = `<button class="btn btn-sm" data-action="avatar-use-logo" data-scope="${server ? "server" : "default"}">${esc(t("השתמש בלוגו של האפליקציה"))}</button>`;
  const bannerUndo = server ? (S.spBanner ? `<button class="btn btn-sm btn-ghost" data-action="sp-discard" data-field="banner">${esc(t("בטל באנר"))}</button>`
      : v.ownBanner ? `<button class="btn btn-sm btn-ghost" data-action="sp-reset" data-field="banner">${esc(t("חזרה לבאנר הרגיל"))}</button>` : "")
    : (S.bannerDraft ? `<button class="btn btn-sm btn-ghost" data-action="discard-banner">בטל באנר</button>` : "");
  const nameField = server
    ? `<div class="field"><span class="label">${esc(t("שם הבוט בשרת הזה (כינוי)"))}</span>
         <input type="text" id="sp-nick" maxlength="32" value="${esc(sp.nick)}" placeholder="${esc((p && p.username) || sp.default_name)}">
         <span class="hint">${esc(t("ריק = השם הרגיל של הבוט"))}</span></div>`
    : `<div class="field"><span class="label">שם הבוט</span>
         <input type="text" id="bot-username" maxlength="32" value="${esc(p ? p.username : "")}" ${p ? "" : "disabled"}>
         <span class="hint">2–32 תווים. דיסקורד מאפשר לשנות שם רק כמה פעמים בשעה.</span></div>`;
  return scopeBar + `
    <div class="card section personalizer">
      <div class="pz-col">
        <div class="field"><span class="label">אייקון</span>
          <label class="pz-icon file-btn"><img id="avatar-preview" alt="" src="${esc(v.avatar)}">
            <input type="file" id="${ids.avatar}" accept="image/png,image/jpeg,image/gif,image/webp"><span class="pz-edit">✎</span></label>
          <div class="row" style="gap:6px;flex-wrap:wrap">${useLogo}${avatarUndo}</div>
        </div>
        <div class="field"><span class="label">באנר (רקע)</span>
          <label class="pz-banner file-btn" style="${v.banner ? `background-image:url('${esc(v.banner)}')` : ""}">
            <input type="file" id="${ids.banner}" accept="image/png,image/jpeg,image/gif,image/webp"><span class="pz-edit">✎</span></label>
          ${bannerUndo}
          <span class="hint">מומלץ 680×240. PNG, JPG, GIF או WEBP, עד 8MB</span>
        </div>
      </div>
      <div class="pz-col grow">
        ${nameField}
        ${server ? `<div class="field"><span class="label">${esc(t("ביו בשרת הזה"))}</span><textarea id="sp-bio" maxlength="190" rows="2">${esc(sp.bio)}</textarea></div>` : ""}
        <div class="field"><span class="label">סטטוס הבוט</span><select id="pr-status">${statusOpts}</select>
          ${server ? `<span class="hint">${esc(t("הסטטוס והפעילות משותפים לכל השרתים (מגבלה של דיסקורד)."))}</span>` : ""}</div>
        <div class="row" style="align-items:flex-start">
          <div class="field grow"><span class="label">סוג פעילות</span><select id="pr-activity">${actOpts}</select></div>
          <div class="field grow"><span class="label">טקסט הסטטוס</span><input type="text" id="pr-text" maxlength="128" value="${esc(pr.text)}" ${pr.activity === "none" ? "disabled" : ""}></div>
        </div>
        ${pr.activity === "streaming" ? `<div class="field"><span class="label">קישור לשידור (Twitch או YouTube)</span><input type="text" class="ltr" id="pr-url" value="${esc(pr.url)}" placeholder="https://twitch.tv/..."></div>` : ""}
        <span class="hint">הסטטוס והפעילות מתעדכנים מיד כשהבוט מחובר. דיסקורד מגביל החלפת תמונה ובאנר לפעמיים בערך בשעה.</span>
        <div class="row" style="margin-top:12px"><button class="btn btn-primary" data-action="${server ? "sp-save" : "save-profile"}" ${p || server ? "" : "disabled"}>${esc(server ? t("שמור לשרת הזה") : t("שמור שינויים"))}</button></div>
      </div>
      <div class="pz-col pz-preview" id="pz-preview">${viewProfilePreview()}</div>
    </div>
    <div id="profile-msg"></div>`;
}

function refreshProfilePreview() { const box = $("#pz-preview"); if (box) box.innerHTML = viewProfilePreview(); }

async function loadProfile() {
  try {
    const p = await api("GET", "/profile");
    const changed = !S.profile || S.profile.username !== p.username || S.profile.avatar_url !== p.avatar_url;
    S.profile = p;
    if (changed && S.page === "profile") { S.presenceDraft = null; $("#main").innerHTML = viewProfile(); }
  } catch (e) {
    const box = $("#profile-msg");
    if (box) box.innerHTML = `<div class="notice error">${esc(errMessage(e))}</div>`;
  }
}

async function loadServerProfile() {
  if (!S.guildId) return;
  try { S.serverProfile = { ...(await api("GET", `/guilds/${S.guildId}/member-profile`)), gid: S.guildId }; }
  catch (e) { S.serverProfile = { gid: S.guildId, account: null }; }
  S.spAvatar = null; S.spBanner = null;
  if (S.page === "profile") $("#main").innerHTML = viewProfile();
}

/* ===== updates ===== */
function viewUpdates() {
  const u = S.updates;
  if (!u) return `<div class="empty">טוען…</div>`;
  const list = u.releases.length ? u.releases.map((r) => `
      <div class="card section release ${r.is_current ? "current" : ""}">
        <div class="row">
          <h3 class="ltr" style="margin:0">${esc(r.tag)}</h3>
          ${r === u.releases[0] ? `<span class="badge badge-ok">${esc(t("הכי חדשה"))}</span>` : ""}
          ${r.is_current ? `<span class="badge">${esc(t("הגרסה שלך"))}</span>` : ""}
          ${r.prerelease ? `<span class="badge badge-soon">${esc(t("גרסת ניסיון"))}</span>` : ""}
          <span class="muted small">${r.published ? new Date(r.published).toLocaleDateString() : ""}</span>
          <div class="spacer"></div>
          ${r.is_current ? "" : r.zip_url
            ? `<button class="btn ${r.is_newer ? "btn-primary" : ""}" data-action="install-version" data-tag="${esc(r.tag)}" data-newer="${r.is_newer ? 1 : 0}">${esc(r.is_newer ? t("עדכן לגרסה הזו") : t("חזור לגרסה הזו"))}</button>`
            : `<span class="muted small">${esc(t("אין קובץ התקנה"))}</span>`}
        </div>
        ${r.notes ? `<pre class="release-notes no-translate">${esc(r.notes)}</pre>` : ""}
      </div>`).join("") : `<div class="muted small">${esc(t("לא נמצאו גרסאות במאגר."))}</div>`;
  return `
    <div class="card section">
      <div class="row"><h3 style="margin:0">${esc(t("הגרסה המותקנת:"))} <span class="ltr">${esc(u.version)}</span></h3><div class="spacer"></div>
        <button class="btn" data-action="check-updates">${esc(t("בדוק עדכונים"))}</button></div>
      <div class="field" style="margin-top:14px"><span class="label">${esc(t("מאגר GitHub לעדכונים"))}</span>
        <div class="input-group"><input type="text" id="update-repo" class="ltr" value="${esc(u.repo)}" placeholder="owner/Free-Discord-Bot">
          <button class="btn btn-primary" data-action="save-update-repo">${esc(t("שמור"))}</button></div>
        <span class="hint">${esc(t("האפליקציה בודקת את ה-Releases במאגר. הגרסה החדשה ביותר מוצגת למעלה, וכל הגרסאות הקודמות זמינות לחזרה ידנית."))}</span></div>
      ${u.error ? `<div class="notice error">${esc(u.error)}</div>` : ""}
    </div>
    ${u.repo ? list : ""}`;
}

async function loadUpdates() {
  try { S.updates = await api("GET", "/updates"); } catch (e) { toast(errMessage(e), "error"); return; }
  if (S.page === "updates") $("#main").innerHTML = viewUpdates();
}

/* ===== permissions & invite ===== */
function viewPermissions() {
  if (!S.catalog) return `<div class="empty">טוען…</div>`;
  if (!S.permSel) S.permSel = new Set(S.catalog.recommended_permissions);
  const perms = S.catalog.permissions.map((p) => `
    <label class="perm ${p.dangerous ? "danger" : ""}">
      <input type="checkbox" data-perm="${p.key}" ${S.permSel.has(p.key) ? "checked" : ""}>
      <span>${esc(p.label)}</span>
      ${p.key === "administrator" ? `<span class="badge badge-danger">מסוכן</span>` : ""}
    </label>`).join("");
  const guildOpts = [`<option value="">${esc(t("אבחר בדיסקורד"))}</option>`]
    .concat(S.guilds.map((g) => `<option value="${esc(g.id)}">${esc(g.name)}</option>`)).join("");
  return `
    <div class="notice">${t("בוט לא יכול לתת לעצמו הרשאות. האפליקציה רק יוצרת קישור הזמנה: אתה פותח אותו, בוחר שרת ולוחץ <b>Authorize</b> בדיסקורד. ברירת המחדל היא ההרשאות המינימליות שהמודולים הפעילים צריכים. ככה גם מוסיפים את הבוט לשרת חדש.")}</div>
    <div class="card section">
      <div class="row" style="margin-bottom:12px">
        <h3>הרשאות</h3><div class="spacer"></div>
        <button class="btn btn-sm" data-action="perm-recommended">רק מה שהמודולים צריכים</button>
        <button class="btn btn-sm btn-ghost" data-action="perm-none">נקה הכל</button>
      </div>
      <div class="perm-list">${perms}</div>
    </div>
    <div class="card section">
      <h3>קישור הזמנה</h3>
      <div class="row" style="margin-top:10px">
        ${(S.status.accounts || []).length > 1 ? `<select id="invite-account" class="no-translate" style="width:auto">${S.status.accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join("")}</select>` : ""}
        <select id="invite-guild" class="no-translate" style="width:auto;min-width:220px">${guildOpts}</select>
        <button class="btn btn-primary" data-action="make-invite">צור קישור</button>
      </div>
      ${S.invite ? `<div class="invite-box">${esc(S.invite.url)}</div>
        <div class="row" style="margin-top:10px">
          <a class="btn" href="${esc(S.invite.url)}" target="_blank" rel="noopener">פתח בדפדפן</a>
          <button class="btn" data-action="copy-invite">העתק</button>
          <span class="muted small ltr">permissions=${esc(S.invite.permissions_value)}</span>
        </div>` : ""}
    </div>`;
}

/* ===== pickers & field renderers ===== */
function channelPicker(attrs, value, objects, emptyLabel = "ללא", list = "text_channels") {
  if (!objects) return `<input type="text" class="ltr" ${attrs} value="${esc(value)}" placeholder="מזהה ערוץ (הפעל את הבוט כדי לבחור מרשימה)">`;
  const kind = list === "voice_channels" ? "voice" : "text";
  const cur = (objects[list] || []).find((c) => c.id === value);
  return `<div class="chan-combo" data-list="${list}" data-kind="${kind}" data-empty="${esc(emptyLabel)}">
      <input type="hidden" ${attrs} value="${esc(value)}">
      <button type="button" class="chan-btn" data-action="chan-open">${chanLabel(cur, kind, emptyLabel)}<span class="caret">▾</span></button>
    </div>`;
}

const CHANNEL_SUGGESTIONS = { welcome: "welcome", logs: "logs", starboard: "starboard", suggestions: "suggestions", ticketing: "tickets",
  birthdays: "birthdays", levels: "level-ups", social_alerts: "alerts", invite_tracker: "invites", embed_messages: "announcements",
  reminders: "reminders", restricted_channels: "media", temp_channels: "Join to Create", giveaways: "giveaways" };

function chanLabel(c, kind, emptyLabel) {
  if (!c) return `<span class="muted">${esc(t(emptyLabel))}</span>`;
  return `<span class="chan-ico">${kind === "voice" ? "🔊" : "#"}</span><span class="no-translate chan-name">${esc(c.name)}</span>`;
}

function slugChannel(name, kind) {
  name = (name || "").trim();
  return kind === "voice" ? name.slice(0, 100) : name.toLowerCase().replace(/[\s_]+/g, "-").replace(/[^\p{L}\p{N}\p{Emoji}\-│|]/gu, "").slice(0, 100);
}

function openChanCombo(combo) {
  document.querySelectorAll(".chan-pop").forEach((p) => p.remove());
  const list = combo.dataset.list, kind = combo.dataset.kind;
  const chans = (S.guild && S.guild.objects && S.guild.objects[list]) || [];
  const groups = {};
  chans.forEach((c) => { (groups[c.category || ""] ||= []).push(c); });
  const key = S.page.startsWith("mod:") ? S.page.slice(4) : "";
  const suggestion = CHANNEL_SUGGESTIONS[key] || (kind === "voice" ? "Voice" : "new-channel");
  const pop = document.createElement("div");
  pop.className = "chan-pop";
  pop.innerHTML = `
    <input type="search" class="chan-search" placeholder="${esc(t("חיפוש ערוץ"))}" autocomplete="off">
    <div class="chan-list">
      <div class="chan-sec">${esc(t("אפשרויות"))}</div>
      <button type="button" class="chan-opt" data-action="chan-pick" data-id="">${esc(t(combo.dataset.empty || "ללא"))}</button>
      <button type="button" class="chan-opt chan-create" data-action="chan-create" data-name="${esc(suggestion)}">✎ <span>${esc(t("צור לי ערוץ"))}</span> <b class="no-translate">${kind === "voice" ? "🔊" : "#"}${esc(slugChannel(suggestion, kind))}</b></button>
      ${Object.entries(groups).map(([cat, cs]) => `
        <div class="chan-sec no-translate">${esc(cat || t("ערוצים"))}</div>
        ${cs.map((c) => `<button type="button" class="chan-opt no-translate" data-action="chan-pick" data-id="${esc(c.id)}" data-search="${esc(c.name.toLowerCase())}">
          <span class="chan-ico">${kind === "voice" ? "🔊" : "#"}</span>${esc(c.name)}</button>`).join("")}`).join("")}
    </div>`;
  combo.appendChild(pop);
  const search = pop.querySelector(".chan-search");
  search.focus();
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    pop.querySelectorAll(".chan-opt[data-search]").forEach((b) => { b.style.display = !q || b.dataset.search.includes(q) ? "" : "none"; });
    const create = pop.querySelector(".chan-create");
    const name = q ? search.value.trim() : suggestion;
    create.dataset.name = name;
    create.querySelector("b").textContent = (kind === "voice" ? "🔊" : "#") + slugChannel(name, kind);
  });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.remove(); });
}

function setChanValue(combo, id) {
  const input = combo.querySelector("input[type=hidden]");
  const chans = (S.guild && S.guild.objects && S.guild.objects[combo.dataset.list]) || [];
  input.value = id;
  combo.querySelector(".chan-btn").innerHTML = chanLabel(chans.find((c) => c.id === id), combo.dataset.kind, combo.dataset.empty) + `<span class="caret">▾</span>`;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  const pop = combo.querySelector(".chan-pop");
  if (pop) pop.remove();
}

function rolePicker(attrs, value, objects, emptyLabel = "בחר תפקיד") {
  if (!objects) return `<input type="text" class="ltr" ${attrs} value="${esc(value)}" placeholder="מזהה תפקיד">`;
  const opts = [`<option value="">${esc(emptyLabel)}</option>`].concat(objects.roles.map((r) =>
    `<option value="${esc(r.id)}" ${r.id === value ? "selected" : ""} ${r.assignable ? "" : "disabled"}>@${esc(r.name)}${r.assignable ? "" : " (מעל תפקיד הבוט)"}</option>`));
  return `<select ${attrs}>${opts.join("")}</select>`;
}

function multiPicker(fkey, values, items, kind) {
  if (!items) return `<input type="text" class="ltr" data-field="${fkey}" data-ftype="ids" value="${esc(values.join(", "))}" placeholder="מזהים מופרדים בפסיקים (הפעל את הבוט כדי לבחור מרשימה)">`;
  if (!items.length) return `<div class="muted small">אין פריטים</div>`;
  return `<div class="multi" data-field="${fkey}" data-ftype="multi">${items.map((it) => `
      <label class="multi-item ${kind === "role" && !it.assignable ? "disabled" : ""}">
        <input type="checkbox" value="${esc(it.id)}" ${values.includes(it.id) ? "checked" : ""}>
        <span>${kind === "role" ? "@" : "# "}${esc(it.name)}</span>
      </label>`).join("")}</div>`;
}

function simpleInput(f, value, attrs, o) {
  switch (f.type) {
    case "number": return `<input type="text" inputmode="numeric" class="ltr narrow" ${attrs} value="${esc(value)}">`;
    case "date": return `<input type="date" class="ltr" ${attrs} value="${esc(value)}">`;
    case "bool": return `<label class="switch"><input type="checkbox" ${attrs} ${value ? "checked" : ""}><span class="slider"></span></label>`;
    case "select": return `<select ${attrs}>${f.options.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    case "channel": return channelPicker(attrs, value, o);
    case "voice_channel": return channelPicker(attrs, value, o, "ללא", "voice_channels");
    case "role": return rolePicker(attrs, value, o);
    case "lines": return `<textarea ${attrs} rows="3">${esc((value || []).join("\n"))}</textarea>`;
    case "textarea": return `<textarea ${attrs}>${esc(value)}</textarea>`;
    default: return `<input type="text" ${attrs} value="${esc(value)}" maxlength="${f.max || 200}">`;
  }
}

const CMD_EXAMPLES = {
  type: { name: "type", kind: "slash", response: "{args}", delete_trigger: true, send_mode: "channel", description: "הבוט כותב את ההודעה שלך", arg_hint: "מה הבוט יכתוב" },
  randomnumber: { name: "randomnumber", kind: "slash", response: "🎲 {random}", send_mode: "reply", description: "מספר אקראי בין שני מספרים", arg_hint: "למשל: 1 to 1000" },
  coin: { name: "coin", kind: "both", response: "🪙 {choose:עץ|פלי}", send_mode: "reply", description: "הטלת מטבע", arg_hint: "" },
};

function commandRow(c, o) {
  c = { name: "", response: "", role_id: "", role_action: "none", kind: "slash", send_mode: "channel", delete_trigger: false, description: "", arg_hint: "", ...(c || {}) };
  const actions = [["none", "בלי תפקיד"], ["add", "נותן תפקיד"], ["remove", "מוריד תפקיד"], ["toggle", "נותן או מוריד"]];
  const kinds = [["slash", "פקודת סלאש (/)"], ["prefix", "פקודת טקסט (קידומת)"], ["both", "גם וגם"]];
  const modes = [["channel", "הבוט שולח בערוץ"], ["reply", "תשובה לפקודה"], ["dm", "הודעה פרטית"]];
  const sel = (cls, list, v) => `<select class="${cls}">${list.map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(t(l))}</option>`).join("")}</select>`;
  return `<div class="cmd-row cmd-row-v2">
      <div class="cmd-line">
        <span class="cmd-slash ltr">${c.kind === "prefix" ? "!" : "/"}</span>
        <input type="text" class="cmd-name ltr" value="${esc(c.name)}" placeholder="type" maxlength="32">
        ${sel("cmd-kind", kinds, c.kind)}
        ${sel("cmd-mode", modes, c.send_mode)}
        <label class="row small" title="${esc(t("בפקודת טקסט: הבוט מוחק את ההודעה שלך ושולח רק את התשובה"))}"><input type="checkbox" class="cmd-del" ${c.delete_trigger ? "checked" : ""}> ${esc(t("למחוק את ההודעה שלי"))}</label>
        <div class="spacer"></div>
        <button class="btn btn-sm btn-ghost" data-action="row-remove" title="הסר">✕</button>
      </div>
      <textarea class="cmd-resp" rows="2" placeholder="${esc(t("התשובה של הבוט. למשל: {args} או 🎲 {random}"))}">${esc(c.response)}</textarea>
      <div class="cmd-line">
        <input type="text" class="cmd-desc" value="${esc(c.description)}" placeholder="${esc(t("תיאור בדיסקורד (לפקודת סלאש)"))}" maxlength="100">
        <input type="text" class="cmd-hint" value="${esc(c.arg_hint)}" placeholder="${esc(t("הסבר לשדה הטקסט (לא חובה)"))}" maxlength="100">
      </div>
      <div class="cmd-role">
        <select class="cmd-raction">${actions.map(([v, l]) => `<option value="${v}" ${v === c.role_action ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${rolePicker('class="cmd-roleid"', c.role_id, o, "תפקיד")}
      </div>
    </div>`;
}

function listRow(f, item, o) {
  return `<div class="list-row">${f.item_fields.map((itf) => `
      <label class="list-cell"><span class="label">${esc(itf.label)}</span>
        ${simpleInput(itf, item[itf.key] ?? itf.default, `data-ikey="${itf.key}" data-itype="${itf.type}"`, o)}</label>`).join("")}
      <button class="btn btn-sm btn-ghost" data-action="row-remove" title="הסר">✕</button>
    </div>`;
}

function rrPairRow(objects) {
  return `<div class="rr-pair">
      <input type="text" class="rr-emoji" placeholder="😀" maxlength="64">
      ${rolePicker('class="rr-role"', "", objects)}
      <button class="btn btn-sm btn-ghost" data-action="row-remove" title="הסר">✕</button>
    </div>`;
}

function renderField(f, value, g) {
  const o = g.objects;
  const hint = f.hint ? `<span class="hint">${esc(f.hint)}</span>` : "";
  const attrs = `data-field="${f.key}" data-ftype="${f.type}"`;
  let input;
  switch (f.type) {
    case "bool":
      return `<div class="row field-bool"><label class="switch"><input type="checkbox" ${attrs} ${value ? "checked" : ""}><span class="slider"></span></label><span>${esc(f.label)}</span></div>`;
    case "rule": {
      const params = (f.params || []).map((p) => `<label class="list-cell"><span class="label">${esc(p.label)}</span>${simpleInput(p, value[p.key], `data-pkey="${p.key}" data-ptype="${p.type}"`, o)}</label>`).join("");
      return `<div class="rule-row ${value.on ? "on" : ""}" ${attrs}>
          <label class="switch"><input type="checkbox" class="rule-on" ${value.on ? "checked" : ""}><span class="slider"></span></label>
          <div class="grow"><b>${esc(f.label)}</b>${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ""}
            ${params ? `<div class="rule-params">${params}</div>` : ""}</div>
          <select class="rule-action">${f.actions.map(([v, l]) => `<option value="${v}" ${v === value.action ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
        </div>`;
    }
    case "list":
      input = `<div class="list-field" ${attrs}>${(value || []).map((it) => listRow(f, it, o)).join("")}</div>
        <button class="btn btn-sm" data-action="list-add" data-fkey="${f.key}">+ הוסף שורה</button>`;
      break;
    case "commands":
      input = `<div class="cmd-list" ${attrs}>${(value || []).map((c) => commandRow(c, o)).join("")}</div>
        <div class="row" style="flex-wrap:wrap"><button class="btn btn-sm" data-action="cmd-add">+ הוסף פקודה</button>
          <span class="muted small">${esc(t("דוגמאות:"))}</span>
          ${Object.keys(CMD_EXAMPLES).map((k) => `<button class="btn btn-sm btn-ghost ltr" data-action="cmd-example" data-ex="${k}">/${k}</button>`).join("")}</div>`;
      break;
    case "channels": input = multiPicker(f.key, value, o && o.text_channels, "channel"); break;
    case "roles": input = multiPicker(f.key, value, o && o.roles, "role"); break;
    case "reaction_roles": return viewReactionRoles(value, g);
    case "automations":
      if (!S.dirty || !S.autos) { S.autos = (value || []).map(normalizeAuto); S.autoOpen = null; }
      return `<div class="field wide" ${attrs}><div id="autos">${viewAutosInner()}</div></div>`;
    default: input = simpleInput(f, value, attrs, o);
  }
  const wide = ["textarea", "lines", "commands", "channels", "roles", "list"].includes(f.type);
  return `<div class="field ${wide ? "wide" : ""}"><span class="label">${esc(f.label)}</span>${input}${hint}</div>`;
}

function viewReactionRoles(groups, g) {
  const o = g.objects;
  const existing = groups.length ? groups.map((grp) => `
      <div class="rr-group">
        <div class="grow">
          <div><b># ${esc(grp.channel_name || grp.channel_id)}</b>
            · <a href="https://discord.com/channels/${esc(g.id)}/${esc(grp.channel_id)}/${esc(grp.message_id)}" target="_blank" rel="noopener">להודעה</a></div>
          <div class="module-perms">${grp.pairs.map((p) => `<span class="badge">${esc(p.emoji)} → @${esc(p.role_name || p.role_id)}</span>`).join("")}</div>
        </div>
        <button class="btn btn-sm btn-ghost" data-action="rr-delete" data-mid="${esc(grp.message_id)}">הסר</button>
      </div>`).join("") : `<div class="muted small">עוד אין הודעות תפקידים.</div>`;
  const form = o ? `
      <div class="rr-new">
        <div class="section-title small-title">הודעה חדשה</div>
        <div class="field"><span class="label">ערוץ</span>${channelPicker('id="rr-channel"', "", o, "בחר ערוץ")}</div>
        <div class="field"><span class="label">טקסט ההודעה (לא חובה)</span><textarea id="rr-content" placeholder="ריק = רשימה אוטומטית של האימוג'ים והתפקידים"></textarea></div>
        <span class="label">אימוג'י ← תפקיד</span>
        <div id="rr-pairs">${rrPairRow(o)}</div>
        <div class="row" style="margin-top:8px">
          <button class="btn btn-sm" data-action="rr-add-pair">+ שורה</button>
          <div class="spacer"></div>
          <button class="btn btn-primary" data-action="rr-publish">פרסם בדיסקורד</button>
        </div>
      </div>` : `<div class="notice warn">כדי לפרסם הודעת תפקידים חדשה, הבוט צריך להיות מחובר.</div>`;
  return `<div class="field wide no-dirty">${existing}${form}</div>`;
}

/* ===== automations builder (WHEN SOMEONE -> IF -> DO THIS) ===== */
const TRIGGER_ICONS = {
  message_send: "✉️", message_delete: "🗑️", message_edit: "✏️", button_click: "🖱️", reaction_add: "👍",
  role_gain: "🎭", role_lose: "➖", voice_join: "🔊", voice_leave: "🔈", thread_create: "🧵", member_join: "👋",
};
const ACTION_ICONS = {
  send_message: "📨", reply: "↩️", add_roles: "➕", remove_roles: "➖", give_coins: "🪙", take_coins: "💸",
  give_xp: "⭐", take_xp: "📉", add_reaction: "😀", pin_message: "📌", create_thread: "🧵", delete_message: "🗑️",
};
const AUTO_TEMPLATES = {
  blank: { label: N_("ריקה"), make: () => ({ name: "", trigger: "", conditions: [], actions: [] }) },
  welcomer: { label: N_("👋 ברוכים הבאים"), make: () => ({ name: t("ברוכים הבאים"), trigger: "member_join", conditions: [],
    actions: [{ type: "send_message", target: "channel", channel_id: "", text: t("ברוך הבא {user} לשרת **{server}**! 🎉") }] }) },
  thread: { label: N_("🧵 שרשור לכל הודעה"), make: () => ({ name: t("שרשור אוטומטי"), trigger: "message_send",
    conditions: [{ type: "in_channels", values: [] }], actions: [{ type: "create_thread", name: t("דיון: {username}") }] }) },
  verify: { label: N_("✅ אימות במילה"), make: () => ({ name: t("אימות"), trigger: "message_send",
    conditions: [{ type: "content_exact", values: [t("!אימות")] }],
    actions: [{ type: "add_roles", roles: [] }, { type: "delete_message" }, { type: "send_message", target: "dm", text: t("אומתת בשרת {server} ✅") }] }) },
  button_role: { label: N_("🖱️ תפקיד בלחיצת כפתור"), make: () => ({ name: t("תפקיד בלחיצת כפתור"), trigger: "button_click",
    conditions: [{ type: "button_is", values: [t("קבל תפקיד")] }], actions: [{ type: "add_roles", roles: [] }] }) },
  starboard: { label: N_("⭐ Starboard פשוט"), make: () => ({ name: "Starboard", trigger: "reaction_add",
    conditions: [{ type: "reacted_with", values: ["⭐"] }],
    actions: [{ type: "send_message", target: "channel", channel_id: "", text: t("⭐ {user} סימן הודעה בכוכב ב-{channel}") }] }) },
  voice_role: { label: N_("🔊 תפקיד בזמן שיחה קולית"), make: () => ({ name: t("תפקיד בשיחה קולית"), trigger: "voice_join", conditions: [],
    actions: [{ type: "add_roles", roles: [] }] }) },
  role_announce: { label: N_("🎭 הכרזה על תפקיד"), make: () => ({ name: t("הכרזה על תפקיד"), trigger: "role_gain",
    conditions: [{ type: "gained_role", values: [] }],
    actions: [{ type: "send_message", target: "channel", channel_id: "", text: t("{user} קיבל את התפקיד **{role}**! 🎉") }] }) },
};

function migrateAutoV1(a) {
  if (!a.trigger || typeof a.trigger === "string") return a;
  const tr = a.trigger, conds = [];
  if (tr.channel_id) conds.push({ type: "in_channels", values: [tr.channel_id] });
  let trigger = "member_join";
  if (tr.type === "message") {
    trigger = "message_send";
    if (tr.match && tr.match !== "any" && tr.text) conds.push({ type: { exact: "content_exact", starts_with: "content_starts" }[tr.match] || "content_contains", values: [tr.text] });
  } else if (tr.type === "reaction_add") { trigger = "reaction_add"; if (tr.emoji) conds.push({ type: "reacted_with", values: [tr.emoji] }); }
  else if (tr.type === "role_added" || tr.type === "role_removed") {
    trigger = tr.type === "role_added" ? "role_gain" : "role_lose";
    if (tr.role_id) conds.push({ type: tr.type === "role_added" ? "gained_role" : "lost_role", values: [tr.role_id] });
  }
  const c = a.conditions && !Array.isArray(a.conditions) ? a.conditions : {};
  if (c.required_role_id) conds.push({ type: "has_any_role", values: [c.required_role_id] });
  const actions = (a.actions || []).map((x) => (x.type === "add_role" || x.type === "remove_role") ? { type: x.type + "s", roles: x.role_id ? [x.role_id] : [] } : x);
  return { ...a, trigger, conditions: conds, actions, ignore_bots: c.ignore_bots !== false };
}

function normalizeAuto(a) {
  a = migrateAutoV1(a || {});
  return {
    id: a.id || "", name: a.name || "", enabled: a.enabled !== false, cooldown: a.cooldown || 0,
    ignore_bots: a.ignore_bots !== false, retry: !!a.retry, trigger: a.trigger || "",
    conditions: (a.conditions || []).map((c) => ({ type: c.type, values: c.values || [] })),
    actions: (a.actions || []).map((x) => ({ target: "same_channel", channel_id: "", text: "", roles: [], emoji: "", name: "", amount: 10, buttons: [], ...x })),
  };
}

const AS = () => S.catalog.automations;
const condAllowed = (key, trig) => AS().conditions[key].triggers.includes(trig);
const actionAllowed = (key, trig) => !AS().actions[key].needs_message || AS().message_triggers.includes(trig);

function autoSummary(a) {
  if (!a.trigger) return t("עוד לא נבחר אירוע");
  const conds = a.conditions.length ? " · " + t("{n} תנאים", { n: a.conditions.length }) : "";
  return t("כשמישהו {what}{conds} ← {actions}", {
    what: AS().triggers[a.trigger], conds, actions: a.actions.map((x) => AS().actions[x.type].label).join(" · ") || "—",
  });
}

function valueEditor(kind, values, attrs, o) {
  if ((kind === "channels" || kind === "roles") && o) {
    const items = kind === "channels" ? o.text_channels : o.roles;
    return `<div class="multi" ${attrs} data-kind="${kind}">${items.map((it) => `
      <label class="multi-item"><input type="checkbox" value="${esc(it.id)}" ${values.includes(it.id) ? "checked" : ""}>
        <span class="no-translate">${kind === "roles" ? "@" : "# "}${esc(it.name)}</span></label>`).join("")}</div>`;
  }
  const ph = { channels: t("מזהי ערוצים, אחד בכל שורה"), roles: t("מזהי תפקידים, אחד בכל שורה"), users: t("מזהי משתמשים, אחד בכל שורה"),
    words: t("מילה או משפט בכל שורה"), emojis: t("אימוג'י בכל שורה") }[kind] || "";
  return `<textarea rows="2" class="${kind === "words" || kind === "emojis" ? "" : "ltr"}" ${attrs} data-kind="${kind}" placeholder="${esc(ph)}">${esc(values.join("\n"))}</textarea>`;
}

function viewActionCard(a, x, j, o) {
  const p = (k) => `data-act="${j}.${k}"`;
  let body = "";
  if (x.type === "send_message") {
    const targets = Object.fromEntries(Object.entries(AS().targets).filter(([k]) => k !== "same_channel" || AS().channel_triggers.includes(a.trigger)));
    body += `<div class="row"><label class="list-cell"><span class="label">${esc(t("איפה"))}</span>${sel(p("target"), targets, x.target)}</label>
      ${x.target === "channel" ? `<label class="list-cell"><span class="label">${esc(t("ערוץ"))}</span>${channelPicker(p("channel_id"), x.channel_id, o, t("בחר ערוץ"))}</label>` : ""}</div>
      <label class="list-cell wide-cell"><span class="label">${esc(t("טקסט"))}</span><textarea ${p("text")}>${esc(x.text)}</textarea></label>
      <label class="list-cell wide-cell"><span class="label">${esc(t("כפתורים מתחת להודעה (לא חובה, אחד בכל שורה)"))}</span><textarea rows="2" ${p("buttons")}>${esc((x.buttons || []).join("\n"))}</textarea>
        <span class="hint">${esc(t("לחיצה על כפתור מפעילה אוטומציות עם האירוע 'לוחץ על כפתור'."))}</span></label>`;
  }
  if (x.type === "reply") body += `<label class="list-cell wide-cell"><span class="label">${esc(t("טקסט"))}</span><textarea ${p("text")}>${esc(x.text)}</textarea></label>`;
  if (x.type === "add_roles" || x.type === "remove_roles") body += `<label class="list-cell wide-cell"><span class="label">${esc(t("תפקידים"))}</span>${valueEditor("roles", x.roles || [], p("roles"), o)}</label>`;
  if (["give_coins", "take_coins", "give_xp", "take_xp"].includes(x.type)) body += `<label class="list-cell"><span class="label">${esc(t("כמות"))}</span><input type="text" inputmode="numeric" class="ltr narrow" ${p("amount")} value="${esc(x.amount)}"></label>`;
  if (x.type === "add_reaction") body += `<label class="list-cell"><span class="label">${esc(t("אימוג'י"))}</span><input type="text" ${p("emoji")} value="${esc(x.emoji)}" maxlength="64"></label>`;
  if (x.type === "create_thread") body += `<label class="list-cell"><span class="label">${esc(t("שם השרשור"))}</span><input type="text" ${p("name")} value="${esc(x.name)}" maxlength="90"></label>`;
  return `<div class="ab-card added do">
      <div class="row"><span class="ab-icon">${ACTION_ICONS[x.type] || "▶"}</span><b class="grow">${esc(AS().actions[x.type].label)}</b>
        ${j > 0 ? `<button class="btn btn-sm btn-ghost" data-action="act-up" data-j="${j}" title="${esc(t("למעלה"))}">↑</button>` : ""}
        <button class="btn btn-sm btn-ghost" data-action="act-del" data-j="${j}" title="${esc(t("הסר"))}">🗑</button></div>
      ${body ? `<div class="ab-body">${body}</div>` : ""}
    </div>`;
}

function viewAutoEditor(a, i, o) {
  const as = AS();
  const triggerSection = a.trigger
    ? `<div class="ab-card added when"><div class="row"><span class="ab-icon">${TRIGGER_ICONS[a.trigger]}</span>
         <div class="grow"><div class="muted small">${esc(t("אירוע"))}</div><b>${esc(as.triggers[a.trigger])}</b></div>
         <button class="btn btn-sm btn-ghost" data-action="auto-trigger-clear" title="${esc(t("הסר"))}">🗑</button></div></div>`
    : `<div class="ab-grid">${Object.entries(as.triggers).map(([k, label]) => `
         <button class="ab-card pick when" data-action="auto-trigger" data-key="${k}"><span class="ab-icon">${TRIGGER_ICONS[k]}</span><b>${esc(label)}</b><span class="ab-plus">+</span></button>`).join("")}</div>`;
  const addedConds = a.conditions.map((c, ci) => {
    const def = as.conditions[c.type];
    return `<div class="ab-card added if"><div class="row"><span class="ab-icon">⏷</span><b class="grow">${esc(def.label)}</b>
        <button class="btn btn-sm btn-ghost" data-action="cond-del" data-ci="${ci}" title="${esc(t("הסר"))}">🗑</button></div>
        ${def.kind !== "none" ? `<div class="ab-body">${valueEditor(def.kind, c.values, `data-cond="${ci}"`, o)}</div>` : ""}</div>`;
  }).join("");
  const freeConds = Object.entries(as.conditions).filter(([k]) => condAllowed(k, a.trigger) && !a.conditions.some((c) => c.type === k));
  const freeActions = Object.keys(as.actions).filter((k) => actionAllowed(k, a.trigger));
  return `
    <div class="ab-top row">
      <button class="btn btn-sm btn-ghost" data-action="auto-close">← ${esc(t("לכל האוטומציות"))}</button>
      <input type="text" class="ab-name" data-ap="name" value="${esc(a.name)}" maxlength="60" placeholder="${esc(t("שם האוטומציה"))}">
      <label class="switch" title="${esc(t("פעילה"))}"><input type="checkbox" data-auto-toggle="${i}" ${a.enabled ? "checked" : ""}><span class="slider"></span></label>
    </div>
    <div class="ab-section"><span class="ab-badge when">${esc(t("כשמישהו"))}</span>${triggerSection}</div>
    ${a.trigger ? `
    <div class="ab-section"><span class="ab-badge if">${esc(t("אם"))}</span>
      <div class="muted small" style="margin-bottom:8px">${esc(t("כל התנאים צריכים להתקיים. בלי תנאים, האוטומציה רצה בכל פעם."))}</div>
      ${addedConds}
      ${freeConds.length && a.conditions.length < as.max_conditions ? `<div class="ab-grid">${freeConds.map(([k, d]) => `
        <button class="ab-card pick if" data-action="cond-add" data-key="${k}"><span class="ab-icon">⏷</span><b>${esc(d.label)}</b><span class="ab-plus">+</span></button>`).join("")}</div>` : ""}
    </div>
    <div class="ab-section"><span class="ab-badge do">${esc(t("עשה את זה"))}</span>
      ${a.actions.map((x, j) => viewActionCard(a, x, j, o)).join("")}
      ${a.actions.length < as.max_actions ? `<div class="ab-grid">${freeActions.map((k) => `
        <button class="ab-card pick do" data-action="act-add" data-key="${k}"><span class="ab-icon">${ACTION_ICONS[k]}</span><b>${esc(as.actions[k].label)}</b><span class="ab-plus">+</span></button>`).join("")}</div>` : ""}
      <span class="hint">${esc(t("משתנים: {user} {username} {server} {channel} {message} {role}"))}</span>
    </div>
    <div class="ab-section ab-options">
      <label class="row"><label class="switch"><input type="checkbox" data-ap="retry" ${a.retry ? "checked" : ""}><span class="slider"></span></label>
        <span><b>${esc(t("ניסיון חוזר בכישלון"))}</b><br><span class="muted small">${esc(t("אם פעולה נכשלת בגלל עומס של דיסקורד, הבוט ינסה שוב עד 3 פעמים (אחרי 5 שניות, 30 שניות ו-2 דקות)."))}</span></span></label>
      <label class="row"><label class="switch"><input type="checkbox" data-ap="ignore_bots" ${a.ignore_bots ? "checked" : ""}><span class="slider"></span></label><span>${esc(t("להתעלם מבוטים"))}</span></label>
      <label class="list-cell" style="max-width:320px"><span class="label">${esc(t("המתנה בין הפעלות לאותו משתמש (שניות)"))}</span>
        <input type="text" inputmode="numeric" class="ltr narrow" data-ap="cooldown" value="${esc(a.cooldown)}"></label>
    </div>` : ""}`;
}

function sel(attrs, options, value) {
  return `<select ${attrs}>${Object.entries(options).map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
}

function viewAutosInner() {
  const as = AS();
  const o = S.guild.objects;
  if (S.autoOpen !== null && S.autos[S.autoOpen]) return viewAutoEditor(S.autos[S.autoOpen], S.autoOpen, o);
  const list = S.autos.length ? S.autos.map((a, i) => `
      <div class="auto-card ${a.enabled ? "" : "disabled"}">
        <div class="row">
          <label class="switch"><input type="checkbox" data-auto-toggle="${i}" ${a.enabled ? "checked" : ""}><span class="slider"></span></label>
          <span class="ab-icon">${TRIGGER_ICONS[a.trigger] || "⚡"}</span>
          <div class="grow"><b class="no-translate">${esc(a.name || t("אוטומציה {n}", { n: i + 1 }))}</b><div class="muted small">${esc(autoSummary(a))}</div></div>
          <button class="btn btn-sm" data-action="auto-edit" data-i="${i}">${esc(t("ערוך"))}</button>
          <button class="btn btn-sm btn-ghost" data-action="auto-dup" data-i="${i}" title="${esc(t("שכפל"))}">⧉</button>
          <button class="btn btn-sm btn-ghost" data-action="auto-del" data-i="${i}" title="${esc(t("מחק"))}">🗑</button>
        </div>
      </div>`).join("") : `<div class="muted small" style="padding:8px 0">${esc(t("עוד אין אוטומציות. בחר תבנית ולחץ \"צור\"."))}</div>`;
  return `
    <div class="row" style="margin-bottom:10px">
      <span class="muted small">${S.autos.length} / ${as.max_automations}</span><div class="spacer"></div>
      <span class="no-dirty">${sel('id="auto-template" style="width:auto"', Object.fromEntries(Object.entries(AUTO_TEMPLATES).map(([k, v]) => [k, t(v.label)])), "blank")}</span>
      <button class="btn btn-primary btn-sm" data-action="auto-new" ${S.autos.length >= as.max_automations ? "disabled" : ""}>+ ${esc(t("צור אוטומציה"))}</button>
    </div>${list}`;
}

function rerenderAutos() { const box = $("#autos"); if (box) box.innerHTML = viewAutosInner(); }

function readValues(el) {
  if (el.classList.contains("multi")) return [...el.querySelectorAll("input:checked")].map((i) => i.value);
  return el.value.split("\n").map((s) => s.trim()).filter(Boolean);
}

function setAutoValue(el) {
  const a = S.autos[S.autoOpen];
  if (!a) return;
  const host = el.closest("[data-cond], [data-act], [data-ap]") || el;
  if (host.dataset.ap) {
    a[host.dataset.ap] = el.type === "checkbox" ? el.checked : el.value;
  } else if (host.dataset.cond !== undefined) {
    a.conditions[+host.dataset.cond].values = readValues(host);
  } else if (host.dataset.act) {
    const [j, k] = host.dataset.act.split(".");
    const x = a.actions[+j];
    if (k === "roles") x.roles = readValues(host);
    else if (k === "buttons") x.buttons = readValues(host);
    else x[k] = el.value;
    if (k === "target") rerenderAutos();
  }
}

/* ===== module cards, categories, module pages ===== */
function moduleWarnings(m, g) {
  const out = [];
  const intents = S.status.intents;
  if (intents && m.intents.length) {
    const missing = m.intents.filter((i) => !intents[i]);
    if (missing.length) out.push({ intent: true, badge: "חסר Intent", text: t("צריך להפעיל {list}. לחץ 'הפעל אוטומטית' למעלה.", { list: missing.map((i) => S.catalog.intent_labels[i]).join(" + ") }) });
  }
  if (g && g.objects && m.permissions.length) {
    const have = new Set(g.objects.bot_permissions);
    const missing = have.has("administrator") ? [] : m.permissions.filter((p) => !have.has(p));
    if (missing.length) out.push({ badge: t("חסרות הרשאות"), text: t("לבוט חסרות בשרת הזה ההרשאות: {list}. צור קישור הזמנה חדש בעמוד 'הרשאות והזמנה'.", { list: missing.map(permLabel).join(", ") }) });
  }
  return out;
}

function intentBanner(mods) {
  const intents = S.status.intents;
  if (!intents || !S.catalog) return "";
  const missing = [...new Set(mods.flatMap((m) => m.intents).filter((i) => !intents[i]))];
  if (!missing.length) return "";
  const list = missing.map((i) => S.catalog.intent_labels[i]).join(" + ");
  const portal = S.status.app_id ? `<a class="btn btn-sm btn-ghost" href="https://discord.com/developers/applications/${esc(S.status.app_id)}/bot" target="_blank" rel="noopener">${esc(t("פתח Developer Portal"))}</a>` : "";
  return `<div class="notice warn row"><span class="grow">${esc(t("לחלק מהמודולים חסר {list}. אפשר להפעיל אוטומטית בלחיצה (הבוט יתחבר מחדש), או ידנית ב-Developer Portal, בלשונית Bot.", { list }))}</span>
    <button class="btn btn-sm btn-primary" data-action="enable-intents">${esc(t("הפעל אוטומטית"))}</button>${portal}</div>`;
}

function moduleToggle(m, g) {
  return `<label class="switch" title="${m.status === "soon" ? "המתג נשמר, והמודול יעבוד כשייבנה" : ""}">
      <input type="checkbox" data-module="${m.key}" ${g.modules[m.key] ? "checked" : ""}><span class="slider"></span></label>`;
}

function noGuild() {
  return `<div class="empty"><h3>אין שרת נבחר</h3>
    <p>${t('הזמן את הבוט לשרת (בעמוד <a href="#" data-nav="permissions">הרשאות והזמנה</a>) והפעל אותו. השרתים יופיעו כאן אוטומטית.')}</p></div>`;
}

function viewCategory(catKey) {
  if (!S.catalog) return `<div class="empty">טוען…</div>`;
  if (!S.guild) return noGuild();
  const g = S.guild;
  const cat = S.catalog.categories.find((c) => c.key === catKey);
  const cards = S.catalog.modules.filter((m) => m.category === catKey).map((m) => {
    const on = !!g.modules[m.key];
    const warns = moduleWarnings(m, g);
    return `
      <div class="card module-card clickable" data-open="${m.key}">
        <div class="module-head">
          <div class="grow">
            <h3>${esc(m.label)}</h3>
            <p class="desc">${esc(m.description)}</p>
          </div>
          ${moduleToggle(m, g)}
        </div>
        <div class="row">
          ${m.status === "soon" ? `<span class="badge badge-soon">בקרוב</span>` : on ? `<span class="badge badge-ok">פעיל</span>` : `<span class="badge">כבוי</span>`}
          ${warns.map((w) => `<span class="badge badge-danger" title="${esc(w.text)}">${esc(w.badge)}</span>`).join("")}
          <div class="spacer"></div>
          <span class="muted small">${m.status === "soon" ? "" : "הגדרות ←"}</span>
        </div>
      </div>`;
  }).join("");
  return `
    <div class="row section-sub">
      <span>${I18N.lang === "en" ? "" : esc(cat ? cat.en : "") + " · "}${esc(t("שרת:"))} <b class="no-translate">${esc(g.name)}</b></span>
      ${g.objects ? "" : `<span class="badge badge-soon">הבוט כבוי או לא בשרת: בחירה מרשימות לא זמינה</span>`}
      <div class="spacer"></div>
      ${guildBotSelect(g)}
      ${guildLangSelect()}
      ${g.present === false || !g.objects ? `<button class="btn btn-sm btn-ghost" data-action="forget-guild">הסר פרופיל שרת</button>` : ""}
    </div>
    ${intentBanner(S.catalog.modules.filter((m) => m.category === catKey && m.status !== "soon"))}
    <div class="grid grid-cards">${cards}</div>`;
}

function viewModulePage(key) {
  const m = mod(key);
  if (!m) return `<div class="empty">המודול לא נמצא</div>`;
  if (!S.guild) return noGuild();
  const g = S.guild;
  const cat = S.catalog.categories.find((c) => c.key === m.category);
  const warns = moduleWarnings(m, g);
  const fields = m.fields.length ? `
      <div class="card section" data-module-form="${m.key}">
        <div class="form-grid">${m.fields.map((f) => renderField(f, g.config[m.key][f.key], g)).join("")}</div>
      </div>` : "";
  const soon = m.status === "soon" ? `<div class="card section empty"><h3>המודול הזה עוד לא נבנה</h3><p>אפשר כבר להפעיל את המתג: הבחירה נשמרת, והמודול יעבוד כשייבנה.</p></div>` : "";
  return `
    <div class="row crumbs"><span><a href="#" data-nav="cat:${m.category}">${esc(cat ? cat.label : "")}</a> › ${esc(m.label)} · ${esc(t("שרת:"))} <b class="no-translate">${esc(g.name)}</b></span><div class="spacer"></div>${guildBotSelect(g)}${guildLangSelect()}</div>
    <div class="card section module-hero">
      <div class="module-head">
        <div class="grow">
          <h2>${esc(m.label)} ${m.status === "soon" ? `<span class="badge badge-soon">בקרוב</span>` : ""}</h2>
          <p class="desc">${esc(m.description)}</p>
        </div>
        ${moduleToggle(m, g)}
      </div>
      ${m.commands.length ? `<div class="row" style="margin-top:12px"><span class="label">פקודות:</span>${m.commands.map((c) => `<span class="badge ltr">${esc(c)}</span>`).join("")}</div>` : ""}
      ${m.permissions.length ? `<div class="row" style="margin-top:8px"><span class="label">הרשאות שהבוט צריך:</span>${m.permissions.map((p) => `<span class="badge">${esc(permLabel(p))}</span>`).join("")}</div>` : ""}
    </div>
    ${intentBanner([m])}
    ${warns.filter((w) => !w.intent).map((w) => `<div class="notice warn">${esc(w.text)}</div>`).join("")}
    ${key === "claude_chat" ? viewClaudeStatus() : ""}
    ${soon}${fields}
    ${key === "economy" ? `<div class="card section"><div class="row"><h3>💰 העשירים בשרת</h3><div class="spacer"></div><button class="btn btn-sm" data-action="eco-reload">רענן</button></div><div id="eco-list" class="muted small" style="margin-top:10px">טוען…</div></div>` : ""}
    ${key === "levels" ? `<div class="card section"><div class="row"><h3>🏆 לוח המובילים</h3><div class="spacer"></div><button class="btn btn-sm" data-action="levels-reload">רענן</button></div><div id="lb-list" class="muted small" style="margin-top:10px">טוען…</div></div>` : ""}
    ${moduleExtras(key)}
    ${key === "warnings" ? `<div class="card section"><div class="row"><h3>אזהרות אחרונות בשרת</h3><div class="spacer"></div><button class="btn btn-sm" data-action="warnings-reload">רענן</button></div><div id="warn-list" class="muted small" style="margin-top:10px">טוען…</div></div>` : ""}`;
}

/* ===== extras for the newer modules: buttons and data tables ===== */
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(I18N.lang === "he" ? "he-IL" : I18N.lang);
const userCell = (id, name) => `<td class="no-translate">${esc(name || id)}${id ? `<div class="muted small ltr">${esc(id)}</div>` : ""}</td>`;
const EXTRA_TABLES = {
  birthdays: { title: "🎂 ימי הולדת רשומים", url: "birthdays", empty: "עוד לא נרשמו ימי הולדת.",
    head: ["משתמש", "תאריך", ""],
    row: (r) => `<tr>${userCell(r.user_id, r.user_name)}<td class="ltr">${String(r.day).padStart(2, "0")}/${String(r.month).padStart(2, "0")}${r.year ? "/" + r.year : ""}</td>
      <td><button class="btn btn-sm btn-ghost" data-action="bday-delete" data-bid="${r.id}">מחק</button></td></tr>` },
  giveaways: { title: "🎉 הגרלות", url: "giveaways", empty: "עוד אין הגרלות. מתחילים עם ‎/giveaway start בדיסקורד.",
    head: ["#", "פרס", "מסתיים", "משתתפים", "סטטוס"],
    row: (r) => `<tr><td>${r.id}</td><td class="no-translate">${esc(r.prize)}</td><td class="small">${fmtDate(r.ends)}</td><td>${r.entries}</td>
      <td>${r.ended ? `<span class="badge">${esc(t("הסתיימה"))}</span>` : `<span class="badge badge-ok">${esc(t("פעילה"))}</span>`}</td></tr>` },
  invite_tracker: { title: "🏆 המזמינים המובילים", url: "invites", empty: "עוד אין הזמנות במעקב.",
    head: ["משתמש", "הצטרפו", "עזבו", "סה\"כ"],
    row: (r) => `<tr>${userCell(r.user_id, r.user_name)}<td>${r.joins}</td><td>${r.leaves}</td><td><b>${r.total}</b></td></tr>` },
  ticketing: { title: "🎫 כרטיסים", url: "tickets", empty: "עוד לא נפתחו כרטיסים.",
    head: ["#", "פתח", "נפתח", "סטטוס"],
    row: (r) => `<tr><td>${r.number}</td>${userCell(r.user_id, r.user_name)}<td class="small">${fmtDate(r.created)}</td>
      <td>${r.closed ? `<span class="badge">${esc(t("נסגר"))}</span>` : `<span class="badge badge-ok">${esc(t("פתוח"))}</span>`}</td></tr>` },
  suggestions: { title: "💡 הצעות אחרונות", url: "suggestions", empty: "עוד אין הצעות.",
    head: ["#", "הצעה", "מציע", "סטטוס"],
    row: (r) => `<tr><td>${r.number}</td><td class="no-translate">${esc(r.text.slice(0, 120))}</td>${userCell(r.user_id, r.user_name)}<td>${esc(r.status)}</td></tr>` },
};

function moduleExtras(key) {
  let out = "";
  if (key === "embed_messages") out += `<div class="card section"><div class="row"><h3>${esc(t("תצוגה מקדימה"))}</h3><div class="spacer"></div>
    <span class="muted small">${esc(t("שמור קודם, ואז פרסם. ההודעה נשלחת לערוץ שבחרת."))}</span>
    <button class="btn btn-primary" data-action="embed-send">${esc(t("פרסם"))}</button></div>
    <div id="embed-preview" class="embed-preview-wrap no-translate" style="margin-top:12px"></div></div>`;
  if (key === "ticketing") out += `<div class="card section row"><span class="grow muted">${esc(t("אחרי השמירה, פרסם את ההודעה עם הכפתור בערוץ שבחרת."))}</span>
    <button class="btn btn-primary" data-action="ticket-panel">${esc(t("פרסם כפתור"))}</button></div>`;
  if (key === "social_alerts") out += `<div class="notice">${esc(t("TikTok ו-X (טוויטר) לא נתמכים: אין להם API או פיד חינמי. בפעם הראשונה הבוט רק זוכר מה כבר קיים, ומתריע רק על תוכן חדש."))}</div>
    <div class="card section"><div class="row"><h3>${esc(t("בדיקת המקורות"))}</h3><div class="spacer"></div>
      <button class="btn btn-sm" data-action="social-reload">${esc(t("רענן"))}</button></div>
      <p class="desc">${esc(t("שמור קודם. 'בדוק' מראה מה המקור מחזיר עכשיו, ו'שלח בדיקה' שולח התראה לדוגמה לערוץ."))}</p>
      <div id="social-status" class="muted small" style="margin-top:10px">${esc(t("טוען…"))}</div></div>`;
  if (key === "birthdays") out += `<div class="card section"><h3>${esc(t("הוספת יום הולדת"))}</h3>
    <div class="row" style="margin-top:10px;flex-wrap:wrap">
      <input type="text" id="bday-name" placeholder="${esc(t("שם או מזהה משתמש"))}" style="max-width:240px">
      <input type="text" id="bday-date" class="ltr" placeholder="24/12" style="max-width:140px">
      <button class="btn btn-primary" data-action="bday-add">${esc(t("הוסף"))}</button></div></div>`;
  if (key === "tools") out += `<div class="card section">
    <div class="row"><h3>${esc(t("הוספה לחשבון (Add App)"))}</h3></div>
    <p class="desc">${esc(t("אפשר להוסיף את הבוט לחשבון דיסקורד, ואז הפקודות האלה עובדות גם בהודעות פרטיות ובכל שרת, גם בשרתים שהבוט לא נמצא בהם. לחץ כדי להפעיל את זה ולקבל את הקישור."))}</p>
    <div class="row" style="margin-top:10px;flex-wrap:wrap">
      <button class="btn btn-primary" data-action="user-install">${esc(t("הפעל וקבל קישור"))}</button>
      <input type="text" id="user-install-link" class="ltr grow" readonly placeholder="https://discord.com/oauth2/authorize?…" style="min-width:260px">
      <button class="btn" data-action="copy-user-install">${esc(t("העתק"))}</button>
    </div>
    <p class="muted small" style="margin-top:8px">${esc(t("פתח את הקישור בדפדפן ובחר 'Add to My Apps'. /gif צריך ffmpeg במחשב הזה (winget install ffmpeg)."))}</p></div>`;
  const tbl = EXTRA_TABLES[key];
  if (tbl) out += `<div class="card section"><div class="row"><h3>${esc(t(tbl.title))}</h3><div class="spacer"></div>
    <button class="btn btn-sm" data-action="extra-reload">${esc(t("רענן"))}</button></div><div id="extra-list" class="muted small" style="margin-top:10px">${esc(t("טוען…"))}</div></div>`;
  return out;
}

async function loadExtraTable(key) {
  const tbl = EXTRA_TABLES[key], box = $("#extra-list");
  if (!tbl || !box || !S.guildId) return;
  let rows;
  try { rows = await api("GET", `/guilds/${S.guildId}/${tbl.url}`); } catch (e) { box.textContent = errMessage(e); return; }
  if (!rows.length) { box.textContent = t(tbl.empty); return; }
  box.classList.remove("muted", "small");
  box.innerHTML = `<table class="table"><thead><tr>${tbl.head.map((h) => `<th>${esc(t(h))}</th>`).join("")}</tr></thead><tbody>${rows.map(tbl.row).join("")}</tbody></table>`;
}

async function loadSocialStatus() {
  const box = $("#social-status");
  if (!box || !S.guildId) return;
  let rows;
  try { rows = await api("GET", `/guilds/${S.guildId}/social/status`); } catch (e) { box.textContent = errMessage(e); return; }
  if (!rows.length) { box.textContent = t("עוד אין מקורות שמורים. הוסף שורה למעלה ושמור."); return; }
  box.classList.remove("muted", "small");
  box.innerHTML = `<table class="table"><thead><tr><th>${esc(t("רשת"))}</th><th>${esc(t("מקור"))}</th><th>${esc(t("בדיקה אחרונה"))}</th><th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.kind)}</td><td class="no-translate ltr">${esc(r.source)}</td>
      <td>${r.at ? `<span class="badge ${r.ok ? "badge-ok" : "badge-danger"}">${r.ok ? "✓" : "✕"}</span> <span class="small">${esc(r.msg || "")}</span><div class="muted small">${fmtDate(r.at)}</div>` : `<span class="muted small">${esc(t("עוד לא נבדק"))}</span>`}</td>
      <td style="white-space:nowrap"><button class="btn btn-sm" data-action="social-test" data-idx="${r.index}">${esc(t("בדוק"))}</button>
        <button class="btn btn-sm btn-ghost" data-action="social-send" data-idx="${r.index}">${esc(t("שלח בדיקה"))}</button></td></tr>`).join("")}
    </tbody></table>`;
}

function embedPreviewHTML(c) {
  const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : "";
  const md = (s) => esc(s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>").replace(/__(.+?)__/g, "<u>$1</u>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
  const color = /^#[0-9a-f]{6}$/i.test(c.color || "") ? c.color : "#6D5DFC";
  const fields = (c.fields || []).filter((f) => f.name && f.value);
  const empty = !c.title && !c.description && !c.author_name && !fields.length;
  return `
    ${c.content ? `<div class="emb-content">${md(c.content)}</div>` : ""}
    ${empty ? `<div class="muted small">${esc(t("ה-Embed ריק. מלא כותרת או תוכן."))}</div>` : `
    <div class="emb" style="border-inline-start-color:${esc(color)}">
      <div class="emb-main">
        ${c.author_name ? `<div class="emb-author">${safeUrl(c.author_icon) ? `<img src="${esc(c.author_icon)}" alt="">` : ""}${esc(c.author_name)}</div>` : ""}
        ${c.title ? `<div class="emb-title">${safeUrl(c.url) ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</div>` : ""}
        ${c.description ? `<div class="emb-desc">${md(c.description)}</div>` : ""}
        ${fields.length ? `<div class="emb-fields">${fields.map((f) => `<div class="emb-field ${f.inline ? "inline" : ""}"><b>${esc(f.name)}</b><div>${md(f.value)}</div></div>`).join("")}</div>` : ""}
        ${safeUrl(c.image_url) ? `<img class="emb-image" src="${esc(c.image_url)}" alt="">` : ""}
        ${c.footer || c.timestamp ? `<div class="emb-footer">${esc(c.footer || "")}${c.footer && c.timestamp ? " • " : ""}${c.timestamp ? esc(new Date().toLocaleString()) : ""}</div>` : ""}
      </div>
      ${safeUrl(c.thumbnail_url) ? `<img class="emb-thumb" src="${esc(c.thumbnail_url)}" alt="">` : ""}
    </div>`}`;
}

function refreshEmbedPreview() {
  const box = $("#embed-preview");
  if (box && S.page === "mod:embed_messages") box.innerHTML = embedPreviewHTML(collectModuleForm("embed_messages"));
}

function afterModuleRender(key) {
  loadExtraTable(key);
  if (key === "embed_messages") refreshEmbedPreview();
  if (key === "social_alerts") loadSocialStatus();
  if (key === "levels") loadLeaderboard();
  if (key === "economy") loadRichList();
  if (key === "warnings") loadWarnings();
  if (key === "claude_chat") loadClaudeUsage();
}

async function loadWarnings() {
  const box = $("#warn-list");
  if (!box || !S.guildId) return;
  let rows;
  try { rows = await api("GET", `/guilds/${S.guildId}/warnings`); } catch (e) { box.textContent = errMessage(e); return; }
  if (!rows.length) { box.innerHTML = "אין אזהרות."; return; }
  box.classList.remove("muted", "small");
  box.innerHTML = `<table class="table"><thead><tr><th>#</th><th>משתמש</th><th>סיבה</th><th>על ידי</th><th>מתי</th><th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${r.id}</td><td class="no-translate">${esc(r.user_name)}<div class="muted small ltr">${esc(r.user_id)}</div></td>
      <td class="no-translate">${esc(r.reason)}</td><td class="no-translate">${esc(r.moderator_name)}</td><td class="small">${new Date(r.created * 1000).toLocaleString("he-IL")}</td>
      <td><button class="btn btn-sm btn-ghost" data-action="warning-delete" data-wid="${r.id}">מחק</button></td></tr>`).join("")}
    </tbody></table>`;
}

async function loadLeaderboard() {
  const box = $("#lb-list");
  if (!box || !S.guildId) return;
  let rows;
  try { rows = await api("GET", `/guilds/${S.guildId}/levels?limit=50`); } catch (e) { box.textContent = errMessage(e); return; }
  if (!rows.length) { box.innerHTML = "עוד אין XP לאף אחד. ה-XP מתחיל להיצבר כשהמודול פעיל והבוט מחובר."; return; }
  box.classList.remove("muted", "small");
  const medal = (r) => ({ 1: "🥇", 2: "🥈", 3: "🥉" }[r] || `#${r}`);
  box.innerHTML = `<table class="table"><thead><tr><th>דירוג</th><th>משתמש</th><th>רמה</th><th>התקדמות</th><th>XP</th><th>הודעות</th><th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${medal(r.rank)}</td><td class="no-translate">${esc(r.user_name || r.user_id)}<div class="muted small ltr">${esc(r.user_id)}</div></td>
      <td><b>${r.level}</b></td>
      <td><div class="xpbar"><span style="width:${Math.round((r.into / r.needed) * 100)}%"></span></div><div class="muted small ltr">${r.into} / ${r.needed}</div></td>
      <td class="ltr">${r.xp.toLocaleString()}</td><td class="ltr">${r.messages.toLocaleString()}</td>
      <td><button class="btn btn-sm btn-ghost" data-action="xp-reset" data-uid="${esc(r.user_id)}" data-name="${esc(r.user_name || r.user_id)}">אפס</button></td></tr>`).join("")}
    </tbody></table>`;
}

async function loadRichList() {
  const box = $("#eco-list");
  if (!box || !S.guildId) return;
  let rows;
  try { rows = await api("GET", `/guilds/${S.guildId}/economy?limit=50`); } catch (e) { box.textContent = errMessage(e); return; }
  if (!rows.length) { box.innerHTML = "עוד אין ארנקים. הם נוצרים כשמישהו משתמש בפקודה של הכלכלה."; return; }
  const cfg = S.guild.config.economy;
  box.classList.remove("muted", "small");
  box.innerHTML = `<table class="table"><thead><tr><th>#</th><th>משתמש</th><th>יתרה</th><th>רצף יומי</th><th>פריטים</th><th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${r.rank}</td><td class="no-translate">${esc(r.user_name || r.user_id)}<div class="muted small ltr">${esc(r.user_id)}</div></td>
      <td>${esc(cfg.currency_icon)} <b class="ltr">${r.balance.toLocaleString()}</b></td><td>${r.streak ? "🔥 " + r.streak : "—"}</td><td>${r.items}</td>
      <td><button class="btn btn-sm btn-ghost" data-action="eco-reset" data-uid="${esc(r.user_id)}" data-name="${esc(r.user_name || r.user_id)}">אפס</button></td></tr>`).join("")}
    </tbody></table>`;
}

function collectModuleForm(key) {
  const form = document.querySelector(`[data-module-form="${key}"]`);
  const config = {};
  if (!form) return config;
  const readCell = (el, type) => (type === "lines" ? el.value.split("\n").map((s) => s.trim()).filter(Boolean) : type === "bool" ? el.checked : el.value);
  form.querySelectorAll("[data-field]").forEach((el) => {
    const f = el.dataset.field, t = el.dataset.ftype;
    if (t === "bool") config[f] = el.checked;
    else if (t === "lines") config[f] = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (t === "ids") config[f] = el.value.split(/[\s,]+/).filter(Boolean);
    else if (t === "multi") config[f] = [...el.querySelectorAll("input:checked")].map((i) => i.value);
    else if (t === "rule") {
      const v = { on: el.querySelector(".rule-on").checked, action: el.querySelector(".rule-action").value };
      el.querySelectorAll("[data-pkey]").forEach((p) => { v[p.dataset.pkey] = readCell(p, p.dataset.ptype); });
      config[f] = v;
    } else if (t === "list") {
      config[f] = [...el.querySelectorAll(".list-row")].map((row) => {
        const item = {};
        row.querySelectorAll("[data-ikey]").forEach((c) => { item[c.dataset.ikey] = readCell(c, c.dataset.itype); });
        return item;
      });
    } else if (t === "commands") {
      config[f] = [...el.querySelectorAll(".cmd-row")].map((r) => ({
        name: r.querySelector(".cmd-name").value, response: r.querySelector(".cmd-resp").value,
        role_action: r.querySelector(".cmd-raction").value, role_id: r.querySelector(".cmd-roleid").value,
        kind: (r.querySelector(".cmd-kind") || {}).value || "prefix", send_mode: (r.querySelector(".cmd-mode") || {}).value || "channel",
        delete_trigger: !!(r.querySelector(".cmd-del") || {}).checked, description: (r.querySelector(".cmd-desc") || {}).value || "",
        arg_hint: (r.querySelector(".cmd-hint") || {}).value || "",
      }));
    } else if (t === "automations") config[f] = S.autos;
    else if (t !== "reaction_roles") config[f] = el.value;
  });
  return config;
}

/* ===== Claude chat status ===== */
function viewClaudeStatus() {
  const set = S.settings ? S.settings.settings : {};
  const prov = (S.guild && S.guild.config.claude_chat && S.guild.config.claude_chat.provider) || "claude";
  const PROV = { claude: ["anthropic_key", "Anthropic API Key"], openai: ["openai_key", "OpenAI API Key"], grok: ["xai_key", "xAI API Key"] }[prov] || ["anthropic_key", "Anthropic API Key"];
  const key = S.settings ? S.settings.secrets[PROV[0]].set : false;
  const check = (ok, yes, no) => `<div class="row"><span class="badge ${ok ? "badge-ok" : "badge-danger"}">${ok ? "✓" : "✕"}</span><span>${ok ? yes : no}</span></div>`;
  return `
    <div class="card section">
      <h3>מצב הצ'אט</h3>
      <div class="grid" style="grid-template-columns:1fr 1fr;margin-top:10px">
        <div class="grid" style="gap:8px">
          ${check(key, t("{key} שמור", { key: PROV[1] }), t("חסר {key}", { key: PROV[1] }))}
          ${check(!!set.allowed_user_id, `משתמש מורשה: <span class="ltr">${esc(set.allowed_user_id)}</span>`, "לא הוגדר User ID מורשה: הבוט לא יענה לאף אחד")}
          <div class="row"><span class="badge">#</span><span>${set.allowed_channel_id ? `ערוץ מורשה: <span class="ltr">${esc(set.allowed_channel_id)}</span>` : "כל ערוץ (בתיוג) והודעות פרטיות"}</span></div>
          <div class="muted small">${t('משנים את אלה בעמוד <a href="#" data-nav="connection">חיבור והגדרות</a>.')}</div>
        </div>
        <div id="claude-usage"><div class="muted small">טוען שימוש…</div></div>
      </div>
    </div>`;
}

async function loadClaudeUsage() {
  let data;
  try { data = await api("GET", "/claude/usage"); } catch { return; }
  const box = $("#claude-usage");
  if (!box) return;
  const today = data.days[0];
  const week = data.days.reduce((a, d) => ({ m: a.m + d.messages, c: a.c + d.est_cost_usd }), { m: 0, c: 0 });
  box.innerHTML = `
    <div class="grid" style="grid-template-columns:1fr 1fr">
      <div><div class="stat-label">תשובות היום</div><div class="stat-value">${today.messages}</div></div>
      <div><div class="stat-label">7 ימים</div><div class="stat-value">${week.m}</div></div>
    </div>
    <div class="muted small" style="margin-top:8px">עלות משוערת ל-7 ימים: <span class="ltr">${"$" + week.c.toFixed(4)}</span>
      (לפי מחירון המודלים. החיוב האמיתי מופיע ב-Anthropic Console)</div>`;
}

/* ===== quick search (Ctrl+K) ===== */
let searchSel = 0;
function searchIndex() {
  const items = GENERAL_NAV.map((n) => ({ label: n.label, sub: "כללי", page: n.page }));
  if (S.catalog) {
    S.catalog.categories.forEach((c) => items.push({ label: c.label, sub: c.en, page: "cat:" + c.key }));
    S.catalog.modules.forEach((m) => {
      const cat = S.catalog.categories.find((c) => c.key === m.category);
      items.push({ label: m.label, sub: `${cat ? cat.label : ""} · ${m.description}`, page: "mod:" + m.key });
      m.fields.forEach((f) => items.push({ label: f.label, sub: `${m.label} › הגדרה`, page: "mod:" + m.key }));
      m.commands.forEach((c) => items.push({ label: c, sub: `${m.label} › פקודה`, page: "mod:" + m.key }));
    });
  }
  return items;
}

function openSearch() {
  $("#search").classList.remove("hidden");
  const input = $("#search-input");
  input.value = "";
  searchSel = 0;
  renderSearch();
  input.focus();
}
function closeSearch() { $("#search").classList.add("hidden"); }

function searchResults() {
  const q = $("#search-input").value.trim().toLowerCase();
  const all = searchIndex();
  if (!q) return all.slice(0, 12);
  return all.filter((i) => (i.label + " " + i.sub).toLowerCase().includes(q)).slice(0, 12);
}

function renderSearch() {
  const res = searchResults();
  searchSel = Math.min(searchSel, Math.max(0, res.length - 1));
  $("#search-results").innerHTML = res.length ? res.map((r, i) => `
      <div class="search-item ${i === searchSel ? "sel" : ""}" data-search-page="${esc(r.page)}">
        <b>${esc(r.label)}</b><span class="muted small">${esc(r.sub)}</span></div>`).join("")
    : `<div class="muted small" style="padding:12px">לא נמצאו תוצאות</div>`;
}

/* ===== actions ===== */
const actions = {
  async power(btn) {
    const running = ["connecting", "connected", "reconnecting"].includes(S.status.state);
    await guarded(btn, async () => onStatus(await api("POST", running ? "/bot/stop" : "/bot/start")));
  },
  "clear-logs"() { S.logs = []; renderLogs(); },
  "toggle-show"(btn) {
    const input = document.getElementById(btn.dataset.target);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "הסתר" : "הצג";
  },
  async "save-secret"(btn) {
    const name = btn.dataset.name;
    const input = document.getElementById("secret-" + name);
    if (!input.value.trim()) { toast("השדה ריק", "error"); return; }
    await guarded(btn, async () => {
      await api("PUT", "/secrets/" + name, { value: input.value });
      input.value = "";
      S.settings = await api("GET", "/settings");
      render();
      toast("נשמר", "ok");
    });
  },
  async "test-secret"(btn) {
    await guarded(btn, async () => toast((await api("POST", `/secrets/${btn.dataset.name}/test`)).message, "ok"));
  },
  async "delete-secret"(btn) {
    if (!(await confirmDialog(t("למחוק את {name}?", { name: btn.dataset.label }), "הערך יימחק מ-Windows Credential Manager. כדי להשתמש בו שוב, תצטרך להדביק אותו מחדש.", "מחק"))) return;
    await guarded(btn, async () => {
      await api("DELETE", "/secrets/" + btn.dataset.name);
      S.settings = await api("GET", "/settings");
      render();
      toast("נמחק", "ok");
    });
  },
  async "save-ids"(btn) {
    await guarded(btn, async () => {
      S.settings = await api("PUT", "/settings", {
        allowed_user_id: $("#allowed_user_id").value.trim(),
        allowed_channel_id: $("#allowed_channel_id").value.trim(),
      });
      toast("נשמר", "ok");
    });
  },
  async "save-profile"(btn) {
    const body = {};
    const name = $("#bot-username").value.trim();
    if (S.profile && name !== S.profile.username) body.username = name;
    if (S.avatarDraft) body.avatar = S.avatarDraft;
    if (S.bannerDraft) body.banner = S.bannerDraft;
    const pr = presenceDraft();
    const presenceChanged = JSON.stringify(pr) !== JSON.stringify(S.profile.presence);
    if (!Object.keys(body).length && !presenceChanged) { toast("אין שינויים"); return; }
    await guarded(btn, async () => {
      if (presenceChanged) S.profile.presence = await api("PUT", "/presence", pr);
      if (Object.keys(body).length) S.profile = await api("PATCH", "/profile", body);
      S.avatarDraft = null; S.bannerDraft = null; S.presenceDraft = null;
      render();
      toast("פרופיל הבוט עודכן", "ok");
    });
  },
  "discard-avatar"() { S.avatarDraft = null; render(); },
  async "avatar-use-logo"(btn) {
    // puts the app logo in as the new picture (a draft: it reaches Discord only after Save)
    const blob = await (await fetch("logo.png")).blob();
    const data = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
    if (btn.dataset.scope === "server") { S.spAvatar = data; $("#main").innerHTML = viewProfile(); }
    else { S.avatarDraft = data; render(); }
    toast(t("הלוגו נבחר. לחץ שמור כדי להחיל אותו על הבוט."), "ok");
  },
  "discard-banner"() { S.bannerDraft = null; render(); },
  "perm-recommended"() { S.permSel = new Set(S.catalog.recommended_permissions); render(); },
  "perm-none"() { S.permSel = new Set(); render(); },
  "extra-reload"() { loadExtraTable(S.page.slice(4)); },
  "chan-open"(btn) { const combo = btn.closest(".chan-combo"); if (combo.querySelector(".chan-pop")) combo.querySelector(".chan-pop").remove(); else openChanCombo(combo); },
  "chan-pick"(btn) { setChanValue(btn.closest(".chan-combo"), btn.dataset.id); },
  async "chan-create"(btn) {
    const combo = btn.closest(".chan-combo");
    const kind = combo.dataset.kind;
    await guarded(btn, async () => {
      const ch = await api("POST", `/guilds/${S.guildId}/channels`, { name: slugChannel(btn.dataset.name, kind), kind });
      const list = S.guild.objects[combo.dataset.list] || (S.guild.objects[combo.dataset.list] = []);
      list.push({ id: ch.id, name: ch.name, category: "" });
      setChanValue(combo, ch.id);
      toast(t("הערוץ {name} נוצר", { name: (kind === "voice" ? "🔊" : "#") + ch.name }), "ok");
    });
  },
  async "sp-save"(btn) {
    const body = { nick: $("#sp-nick").value, bio: $("#sp-bio").value };
    if (S.spAvatar) body.avatar = S.spAvatar;
    if (S.spBanner) body.banner = S.spBanner;
    const pr = presenceDraft();
    const presenceChanged = S.profile && JSON.stringify(pr) !== JSON.stringify(S.profile.presence);
    await guarded(btn, async () => {
      if (presenceChanged) S.profile.presence = await api("PUT", "/presence", pr);
      await api("PUT", `/guilds/${S.guildId}/member-profile`, body);
      toast("הפרופיל בשרת הזה עודכן", "ok");
      await loadServerProfile();
    });
  },
  "profile-scope"(btn) {
    S.profileScope = btn.dataset.scope; S.spAvatar = null; S.spBanner = null; render();
    if (btn.dataset.scope === "character" && (!S.persona || S.persona.gid !== S.guildId)) loadPersona();
  },
  "sp-discard"(btn) { if (btn.dataset.field === "banner") S.spBanner = null; else S.spAvatar = null; $("#main").innerHTML = viewProfile(); },
  async "sp-reset"(btn) {
    await guarded(btn, async () => {
      await api("PUT", `/guilds/${S.guildId}/member-profile`, { reset: [btn.dataset.field] });
      await loadServerProfile();
    });
  },
  async "account-add"(btn) {
    const token = $("#acc-token").value.trim();
    if (!token) { toast("השדה ריק", "error"); return; }
    await guarded(btn, async () => {
      await api("POST", "/accounts", { label: $("#acc-label").value.trim(), token });
      S.status = await api("GET", "/status");
      render();
      toast("הבוט נוסף", "ok");
    });
  },
  async "account-remove"(btn) {
    if (!(await confirmDialog(t("להסיר את {name}?", { name: btn.dataset.label }), "הבוט הזה ינותק והטוקן שלו יימחק מהאפליקציה. השרתים שהוא הפעיל יחזרו לבוט הראשי (אם הוא נמצא בהם). הנתונים של השרתים לא נמחקים.", "הסר"))) return;
    await guarded(btn, async () => {
      await api("DELETE", "/accounts/" + btn.dataset.id);
      S.status = await api("GET", "/status");
      render();
    });
  },
  "social-reload"() { loadSocialStatus(); },
  async "social-test"(btn) {
    if (S.dirty) { toast("שמור קודם את השינויים", "error"); return; }
    await guarded(btn, async () => toast((await api("POST", `/guilds/${S.guildId}/social/test/${btn.dataset.idx}`)).message, "ok"));
  },
  async "social-send"(btn) {
    if (S.dirty) { toast("שמור קודם את השינויים", "error"); return; }
    await guarded(btn, async () => { await api("POST", `/guilds/${S.guildId}/social/test/${btn.dataset.idx}?send=true`); toast("התראת בדיקה נשלחה", "ok"); });
  },
  async "embed-send"(btn) {
    if (S.dirty) { toast("שמור קודם את השינויים", "error"); return; }
    await guarded(btn, async () => { await api("POST", `/guilds/${S.guildId}/embed/send`); toast("ההודעה פורסמה", "ok"); });
  },
  async "ticket-panel"(btn) {
    if (S.dirty) { toast("שמור קודם את השינויים", "error"); return; }
    await guarded(btn, async () => { await api("POST", `/guilds/${S.guildId}/ticket-panel`); toast("הכפתור פורסם", "ok"); });
  },
  async "bday-add"(btn) {
    const who = $("#bday-name").value.trim(), date = $("#bday-date").value.trim();
    if (!who || !date) { toast("צריך שם ותאריך", "error"); return; }
    const body = /^\d{15,21}$/.test(who) ? { user_id: who, date } : { name: who, date };
    await guarded(btn, async () => {
      await api("POST", `/guilds/${S.guildId}/birthdays`, body);
      $("#bday-name").value = ""; $("#bday-date").value = "";
      loadExtraTable("birthdays");
      toast("נשמר", "ok");
    });
  },
  async "bday-delete"(btn) {
    await guarded(btn, async () => { await api("DELETE", `/guilds/${S.guildId}/birthdays/${btn.dataset.bid}`); loadExtraTable("birthdays"); });
  },
  async "enable-intents"(btn) {
    await guarded(btn, async () => {
      onStatus(await api("POST", "/intents/enable"));
      render();
      toast("ה-Intents הופעלו. הבוט מתחבר מחדש.", "ok");
    });
  },
  async "make-invite"(btn) {
    await guarded(btn, async () => {
      S.invite = await api("POST", "/invite", { permissions: [...S.permSel], guild_id: $("#invite-guild").value, account: ($("#invite-account") || {}).value || "" });
      render();
    });
  },
  async "user-install"(btn) {
    await guarded(btn, async () => {
      const res = await api("POST", "/user-install");
      $("#user-install-link").value = res.link;
      toast(t("Add App הופעל. העתק את הקישור ופתח אותו בדפדפן."), "ok");
    });
  },
  async "copy-user-install"() {
    const v = $("#user-install-link").value;
    if (!v) { toast(t("לחץ קודם 'הפעל וקבל קישור'."), "error"); return; }
    try { await navigator.clipboard.writeText(v); toast("הועתק", "ok"); } catch { toast(v, "ok"); }
  },
  async "copy-invite"() {
    try { await navigator.clipboard.writeText(S.invite.url); toast("הועתק", "ok"); }
    catch { toast("ההעתקה נכשלה. סמן את הקישור והעתק ידנית.", "error"); }
  },
  async "save-module"(btn) {
    const key = S.page.slice(4);
    const config = collectModuleForm(key);
    if (key === "automations" && (config.items || []).some((a) => a.enabled && a.actions.some((x) => x.type === "delete_message"))) {
      const ok = await confirmDialog("אוטומציה שמוחקת הודעות",
        "אחת האוטומציות מוחקת הודעות אוטומטית, בלי אישור בכל פעם. להמשיך?", "כן, שמור", false);
      if (!ok) return;
    }
    if (key === "warnings" && (config.steps || []).some((s) => ["kick", "ban", "tempban"].includes(s.action))) {
      const ok = await confirmDialog("הרחקה או חסימה אוטומטית",
        "הגדרת שהבוט ירחיק או יחסום משתמשים אוטומטית כשהם מגיעים למספר אזהרות, בלי אישור ידני בכל פעם. להמשיך?", "כן, שמור", false);
      if (!ok) return;
    }
    await guarded(btn, async () => {
      S.guild = await api("PUT", `/guilds/${S.guildId}/modules/${key}/config`, { config });
      setDirty(false);
      render();
      toast("ההגדרות נשמרו", "ok");
    });
  },
  "cancel-module"() { setDirty(false); render(); },
  "auto-new"() {
    const tpl = AUTO_TEMPLATES[$("#auto-template").value] || AUTO_TEMPLATES.blank;
    S.autos.push(normalizeAuto(tpl.make()));
    S.autoOpen = S.autos.length - 1;
    setDirty(true); rerenderAutos();
  },
  "auto-edit"(btn) { S.autoOpen = +btn.dataset.i; rerenderAutos(); },
  "auto-close"() { S.autoOpen = null; rerenderAutos(); },
  "auto-dup"(btn) {
    if (S.autos.length >= AS().max_automations) { toast("הגעת למקסימום אוטומציות", "error"); return; }
    const copy = JSON.parse(JSON.stringify(S.autos[+btn.dataset.i]));
    copy.id = ""; copy.name = (copy.name || t("אוטומציה")) + " " + t("(עותק)");
    S.autos.splice(+btn.dataset.i + 1, 0, copy);
    setDirty(true); rerenderAutos();
  },
  async "auto-del"(btn) {
    const a = S.autos[+btn.dataset.i];
    if (!(await confirmDialog(t('למחוק את "{name}"?', { name: a.name || t("האוטומציה") }), "האוטומציה תימחק אחרי שתלחץ 'שמור שינויים'.", "מחק"))) return;
    S.autos.splice(+btn.dataset.i, 1);
    S.autoOpen = null;
    setDirty(true); rerenderAutos();
  },
  "auto-trigger"(btn) {
    const a = S.autos[S.autoOpen];
    a.trigger = btn.dataset.key;
    a.conditions = a.conditions.filter((c) => condAllowed(c.type, a.trigger));
    a.actions = a.actions.filter((x) => actionAllowed(x.type, a.trigger));
    a.actions.forEach((x) => { if (x.target === "same_channel" && !AS().channel_triggers.includes(a.trigger)) x.target = "channel"; });
    setDirty(true); rerenderAutos();
  },
  "auto-trigger-clear"() { S.autos[S.autoOpen].trigger = ""; setDirty(true); rerenderAutos(); },
  "cond-add"(btn) { S.autos[S.autoOpen].conditions.push({ type: btn.dataset.key, values: [] }); setDirty(true); rerenderAutos(); },
  "cond-del"(btn) { S.autos[S.autoOpen].conditions.splice(+btn.dataset.ci, 1); setDirty(true); rerenderAutos(); },
  "act-add"(btn) {
    const a = S.autos[S.autoOpen];
    const x = normalizeAuto({ actions: [{ type: btn.dataset.key }] }).actions[0];
    if (!AS().channel_triggers.includes(a.trigger)) x.target = "channel";
    a.actions.push(x);
    setDirty(true); rerenderAutos();
  },
  "act-del"(btn) { S.autos[S.autoOpen].actions.splice(+btn.dataset.j, 1); setDirty(true); rerenderAutos(); },
  "act-up"(btn) {
    const acts = S.autos[S.autoOpen].actions, j = +btn.dataset.j;
    [acts[j - 1], acts[j]] = [acts[j], acts[j - 1]];
    setDirty(true); rerenderAutos();
  },
  "cmd-add"(btn) { document.querySelector(".cmd-list").insertAdjacentHTML("beforeend", commandRow(null, S.guild.objects)); setDirty(true); },
  "cmd-example"(btn) {
    const ex = { ...CMD_EXAMPLES[btn.dataset.ex] };
    ex.description = t(ex.description); ex.arg_hint = t(ex.arg_hint); ex.response = t(ex.response);
    document.querySelector(".cmd-list").insertAdjacentHTML("beforeend", commandRow(ex, S.guild.objects)); setDirty(true);
  },
  "list-add"(btn) {
    const m = mod(S.page.slice(4));
    const f = m.fields.find((x) => x.key === btn.dataset.fkey);
    const box = btn.previousElementSibling;
    if (box.childElementCount >= (f.max_items || 50)) { toast(`עד ${f.max_items || 50} שורות`, "error"); return; }
    box.insertAdjacentHTML("beforeend", listRow(f, {}, S.guild.objects));
    setDirty(true);
  },
  "row-remove"(btn) {
    const inRR = btn.closest("#rr-pairs");
    btn.parentElement.remove();
    if (!inRR) setDirty(true);
  },
  "rr-add-pair"() {
    const box = $("#rr-pairs");
    if (box.childElementCount >= 20) { toast("עד 20 שורות", "error"); return; }
    box.insertAdjacentHTML("beforeend", rrPairRow(S.guild.objects));
  },
  async "rr-publish"(btn) {
    const pairs = [...document.querySelectorAll("#rr-pairs .rr-pair")]
      .map((r) => ({ emoji: r.querySelector(".rr-emoji").value.trim(), role_id: r.querySelector(".rr-role").value }))
      .filter((p) => p.emoji || p.role_id);
    await guarded(btn, async () => {
      S.guild = await api("POST", `/guilds/${S.guildId}/reaction-roles`, { channel_id: $("#rr-channel").value, content: $("#rr-content").value, pairs });
      render();
      toast("ההודעה פורסמה בדיסקורד", "ok");
    });
  },
  async "rr-delete"(btn) {
    const res = await confirmDialog("להסיר את הודעת התפקידים?",
      "הבוט יפסיק לתת תפקידים לפי הריאקשנים בהודעה הזו. תפקידים שכבר ניתנו נשארים.", "הסר", true, "למחוק גם את ההודעה מדיסקורד");
    if (!res.ok) return;
    await guarded(btn, async () => {
      S.guild = await api("DELETE", `/guilds/${S.guildId}/reaction-roles/${btn.dataset.mid}?delete_message=${res.checked}`);
      render();
      toast("הוסר", "ok");
    });
  },
  "warnings-reload"() { loadWarnings(); },
  "check-updates"() { S.updates = null; render(); },
  async "save-update-repo"(btn) {
    await guarded(btn, async () => { S.updates = await api("PUT", "/updates/repo", { repo: $("#update-repo").value }); render(); toast("נשמר", "ok"); });
  },
  async "install-version"(btn) {
    const newer = btn.dataset.newer === "1";
    const ok = await confirmDialog(newer ? t("לעדכן לגרסה {tag}?", { tag: btn.dataset.tag }) : t("לחזור לגרסה {tag}?", { tag: btn.dataset.tag }),
      newer ? "הקבצים של האפליקציה יוחלפו בגרסה שבחרת. ההגדרות, הסודות והנתונים שלך לא משתנים, וגיבוי של הגרסה הנוכחית נשמר. עדכון רגיל נכנס לתוקף מיד, בלי לסגור את האפליקציה. רק עדכון גדול צריך לפתוח את האפליקציה מחדש."
        : "⚠️ גרסאות ישנות עובדות פחות טוב: יש בהן באגים שתוקנו מאז, חסרים בהן תיקוני אבטחה ויציבות, והבוט עלול להתנהג בשרת שלך בצורה לא צפויה. בגרסאות 1.0.0 עד 1.0.2 גם העדכון מתוך האפליקציה לא עובד, ותצטרך להוריד גרסה חדשה ידנית. מומלץ להישאר בגרסה האחרונה.\nההגדרות והנתונים שלך לא משתנים, וגיבוי של הגרסה הנוכחית נשמר.",
      newer ? "עדכן" : "חזור לגרסה", !newer);
    if (!ok) return;
    await guarded(btn, async () => {
      btn.textContent = t("מתקין…");
      const res = await api("POST", "/updates/install", { tag: btn.dataset.tag });
      if (!res.restart_required) { await applyLiveUpdate(res.installed); return; }
      const now = await confirmDialog("הגרסה הותקנה", "לחץ 'הפעל מחדש' והאפליקציה תיסגר ותיפתח מחדש לבד על הגרסה החדשה. הבוט יתנתק לכמה שניות ויחזור.", "הפעל מחדש", false);
      if (now) { await api("POST", "/app/restart"); toast("האפליקציה נפתחת מחדש…", "ok"); }
      else loadUpdates();
    });
  },
  "levels-reload"() { loadLeaderboard(); },
  "eco-reload"() { loadRichList(); },
  async "eco-reset"(btn) {
    if (!(await confirmDialog(`לאפס את הארנק של ${btn.dataset.name}?`, "היתרה, הרצף היומי והפריטים שלו בשרת הזה יימחקו. אי אפשר לבטל.", "אפס"))) return;
    await guarded(btn, async () => { await api("DELETE", `/guilds/${S.guildId}/economy/${btn.dataset.uid}`); toast("אופס", "ok"); loadRichList(); });
  },
  async "xp-reset"(btn) {
    if (!(await confirmDialog(`לאפס את ה-XP של ${btn.dataset.name}?`, "כל ה-XP וספירת ההודעות שלו בשרת הזה יימחקו, והוא יחזור לרמה 0. אי אפשר לבטל. תפקידי פרס שכבר קיבל לא יוסרו אוטומטית.", "אפס"))) return;
    await guarded(btn, async () => { await api("DELETE", `/guilds/${S.guildId}/levels/${btn.dataset.uid}`); toast("אופס", "ok"); loadLeaderboard(); });
  },
  async "warning-delete"(btn) {
    if (!(await confirmDialog(`למחוק את אזהרה #${btn.dataset.wid}?`, "האזהרה תימחק לצמיתות ולא תיספר יותר.", "מחק"))) return;
    await guarded(btn, async () => { await api("DELETE", `/guilds/${S.guildId}/warnings/${btn.dataset.wid}`); toast("נמחקה", "ok"); loadWarnings(); });
  },
  async "forget-guild"(btn) {
    if (!(await confirmDialog(`להסיר את הפרופיל של "${S.guild.name}"?`, "כל ההגדרות והמתגים של השרת הזה יימחקו מהאפליקציה. הבוט עצמו לא יוצא מהשרת.", "הסר"))) return;
    await guarded(btn, async () => { await api("DELETE", "/guilds/" + S.guildId); await refreshGuilds(); render(); });
  },
  search() { openSearch(); },
};

/* ===== app look (theme, colors, font, custom CSS) ===== */
const LIGHT_VARS = { "--bg": "#f4f5f9", "--panel": "#ffffff", "--panel-2": "#eef0f5", "--border": "#d9dce5", "--text": "#1c1f2a", "--muted": "#5d6475" };
function applyAppearance(a) {
  a = a || {};
  const root = document.documentElement.style;
  ["--bg", "--panel", "--panel-2", "--border", "--text", "--muted", "--accent", "--accent-2", "--radius"].forEach((v) => root.removeProperty(v));
  if (a.theme === "light") Object.entries(LIGHT_VARS).forEach(([k, v]) => root.setProperty(k, v));
  if (a.accent) { root.setProperty("--accent", a.accent); root.setProperty("--accent-2", a.accent); }
  if (a.background) root.setProperty("--bg", a.background);
  if (a.surface) { root.setProperty("--panel", a.surface); root.setProperty("--panel-2", a.surface); }
  if (a.text) root.setProperty("--text", a.text);
  if (a.radius !== undefined && a.radius !== "") root.setProperty("--radius", a.radius + "px");
  document.documentElement.style.fontSize = "";
  document.body.style.fontSize = a.font_scale ? (14 * a.font_scale / 100) + "px" : "";
  document.body.classList.toggle("compact", !!a.compact);
  document.body.classList.toggle("theme-light", a.theme === "light");
  let tag = document.getElementById("user-css");
  if (!tag) { tag = document.createElement("style"); tag.id = "user-css"; document.head.appendChild(tag); }
  tag.textContent = a.custom_css || "";
}

/* ===== Console page: typed commands + talking as the bot ===== */
function viewConsole() {
  const guildOpts = S.guilds.map((g) => `<option value="${esc(g.id)}" ${g.id === (S.chatGuild || S.guildId) ? "selected" : ""}>${esc(g.name)}</option>`).join("");
  return `
    <div class="console-grid">
      <div class="card section">
        <div class="row"><h3>${esc(t("לוגים חיים וקונסול"))}</h3><div class="spacer"></div>
          <select id="log-filter" style="width:auto">
            ${["ALL", "INFO", "WARNING", "ERROR"].map((l) => `<option ${S.logFilter === l ? "selected" : ""} value="${l}">${l === "ALL" ? "הכל" : l}</option>`).join("")}
          </select>
          <button class="btn btn-sm btn-ghost" data-action="console-clear">${esc(t("נקה"))}</button></div>
        <div class="muted small" style="margin-top:4px">${esc(t("הקלד help כדי לראות את כל הפקודות"))}</div>
        <div id="logs" class="logs console-logs" style="margin-top:10px"></div>
        <div class="console-input ltr"><span class="prompt">›</span>
          <input id="console-input" autocomplete="off" spellcheck="false" placeholder="help · status · say #general hi · module GOLA levels on"></div>
      </div>
      <div class="card section">
        <div class="row"><h3>${esc(t("דבר בשם הבוט"))}</h3></div>
        <div class="row" style="margin:10px 0;flex-wrap:wrap">
          <select id="chat-guild" class="no-translate" style="width:auto;min-width:160px">${guildOpts}</select>
          <select id="chat-channel" class="no-translate" style="width:auto;min-width:180px"><option value="">${esc(t("בחר ערוץ"))}</option></select>
          <button class="btn btn-sm" data-action="chat-reload">${esc(t("רענן"))}</button>
        </div>
        <div id="chat-box" class="chat-box"><div class="muted small">${esc(t("בחר שרת וערוץ. הבוט צריך להיות מחובר."))}</div></div>
        <div class="row" style="margin-top:10px;align-items:flex-end">
          <textarea id="chat-input" rows="2" class="grow" placeholder="${esc(t("כתוב הודעה בשם הבוט… (Enter שולח, Shift+Enter שורה חדשה)"))}"></textarea>
          <button class="btn btn-primary" data-action="chat-send">${esc(t("שלח"))}</button>
        </div>
      </div>
    </div>`;
}

let consoleHistory = [], consoleIdx = -1, chatTimer = null;
function consoleOut(lines, cls = "log-console") {
  const box = $("#logs");
  if (!box) return;
  if (box.firstElementChild && box.firstElementChild.classList.contains("muted")) box.innerHTML = "";
  box.insertAdjacentHTML("beforeend", lines.map((l) => `<div class="log-line ${cls}"><span class="msg">${esc(l)}</span></div>`).join(""));
  box.scrollTop = box.scrollHeight;
}

async function runConsole(line) {
  if (!line.trim()) return;
  consoleHistory.unshift(line); consoleIdx = -1;
  consoleOut(["› " + line], "log-cmd");
  if (line.trim().toLowerCase() === "clear") { $("#logs").innerHTML = ""; return; }
  try { consoleOut((await api("POST", "/console", { line })).lines); }
  catch (e) { consoleOut([errMessage(e)], "log-ERROR"); }
}

async function afterConsoleRender() {
  renderLogs();
  const gid = S.chatGuild || S.guildId;
  if (gid) await loadChatChannels(gid);
  clearInterval(chatTimer);
  chatTimer = setInterval(() => { if (S.page === "dashboard" && S.chatChannel) loadChatMessages(true); else if (S.page !== "dashboard") clearInterval(chatTimer); }, 5000);
}

async function loadChatChannels(gid) {
  S.chatGuild = gid;
  const sel = $("#chat-channel");
  if (!sel) return;
  let g;
  try { g = await api("GET", "/guilds/" + gid); } catch (e) { toast(errMessage(e), "error"); return; }
  const chans = (g.objects && g.objects.text_channels) || [];
  if (!chans.find((c) => c.id === S.chatChannel)) S.chatChannel = "";
  sel.innerHTML = `<option value="">${esc(t("בחר ערוץ"))}</option>` + chans.map((c) =>
    `<option value="${esc(c.id)}" ${c.id === S.chatChannel ? "selected" : ""}># ${esc(c.name)}${c.category ? " · " + esc(c.category) : ""}</option>`).join("");
  if (!g.objects) $("#chat-box").innerHTML = `<div class="muted small">${esc(t("הבוט כבוי או לא בשרת הזה."))}</div>`;
  if (S.chatChannel) loadChatMessages();
}

async function loadChatMessages(quiet) {
  const box = $("#chat-box");
  if (!box || !S.chatGuild || !S.chatChannel) return;
  let msgs;
  try { msgs = await api("GET", `/guilds/${S.chatGuild}/channels/${S.chatChannel}/messages?limit=40`); }
  catch (e) { if (!quiet) box.innerHTML = `<div class="muted small">${esc(errMessage(e))}</div>`; return; }
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.innerHTML = msgs.map((m) => `
    <div class="chat-msg no-translate">
      <img class="chat-avatar" src="${esc(m.avatar)}" alt="">
      <div class="grow"><div><b>${esc(m.author)}</b>${m.bot ? ` <span class="badge">BOT</span>` : ""} <span class="muted small">${new Date(m.time * 1000).toLocaleString()}</span></div>
        <div class="chat-text">${esc(m.content) || (m.embeds ? `<span class="muted">[embed]</span>` : "")}</div>
        ${m.attachments.map((a) => `<a href="${esc(a)}" target="_blank" rel="noopener" class="small">📎 ${esc(a.split("/").pop().split("?")[0])}</a>`).join(" ")}</div>
    </div>`).join("") || `<div class="muted small">${esc(t("אין הודעות בערוץ הזה."))}</div>`;
  if (atBottom || !quiet) box.scrollTop = box.scrollHeight;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  if (!S.chatChannel) { toast("בחר ערוץ", "error"); return; }
  try {
    await api("POST", `/guilds/${S.chatGuild}/channels/${S.chatChannel}/messages`, { content: text });
    input.value = "";
    loadChatMessages();
  } catch (e) { toast(errMessage(e), "error"); }
}

/* ===== Customize page ===== */
const CUST_TABS = [["texts", "טקסטים של הבוט"], ["colors", "צבעים ואימוג'ים"], ["commands", "פקודות סלאש"], ["look", "מראה האפליקציה"]];
const PALETTE_LABELS = { info: "מידע", success: "הצלחה", warning: "אזהרה", danger: "סכנה / מחיקה", brand: "צבע ראשי (כרטיסים, חנות, מובילים)",
  starboard: "Starboard", birthdays: "ימי הולדת", done: "הצעה שבוצעה" };
const EMOJI_LABELS = { vote_up: "הצבעה בעד (הצעות)", vote_down: "הצבעה נגד (הצעות)", giveaway: "הגרלות", ticket: "כרטיסים", close: "סגירה" };

function viewCustomize() {
  const tab = S.custTab || "texts";
  const tabs = `<div class="tabs">${CUST_TABS.map(([k, l]) => `<button class="tab ${k === tab ? "active" : ""}" data-action="cust-tab" data-tab="${k}">${esc(t(l))}</button>`).join("")}</div>`;
  let body = `<div class="empty">${esc(t("טוען…"))}</div>`;
  if (tab === "look") body = viewLook();
  else if (tab === "commands") body = S.cmds ? viewCommandsTab() : body;
  else if (!S.guild) body = noGuild();
  else if (S.cust && S.cust.gid === S.guildId) body = tab === "texts" ? viewTextsTab() : viewColorsTab();
  return tabs + body;
}

async function afterCustomizeRender() {
  const tab = S.custTab || "texts";
  if ((tab === "texts" || tab === "colors") && S.guild && (!S.cust || S.cust.gid !== S.guildId)) {
    try { S.cust = { ...(await api("GET", `/guilds/${S.guildId}/custom`)), gid: S.guildId }; } catch (e) { toast(errMessage(e), "error"); return; }
    render();
  }
  if (tab === "commands" && !S.cmds) {
    try { S.cmds = await api("GET", "/commands"); } catch (e) { toast(errMessage(e), "error"); return; }
    render();
  }
}

function viewTextsTab() {
  const c = S.cust, q = (S.custQuery || "").toLowerCase(), grp = S.custGroup || "";
  const groups = [...new Set(c.catalog.map((r) => r.group))];
  const rows = c.catalog.filter((r) => (!grp || r.group === grp) && (!q || (r.default + " " + (c.texts[r.key] || "")).toLowerCase().includes(q)));
  const changed = Object.keys(c.texts).length;
  return `
    <div class="notice">${esc(t("כל הודעה שהבוט שולח בדיסקורד בשרת הזה. כתוב נוסח משלך, או השאר ריק כדי להשתמש בברירת המחדל. משתנים כמו {user} חייבים להישאר עם אותו שם."))}</div>
    <div class="card section">
      <div class="row" style="flex-wrap:wrap">
        <input id="cust-search" type="search" value="${esc(S.custQuery || "")}" placeholder="${esc(t("חיפוש בטקסטים…"))}" style="max-width:280px">
        <select id="cust-group" style="width:auto"><option value="">${esc(t("כל המודולים"))} (${c.catalog.length})</option>
          ${groups.map((g) => `<option value="${esc(g)}" ${g === grp ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>
        <span class="muted small">${esc(t("{n} טקסטים שונו", { n: changed }))}</span>
        <div class="spacer"></div>
        <button class="btn btn-primary" data-action="cust-save">${esc(t("שמור"))}</button>
      </div>
      <div class="ct-list">${rows.slice(0, 400).map((r) => `
        <div class="ct-row ${c.texts[r.key] ? "changed" : ""}">
          <div class="ct-head"><span class="badge">${esc(r.group)}</span>${r.placeholders.length ? `<span class="muted small ltr">${r.placeholders.map((p) => "{" + esc(p) + "}").join(" ")}</span>` : ""}</div>
          <div class="ct-default no-translate">${esc(r.default)}</div>
          <div class="row"><textarea rows="1" class="grow no-translate" data-ct="${esc(r.key)}" placeholder="${esc(t("ברירת מחדל"))}">${esc(c.texts[r.key] || "")}</textarea>
            <button class="btn btn-sm btn-ghost" data-action="ct-reset" title="${esc(t("איפוס"))}">↺</button></div>
        </div>`).join("") || `<div class="muted small">${esc(t("לא נמצאו טקסטים"))}</div>`}</div>
    </div>`;
}

function viewColorsTab() {
  const c = S.cust;
  const colorRow = (k) => {
    const val = c.colors[k] || c.palette[k];
    return `<div class="row cc-row"><span class="swatch" style="background:${esc(val)}"></span><span class="grow">${esc(t(PALETTE_LABELS[k] || k))}</span>
      <input type="color" data-cc="${k}" value="${esc(val.toLowerCase())}"><span class="muted small ltr">${esc(val)}</span>
      <button class="btn btn-sm btn-ghost" data-action="cc-reset" data-key="${k}" title="${esc(t("איפוס"))}">↺</button></div>`;
  };
  const emojiRow = (k) => `<div class="row cc-row"><span class="grow">${esc(t(EMOJI_LABELS[k] || k))}</span>
      <input type="text" data-ce="${k}" value="${esc(c.emojis[k] || "")}" placeholder="${esc(c.emoji_defaults[k])}" maxlength="64" style="max-width:140px"></div>`;
  return `
    <div class="notice">${esc(t("הצבעים של כל ה-Embeds שהבוט שולח בשרת הזה, ואימוג'ים של כפתורים והצבעות. את האימוג'ים שבתוך ההודעות משנים בלשונית 'טקסטים של הבוט'."))}</div>
    <div class="grid" style="grid-template-columns:1fr 1fr">
      <div class="card section"><h3>${esc(t("צבעים"))}</h3>${Object.keys(c.palette).map(colorRow).join("")}</div>
      <div class="card section"><h3>${esc(t("אימוג'ים"))}</h3>${Object.keys(c.emoji_defaults).map(emojiRow).join("")}</div>
    </div>
    <div class="row" style="margin-top:12px"><div class="spacer"></div><button class="btn btn-primary" data-action="cust-save">${esc(t("שמור"))}</button></div>`;
}

function viewCommandsTab() {
  const { commands, custom } = S.cmds;
  return `
    <div class="notice">${esc(t("שינוי שם, תיאור או כיבוי של פקודות סלאש. דיסקורד מציג את אותן פקודות בכל השרתים, אז זה חל על כל השרתים. אחרי השמירה הבוט מתחבר מחדש."))}</div>
    <div class="card section">
      <table class="table"><thead><tr><th>${esc(t("פקודה"))}</th><th>${esc(t("שם חדש"))}</th><th>${esc(t("תיאור"))}</th><th>${esc(t("פעילה"))}</th></tr></thead><tbody>
      ${commands.map((c) => { const o = custom[c.name] || {}; return `<tr data-cmd="${esc(c.name)}">
        <td class="ltr"><b>/${esc(c.name)}</b>${c.subcommands.length ? `<div class="muted small">${c.subcommands.map(esc).join(", ")}</div>` : ""}</td>
        <td><input type="text" class="ltr cmd-new" value="${esc(o.name || "")}" placeholder="${esc(c.name)}" maxlength="32"></td>
        <td><input type="text" class="cmd-desc" value="${esc(o.description || "")}" placeholder="${esc(t(c.description))}" maxlength="100"></td>
        <td><label class="switch"><input type="checkbox" class="cmd-on" ${o.enabled === false ? "" : "checked"}><span class="slider"></span></label></td></tr>`; }).join("")}
      </tbody></table>
      <div class="row" style="margin-top:12px"><div class="spacer"></div><button class="btn btn-primary" data-action="cmds-save">${esc(t("שמור והחל"))}</button></div>
    </div>`;
}

function lookDraft() {
  if (!S.lookDraft) S.lookDraft = { theme: "dark", accent: "#6D5DFC", background: "", surface: "", text: "", font_scale: 100, radius: 12, compact: false, custom_css: "", ...((S.settings && S.settings.settings.appearance) || {}) };
  return S.lookDraft;
}

function viewLook() {
  const a = lookDraft();
  const color = (k, label) => `<div class="field"><span class="label">${esc(t(label))}</span>
    <div class="row"><input type="color" data-look="${k}" value="${esc((a[k] || (k === "accent" ? "#6d5dfc" : "#000000")).toLowerCase())}">
    <button class="btn btn-sm btn-ghost" data-action="look-reset" data-key="${k}">${esc(t("ברירת מחדל"))}</button>
    <span class="muted small ltr">${esc(a[k] || t("ברירת מחדל"))}</span></div></div>`;
  return `
    <div class="notice">${esc(t("המראה של האפליקציה הזו (לא של הבוט בדיסקורד). השינויים מוצגים מיד, ונשמרים כשלוחצים שמור."))}</div>
    <div class="card section"><div class="form-grid">
      <div class="field"><span class="label">${esc(t("ערכת נושא"))}</span><select data-look="theme" style="width:auto">
        <option value="dark" ${a.theme === "dark" ? "selected" : ""}>${esc(t("כהה"))}</option><option value="light" ${a.theme === "light" ? "selected" : ""}>${esc(t("בהירה"))}</option></select></div>
      ${color("accent", "צבע ראשי")}
      ${color("background", "צבע רקע")}
      ${color("surface", "צבע כרטיסים")}
      ${color("text", "צבע טקסט")}
      <div class="field"><span class="label">${esc(t("גודל טקסט"))}: <span class="ltr">${a.font_scale}%</span></span><input type="range" min="80" max="140" step="5" data-look="font_scale" value="${a.font_scale}"></div>
      <div class="field"><span class="label">${esc(t("עיגול פינות"))}: <span class="ltr">${a.radius}px</span></span><input type="range" min="0" max="24" data-look="radius" value="${a.radius}"></div>
      <div class="row field-bool"><label class="switch"><input type="checkbox" data-look="compact" ${a.compact ? "checked" : ""}><span class="slider"></span></label><span>${esc(t("תצוגה צפופה"))}</span></div>
      <div class="field wide"><span class="label">${esc(t("CSS מותאם (למתקדמים)"))}</span>
        <textarea data-look="custom_css" rows="6" class="ltr no-translate" placeholder=".sidebar { background: #101820; }">${esc(a.custom_css || "")}</textarea>
        <span class="hint">${esc(t("שולטים בכל פרט בעיצוב. אי אפשר לטעון קבצים מבחוץ."))}</span></div>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn btn-ghost" data-action="look-defaults">${esc(t("חזרה לברירת המחדל"))}</button><div class="spacer"></div>
      <button class="btn btn-primary" data-action="look-save">${esc(t("שמור"))}</button></div></div>`;
}

Object.assign(actions, {
  "console-clear"() { const b = $("#logs"); if (b) b.innerHTML = ""; },
  "chat-reload"() { loadChatMessages(); },
  "chat-send"() { sendChat(); },
  "cust-tab"(btn) { S.custTab = btn.dataset.tab; render(); afterCustomizeRender(); },
  "ct-reset"(btn) { const ta = btn.parentElement.querySelector("textarea"); ta.value = ""; delete S.cust.texts[ta.dataset.ct]; ta.closest(".ct-row").classList.remove("changed"); },
  "cc-reset"(btn) { delete S.cust.colors[btn.dataset.key]; render(); },
  async "cust-save"(btn) {
    document.querySelectorAll("[data-ce]").forEach((el) => { if (el.value.trim()) S.cust.emojis[el.dataset.ce] = el.value.trim(); else delete S.cust.emojis[el.dataset.ce]; });
    await guarded(btn, async () => {
      const saved = await api("PUT", `/guilds/${S.guildId}/custom`, { config: { texts: S.cust.texts, colors: S.cust.colors, emojis: S.cust.emojis } });
      Object.assign(S.cust, saved);
      render();
      toast("נשמר. הבוט משתמש בזה מעכשיו.", "ok");
    });
  },
  async "cmds-save"(btn) {
    const custom = {};
    document.querySelectorAll("[data-cmd]").forEach((row) => {
      const o = { name: row.querySelector(".cmd-new").value.trim(), description: row.querySelector(".cmd-desc").value.trim(), enabled: row.querySelector(".cmd-on").checked };
      if (o.name || o.description || !o.enabled) custom[row.dataset.cmd] = o;
    });
    await guarded(btn, async () => {
      const res = await api("PUT", "/commands", { config: custom });
      S.cmds.custom = res.custom;
      render();
      toast(res.restarted ? "נשמר. הבוט מתחבר מחדש עם הפקודות החדשות." : "נשמר. השינוי יחול כשהבוט יופעל.", "ok");
    });
  },
  "look-reset"(btn) { lookDraft()[btn.dataset.key] = btn.dataset.key === "accent" ? "#6D5DFC" : ""; applyAppearance(lookDraft()); render(); },
  "look-defaults"() { S.lookDraft = null; S.lookDraft = { ...lookDraft(), theme: "dark", accent: "#6D5DFC", background: "", surface: "", text: "", font_scale: 100, radius: 12, compact: false, custom_css: "" }; applyAppearance(S.lookDraft); render(); },
  async "look-save"(btn) {
    await guarded(btn, async () => {
      S.settings = await api("PUT", "/settings", { appearance: lookDraft() });
      S.lookDraft = null;
      applyAppearance(S.settings.settings.appearance);
      toast("נשמר", "ok");
    });
  },
});

document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset && el.dataset.ct !== undefined && S.cust) {
    if (el.value.trim()) S.cust.texts[el.dataset.ct] = el.value; else delete S.cust.texts[el.dataset.ct];
    el.closest(".ct-row").classList.toggle("changed", !!el.value.trim());
  }
  if (el.dataset && el.dataset.cc && S.cust) {
    S.cust.colors[el.dataset.cc] = el.value.toUpperCase();
    const sw = el.parentElement.querySelector(".swatch"); if (sw) sw.style.background = el.value;
  }
  if (el.dataset && el.dataset.look) {
    const a = lookDraft(), k = el.dataset.look;
    a[k] = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.type === "color" ? el.value.toUpperCase() : el.value;
    applyAppearance(a);
    if (el.type === "range") { const lbl = el.parentElement.querySelector(".label .ltr"); if (lbl) lbl.textContent = a[k] + (k === "radius" ? "px" : "%"); }
  }
  if (el.id === "cust-search") { S.custQuery = el.value; const pos = el.selectionStart; render(); const s = $("#cust-search"); s.focus(); s.setSelectionRange(pos, pos); }
});
document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.id === "cust-group") { S.custGroup = el.value; render(); }
  if (el.dataset && el.dataset.look === "theme") { lookDraft().theme = el.value; applyAppearance(lookDraft()); }
  if (el.id === "chat-guild") { S.chatChannel = ""; loadChatChannels(el.value); }
  if (el.id === "chat-channel") { S.chatChannel = el.value; loadChatMessages(); }
  if (el.classList && el.classList.contains("cmd-kind")) {
    const slash = el.closest(".cmd-row").querySelector(".cmd-slash");
    if (slash) slash.textContent = el.value === "prefix" ? "!" : "/";
  }
});
document.addEventListener("keydown", (e) => {
  if (e.target.id === "console-input") {
    if (e.key === "Enter") { const v = e.target.value; e.target.value = ""; runConsole(v); }
    else if (e.key === "ArrowUp" && consoleHistory.length) { consoleIdx = Math.min(consoleIdx + 1, consoleHistory.length - 1); e.target.value = consoleHistory[consoleIdx]; e.preventDefault(); }
    else if (e.key === "ArrowDown") { consoleIdx = Math.max(consoleIdx - 1, -1); e.target.value = consoleIdx >= 0 ? consoleHistory[consoleIdx] : ""; e.preventDefault(); }
  }
  if (e.target.id === "chat-input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

/* ===== Bot Profile -> Character (the AI talks as a character you describe, per server) ===== */
const PERSONA_SLIDERS = [
  ["length", "אורך תשובה", ["קצר", "בינוני", "ארוך", "ארוך מאוד"]],
  ["demeanor", "התנהגות", ["לא מתעניין", "", "ניטרלי", "", "חקרן"]],
  ["vulgarity", "רמת גסות", ["מנומס", "", "ניטרלי", "", "גס"]],
  ["content", "תוכן", ["לכל המשפחה", "", "ניטרלי", "", "למבוגרים"]],
];
const PERSONA_TAGS = [["traits", "תכונות אופי"], ["loves", "אוהב"], ["hates", "שונא"]];

async function loadPersona() {
  if (!S.guildId) return;
  try {
    const d = await api("GET", `/guilds/${S.guildId}/persona`);
    S.persona = { ...d, gid: S.guildId };
    S.pDraft = JSON.parse(JSON.stringify(d.persona));
    S.pAvatar = null; S.pChat = [];
  } catch (e) { toast(errMessage(e), "error"); }
  if (S.page === "profile") render();
}

function viewCharacter() {
  if (!S.guild) return noGuild();
  if (!S.persona || S.persona.gid !== S.guildId) return `<div class="card section empty">${esc(t("טוען…"))}</div>`;
  const p = S.pDraft, d = S.persona;
  const avatar = S.pAvatar || p.avatar_url || NONE_IMG;
  const tagField = ([key, label]) => `
    <div class="field wide"><span class="label">${esc(t(label))}</span>
      <div class="tag-box" data-tags="${key}">
        ${p[key].map((v, i) => `<span class="tag no-translate">${esc(v)}<button type="button" data-action="tag-remove" data-key="${key}" data-i="${i}">✕</button></span>`).join("")}
        <input type="text" class="tag-input" data-tag-input="${key}" placeholder="${esc(t("כתוב ולחץ Enter"))}" maxlength="150">
      </div></div>`;
  const slider = ([key, label, ends]) => {
    const n = d.sliders[key].length;
    return `<div class="field wide p-slider"><span class="label">${esc(t(label))}</span>
      <div class="p-ends">${ends.map((e) => `<span>${esc(e ? t(e) : "")}</span>`).join("")}</div>
      <input type="range" min="0" max="${n - 1}" step="1" value="${p[key]}" data-ps="${key}">
      <div class="hint" data-ps-label="${key}"><b>${esc(t("רמה {n}", { n: p[key] }))}:</b> ${esc(t(d.sliders[key][p[key]]))}</div></div>`;
  };
  return `
    <div class="persona-grid">
      <div class="card section">
        <div class="row" style="margin-bottom:6px"><h3>${esc(t("פרופיל הדמות"))}</h3><div class="spacer"></div>
          <label class="row small"><label class="switch"><input type="checkbox" data-pf="enabled" ${p.enabled ? "checked" : ""}><span class="slider"></span></label>
          ${esc(t("ה-AI מדבר כדמות הזו בשרת הזה"))}</label></div>
        <p class="desc" style="margin-bottom:12px">${esc(t("מספיק לכתוב שם של דמות מוכרת: ה-AI יודע איך היא מדברת ומתנהגת. שאר השדות לא חובה, והם רק מחדדים את הדמות. 'מלא אוטומטית' ממלא אותם בשבילך."))}</p>
        <div class="form-grid">
          <div class="field wide"><span class="label">${esc(t("שם"))} *</span>
            <div class="row"><input type="text" class="grow" data-pf="name" value="${esc(p.name)}" maxlength="80" placeholder="Deadpool / Benjamin Netanyahu / ...">
              <button class="btn" data-action="persona-generate">✨ ${esc(t("מלא אוטומטית מהשם"))}</button></div></div>
          <div class="field"><span class="label">${esc(t("תמונה"))}</span>
            <label class="pz-icon file-btn"><img alt="" src="${esc(avatar)}"><input type="file" id="pc-avatar-file" accept="image/png,image/jpeg,image/gif,image/webp"><span class="pz-edit">✎</span></label>
            <span class="hint">${esc(t("משמשת כתמונת הבוט בשרת הזה אם מסמנים את האפשרות למטה"))}</span></div>
          <div class="field"><span class="label">${esc(t("תיאור"))}</span><input type="text" data-pf="description" value="${esc(p.description)}" maxlength="200"></div>
          <div class="field"><span class="label">${esc(t("קטגוריה"))}</span><select data-pf="category">${d.categories.map(([k, l]) => `<option value="${k}" ${k === p.category ? "selected" : ""}>${esc(t(l))}</option>`).join("")}</select></div>
          <div class="field"><span class="label">${esc(t("יקום"))}</span><input type="text" data-pf="universe" value="${esc(p.universe)}" maxlength="80" placeholder="Marvel / Politics / ..."></div>
          <div class="field wide"><span class="label">${esc(t("ביו"))}</span><textarea data-pf="bio" rows="3" maxlength="2000">${esc(p.bio)}</textarea></div>
          ${PERSONA_TAGS.map(tagField).join("")}
          <div class="field wide"><span class="label">${esc(t("עובדות על הדמות"))}</span>
            <div id="p-facts">${p.facts.map((f, i) => `<div class="row" style="margin-bottom:6px"><input type="text" class="grow no-translate" data-fact="${i}" value="${esc(f)}" maxlength="150">
              <button class="btn btn-sm btn-ghost" data-action="fact-remove" data-i="${i}">✕</button></div>`).join("")}</div>
            <button class="btn btn-sm" data-action="fact-add">+ ${esc(t("הוסף עובדה"))}</button></div>
          ${PERSONA_SLIDERS.map(slider).join("")}
          <div class="row field-bool wide"><input type="checkbox" id="p-apply" ${S.pApply ? "checked" : ""}>
            <label for="p-apply">${esc(t("להשתמש גם בשם ובתמונה של הדמות כפרופיל הבוט בשרת הזה"))}</label></div>
        </div>
        <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="btn btn-primary" data-action="persona-save">${esc(t("שמור דמות"))}</button></div>
      </div>
      <div class="card section persona-chat">
        <div class="row"><img class="chat-avatar" src="${esc(avatar)}" alt=""><b class="no-translate">${esc(p.name || t("הדמות"))}</b><div class="spacer"></div>
          <button class="btn btn-sm btn-ghost" data-action="pchat-reset">${esc(t("איפוס"))}</button></div>
        <p class="muted small" style="margin:6px 0">${esc(t("שיחת ניסיון עם הדמות לפני שמירה (משתמש בספק ה-AI שבחרת, ועולה טוקנים כרגיל)."))}</p>
        <div id="pchat-box" class="chat-box">${viewPersonaChat()}</div>
        <div class="row" style="margin-top:10px;align-items:flex-end">
          <textarea id="pchat-input" rows="2" class="grow" placeholder="${esc(t("כתוב הודעה לדמות…"))}"></textarea>
          <button class="btn btn-primary" data-action="pchat-send">${esc(t("שלח"))}</button></div>
      </div>
    </div>`;
}

function viewPersonaChat() {
  const name = S.pDraft.name || t("הדמות");
  if (!S.pChat || !S.pChat.length) return `<div class="muted small">${esc(t("כתוב משהו כדי לראות איך הדמות עונה."))}</div>`;
  return S.pChat.map((m) => `<div class="chat-msg no-translate"><div class="grow"><b>${esc(m.role === "user" ? t("אתה") : name)}</b>
      <div class="chat-text">${esc(m.content)}</div></div></div>`).join("") + (S.pTyping ? `<div class="muted small">${esc(t("{name} מקליד…", { name }))}</div>` : "");
}

function refreshPersonaChat() {
  const box = $("#pchat-box");
  if (box) { box.innerHTML = viewPersonaChat(); box.scrollTop = box.scrollHeight; }
}

async function sendPersonaChat() {
  const input = $("#pchat-input");
  const text = input.value.trim();
  if (!text || S.pTyping) return;
  if (!S.pDraft.name) { toast("לדמות צריך שם", "error"); return; }
  S.pChat.push({ role: "user", content: text });
  input.value = "";
  S.pTyping = true; refreshPersonaChat();
  try {
    const r = await api("POST", `/guilds/${S.guildId}/persona/test`, { persona: S.pDraft, messages: S.pChat });
    S.pChat.push({ role: "assistant", content: r.reply });
  } catch (e) { S.pChat.pop(); toast(errMessage(e), "error"); }
  S.pTyping = false; refreshPersonaChat();
}

Object.assign(actions, {
  async "persona-generate"(btn) {
    if (!S.pDraft.name.trim()) { toast("לדמות צריך שם", "error"); return; }
    await guarded(btn, async () => {
      const g = await api("POST", `/guilds/${S.guildId}/persona/generate`, { name: S.pDraft.name, universe: S.pDraft.universe });
      Object.assign(S.pDraft, g);
      render();
      toast("הפרופיל מולא. אפשר לערוך ולשמור.", "ok");
    });
  },
  async "persona-save"(btn) {
    S.pApply = !!($("#p-apply") || {}).checked;
    await guarded(btn, async () => {
      const r = await api("PUT", `/guilds/${S.guildId}/persona`, { persona: S.pDraft, apply_profile: S.pApply, avatar: S.pApply ? S.pAvatar : null });
      S.persona.persona = r.persona; S.pDraft = JSON.parse(JSON.stringify(r.persona)); S.pAvatar = null;
      if (S.pApply) S.serverProfile = null;
      render();
      toast(r.persona.enabled ? "הדמות נשמרה. ה-AI מדבר כמוה מעכשיו." : "הדמות נשמרה (כבויה).", "ok");
    });
  },
  "tag-remove"(btn) { S.pDraft[btn.dataset.key].splice(+btn.dataset.i, 1); render(); },
  "fact-add"() { S.pDraft.facts.push(""); render(); const inputs = document.querySelectorAll("[data-fact]"); if (inputs.length) inputs[inputs.length - 1].focus(); },
  "fact-remove"(btn) { S.pDraft.facts.splice(+btn.dataset.i, 1); render(); },
  "pchat-send"() { sendPersonaChat(); },
  "pchat-reset"() { S.pChat = []; refreshPersonaChat(); },
});

document.addEventListener("input", (e) => {
  const el = e.target;
  if (!S.pDraft || S.page !== "profile") return;
  if (el.dataset.pf && el.type !== "checkbox") S.pDraft[el.dataset.pf] = el.value;
  if (el.dataset.fact !== undefined) S.pDraft.facts[+el.dataset.fact] = el.value;
  if (el.dataset.ps) {
    const key = el.dataset.ps, v = +el.value;
    S.pDraft[key] = v;
    const lbl = document.querySelector(`[data-ps-label="${key}"]`);
    if (lbl) lbl.innerHTML = `<b>${esc(t("רמה {n}", { n: v }))}:</b> ${esc(t(S.persona.sliders[key][v]))}`;
  }
});
document.addEventListener("change", (e) => {
  const el = e.target;
  if (!S.pDraft || S.page !== "profile") return;
  if (el.dataset.pf === "enabled") S.pDraft.enabled = el.checked;
  if (el.dataset.pf === "category") S.pDraft.category = el.value;
  if (el.id === "p-apply") S.pApply = el.checked;
  if (el.id === "pc-avatar-file" && el.files[0]) {
    if (el.files[0].size > 8 * 1024 * 1024) { toast("התמונה גדולה מדי (עד 8MB)", "error"); return; }
    const reader = new FileReader();
    reader.onload = () => { S.pAvatar = reader.result; S.pApply = true; render(); };
    reader.readAsDataURL(el.files[0]);
  }
});
document.addEventListener("keydown", (e) => {
  const el = e.target;
  if (el.dataset && el.dataset.tagInput && e.key === "Enter") {
    e.preventDefault();
    const v = el.value.trim(), key = el.dataset.tagInput;
    if (v && !S.pDraft[key].includes(v) && S.pDraft[key].length < 15) S.pDraft[key].push(v);
    render();
    const again = document.querySelector(`[data-tag-input="${key}"]`); if (again) again.focus();
  }
  if (el.id === "pchat-input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendPersonaChat(); }
});

/* ===== mouse Back / Forward buttons and Alt+Left / Alt+Right ===== */
let lastNavStep = 0;
async function navStep(dir) {
  lastNavStep = Date.now();
  const from = dir < 0 ? NAV_HISTORY.back : NAV_HISTORY.forward;
  const to = dir < 0 ? NAV_HISTORY.forward : NAV_HISTORY.back;
  if (!from.length) return;
  const target = from[from.length - 1], current = S.page;
  await go(target, true);
  if (S.page === target) { from.pop(); to.push(current); }
}
document.addEventListener("mouseup", (e) => {
  if (e.button === 3 || e.button === 4) { e.preventDefault(); navStep(e.button === 3 ? -1 : 1); }
});
document.addEventListener("mousedown", (e) => { if (e.button === 3 || e.button === 4) e.preventDefault(); });
document.addEventListener("keydown", (e) => {
  if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    const rtl = document.documentElement.dir === "rtl";
    const back = e.key === (rtl ? "ArrowRight" : "ArrowLeft");
    navStep(back ? -1 : 1);
  }
});
window.addEventListener("popstate", () => {  // a browser-level Back (just in case) also stays inside the app
  history.pushState({ app: true }, "");
  if (Date.now() - lastNavStep > 600) navStep(-1);  // not twice when the mouse button was already handled
});

/* ===== events ===== */
document.addEventListener("click", (e) => {
  if (!e.target.closest(".chan-combo")) document.querySelectorAll(".chan-pop").forEach((p) => p.remove());
  const sr = e.target.closest("[data-search-page]");
  if (sr) { closeSearch(); go(sr.dataset.searchPage); return; }
  if (e.target.id === "search") { closeSearch(); return; }
  const nav = e.target.closest("[data-nav]");
  if (nav) { e.preventDefault(); go(nav.dataset.nav); return; }
  const btn = e.target.closest("[data-action]");
  if (btn && actions[btn.dataset.action]) { e.preventDefault(); actions[btn.dataset.action](btn); return; }
  const card = e.target.closest("[data-open]");
  if (card && !e.target.closest(".switch")) go("mod:" + card.dataset.open);
});

function markDirty(e) {
  const t = e.target;
  if (t.closest && t.closest("#autos [data-ap], #autos [data-act], #autos [data-cond]")) { if (e.type === "change" || t.tagName !== "SELECT") setAutoValue(t); }
  if (t.dataset && t.dataset.autoToggle !== undefined) { S.autos[+t.dataset.autoToggle].enabled = t.checked; if (e.type === "change") rerenderAutos(); }
  if (t.closest("[data-module-form]") && !t.closest(".no-dirty") && !t.dataset.module) {
    setDirty(true);
    const rule = t.closest(".rule-row");
    if (rule && t.classList.contains("rule-on")) rule.classList.toggle("on", t.checked);
  }
}
document.addEventListener("input", markDirty);
document.addEventListener("input", (e) => { if (S.page === "mod:embed_messages" && e.target.closest("[data-module-form]")) refreshEmbedPreview(); });
document.addEventListener("change", (e) => { if (S.page === "mod:embed_messages" && e.target.closest("[data-module-form]")) refreshEmbedPreview(); });

document.addEventListener("change", async (e) => {
  markDirty(e);
  const t = e.target;
  if (t.id === "guild-select") {
    if (t.value === "__add") { renderGuildSelect(); go("permissions"); return; }
    if (S.dirty && !(await confirmDialog("יש שינויים שלא נשמרו", "להחליף שרת בלי לשמור?", "החלף"))) { renderGuildSelect(); return; }
    setDirty(false);
    S.guildId = t.value;
    S.cust = null;
    await loadGuild();
    if (S.page === "profile") { S.serverProfile = null; S.persona = null; render(); loadServerProfile(); if (profileScope() === "character") loadPersona(); }
    if (S.page === "customize") render(), afterCustomizeRender();
    return;
  }
  if (t.id === "log-filter") { S.logFilter = t.value; renderLogs(); return; }
  if (t.id === "ui-lang" || t.id === "ui-lang-2") { setUiLang(t.value); return; }
  if (t.id === "guild-lang") {
    try {
      S.guild = await api("PUT", `/guilds/${S.guildId}/language`, { language: t.value });
      toast(window.t("שפת הבוט עודכנה"), "ok");
      if (!S.dirty) render();
    } catch (err) { toast(errMessage(err), "error"); }
    return;
  }
  if (t.id === "sp-avatar-file" || t.id === "sp-banner-file") {
    const file = t.files[0];
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { toast("התמונה גדולה מדי (עד 8MB)", "error"); t.value = ""; return; }
    const reader = new FileReader();
    reader.onload = () => { if (t.id === "sp-banner-file") S.spBanner = reader.result; else S.spAvatar = reader.result; $("#main").innerHTML = viewProfile(); };
    reader.readAsDataURL(file);
    return;
  }
  if (t.id === "guild-bot") {
    try {
      S.guild = await api("PUT", `/guilds/${S.guildId}/bot`, { account: t.value });
      toast("נשמר", "ok");
      render();
    } catch (err) { toast(errMessage(err), "error"); }
    return;
  }
  if (t.id === "avatar-file" || t.id === "banner-file") {
    const file = t.files[0];
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { toast("התמונה גדולה מדי (עד 8MB)", "error"); t.value = ""; return; }
    const reader = new FileReader();
    const isBanner = t.id === "banner-file";
    reader.onload = () => { if (isBanner) S.bannerDraft = reader.result; else S.avatarDraft = reader.result; render(); };
    reader.readAsDataURL(file);
    return;
  }
  if (t.id === "pr-status" || t.id === "pr-activity") {
    const pr = presenceDraft();
    if (t.id === "pr-status") pr.status = t.value; else pr.activity = t.value;
    if (t.id === "pr-activity") { render(); return; }
    refreshProfilePreview();
    return;
  }
  if (t.dataset.setting) {
    try { S.settings = await api("PUT", "/settings", { [t.dataset.setting]: t.checked }); toast("נשמר", "ok"); if (t.dataset.setting === "start_with_windows") render(); }
    catch (err) { t.checked = !t.checked; toast(errMessage(err), "error"); }
    return;
  }
  if (t.dataset.perm) {
    if (t.dataset.perm === "administrator" && t.checked) {
      const ok = await confirmDialog("לתת הרשאת Administrator?",
        "Administrator נותן לבוט שליטה מלאה בשרת ועוקף את כל ההגבלות. אם הטוקן ידלוף, מי שמחזיק בו יוכל למחוק את השרת.\nמומלץ להשאיר כבוי ולבחור רק הרשאות ספציפיות.", "כן, אני מבין");
      if (!ok) { t.checked = false; return; }
    }
    if (t.checked) S.permSel.add(t.dataset.perm); else S.permSel.delete(t.dataset.perm);
    S.invite = null;
    return;
  }
  if (t.dataset.module) {
    try {
      S.guild = await api("PUT", `/guilds/${S.guildId}/modules/${t.dataset.module}`, { enabled: t.checked });
      S.catalog = await api("GET", "/catalog");
      S.permSel = null;
      toast(t.checked ? "המודול הופעל" : "המודול כובה", "ok");
      if (S.page.startsWith("cat:")) render();
    } catch (err) { t.checked = !t.checked; toast(errMessage(err), "error"); }
  }
});

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); return; }
  if ($("#search").classList.contains("hidden")) return;
  const res = searchResults();
  if (e.key === "Escape") closeSearch();
  else if (e.key === "ArrowDown") { searchSel = Math.min(searchSel + 1, res.length - 1); renderSearch(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { searchSel = Math.max(searchSel - 1, 0); renderSearch(); e.preventDefault(); }
  else if (e.key === "Enter" && res[searchSel]) { closeSearch(); go(res[searchSel].page); }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "search-input") { searchSel = 0; renderSearch(); }
  if (e.target.id === "pr-text") { presenceDraft().text = e.target.value; refreshProfilePreview(); }
  if (e.target.id === "pr-url") presenceDraft().url = e.target.value.trim();
  if (e.target.id === "bot-username" || e.target.id === "sp-nick") refreshProfilePreview();
});
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

/* ===== boot ===== */
async function boot() {
  history.pushState({ app: true }, "");  // so a browser-level Back never leaves the app
  renderNav();
  render();
  if (!TOKEN) {
    $("#main").innerHTML = `<div class="empty"><h3>אין הרשאת גישה</h3><p>פתח את האפליקציה דרך start.cmd.</p></div>`;
    return;
  }
  try { await loadI18n(); } catch (e) { toast(errMessage(e), "error"); }
  try {
    [S.settings, S.catalog] = await Promise.all([api("GET", "/settings"), api("GET", "/catalog")]);
  } catch (e) { toast(errMessage(e), "error"); }
  applyAppearance(S.settings && S.settings.settings.appearance);
  renderNav();
  connectWS();
  await refreshGuilds();
  render();
}

boot();
