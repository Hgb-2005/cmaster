/* 做题页：按账号隔离草稿、保留通过代码、恢复最近十次提交。 */
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("problem-data").textContent);
  var storeKey = "cmaster:code:v2:" + data.storageId + ":" + data.qid;
  var handoffKey = "cmaster:login-draft:" + data.qid;
  var saved = null;
  try {
    saved = localStorage.getItem(storeKey);
    if (saved === null && data.allowLegacyDraft) {
      saved = localStorage.getItem("cmaster:code:" + data.qid);
      if (saved !== null) localStorage.setItem(storeKey, saved);
    }
  } catch (e) { /* 存储不可用时仍能使用编辑器。 */ }
  // 游客点击提交后主动进入登录流程，草稿只在当前标签页交接一次。
  if (data.loggedIn) {
    try {
      var pending = sessionStorage.getItem(handoffKey);
      if (pending !== null) { saved = pending; sessionStorage.removeItem(handoffKey); }
    } catch (e) { /* 会话存储不可用时仍保留游客本地草稿。 */ }
  }

  var cm = CMaster.createEditor(document.getElementById("code"), {
    value: saved !== null ? saved : (data.lastCode != null ? data.lastCode : data.template),
    onRun: function (kind) { kind === "submit" ? submit() : run(); }
  });
  var resultArea = document.getElementById("result-area");
  var btnRun = document.getElementById("btn-run");
  var btnSubmit = document.getElementById("btn-submit");
  var status = document.getElementById("draft-status");
  var restoreAccepted = document.getElementById("restore-accepted");
  var undoRestore = document.getElementById("undo-restore");
  var historySelect = document.getElementById("submission-history");
  var solution = data.solution || {};
  var previousCode = null;
  var busy = false;

  function saveDraft() {
    try {
      localStorage.setItem(storeKey, cm.getValue());
      status.textContent = "已保存在此浏览器 · 通过后也会保留";
    } catch (e) {
      status.textContent = "浏览器存储不可用，请复制代码备份；登录后的提交仍会保存";
    }
  }
  // 立即保存，快速刷新和切换账号也不会丢掉最后几次输入。
  cm.on("change", saveDraft);
  window.addEventListener("pagehide", saveDraft);
  saveDraft();

  function setBusy(on, kind) {
    busy = on;
    CMaster.setBusy(btnRun, on, kind === "run" ? "运行中" : "请稍候");
    CMaster.setBusy(btnSubmit, on, kind === "submit" ? "判题中" : "请稍候");
  }

  function restore(code) {
    if (typeof code !== "string") return;
    previousCode = cm.getValue();
    cm.setValue(code);
    undoRestore.hidden = false;
    cm.focus();
    CMaster.toast("已载入代码，可撤销恢复", "ok");
  }

  function renderHistory() {
    restoreAccepted.hidden = !(solution.accepted && typeof solution.accepted.code === "string");
    if (!historySelect) return;
    var history = (solution.history || []).slice().reverse();
    historySelect.replaceChildren();
    history.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.status + " · " + new Date(entry.ts * 1000).toLocaleString("zh-CN", { hour12: false });
      historySelect.appendChild(option);
    });
    document.getElementById("submission-controls").hidden = !history.length;
  }

  function refreshHistory() {
    return fetch("/api/submissions/" + encodeURIComponent(data.qid), {
      credentials: "same-origin", headers: { "X-CMaster-Account": data.storageId }
    })
      .then(function (r) { if (!r.ok) throw new Error("提交历史暂不可用"); return r.json(); })
      .then(function (d) { if (d.ok) { solution = d.solution || {}; renderHistory(); } })
      .catch(function () { CMaster.toast("结果已返回，提交历史暂时未能刷新", "bad"); });
  }

  function run() {
    if (busy) return;
    if (!cm.getValue().trim()) { CMaster.toast("先写点代码", "bad"); return; }
    saveDraft();
    setBusy(true, "run");
    CMaster.setJudging(resultArea, true, "运行中");
    CMaster.api("/api/run", { code: cm.getValue(), stdin: data.firstInput })
      .then(function (d) {
        if (!d.ok) { CMaster.toast(d.error || "运行失败", "bad"); return; }
        CMaster.renderRunResult(d.result, resultArea);
      })
      .catch(function (e) { CMaster.toast(e.message || "网络错误", "bad"); })
      .finally(function () { setBusy(false); CMaster.setJudging(resultArea, false); });
  }

  function submit() {
    if (busy) return;
    saveDraft();
    if (!data.loggedIn) {
      try { sessionStorage.setItem(handoffKey, cm.getValue()); } catch (e) {}
      CMaster.toast("先登录，才能记录进度", "bad");
      setTimeout(function () { location.href = "/login?next=" + encodeURIComponent(location.pathname); }, 700);
      return;
    }
    var submittedCode = cm.getValue();
    if (!submittedCode.trim()) { CMaster.toast("先写点代码", "bad"); return; }
    setBusy(true, "submit");
    CMaster.setJudging(resultArea, true, "判题中");
    CMaster.api("/api/submit", { qid: data.qid, code: submittedCode, accountId: data.storageId })
      .then(function (d) {
        if (!d.ok) { CMaster.toast(d.error || "提交失败", "bad"); return; }
        CMaster.renderResult(d.result, resultArea);
        if (d.result.status === "AC") CMaster.toast("通过！你的代码已保留", "ok");
        if (d.result.owner === "system" || d.result.status === "SE") {
          var note = document.createElement("p");
          note.className = "hint-box";
          note.textContent = "这是判题服务的问题，本次未计入尝试次数和错题。代码已保留，可稍后重试。";
          resultArea.appendChild(note);
          var retry = document.createElement("button");
          retry.type = "button";
          retry.className = "btn btn-sm";
          retry.id = "retry-submit";
          retry.textContent = "重新提交";
          retry.addEventListener("click", submit);
          resultArea.appendChild(retry);
        }
        resultArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
        return refreshHistory();
      })
      .catch(function (e) { CMaster.toast(e.message || "网络错误", "bad"); })
      .finally(function () { setBusy(false); CMaster.setJudging(resultArea, false); });
  }

  btnRun.addEventListener("click", run);
  btnSubmit.addEventListener("click", submit);
  document.getElementById("btn-reset").addEventListener("click", function () { restore(data.template); });
  restoreAccepted.addEventListener("click", function () { restore(solution.accepted.code); });
  undoRestore.addEventListener("click", function () {
    if (previousCode === null) return;
    cm.setValue(previousCode);
    previousCode = null;
    undoRestore.hidden = true;
    cm.focus();
  });
  if (historySelect) document.getElementById("restore-version").addEventListener("click", function () {
    var entry = (solution.history || []).find(function (item) { return item.id === historySelect.value; });
    if (entry) restore(entry.code);
  });
  renderHistory();

  document.getElementById("btn-answer").addEventListener("click", function () {
    var area = document.getElementById("answer-area");
    if (area.dataset.shown === "1") { area.replaceChildren(); area.dataset.shown = "0"; return; }
    if (!data.loggedIn) { CMaster.toast("先登录再看参考答案", "bad"); return; }
    fetch("/api/answer/" + encodeURIComponent(data.qid), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("获取失败"); return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error("获取失败");
        var wrap = document.createElement("div");
        wrap.className = "result";
        var head = document.createElement("div");
        head.className = "result-head";
        head.textContent = "参考代码（先自己写，卡住再看）";
        var body = document.createElement("div");
        body.className = "result-body";
        var pre = document.createElement("pre");
        pre.textContent = d.answer;
        body.appendChild(pre);
        wrap.append(head, body);
        area.replaceChildren(wrap);
        area.dataset.shown = "1";
      })
      .catch(function () { CMaster.toast("获取失败，请稍后重试", "bad"); });
  });
})();
