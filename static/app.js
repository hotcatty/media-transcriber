/* 转录小工具 · 太空首页：粘贴链接 → 逐字稿 */

let currentTaskId = null;
let currentTask = null;
let eventSource = null;
let lastText = "";
let loginSite = "bilibili";
let loginHost = "bilibili.com";
let loginUrl = "https://www.bilibili.com";
let autoOpenedId = null;
let jobCollapsed = false;

let etaTimer = null;
let remainFloor = { id: null, value: null };

const $ = (id) => document.getElementById(id);

function formatRemain(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 8) return "即将完成";
  if (s < 90) return `剩余约${s}s`;
  return `剩余约${Math.max(1, Math.round(s / 60))}分钟`;
}

function formatModelRemain(seconds) {
  let tail = "可能还需要几分钟";
  if (seconds != null && !Number.isNaN(Number(seconds))) {
    const s = Math.max(0, Math.round(Number(seconds)));
    tail = s < 60 ? "可能还需要不到1分钟" : `可能还需要${Math.max(1, Math.round(s / 60))}分钟`;
  }
  return `首次使用需要下载语音分析模型，${tail}`;
}

function liveRemain(task) {
  const steps = (task && task.steps) || [];
  const cur = steps.find((s) => s.state === "current");
  if (!cur) return null;
  let remain = null;
  if (cur.eta_deadline) {
    remain = Math.max(0, Number(cur.eta_deadline) - Date.now() / 1000);
  } else if (cur.eta_seconds != null) {
    remain = Number(cur.eta_seconds);
    if (cur.eta_at) remain -= Date.now() / 1000 - Number(cur.eta_at);
    remain = Math.max(0, remain);
  }
  if (remain == null || !task || !task.id) return remain;
  if (remainFloor.id !== task.id) {
    remainFloor = { id: task.id, value: remain };
    return remain;
  }
  remain = Math.min(remain, remainFloor.value);
  remainFloor.value = remain;
  return remain;
}

function paintEta() {
  const step = document.querySelector("#jobPanel .step.current");
  if (!step || !currentTask) return;
  const remain = liveRemain(currentTask);
  if (remain == null) return;
  if (step.classList.contains("step--model")) {
    const el = step.querySelector(".step-hint--eta");
    if (el) el.textContent = formatModelRemain(remain);
    return;
  }
  const el = step.querySelector(".step-hint");
  if (!el) return;
  el.textContent = formatRemain(remain);
}

function startEtaClock() {
  if (etaTimer) return;
  etaTimer = setInterval(() => {
    if (!currentTask || !["queued", "running"].includes(currentTask.status)) {
      stopEtaClock();
      return;
    }
    paintEta();
  }, 1000);
}

function stopEtaClock() {
  if (!etaTimer) return;
  clearInterval(etaTimer);
  etaTimer = null;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function detailText(data, fallback) {
  if (data && typeof data.message === "string" && data.message.trim()) return data.message.trim();
  const d = data && data.detail;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d) && d[0] && d[0].msg) return d[0].msg;
  return fallback;
}

function looksLikeUrl(s) {
  s = String(s || "").trim();
  return /^(https?:\/\/|www\.)/i.test(s) || /spm_id_from=|vd_source=/.test(s);
}

function isValidShareUrl(s) {
  s = String(s || "").trim();
  if (!looksLikeUrl(s)) return false;
  if (/^www\./i.test(s)) s = "https://" + s;
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch (_) {
    return /spm_id_from=|vd_source=/.test(s);
  }
}

function isUrlFormatError(msg) {
  return /请粘贴|链接无效|正确格式|还不支持这个链接|unsupported url|no valid video url|打不开这个链接|无效的网址|不是视频|请重新填写/i.test(String(msg || ""));
}

function loginRequiredMsg(msg) {
  const low = String(msg || "").toLowerCase().replace(/[’‘]/g, "'");
  if (/充电视频/.test(String(msg || ""))) return false;
  return /登录状态|需要登录后|sign in|not a bot|login required|cookies are needed|use --cookies/.test(low);
}

const LOGIN_PARSE_HINT = "该视频需在登录状态下才能解析";
const CHARGE_NEED_LOGIN_HINT = "该视频为充电视频，请读取已充电账号的登录状态";

