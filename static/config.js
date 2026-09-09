/* Public GitHub Pages sets window.MT_API before this file loads. Localhost leaves it empty. */
window.MT_API = window.MT_API || "";
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
