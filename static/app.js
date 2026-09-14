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
let startGen = 0;

const SERVICE_ZIP_MAC = "https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-macOS.zip";
const SERVICE_ZIP_WIN = "https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-windows.zip";

const $ = (id) => document.getElementById(id);

const OP_LOG_MAX = 240;
const opLog = [];

function safeUrl(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  try {
    const u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`);
    return `${u.host}${u.pathname}`.slice(0, 180);
  } catch (_) {
    return "url";
  }
}

function opNote(event, detail) {
  const row = { t: new Date().toISOString(), event: String(event || "") };
  if (detail && typeof detail === "object") {
    Object.keys(detail).forEach((k) => {
      if (/cookie|password|token|secret|sess/i.test(k)) return;
      const v = detail[k];
      if (v == null) return;
      row[k] = typeof v === "string" ? v.slice(0, 240) : v;
    });
  }
  opLog.push(row);
  if (opLog.length > OP_LOG_MAX) opLog.shift();
}

function opLogText() {
  const head = [
    "转录小工具 操作日志",
    `导出时间 ${new Date().toISOString()}`,
    `页面 ${safeUrl(location.href) || location.pathname}`,
    `检测系统 ${detectedServicePlatform() || "未知"}`,
    `当前选择 ${currentServicePlatform() || "无"}`,
    `本机接口 ${window.MT_API ? "已指向本机" : "未指向"}`,
    `浏览器 ${String(navigator.userAgent || "").slice(0, 240)}`,
    "",
  ];
  const lines = opLog.map((row) => {
    const { t, event, ...rest } = row;
    const extra = Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : "";
    return `${t}  ${event}${extra}`;
  });
  return head.concat(lines).join("\n");
}

function downloadOpLog() {
  const blob = new Blob([opLogText()], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "转录小工具-操作日志.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  opNote("log.downloaded", { lines: opLog.length });
}

function isMacDesktop() {
  const ua = navigator.userAgent || "";
  return /Mac/i.test(ua) && !/iPhone|iPad|iPod/i.test(ua);
}

function isWindowsDesktop() {
  return /Windows|Win64|Win32/i.test(navigator.userAgent || "");
}

let serviceChoice = "";
try {
  const os = new URLSearchParams(location.search).get("os");
  if (os === "mac" || os === "win") serviceChoice = os;
} catch (_) {}

let labPreview = false;

function detectedServicePlatform() {
  if (isMacDesktop()) return "mac";
  if (isWindowsDesktop()) return "win";
  return "";
}

function currentServicePlatform() {
  return serviceChoice || detectedServicePlatform();
}

function timeoutSignal(ms) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(ms);
  }
  const ctrl = new AbortController();
  setTimeout(() => ctrl.abort(), ms);
  return ctrl.signal;
}

async function serviceState() {
  const base = window.MT_API || "";
  try {
    const r = await fetch(`${base}/api/ping`, {
      mode: base ? "cors" : "same-origin",
      cache: "no-store",
      signal: timeoutSignal(900),
    });
    if (!r.ok) return "down";
    const j = await r.json();
    if (!(j && j.ok && j.app === "media-transcriber")) return "down";
    return j.preparing ? "starting" : "ready";
  } catch (_) {
    return "down";
  }
}

const SERVICE_LEAD = "仅首次使用需要安装，所有功能均在本地运行，不涉及隐私问题。";
const SERVICE_LEAD_OTHER = "目前测试包支持 Mac 和 Windows。请用电脑打开这一页后再下载。";

function paintServiceGuide() {
  const platform = currentServicePlatform();
  const lead = $("serviceLead");
  if (lead) lead.textContent = platform ? SERVICE_LEAD : SERVICE_LEAD_OTHER;
  const show = (id, on) => {
    const el = $(id);
    if (el) el.hidden = !on;
  };
  show("serviceGuideMac", platform === "mac");
  show("serviceGuideWin", platform === "win");
  const zipLink = $("serviceZipLink");
  const zip = serviceZipFor(platform);
  if (zipLink) {
    zipLink.hidden = !zip;
    if (zip) {
      zipLink.href = zip;
      zipLink.target = "_blank";
    }
  }
  document.querySelectorAll("[data-service-os]").forEach((btn) => {
    const on = btn.getAttribute("data-service-os") === platform;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", String(on));
  });
}

function openServiceGuide() {
  paintServiceGuide();
  openOverlay("serviceOverlay");
}

function serviceZipFor(platform) {
  if (platform === "mac") return SERVICE_ZIP_MAC;
  if (platform === "win") return SERVICE_ZIP_WIN;
  return "";
}

function fetchServicePackage() {
  const platform = currentServicePlatform();
  const zip = serviceZipFor(platform);
  if (!zip) {
    opNote("download.skip", { reason: "no-package", platform });
    return false;
  }
  let opened = false;
  try {
    opened = !!window.open(zip, "_blank", "noopener");
  } catch (err) {
    opNote("download.open-error", { platform, message: String(err && err.message || err) });
  }
  opNote("download.start", { platform, zip, opened });
  return true;
}

function goDownloadService() {
  opNote("download.click", { platform: currentServicePlatform() });
  fetchServicePackage();
  openServiceGuide();
}

function waitingForService(task) {
  const steps = (task && task.steps) || [];
  return steps.some((s) => s.id === "model" && s.need_service);
}

function paintFirstUse(url) {
  currentTask = {
    status: "running",
    title: url,
    source: url,
    steps: [
      { id: "model", label: "下载turbo模型", state: "current", need_service: true },
      { id: "parse", label: "解析视频", state: "pending" },
      { id: "download", label: "下载音频", state: "pending" },
      { id: "transcribe", label: "语音识别", state: "pending" },
      { id: "finalize", label: "整理文稿", state: "pending" },
    ],
  };
  renderJob(currentTask);
  startEtaClock();
  opNote("first-use", { host: safeUrl(url), platform: currentServicePlatform() });
}

function paintParsePending(url) {
  currentTask = {
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
  renderJob(currentTask);
  startEtaClock();
}

async function waitUntilReady(ms) {
  const gen = startGen;
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (gen !== startGen) return false;
    if (await serviceState() === "ready") return true;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return false;
}

async function bringServiceUp(url) {
  startGen += 1;
  paintFirstUse(url);
  if (await waitUntilReady(8000)) {
    closeOverlay("serviceOverlay");
    return true;
  }
  if (!currentTask) return false;
  const ready = await waitUntilReady(10 * 60 * 1000);
  if (ready) closeOverlay("serviceOverlay");
  return ready;
}

function formatRemain(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 90) return `剩余约${Math.max(1, s)}s`;
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
  if (cur.id === "transcribe") {
    const detail = (task && task.detail) || {};
    const dur = Number(task.duration || detail.total_seconds || 0);
    const processed = Number(detail.processed_seconds || 0);
    const speed = Number(task.speed_x || detail.speed_x || 0);
    if (dur > 0 && speed > 0.1) {
      let computed = Math.max(0, (dur - processed) / speed);
      if (detail.eta_at) computed = Math.max(0, computed - (Date.now() / 1000 - Number(detail.eta_at)));
      if (remain == null || remain < 8 || computed > remain + 8) remain = computed;
    }
  }
  if (remain == null || !task || !task.id) return remain;
  if (cur.id === "model" || cur.id === "transcribe") return remain;
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
  if (waitingForService(currentTask)) return;
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

function isNetworkDown(msg) {
  return /failed to fetch|networkerror|load failed|network request failed|failed to load|the network connection was lost|network error|err_connection|econnrefused|ns_error/i.test(String(msg || ""));
}

function humanizeError(raw) {
  const msg = String(raw || "").trim();
  if (!msg) return "出了点问题，请稍后再试";
  if (isUrlFormatError(msg)) return "请输入正确格式的网址";
  if (isNetworkDown(msg)) return "连不上本机转录服务，暂时没法开始转";
  if (/^HTTP\s*[45]\d\d/i.test(msg) || /internal server error|bad gateway|service unavailable/i.test(msg)) {
    return "本机服务出了点问题，暂时没法完成";
  }
  if (/abort|the operation was aborted/i.test(msg)) return "请求中断了，请再试一次";
  if (/[\u4e00-\u9fff]/.test(msg)) return msg;
  return "出了点问题，暂时没法完成这一步";
}

const LOGIN_PARSE_HINT = "该视频需在登录状态下才能解析";
const LOGIN_NEED_TIP = "没有登录时拿不到可解析的地址，需要读取本机已有的登录状态。";
const CHARGE_NEED_LOGIN_HINT = "该视频为充电视频，请读取已充电账号的登录状态";
const CHARGE_NEED_TIP = "充电内容只对已充电账号开放，需要读取那个账号的登录状态。";

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
  const incoming = String(text || "").trim();
  if (loginRequiredMsg(incoming)) return;
  const msg = humanizeError(incoming || "出了点问题，请稍后再试");
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
  }, msg.length > 24 ? 5600 : 2400);
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
  startGen += 1;
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
    if (cur.id === "model") return "正在下载模型";
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
        const charged = s.id === "download" || /充电/.test(s.hint || "");
        const tip = charged ? CHARGE_NEED_TIP : LOGIN_NEED_TIP;
        extra = `<div class="step-login">
          <span class="need has-tip">${escapeHtml(s.hint || (charged ? CHARGE_NEED_LOGIN_HINT : LOGIN_PARSE_HINT))}
            ${UI.infoBtn(`data-inline-info="1"`, "为什么需要登录")}
            <span class="need-tip" role="tooltip">${escapeHtml(tip)}</span>
          </span>
          <button type="button" class="link" data-open-login="1">读取登录状态</button>
        </div>`;
      } else if (s.state === "failed") {
        const reason = s.hint || task.error || task.message || "";
        extra = reason ? `<span class="step-hint">${escapeHtml(reason)}</span>` : "";
      } else if (s.action === "view_audio") {
        extra = `<button type="button" class="link" data-reveal-audio="1">${escapeHtml(s.action_label || "查看音频")}</button>`;
      } else if (s.state === "current" && s.id === "model") {
        if (s.need_service) {
          extra = `<span class="step-extra"><span class="step-hint">首次使用需要下载语音分析模型</span><button type="button" class="link" data-download-service="1">前往下载</button></span>`;
        } else {
          const size = s.size || "";
          const remain = liveRemain(task);
          const hint = formatModelRemain(remain);
          extra = `<span class="step-extra">${size ? `<span class="step-hint">${escapeHtml(size)}</span>` : ""}<span class="step-hint step-hint--eta">${escapeHtml(hint)}</span></span>`;
        }
      } else if (s.state === "current") {
        const remain = liveRemain(task);
        const countdown = remain != null ? formatRemain(remain) : (s.eta || s.hint || "剩余约1s");
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

async function postTranscribe(url) {
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
}

async function submitUrl(url) {
  if (!isValidShareUrl(url)) {
    showToast("请输入正确格式的网址");
    return;
  }
  jobCollapsed = false;
  autoOpenedId = null;
  setBusy(true);
  opNote("submit", { host: safeUrl(url), service: window.MT_API ? "remote" : "same-origin" });
  try {
    if (window.MT_API && (await serviceState()) !== "ready") {
      const up = await bringServiceUp(url);
      if (!up) {
        if (!currentTask) return;
        paintFirstUse(url);
        return;
      }
    } else {
      paintParsePending(url);
    }
    await postTranscribe(url);
  } catch (e) {
    const msg = e.message || "";
    if (loginRequiredMsg(msg)) {
      setBusy(false);
      currentTask = asNeedsLogin({ title: url, source: url });
      renderJob(currentTask);
      return;
    }
    if (window.MT_API && isNetworkDown(msg)) {
      const up = await bringServiceUp(url);
      if (up) {
        try {
          await postTranscribe(url);
          return;
        } catch (retryErr) {
          if (loginRequiredMsg(retryErr.message || "")) {
            setBusy(false);
            currentTask = asNeedsLogin({ title: url, source: url });
            renderJob(currentTask);
            return;
          }
          if (isNetworkDown(retryErr.message || "")) {
            if (currentTask) paintFirstUse(url);
            return;
          }
          setBusy(false);
          renderJob(null);
          showToast(retryErr.message || msg);
          return;
        }
      }
      if (!currentTask) return;
      paintFirstUse(url);
      return;
    }
    setBusy(false);
    renderJob(null);
    showToast(msg);
  }
}

function openOverlay(id) { $(id).hidden = false; }
function closeOverlay(id) { $(id).hidden = true; }

function showReadFail(msg) {
  const body = $("readFailBody");
  const text = String(msg || "读取失败，请再试一次");
  if (body) body.textContent = text;
  closeOverlay("loginOverlay");
  openOverlay("readFailOverlay");
  opNote("cookies.fail", { message: text });
}

function retryReadLogin() {
  closeOverlay("readFailOverlay");
  openLogin();
  opNote("cookies.retry", {});
}

function openLogin() {
  const tip = $("loginInfoBtn") && $("loginInfoBtn").closest(".has-tip");
  if (tip) tip.classList.remove("is-open");
  closeOverlay("readFailOverlay");
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
  opNote("cookies.read", { browser, site: loginSite });
  try {
    const fd = new FormData();
    fd.append("browser", browser);
    fd.append("site", loginSite);
    const resp = await fetch(mtApi("/api/import-cookies"), { method: "POST", body: fd });
    const data = await resp.json().catch(() => ({}));
    const msg = detailText(data, "读取失败");
    if (!resp.ok) {
      showReadFail(msg);
      return;
    }
    opNote("cookies.ok", { browser, site: loginSite });
    closeOverlay("loginOverlay");
    if (currentTaskId) {
      const r = await fetch(mtApi(`/api/tasks/${currentTaskId}/resume`), { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        showReadFail(detailText(body, "无法继续解析"));
        return;
      }
      setBusy(true);
      startSSE(currentTaskId);
    }
  } catch (_) {
    showReadFail("读取失败，请确认本机服务还在运行");
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

const LAB_SAMPLE = "https://www.bilibili.com/video/BV1VoYV67EkM";

function runLabScene(scene) {
  if (!scene) return false;
  labPreview = true;
  if ($("urlInput")) {
    $("urlInput").value = LAB_SAMPLE;
    syncUrlClear();
  }
  if (scene === "lab") {
    openOverlay("labOverlay");
    return true;
  }
  if (scene === "first-use") {
    paintFirstUse(LAB_SAMPLE);
    return true;
  }
  if (scene === "install") {
    openServiceGuide();
    return true;
  }
  const sample = {
    title: "示例视频",
    source: LAB_SAMPLE,
  };
  if (scene === "login") {
    applyTask(asNeedsLogin(sample));
    openLogin();
    return true;
  }
  if (scene === "login-fail") {
    applyTask(asNeedsLogin(sample));
    showReadFail("看起来还没在 Chrome 登录B站。请先打开 bilibili.com 并登录，再回来点读取");
    return true;
  }
  if (scene === "login-lock") {
    applyTask(asNeedsLogin(sample));
    showReadFail("请先完全退出 Chrome 再点一次");
    return true;
  }
  if (scene === "charge") {
    applyTask({
      status: "needs_login",
      title: sample.title,
      source: sample.source,
      steps: [
        { id: "parse", label: "解析视频", state: "done" },
        { id: "download", label: "下载音频", state: "needs_login", hint: CHARGE_NEED_LOGIN_HINT },
        { id: "transcribe", label: "语音识别", state: "pending" },
        { id: "finalize", label: "整理文稿", state: "pending" },
      ],
    });
    return true;
  }
  labPreview = false;
  return false;
}

document.addEventListener("DOMContentLoaded", () => {
  opNote("boot", {
    platform: detectedServicePlatform() || "unknown",
    api: window.MT_API ? "on" : "off",
    path: location.pathname,
  });
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
    if (e.target.closest("[data-download-service]")) {
      e.stopPropagation();
      goDownloadService();
      return;
    }
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
      e.stopPropagation();
      const need = e.target.closest(".has-tip");
      document.querySelectorAll(".has-tip.is-open").forEach((el) => {
        if (el !== need) el.classList.remove("is-open");
      });
      if (need) need.classList.toggle("is-open");
      return;
    }
  });

  $("historyBtn").onclick = async () => {
    await loadHistory();
    openOverlay("historyOverlay");
  };
  paintServiceGuide();
  $("historyClose").onclick = () => closeOverlay("historyOverlay");
  $("loginClose").onclick = () => closeOverlay("loginOverlay");
  $("readFailClose").onclick = () => closeOverlay("readFailOverlay");
  $("readFailRetry").onclick = retryReadLogin;
  $("sheetClose").onclick = () => closeOverlay("transcriptOverlay");
  $("serviceClose").onclick = () => closeOverlay("serviceOverlay");
  if ($("labClose")) $("labClose").onclick = () => closeOverlay("labOverlay");
  $("logBtn").onclick = () => {
    opNote("log.open", { lines: opLog.length });
    openOverlay("logOverlay");
  };
  $("logClose").onclick = () => closeOverlay("logOverlay");
  $("logDownload").onclick = downloadOpLog;
  if ($("serviceZipLink")) {
    $("serviceZipLink").addEventListener("click", () => {
      opNote("download.link", { platform: currentServicePlatform(), href: $("serviceZipLink").href });
    });
  }
  document.querySelectorAll("[data-service-os]").forEach((btn) => {
    btn.onclick = () => {
      const next = btn.getAttribute("data-service-os");
      if (!next || next === currentServicePlatform()) return;
      serviceChoice = next;
      paintServiceGuide();
      if (!labPreview) fetchServicePackage();
    };
  });

  $("loginInfoBtn").onclick = (e) => {
    e.stopPropagation();
    const wrap = e.currentTarget.closest(".has-tip");
    document.querySelectorAll(".has-tip.is-open").forEach((el) => {
      if (el !== wrap) el.classList.remove("is-open");
    });
    if (wrap) wrap.classList.toggle("is-open");
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
    if (e.target.closest(".card, #historyBtn, #logBtn")) return;
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
    if (!e.target.closest(".has-tip")) {
      document.querySelectorAll(".has-tip.is-open").forEach((el) => el.classList.remove("is-open"));
    }
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
    const scene = params.get("scene") || (params.get("lab") === "1" ? "lab" : "");
    if (share && !$("urlInput").value) {
      $("urlInput").value = share;
      syncUrlClear();
    }
    if (runLabScene(scene)) {
      return;
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