function asNeedsLogin(task) {
  const steps = Array.isArray(task.steps) ? task.steps.map((s) => (
    s.state === "failed" || s.state === "needs_login"
      ? { ...s, state: "needs_login", label: "解析失败", hint: LOGIN_PARSE_HINT }
      : s
  )) : [
    { id: "parse", label: "解析失败", state: "needs_login", hint: LOGIN_PARSE_HINT },
    { id: "download", label: "下载音频", state: "pending" },
    { id: "transcribe", label: "语音识别", state: "pending" },
    { id: "finalize", label: "整理文稿", state: "pending" },
  ];
  return { ...task, status: "needs_login", steps };
}

let toastTimer = null;
const toastedKeys = new Set();

function showToast(text, onceKey) {
  const el = $("toast");
  if (!el) return;
  const msg = text || "请输入正确格式的网址";
  if (loginRequiredMsg(msg)) return;
  if (onceKey) {
    if (toastedKeys.has(onceKey)) return;
    toastedKeys.add(onceKey);
  }
  el.textContent = msg;
  el.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
    toastTimer = null;
  }, 2400);
}

function platformOf(source) {
  const u = String(source || "").toLowerCase();
  if (u.includes("bilibili.com") || u.includes("b23.tv")) return "bilibili";
  if (u.includes("xiaoyuzhou")) return "xiaoyuzhou";
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "youtube";
  return "generic";
}

function platformIcon(source) {
  const p = platformOf(source);
  if (p === "bilibili") return mtAsset("/static/img/icon-bilibili.png");
  if (p === "xiaoyuzhou") return mtAsset("/static/img/icon-xiaoyuzhou.png");
  if (p === "youtube") return mtAsset("/static/img/icon-youtube-mark.svg");
  return mtAsset("/static/img/icon-xiaoyuzhou.png");
}

function syncLoginSite(task) {
  if (task && task.login_site) {
    loginSite = task.login_site;
    loginHost = task.login_host || (loginSite === "youtube" ? "youtube.com" : "bilibili.com");
    loginUrl = task.login_url || (loginSite === "youtube" ? "https://www.youtube.com" : "https://www.bilibili.com");
    return;
  }
  const p = platformOf(task && task.source);
  loginSite = p === "youtube" ? "youtube" : "bilibili";
  loginHost = loginSite === "youtube" ? "youtube.com" : "bilibili.com";
  loginUrl = loginSite === "youtube" ? "https://www.youtube.com" : "https://www.bilibili.com";
}

function siteLabel() {
  return loginSite === "youtube" ? "YouTube" : "B 站";
}

function setBusy(on) {
  const btn = $("submitBtn");
  const input = $("urlInput");
  btn.disabled = on;
  btn.classList.toggle("is-busy", on);
  if (on) {
    btn.innerHTML = `<img class="spin" src="${mtAsset("/static/img/icon-btn-spin.svg")}" alt="" width="24" height="24">`;
    input.readOnly = true;
  } else {
    syncSubmitLabel();
    input.readOnly = false;
  }
  syncUrlClear();
}

function syncSubmitLabel() {
  const btn = $("submitBtn");
  if (!btn || btn.classList.contains("is-busy")) return;
  const retry = currentTask && (currentTask.status === "failed" || currentTask.status === "needs_login");
  btn.innerHTML = retry
    ? `<span class="btn-label-full">重新转录</span><span class="btn-label-short">重试</span>`
    : `<span class="btn-label-full">转录为逐字稿</span><span class="btn-label-short">转录</span>`;
}

function syncUrlClear() {
  const input = $("urlInput");
  const wrap = $("urlWrap");
  if (!input || !wrap) return;
  const filled = !!input.value.trim() && !input.readOnly;
  wrap.classList.toggle("is-filled", filled);
}

function resetToIdle() {
  stopSSE();
  currentTaskId = null;
  currentTask = null;
  autoOpenedId = null;
  jobCollapsed = false;
  remainFloor = { id: null, value: null };
  setBusy(false);
  renderJob(null);
  const sheet = $("transcriptOverlay");
  if (sheet) sheet.hidden = true;
}

/* BETA: while a job is open, slide the desk to Figma Y=400. Set false to keep idle Y=475. */
const CENTER_JOB_DESK = true;

