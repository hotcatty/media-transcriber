(function () {
  const LOCAL = "http://127.0.0.1:8766";
  const SCHEME = "media-transcriber://open";

  const forceInstall = /(?:\?|&)install=1(?:&|$)/.test(location.search);

  const startBtn = document.getElementById("startBtn");
  const hint = document.getElementById("statusHint");
  const overlay = document.getElementById("installOverlay");
  const title = document.getElementById("installTitle");
  const lead = document.getElementById("installLead");
  const steps = document.getElementById("installSteps");
  const otherHint = document.getElementById("otherHint");
  const downloadBtn = document.getElementById("downloadBtn");
  const retryBtn = document.getElementById("retryBtn");
  const closeBtn = document.getElementById("installClose");

  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isMac = /Mac|Macintosh/.test(navigator.userAgent) && !isIOS;

  function setHint(text) {
    hint.textContent = text;
  }

  function showOverlay() {
    overlay.hidden = false;
  }

  function hideOverlay() {
    overlay.hidden = true;
  }

  function showNotMac() {
    title.textContent = "请用 Mac 打开";
    lead.hidden = true;
    steps.hidden = true;
    downloadBtn.hidden = true;
    retryBtn.hidden = true;
    otherHint.hidden = false;
    otherHint.textContent = "转录要在你自己电脑上跑。现在这一版先支持 Mac，用电脑打开这个网址就可以。";
    showOverlay();
  }

  function timeoutSignal(ms) {
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      return AbortSignal.timeout(ms);
    }
    const ctrl = new AbortController();
    setTimeout(function () { ctrl.abort(); }, ms);
    return ctrl.signal;
  }

  async function probe() {
    try {
      const r = await fetch(LOCAL + "/api/ping", {
        mode: "cors",
        cache: "no-store",
        signal: timeoutSignal(900),
      });
      if (!r.ok) return false;
      const j = await r.json();
      return !!(j && j.ok && j.app === "media-transcriber" && j.preparing === false);
    } catch (e) {
      return false;
    }
  }

  function goLocal() {
    location.replace(LOCAL);
  }

  function openHelper() {
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    frame.src = SCHEME;
    document.body.appendChild(frame);
    setTimeout(function () {
      frame.remove();
    }, 4000);
  }

  async function waitForHelper(ms) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (await probe()) return true;
      await new Promise(function (resolve) {
        setTimeout(resolve, 1500);
      });
    }
    return false;
  }

  async function start() {
    if (!isMac) {
      showNotMac();
      return;
    }
    setHint("正在打开本机组件…");
    startBtn.disabled = true;
    if (await probe()) {
      goLocal();
      return;
    }
    openHelper();
    if (await waitForHelper(12000)) {
      goLocal();
      return;
    }
    startBtn.disabled = false;
    setHint("还没有本机组件的话，先装一下。装过就再点一次「开始使用」。");
    title.textContent = "先在这台电脑准备一下";
    lead.hidden = false;
    steps.hidden = false;
    downloadBtn.hidden = false;
    retryBtn.hidden = false;
    otherHint.hidden = true;
    showOverlay();
  }

  startBtn.addEventListener("click", function () {
    start();
  });
  retryBtn.addEventListener("click", function () {
    hideOverlay();
    start();
  });
  closeBtn.addEventListener("click", hideOverlay);

  (async function boot() {
    if (!isMac) {
      setHint("请用 Mac 打开这个网址。");
      return;
    }
    if (forceInstall) {
      setHint("点「开始使用」会连接本机。下面是第一次要看的安装说明。");
      showOverlay();
      return;
    }
    if (await probe()) {
      setHint("本机组件已经在，正在进入…");
      goLocal();
      return;
    }
    setHint("点「开始使用」。第一次会先准备本机组件，之后打开这个网址就能用。");
  })();
})();
