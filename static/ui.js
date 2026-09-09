/* 第二层零件的 JS 拼装。页面逻辑仍在 app.js。 */
(function (global) {
  function iconBtn({ id, aria, src, size = 24, extra = "", attrs = "" }) {
    const idAttr = id ? ` id="${id}"` : "";
    return `<button type="button" class="icon-btn ${extra}"${idAttr} aria-label="${aria}" ${attrs}>
      <img src="${(global.mtAsset || ((p) => p))(src)}" alt="" width="${size}" height="${size}">
    </button>`;
  }

  function infoBtn(attrs) {
    return iconBtn({
      aria: "了解读取流程",
      src: "/static/img/icon-info.svg",
      size: 20,
      extra: "icon-btn--sm",
      attrs: attrs || "",
    });
  }

  function actionBtn({ id, extra = "", attrs = "", html }) {
    const idAttr = id ? ` id="${id}"` : "";
    return `<button type="button" class="action ${extra}"${idAttr} ${attrs}>${html}</button>`;
  }

  function histRow({ id, status, title, when, statusHtml, icon }) {
    const plat = icon
      ? `<img class="hist-plat" src="${icon}" alt="" width="16" height="16">`
      : "";
    return `<div class="hist-item" data-id="${id}" data-status="${status}" data-open="${id}" role="button" tabindex="0">
      <div class="hist-row">
        <div class="hist-main">
          <span class="hist-title">${title}</span>
          <div class="hist-sub">
            <span class="hist-when">${plat}<span>创建时间 ${when}</span></span>
            ${statusHtml}
          </div>
        </div>
        <div class="hist-more-wrap">
          <button type="button" class="icon-btn icon-btn--more" data-menu="${id}" aria-label="更多" aria-expanded="false">
            <img class="dots-off" src="${(global.mtAsset || ((p) => p))("/static/img/icon-hist-dots.svg")}" alt="" width="24" height="24">
            <img class="dots-hover" src="${(global.mtAsset || ((p) => p))("/static/img/icon-hist-dots-hover.svg")}" alt="" width="24" height="24">
          </button>
          <div class="menu menu--hist" hidden>
            <button type="button" class="menu-item" data-del="${id}">删除</button>
          </div>
        </div>
      </div>
      <div class="hist-rule" aria-hidden="true"></div>
    </div>`;
  }

  global.UI = { iconBtn, infoBtn, actionBtn, histRow };
})(window);