function syncDeskLayout() {
  const desk = document.querySelector(".desk");
  const panel = $("jobPanel");
  if (!desk) return;
  const open = CENTER_JOB_DESK && panel && !panel.hidden;
  desk.classList.toggle("is-centered", !!open);
}

function maybeResetToIdleFromUrl() {
  const input = $("urlInput");
  if (!input || input.readOnly || input.value.trim()) return;
  if (!currentTask && $("jobPanel") && $("jobPanel").hidden) return;
  resetToIdle();
}

function stepIcon(state) {
  const src =
    state === "current" ? mtAsset("/static/img/icon-spinner.svg")
    : state === "done" ? mtAsset("/static/img/icon-done.svg")
    : (state === "failed" || state === "needs_login") ? mtAsset("/static/img/icon-fail.svg")
    : mtAsset("/static/img/icon-pending.svg");
  const cls = state === "current" ? "step-ico is-spin" : "step-ico";
  return `<img class="${cls}" src="${src}" alt="" width="20" height="20">`;
}

function canCollapse(task) {
  return task && ["queued", "running", "needs_login", "failed", "interrupted"].includes(task.status);
}

function compactStatus(task) {
  const steps = Array.isArray(task.steps) ? task.steps : [];
  if (task.status === "failed") {
    const failed = steps.find((s) => s.state === "failed");
    if (failed && failed.id === "parse") return "解析失败";
    if (failed && failed.label) {
      return /失败$/.test(failed.label) ? failed.label : `${failed.label}失败`;
    }
    return "转录失败";
  }
  if (task.status === "interrupted") return "转录中断";
  if (task.status === "needs_login") {
    const cur = steps.find((s) => s.state === "needs_login");
    if (cur && cur.id !== "parse") return cur.label;
    return "解析失败";
  }
  const cur = steps.find((s) => s.state === "current" || s.state === "needs_login");
  if (cur) {
    if (cur.state === "needs_login") return "解析失败";
    const lab = cur.label || "";
    if (lab.startsWith("正在")) return lab;
    return lab ? `正在${lab}` : "转录中";
  }
  return "转录中";
}

function collapseJob() {
  if (!currentTask || !canCollapse(currentTask) || jobCollapsed) return;
  jobCollapsed = true;
  applyTask(currentTask);
}

function expandJob() {
  if (!currentTask || !jobCollapsed) return;
  jobCollapsed = false;
  applyTask(currentTask);
}

