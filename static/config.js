/* Public GitHub Pages sets window.MT_API before this file loads. Localhost leaves it empty. */
window.MT_API = window.MT_API || "";
window.MT_SID_KEY = "mt_sid";
window.mtApi = function (path) {
  return (window.MT_API || "") + path;
};
window.mtAsset = function (path) {
  path = String(path || "");
  if (window.MT_API && path.indexOf("/static/") === 0) {
    return "./" + path.slice("/static/".length);
  }
  return path;
};
window.mtSid = function () {
  try {
    return localStorage.getItem(window.MT_SID_KEY) || "";
  } catch (_) {
    return "";
  }
};
window.mtRememberSid = function (sid) {
  if (!sid) return;
  try {
    localStorage.setItem(window.MT_SID_KEY, sid);
  } catch (_) {}
};
window.mtFetch = function (path, opts) {
  opts = opts ? Object.assign({}, opts) : {};
  const headers = new Headers(opts.headers || {});
  const sid = window.mtSid();
  if (sid) headers.set("X-MT-SID", sid);
  opts.headers = headers;
  if (!opts.credentials) opts.credentials = "same-origin";
  return fetch(window.mtApi(path), opts).then((r) => {
    const got = r.headers.get("X-MT-SID");
    if (got) window.mtRememberSid(got);
    return r;
  });
};
