(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("lab-data").textContent);
  var storeKey = "cmaster:lab:v1:" + data.storageId + ":" + data.id;
  var handoffKey = "cmaster:lab-login-draft:" + data.id;
  var saved = null;
  try {
    saved = localStorage.getItem(storeKey);
    if (saved === null && typeof data.record.last_code === "string") {
      saved = data.record.last_code;
    }
    if (data.loggedIn) {
      var pending = sessionStorage.getItem(handoffKey);
      if (pending !== null) {
        saved = pending;
        sessionStorage.removeItem(handoffKey);
      }
    }
  } catch (error) {
    // 浏览器禁用存储时仍可直接编辑和判题。
  }

  var editor = CMaster.createEditor(document.getElementById("code"), {
    value: saved !== null ? saved : data.starter,
    onRun: function (kind) {
      if (kind === "submit") submit();
      else runVisible();
    }
  });
  var resultArea = document.getElementById("lab-result");
  var runButton = document.getElementById("btn-run");
  var submitButton = document.getElementById("btn-submit");
  var saveStatus = document.getElementById("draft-status");
  var changedCount = document.getElementById("changed-count");
  var busy = false;

  function normalizedLines(value) {
    return String(value || "").replace(/\r\n?/g, "\n").split("\n");
  }

  function changedLines() {
    var before = normalizedLines(data.starter);
    var after = normalizedLines(editor.getValue());
    var start = 0;
    while (start < before.length && start < after.length && before[start] === after[start]) {
      start += 1;
    }
    var endBefore = before.length - 1;
    var endAfter = after.length - 1;
    while (endBefore >= start && endAfter >= start && before[endBefore] === after[endAfter]) {
      endBefore -= 1;
      endAfter -= 1;
    }
    return Math.max(endBefore - start + 1, endAfter - start + 1);
  }

  function updateBudget() {
    var count = changedLines();
    changedCount.textContent = String(count);
    changedCount.parentElement.classList.toggle("over", count > data.maxChangedLines);
  }

  function saveDraft() {
    try {
      localStorage.setItem(storeKey, editor.getValue());
      saveStatus.textContent = "已保存在此浏览器 · " + new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit", minute: "2-digit", hour12: false
      });
    } catch (error) {
      saveStatus.textContent = "浏览器存储不可用，请复制代码备份";
    }
  }

  editor.on("change", function () {
    updateBudget();
    saveDraft();
  });
  window.addEventListener("pagehide", saveDraft);
  updateBudget();
  saveDraft();

  function setBusy(on, kind) {
    busy = on;
    CMaster.setBusy(runButton, on, kind === "run" ? "运行中" : "请稍候");
    CMaster.setBusy(submitButton, on, kind === "submit" ? "提交中" : "请稍候");
  }

  function showRestriction(message) {
    resultArea.replaceChildren();
    var card = document.createElement("aside");
    card.className = "lab-restriction-error";
    var badge = document.createElement("span");
    badge.textContent = "改动超预算";
    var title = document.createElement("strong");
    title.textContent = "这轮只允许修改关键的一行";
    var copy = document.createElement("p");
    copy.textContent = message;
    card.append(badge, title, copy);
    resultArea.appendChild(card);
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function handleResult(payload, submitting) {
    if (!payload.ok) {
      if (payload.restriction) showRestriction(payload.error || "改动行数超过限制");
      else CMaster.toast(payload.error || "判题失败", "bad");
      return;
    }
    CMaster.renderResult(payload.result, resultArea);
    if (submitting && payload.record) {
      var record = document.getElementById("lab-record");
      record.textContent = payload.result.status === "AC" ? "已修复" : "上次：" + payload.result.status;
      record.classList.toggle("is-done", payload.result.status === "AC");
    }
    if (submitting && payload.result.status === "AC") {
      var note = document.createElement("p");
      note.className = "lab-complete-note";
      note.textContent = "修复完成。隐藏边界用例也通过了，可以回到实验目录挑战下一个现场。";
      resultArea.appendChild(note);
      CMaster.toast("修复完成，隐藏用例全部通过", "ok");
    } else if (!submitting) {
      var hint = document.createElement("p");
      hint.className = "lab-visible-note";
      hint.textContent = "这里只显示 " + data.visibleTotal + " 个可见用例；完整的 " +
        (data.visibleTotal + data.hiddenTotal) + " 个用例会在登录提交时一起运行。";
      resultArea.appendChild(hint);
    }
    resultArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function request(kind) {
    if (busy) return;
    var code = editor.getValue();
    if (!code.trim()) {
      CMaster.toast("先修复一段代码", "bad");
      return;
    }
    saveDraft();
    if (kind === "submit" && !data.loggedIn) {
      try {
        sessionStorage.setItem(handoffKey, code);
      } catch (error) {}
      CMaster.toast("提交隐藏用例前需要登录", "bad");
      setTimeout(function () {
        location.href = "/login?next=" + encodeURIComponent(location.pathname);
      }, 650);
      return;
    }
    setBusy(true, kind);
    CMaster.setJudging(resultArea, true, kind === "run" ? "运行可见用例" : "检查隐藏用例");
    var endpoint = "/api/lab/" + encodeURIComponent(data.id) + (kind === "run" ? "/run" : "/submit");
    CMaster.api(endpoint, {
      code: code,
      accountId: kind === "submit" ? data.storageId : undefined
    })
      .then(function (payload) { handleResult(payload, kind === "submit"); })
      .catch(function (error) { CMaster.toast(error.message || "网络错误", "bad"); })
      .finally(function () {
        setBusy(false);
        CMaster.setJudging(resultArea, false);
      });
  }

  function runVisible() {
    request("run");
  }

  function submit() {
    request("submit");
  }

  runButton.addEventListener("click", runVisible);
  submitButton.addEventListener("click", submit);
  document.getElementById("btn-reset").addEventListener("click", function () {
    if (editor.getValue() === data.starter) return;
    editor.setValue(data.starter);
    editor.focus();
    CMaster.toast("已恢复实验起始代码", "ok");
  });

  var hints = Array.prototype.slice.call(document.querySelectorAll("#lab-hints li"));
  var nextHint = document.getElementById("lab-next-hint");
  var hintCounter = document.getElementById("hint-counter");
  var revealed = 0;
  nextHint.addEventListener("click", function () {
    if (revealed >= hints.length) return;
    hints[revealed].hidden = false;
    revealed += 1;
    hintCounter.textContent = "已解锁 " + revealed + " / " + hints.length;
    if (revealed < hints.length) {
      nextHint.textContent = "继续解锁 · " + (revealed + 1);
    } else {
      nextHint.textContent = "线索已全部展开";
      nextHint.disabled = true;
    }
  });
})();