function renderJob(task) {
  const panel = $("jobPanel");
  if (!task || !task.status || task.status === "cancelled") {
    stopEtaClock();
    panel.hidden = true;
    panel.innerHTML = "";
    panel.classList.remove("is-collapsed");
    jobCollapsed = false;
    syncDeskLayout();
    return;
  }
  const status = task.status;
  const collapsed = jobCollapsed && canCollapse(task);
  const compact = status === "completed" || collapsed;
  const interrupted = status === "interrupted";
  const rawTitle = task.title && !looksLikeUrl(task.title) ? task.title : (collapsed ? (task.source || "转录任务") : (task.title || ""));
  const title = escapeHtml(rawTitle);
  const icon = platformIcon(task.source);
  let action = "";
  if (collapsed) {
    action = UI.actionBtn({
      id: "expandJobBtn",
      extra: "action--on-card",
      html: `${escapeHtml(compactStatus(task))}
      <img src="${mtAsset("/static/img/icon-job-chevron.svg")}" alt="" width="20" height="20">`,
    });
  } else if (status === "completed") {
    action = UI.actionBtn({
      extra: "action--on-card",
      attrs: `data-open="${task.id}"`,
      html: `查看文稿 <img src="${mtAsset("/static/img/icon-chevron-right.svg")}" alt="" width="20" height="20">`,
    });
  } else if (["queued", "running"].includes(status)) {
    action = UI.actionBtn({
      id: "stopBtn",
      extra: "action--stop",
      attrs: `aria-label="停止转录"`,
      html: `<span class="job-stop-label">停止转录</span>
      <img class="job-stop-x" src="${mtAsset("/static/img/icon-stop.svg")}" alt="" width="20" height="20">`,
    });
  } else if (interrupted) {
    action = UI.actionBtn({
      extra: "action--on-card",
      attrs: `data-resume="${task.id}"`,
      html: `继续转录`,
    });
  }

  const head = `<div class="job-head">
      <div class="job-title">
        <img src="${icon}" alt="" width="24" height="24">
        <span>${title}</span>
      </div>
      ${action ? `<div class="job-action-slot">${action}</div>` : ""}
    </div>`;

  let body = "";
  if (!compact) {
    const steps = Array.isArray(task.steps) ? task.steps : [];
    body = `<ul class="steps">${steps.map((s) => {
      let extra = "";
      if (s.state === "needs_login") {
        extra = `<div class="step-login">
          <span class="need">${escapeHtml(s.hint || (s.id === "download" ? CHARGE_NEED_LOGIN_HINT : LOGIN_PARSE_HINT))}
            ${UI.infoBtn(`data-inline-info="1"`)}
          </span>
          <button type="button" class="link" data-open-login="1">读取登录状态</button>
        </div>`;
      } else if (s.state === "failed") {
        const reason = s.hint || task.error || task.message || "";
        extra = reason ? `<span class="step-hint">${escapeHtml(reason)}</span>` : "";
      } else if (s.action === "view_audio") {
        extra = `<button type="button" class="link" data-reveal-audio="1">${escapeHtml(s.action_label || "查看音频")}</button>`;
      } else if (s.state === "current" && s.id === "model") {
        const size = s.size || "";
        const remain = liveRemain(task);
        const hint = formatModelRemain(remain);
        extra = `<span class="step-extra">${size ? `<span class="step-hint">${escapeHtml(size)}</span>` : ""}<span class="step-hint step-hint--eta">${escapeHtml(hint)}</span></span>`;
      } else if (s.state === "current") {
        const remain = liveRemain(task);
        const countdown = remain != null ? formatRemain(remain) : (s.eta || s.hint || "即将完成");
        extra = `<span class="step-extra"><span class="step-hint">${escapeHtml(countdown)}</span></span>`;
      } else if (s.state === "done" && s.hint) {
        extra = `<span class="step-hint">${escapeHtml(s.hint)}</span>`;
      }
      const tight = s.action === "view_audio";
      const meta = extra && tight ? " has-meta" : "";
      const kind = s.id === "model" ? " step--model" : "";
      return `<li class="step ${s.state}${kind}${meta}"><span class="step-main">${stepIcon(s.state)}<span class="step-label">${escapeHtml(s.label)}</span></span>${extra}</li>`;
    }).join("")}</ul>`;
  }

  panel.hidden = false;
  panel.classList.toggle("is-collapsed", collapsed);
  panel.innerHTML = `${head}${body}`;
  syncDeskLayout();
}

function applyTask(task, opts) {
  if (!task || task.type === "heartbeat") return;
  const fromHistory = !!(opts && opts.fromHistory);
  currentTask = task;
  currentTaskId = task.id || currentTaskId;
  syncLoginSite(task);
  if (task.source && (fromHistory || !$("urlInput").value)) $("urlInput").value = task.source;
  syncUrlClear();

  if (task.status === "failed" && loginRequiredMsg(task.error || task.message)) {
    task = asNeedsLogin(task);
    currentTask = task;
  }

  if (["queued", "running"].includes(task.status)) {
    setBusy(true);
    renderJob(task);
    startEtaClock();
  } else if (task.status === "needs_login") {
    stopEtaClock();
    setBusy(false);
    renderJob(task);
  } else if (task.status === "completed") {
    stopEtaClock();
    setBusy(false);
    renderJob(task);
    if (!fromHistory && task.id && autoOpenedId !== task.id) {
      autoOpenedId = task.id;
      openTranscript(task);
    }
  } else if (task.status === "failed") {
    stopEtaClock();
    setBusy(false);
    renderJob(task);
  } else if (task.status === "cancelled") {
    stopEtaClock();
    setBusy(false);
    renderJob(null);
  } else if (task.status === "interrupted") {
    stopEtaClock();
    setBusy(false);
    renderJob(task);
  }
}

