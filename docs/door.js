(function () {
  const LOCAL = "http://127.0.0.1:8766";
  const SCHEME = "media-transcriber://open";
  const DOWNLOAD = "https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-macOS.zip";

  const form = document.getElementById("mainCard");
  const startBtn = document.getElementById("submitBtn");
  const historyBtn = document.getElementById("historyBtn");

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
    const q = document.getElementById("urlInput").value.trim();
    location.replace(q ? LOCAL + "/?url=" + encodeURIComponent(q) : LOCAL);
  }

  function openHelper() {
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    frame.src = SCHEME;
    document.body.appendChild(frame);
    setTimeout(function () { frame.remove(); }, 4000);
  }

  function downloadHelper() {
    const a = document.createElement("a");
    a.href = DOWNLOAD;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function waitForHelper(ms) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (await probe()) return true;
      await new Promise(function (resolve) { setTimeout(resolve, 1500); });
    }
    return false;
  }

  async function enter() {
    startBtn.disabled = true;
    if (await probe()) {
      goLocal();
      return;
    }
    openHelper();
    if (await waitForHelper(8000)) {
      goLocal();
      return;
    }
    downloadHelper();
    openHelper();
    if (await waitForHelper(180000)) {
      goLocal();
      return;
    }
    startBtn.disabled = false;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    enter();
  });
  historyBtn.addEventListener("click", function () {
    enter();
  });

  (async function boot() {
    if (await probe()) goLocal();
  })();
})();
