/* 私人笔记：本地保留未保存草稿，账号存档带版本号，冲突时由作者选择。 */
(function () {
  "use strict";
  var boot = document.getElementById("study-note-data");
  if (!boot) return;
  var data = JSON.parse(boot.textContent);
  var record = data.record;
  var baseVersion = record.version;
  var key = "cmaster:note:v1:" + data.storageId + ":" + data.ref;
  var tabId = "tab-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  try {
    var savedTabId = sessionStorage.getItem("cmaster:notes:tab");
    if (savedTabId) tabId = savedTabId;
    else sessionStorage.setItem("cmaster:notes:tab", tabId);
  } catch (e) {}
  var field = document.getElementById("study-note-text");
  var details = document.getElementById("my-study-notes");
  var form = document.getElementById("study-note-form");
  var bookmark = document.getElementById("study-bookmark");
  var save = document.getElementById("study-note-save");
  var status = document.getElementById("study-note-status");
  var conflict = document.getElementById("study-note-conflict");
  var load = document.getElementById("study-note-load");
  var overwrite = document.getElementById("study-note-overwrite");
  var undo = document.getElementById("study-note-undo");
  var previousDraft = null;
  var busy = false;
  var conflicted = false;
  var accountChanged = false;
  var draftStored = true;

  function dirty() { return field.value !== record.note; }

  function remember() {
    var localSaved = false;
    var sessionSaved = false;
    var draft = JSON.stringify({text: field.value, version: baseVersion, owner: tabId});
    // 每个标签页单独留一份，另一页保存或关闭时不会清掉本页未提交的内容。
    try {
      if (dirty()) sessionStorage.setItem(key, draft);
      else sessionStorage.removeItem(key);
      sessionSaved = true;
    } catch (e) {}
    try {
      if (dirty()) localStorage.setItem(key, draft);
      else {
        var existing = JSON.parse(localStorage.getItem(key));
        if (existing && (existing.owner === tabId || existing.text === record.note)) localStorage.removeItem(key);
      }
      localSaved = true;
    } catch (e) {}
    draftStored = localSaved || sessionSaved;
  }

  function render() {
    bookmark.textContent = record.bookmarked ? "★ 已收藏" : "☆ 收藏";
    bookmark.setAttribute("aria-pressed", String(record.bookmarked));
    bookmark.disabled = busy || conflicted || accountChanged;
    save.disabled = busy || conflicted || accountChanged || !dirty();
    load.disabled = busy || accountChanged;
    overwrite.disabled = busy || accountChanged;
    undo.disabled = busy || accountChanged;
    document.getElementById("study-note-count").textContent = field.value.length + " / 8000";
    document.getElementById("study-note-indicator").textContent = dirty() ? " · 未保存" : (record.note ? " · 已记录" : "");
    form.setAttribute("aria-busy", String(busy));
  }

  function editingStatus() {
    if (accountChanged) status.textContent = "账号已切换。草稿保留在原账号中，请刷新后继续。";
    else if (conflicted) status.textContent = "有新版本等待确认，当前草稿仍保留。";
    else if (dirty()) status.textContent = draftStored ? "尚未保存到账号 · 本地草稿已保留" : "浏览器无法保存草稿，请点击保存笔记或复制备份。";
    else status.textContent = record.note ? "已保存到账号" : "草稿暂存在此浏览器，保存后可在其他浏览器登录查看。";
  }

  function showConflict(latest) {
    record = latest;
    conflicted = true;
    conflict.hidden = false;
    details.open = true;
    document.getElementById("study-note-server").textContent = record.note || "（已保存版本没有笔记）";
    remember();
    render();
    editingStatus();
  }

  function change(patch, version) {
    if (busy || accountChanged) return;
    remember();
    busy = true;
    render();
    status.textContent = "正在保存…";
    var payload = Object.assign({version: version}, patch);
    return fetch("/api/notebook/" + encodeURIComponent(data.ref), {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CMaster-Account": data.storageId},
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json().then(function (body) { return {status: response.status, body: body}; });
    }).then(function (result) {
      var body = result.body;
      if (!body.ok) {
        if (body.conflict && body.record) { showConflict(body.record); return; }
        if (result.status === 409 || result.status === 401) accountChanged = true;
        status.textContent = body.error || "保存失败，当前草稿仍保留。";
        return;
      }
      var old = record;
      record = body.record;
      if (Object.prototype.hasOwnProperty.call(patch, "note") ||
          (baseVersion === old.version && old.note === record.note)) baseVersion = record.version;
      conflicted = false;
      conflict.hidden = true;
      remember();
      status.textContent = dirty() ? "已保存刚才的版本 · 新修改仍在本地草稿中" : "已保存到账号";
    }).catch(function () {
      status.textContent = draftStored ? "暂时无法保存到账号，本地草稿已保留，可以重试。" : "保存失败，请复制笔记备份后重试。";
    }).finally(function () {
      busy = false;
      render();
    });
  }

  function readDraft(storage) {
    try { return JSON.parse(storage.getItem(key)); } catch (e) { return null; }
  }

  try {
    var pending = readDraft(sessionStorage) || readDraft(localStorage);
    if (pending && typeof pending.text === "string" && Number.isInteger(pending.version) && pending.version >= 0 && pending.text !== record.note) {
      field.value = pending.text;
      baseVersion = pending.version;
      details.open = true;
      if (baseVersion !== record.version) showConflict(record);
      else status.textContent = "已恢复尚未保存的本地草稿。";
    }
  } catch (e) { /* 存储不可用时仍然可以直接保存到账号。 */ }

  field.addEventListener("input", function () {
    remember();
    render();
    editingStatus();
  });
  field.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      event.stopPropagation();
      if (!save.disabled) form.requestSubmit();
    }
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!conflicted && dirty()) change({note: field.value}, baseVersion);
  });
  bookmark.addEventListener("click", function () {
    if (!conflicted) change({bookmarked: !record.bookmarked}, record.version);
  });
  overwrite.addEventListener("click", function () { change({note: field.value}, record.version); });
  load.addEventListener("click", function () {
    previousDraft = {text: field.value, version: baseVersion};
    field.value = record.note;
    baseVersion = record.version;
    conflicted = false;
    conflict.hidden = true;
    undo.hidden = false;
    remember();
    render();
    status.textContent = "已载入账号保存的版本，可撤销载入。";
  });
  undo.addEventListener("click", function () {
    if (!previousDraft) return;
    field.value = previousDraft.text;
    baseVersion = previousDraft.version;
    previousDraft = null;
    undo.hidden = true;
    if (dirty() && baseVersion !== record.version) showConflict(record);
    else { remember(); render(); editingStatus(); }
  });
  window.addEventListener("pagehide", remember);
  window.addEventListener("beforeunload", function (event) {
    if (dirty() && !draftStored) { event.preventDefault(); event.returnValue = ""; }
  });
  if (location.hash === "#my-study-notes") details.open = true;
  remember();
  render();
})();