function startSSE(taskId) {
  stopSSE();
  currentTaskId = taskId;
  eventSource = new EventSource(mtApi(`/api/task-stream/${taskId}`));
  eventSource.onmessage = (ev) => {
    try {
      const task = JSON.parse(ev.data);
      applyTask(task);
      if (["completed", "failed", "cancelled", "needs_login"].includes(task.status)) stopSSE();
    } catch (_) {}
  };
  eventSource.onerror = async () => {
    stopSSE();
    try {
      const r = await fetch(mtApi(`/api/task-status/${taskId}`));
      if (!r.ok) return;
      const task = await r.json();
      applyTask(task);
      if (task.status === "running" || task.status === "queued") startSSE(taskId);
    } catch (_) {
      /* keep the last rendered card; don't snap back to idle */
    }
  };
}

function stopSSE() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

async function submitUrl(url) {
  if (!isValidShareUrl(url)) {
    showToast("请输入正确格式的网址");
    return;
  }
  jobCollapsed = false;
  autoOpenedId = null;
  setBusy(true);
  const pending = {
    status: "running",
    title: url,
    source: url,
    steps: [
      { id: "parse", label: "解析链接", state: "current", eta_seconds: 12, eta_deadline: Date.now() / 1000 + 12 },
      { id: "download", label: "下载音频", state: "pending" },
      { id: "transcribe", label: "语音识别", state: "pending" },
      { id: "finalize", label: "整理文稿", state: "pending" },
    ],
  };
  currentTask = pending;
  renderJob(pending);
  startEtaClock();
  try {
    const fd = new FormData();
    fd.append("url", url);
    const resp = await fetch(mtApi("/api/transcribe"), { method: "POST", body: fd });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(detailText(data, `HTTP ${resp.status}`));
    if (data.status === "completed" && data.task_id) {
      const r = await fetch(mtApi(`/api/tasks/${data.task_id}`));
      if (r.ok) applyTask(await r.json());
      return;
    }
    startSSE(data.task_id);
  } catch (e) {
    setBusy(false);
    const msg = e.message || "请输入正确格式的网址";
    if (loginRequiredMsg(msg)) {
      currentTask = asNeedsLogin({ title: url, source: url });
      renderJob(currentTask);
      return;
    }
    renderJob(null);
    showToast(isUrlFormatError(msg) ? "请输入正确格式的网址" : msg);
  }
}

function openOverlay(id) { $(id).hidden = false; }
function closeOverlay(id) { $(id).hidden = true; }

function setLoginMsg(text, ok) {
  const el = $("loginMsg");
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("ok", !!ok);
}

function looksLikeNotLoggedIn(msg) {
  return /还没|未登录|没有登录|读不到/.test(msg || "");
}

function fillHelp() {
  $("helpBody").textContent =
    `可能是这台电脑上的浏览器还未登录 ${loginHost}，因此无法获取登录 Cookie。`;
  $("helpGo").href = loginUrl;
  $("helpGo").textContent = `前往${loginHost}`;
}

function openLogin() {
  setLoginMsg("");
  $("loginInfoPop").hidden = true;
  $("loginInfoBtn").setAttribute("aria-expanded", "false");
  fillHelp();
  openOverlay("loginOverlay");
}

async function loadBrowsers() {
  const sel = $("browserSelect");
  try {
    const r = await fetch(mtApi("/api/browsers"));
    const data = await r.json();
    const list = data.browsers || [];
    const opts = list.length ? list : [
      { id: "chrome", label: "Chrome" },
      { id: "safari", label: "Safari" },
      { id: "edge", label: "Edge" },
      { id: "firefox", label: "Firefox" },
    ];
    sel.innerHTML = opts.map((b) =>
      `<option value="${escapeHtml(b.id)}">${escapeHtml(b.label)}</option>`
    ).join("");
  } catch (_) {
    sel.innerHTML = `
      <option value="chrome">Chrome</option>
      <option value="safari">Safari</option>
      <option value="edge">Edge</option>
      <option value="firefox">Firefox</option>`;
  }
}

async function importCookies() {
  const btn = $("importCookieBtn");
  const browser = $("browserSelect").value;
  btn.disabled = true;
  btn.textContent = "正在读取…";
  setLoginMsg("");
  try {
    const fd = new FormData();
    fd.append("browser", browser);
    fd.append("site", loginSite);
    const resp = await fetch(mtApi("/api/import-cookies"), { method: "POST", body: fd });
    const data = await resp.json().catch(() => ({}));
    const msg = detailText(data, "读取失败");
    if (!resp.ok) {
      if (looksLikeNotLoggedIn(msg)) {
        closeOverlay("loginOverlay");
        fillHelp();
        openOverlay("helpOverlay");
      } else {
        setLoginMsg(msg, false);
      }
      return;
    }
    setLoginMsg(data.message || "已读取", true);
    closeOverlay("loginOverlay");
    if (currentTaskId) {
      const r = await fetch(mtApi(`/api/tasks/${currentTaskId}/resume`), { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setLoginMsg(detailText(body, "无法继续解析"), false);
        openOverlay("loginOverlay");
        return;
      }
      setBusy(true);
      startSSE(currentTaskId);
    }
  } catch (_) {
    setLoginMsg("读取失败，请确认本机服务还在运行", false);
  } finally {
    btn.disabled = false;
    btn.textContent = "点击读取";
  }
}

async function openTranscript(task) {
  currentTaskId = task.id;
  currentTask = task;
  syncLoginSite(task);
  $("sheetTitle").textContent = task.title || "逐字稿";
  $("sheetWhen").textContent = task.created_label
    ? `创建时间 ${task.created_label}`
    : "";
  const src = $("sheetSource");
  if (looksLikeUrl(task.source)) {
    src.hidden = false;
    src.href = task.source.startsWith("http") ? task.source : `https://${task.source}`;
  } else {
    src.hidden = true;
  }
  const hasAudio = !!(task.has_audio && String(task.origin || "") !== "subtitle");
  $("openAudioBtn").hidden = !hasAudio;
  $("sheetBody").textContent = "正在读取…";
  const clip = $("sheetClip");
  if (clip) clip.scrollTop = 0;
  openOverlay("transcriptOverlay");
  try {
    const r = await fetch(mtApi(`/api/tasks/${task.id}/text`));
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      $("sheetBody").textContent = "";
      showToast(detailText(data, "暂时读不到正文"));
      return;
    }
    lastText = data.text || "";
    $("sheetBody").textContent = lastText;
  } catch (_) {
    $("sheetBody").textContent = "";
    showToast("暂时读不到正文");
  }
}

async function copyText() {
  const text = lastText || $("sheetBody").textContent;
  if (!text || !text.trim()) {
    showToast("还没有可复制的正文");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast("全文已复制");
  } catch (_) {
    showToast("复制失败，请稍后再试");
  }
}

async function downloadFmt(fmt) {
  if (!currentTaskId) return;
  try {
    const r = await fetch(mtApi(`/api/tasks/${currentTaskId}/export/${fmt}`));
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      showToast(detailText(data, "还没有可导出的正文"));
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `transcript.${fmt}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (_) {
    showToast("导出失败，请稍后再试");
  }
}

function hideAudioActions() {
  const btn = $("openAudioBtn");
  if (btn) btn.hidden = true;
  document.querySelectorAll("[data-reveal-audio]").forEach((el) => el.remove());
}

async function revealAudio() {
  if (!currentTaskId) return;
  if (currentTask && String(currentTask.origin || "") === "subtitle") {
    hideAudioActions();
    showToast("这个任务用的是现成字幕，没有下载音频");
    return;
  }
  try {
    const r = await fetch(mtApi(`/api/tasks/${currentTaskId}/reveal-audio`), { method: "POST" });
    const data = await r.json().catch(() => ({}));
    if (r.ok && data.ok !== false) return;
    const msg = detailText(data, "音频文件已丢失");
    showToast(msg);
    if (/已丢失|没有音频|现成字幕/.test(msg)) hideAudioActions();
  } catch (_) {
    showToast("音频文件已丢失");
    hideAudioActions();
  }
}

function histStatus(t) {
  if (t.status === "interrupted" || (t.status === "cancelled" && t.resumable)) {
    return `<button type="button" class="hist-status warn" data-resume="${t.id}">转录中断，继续转录</button>`;
  }
  if (t.status === "needs_login") {
    return `<button type="button" class="hist-status login" data-resume-login="${t.id}">需要登录，读取登录状态</button>`;
  }
  if (t.status === "failed") {
    return `<button type="button" class="hist-status bad" data-open="${t.id}">转录失败，查看原因</button>`;
  }
  return "";
}

async function loadHistory() {
  const list = $("historyList");
  try {
    const r = await fetch(mtApi("/api/tasks"));
    const data = await r.json();
    const tasks = data.tasks || [];
    if (!tasks.length) {
      list.innerHTML = `<p class="hist-empty">暂无记录</p>`;
      return;
    }
    list.innerHTML = tasks.map((t) => UI.histRow({
      id: t.id,
      status: t.status,
      title: escapeHtml(t.title || ""),
      when: escapeHtml(t.created_label || ""),
      statusHtml: histStatus(t),
      icon: platformIcon(t.source),
    })).join("");
  } catch (_) {
    list.innerHTML = `<p class="hist-empty">加载失败</p>`;
  }
}

function closeHistMenus() {
  document.querySelectorAll(".hist-more-wrap.is-open").forEach((wrap) => {
    wrap.classList.remove("is-open");
    const btn = wrap.querySelector(".icon-btn--more");
    const img = wrap.querySelector(".icon-btn--more img.dots-off");
    const menu = wrap.querySelector(".menu--hist");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (img) img.src = mtAsset("/static/img/icon-hist-dots.svg");
    if (menu) menu.hidden = true;
  });
}

function toggleHistMenu(wrap) {
  const open = wrap.classList.contains("is-open");
  closeHistMenus();
  if (open) return;
  wrap.classList.add("is-open");
  const btn = wrap.querySelector(".icon-btn--more");
  const img = wrap.querySelector(".icon-btn--more img.dots-off");
  const menu = wrap.querySelector(".menu--hist");
  if (btn) btn.setAttribute("aria-expanded", "true");
  if (img) img.src = mtAsset("/static/img/icon-hist-dots-on.svg");
  if (menu) menu.hidden = false;
}

async function openHistoryItem(id) {
  const r = await fetch(mtApi(`/api/tasks/${id}`));
  if (!r.ok) return;
  const task = await r.json();
  jobCollapsed = false;
  if (task.status === "completed") {
    closeOverlay("historyOverlay");
    applyTask(task, { fromHistory: true });
    openTranscript(task);
  } else {
    closeOverlay("historyOverlay");
    applyTask(task, { fromHistory: true });
  }
}

async function resumeTask(id) {
  try {
    const resp = await fetch(mtApi(`/api/tasks/${id}/resume`), { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      showToast(detailText(data, "无法继续转录"));
      return;
    }
  } catch (_) {
    showToast("无法继续转录");
    return;
  }
  jobCollapsed = false;
  closeOverlay("historyOverlay");
  setBusy(true);
  startSSE(id);
}

document.addEventListener("DOMContentLoaded", () => {
  loadBrowsers();
  syncUrlClear();

  $("urlInput").addEventListener("input", () => {
    syncUrlClear();
    maybeResetToIdleFromUrl();
  });
  $("urlClear").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const input = $("urlInput");
    input.value = "";
    syncUrlClear();
    maybeResetToIdleFromUrl();
    input.focus();
  });

  $("mainCard").addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("urlInput").value.trim();
    if (!isValidShareUrl(url)) {
      showToast("请输入正确格式的网址");
      return;
    }
    submitUrl(url);
  });

  $("jobPanel").addEventListener("click", async (e) => {
    if (e.target.closest("#expandJobBtn")) {
      e.stopPropagation();
      expandJob();
      return;
    }
    if (e.target.closest("#stopBtn")) {
      const failed = currentTask && currentTask.status === "failed";
      if (!failed && currentTaskId) {
        await fetch(mtApi(`/api/tasks/${currentTaskId}/cancel`), { method: "POST" });
      }
      resetToIdle();
      return;
    }
    if (e.target.closest("[data-open]")) {
      const id = e.target.closest("[data-open]").getAttribute("data-open");
      const r = await fetch(mtApi(`/api/tasks/${id}`));
      if (r.ok) openTranscript(await r.json());
      return;
    }
    if (e.target.closest("[data-resume]")) {
      await resumeTask(e.target.closest("[data-resume]").getAttribute("data-resume"));
      return;
    }
    if (e.target.closest("[data-reveal-audio]")) {
      e.stopPropagation();
      revealAudio();
      return;
    }
    if (e.target.closest("[data-open-login]")) {
      openLogin();
      return;
    }
    if (e.target.closest("[data-inline-info]")) {
      openLogin();
      $("loginInfoPop").hidden = false;
      $("loginInfoBtn").setAttribute("aria-expanded", "true");
    }
  });

  $("historyBtn").onclick = async () => {
    await loadHistory();
    openOverlay("historyOverlay");
  };
  $("historyClose").onclick = () => closeOverlay("historyOverlay");
  $("loginClose").onclick = () => closeOverlay("loginOverlay");
  $("helpClose").onclick = () => closeOverlay("helpOverlay");
  $("sheetClose").onclick = () => closeOverlay("transcriptOverlay");

  $("loginInfoBtn").onclick = () => {
    const pop = $("loginInfoPop");
    pop.hidden = !pop.hidden;
    $("loginInfoBtn").setAttribute("aria-expanded", String(!pop.hidden));
  };
  $("noCookieBtn").onclick = () => {
    closeOverlay("loginOverlay");
    fillHelp();
    openOverlay("helpOverlay");
  };
  $("importCookieBtn").onclick = importCookies;

  $("copyBtn").onclick = copyText;
  $("openAudioBtn").onclick = revealAudio;
  $("exportBtn").onclick = (e) => {
    e.stopPropagation();
    $("exportMenu").hidden = !$("exportMenu").hidden;
  };
  $("exportMenu").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      downloadFmt(b.dataset.fmt);
      $("exportMenu").hidden = true;
    };
  });

  document.querySelector(".stage").addEventListener("click", (e) => {
    if (e.target.closest(".card, #historyBtn")) return;
    collapseJob();
  });

  document.querySelectorAll(".overlay").forEach((ov) => {
    ov.addEventListener("click", (e) => {
      if (e.target === ov) ov.hidden = true;
    });
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".export")) $("exportMenu").hidden = true;
    if (!e.target.closest(".hist-more-wrap")) closeHistMenus();
  });

  $("historyList").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const item = e.target.closest(".hist-item");
    if (!item || e.target !== item) return;
    e.preventDefault();
    openHistoryItem(item.getAttribute("data-open"));
  });
  $("historyList").addEventListener("click", async (e) => {
    const menuBtn = e.target.closest("[data-menu]");
    const resume = e.target.closest("[data-resume]");
    const resumeLogin = e.target.closest("[data-resume-login]");
    const del = e.target.closest("[data-del]");
    const open = e.target.closest("[data-open]");
    if (menuBtn) {
      e.stopPropagation();
      toggleHistMenu(menuBtn.closest(".hist-more-wrap"));
      return;
    }
    if (resume) {
      await resumeTask(resume.getAttribute("data-resume"));
      return;
    }
    if (resumeLogin) {
      const id = resumeLogin.getAttribute("data-resume-login");
      const r = await fetch(mtApi(`/api/tasks/${id}`));
      if (r.ok) {
        const task = await r.json();
        jobCollapsed = false;
        applyTask(task);
        closeOverlay("historyOverlay");
        openLogin();
      }
      return;
    }
    if (del) {
      e.stopPropagation();
      const id = del.getAttribute("data-del");
      await fetch(mtApi(`/api/tasks/${id}`), { method: "DELETE" });
      if (id === currentTaskId) resetToIdle();
      closeHistMenus();
      loadHistory();
      return;
    }
    if (open) {
      await openHistoryItem(open.getAttribute("data-open"));
    }
  });

  try {
    const params = new URLSearchParams(location.search);
    const share = params.get("url");
    const go = params.get("go") === "1";
    if (share && !$("urlInput").value) {
      $("urlInput").value = share;
      syncUrlClear();
    }
    if (share && go) {
      history.replaceState({}, "", "/");
      $("mainCard").requestSubmit();
    } else {
      fetch(mtApi("/api/tasks")).then((r) => r.json()).then((data) => {
        const running = (data.tasks || []).find((t) =>
          t.status === "running" || t.status === "queued" || t.status === "needs_login"
        );
        if (running) {
          applyTask(running);
          if (running.status === "running" || running.status === "queued") startSSE(running.id);
        }
      }).catch(() => {});
    }
  } catch (_) {}
});

window.addEventListener("beforeunload", stopSSE);
