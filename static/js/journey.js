(function () {
  "use strict";

  var dataNode = document.getElementById("journey-data");
  var textarea = document.getElementById("journey-code");
  if (!dataNode || !textarea) return;

  var data = JSON.parse(dataNode.textContent);
  var day = Number(data.day);
  var draftKey = "cmaster:journey:v1:" + data.storageId + ":day:" + day;
  var handoffKey = "cmaster:journey-login-draft:" + day;
  var saved = null;

  try {
    saved = localStorage.getItem(draftKey);
    if (data.loggedIn) {
      var pending = sessionStorage.getItem(handoffKey);
      if (pending !== null) {
        saved = pending;
        sessionStorage.removeItem(handoffKey);
      }
    }
    if (saved === null && typeof data.record.last_code === "string" &&
        data.record.last_code.length) {
      saved = data.record.last_code;
    }
  } catch (error) {
    // 禁用浏览器存储时仍允许编辑、运行和提交。
  }

  var editor = CMaster.createEditor(textarea, {
    value: saved !== null ? saved : data.starter,
    onRun: function (kind) {
      if (kind === "submit") submit();
      else runVisible();
    }
  });

  var resultArea = document.getElementById("journey-result");
  var runButton = document.getElementById("journey-run");
  var submitButton = document.getElementById("journey-submit");
  var resetButton = document.getElementById("journey-reset");
  var draftStatus = document.getElementById("journey-draft-status");
  var recordStatus = document.getElementById("journey-record-status");
  var attemptsNode = document.getElementById("journey-attempts");
  var completionActions = document.getElementById("journey-completion-actions");
  var nextLink = document.getElementById("journey-next-link");
  var busy = false;
  var completed = Boolean(data.record.completed || data.record.completed_at);

  function saveDraft() {
    try {
      localStorage.setItem(draftKey, editor.getValue());
      draftStatus.textContent = "草稿已保存 " + new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      });
    } catch (error) {
      draftStatus.textContent = "浏览器存储不可用，请复制代码备份";
    }
  }

  editor.on("change", saveDraft);
  window.addEventListener("pagehide", saveDraft);
  saveDraft();

  function setBusy(on, kind) {
    busy = on;
    CMaster.setBusy(runButton, on, kind === "run" ? "运行中" : "请稍候");
    CMaster.setBusy(submitButton, on, kind === "submit" ? "检查边界中" : "请稍候");
    resetButton.disabled = on;
  }

  function goToLogin() {
    var code = editor.getValue();
    saveDraft();
    try {
      sessionStorage.setItem(handoffKey, code);
    } catch (error) {
      // 本地草稿仍可能在登录后恢复。
    }
    CMaster.toast("提交隐藏边界用例前需要登录", "bad");
    setTimeout(function () {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
    }, 550);
  }

  function textElement(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function prependRoundSummary(payload, submitting) {
    var result = payload.result || {};
    var status = result.status || "SE";
    var systemFailure = status === "SE";
    var accepted = status === "AC" && submitting;
    var artifact = textElement("div", "journey-result-artifact" + (accepted ? " is-ok" : ""));
    var copy = document.createElement("div");
    copy.appendChild(textElement(
      "span",
      "",
      systemFailure ? "SYSTEM OUTAGE" : submitting ? "SUBMITTED ITERATION" : "VISIBLE RUN"
    ));
    copy.appendChild(textElement(
      "h3",
      "",
      systemFailure ? "代码已保留，这次不是你的学习失败" :
        accepted ? "第 " + day + " 天已经可以交付" :
        submitting ? "完整边界检查已执行" : "可见用例运行完成"
    ));
    copy.appendChild(textElement(
      "p",
      "",
      systemFailure ? "判题服务暂时不可用。尝试次数没有增加，修好服务后可以直接重试。" :
        accepted ? data.outcome :
        submitting ? "先看失败用例属于哪一类，再围绕第一个差异修改代码。" :
        "正式提交还会运行 " + data.hiddenTotal + " 个隐藏边界用例。"
    ));
    artifact.appendChild(copy);
    artifact.appendChild(textElement(
      "b",
      "",
      result.total ? (result.passed || 0) + " / " + result.total : status
    ));
    resultArea.insertBefore(artifact, resultArea.firstChild);

    if (accepted) {
      var capability = textElement("div", "journey-result-capability");
      capability.appendChild(textElement("span", "", "CAPABILITY ADDED"));
      capability.appendChild(textElement(
        "p",
        "",
        "你已经能独立处理：" + data.codeFocus.join(" · ")
      ));
      resultArea.insertBefore(capability, resultArea.children[1]);
    }
  }

  function updateRecord(payload) {
    var record = payload.record || {};
    var result = payload.result || {};
    var status = result.status || "SE";
    var systemFailure = status === "SE";
    if (payload.completed) completed = true;

    attemptsNode.textContent = String(Math.max(0, Number(record.attempts) || 0));
    recordStatus.classList.toggle("is-done", completed);
    recordStatus.classList.toggle("is-ok", status === "AC");
    recordStatus.textContent = completed ? "已交付" :
      systemFailure ? "系统故障 · 未计入" :
      "上次 " + status;

    if (completed) {
      completionActions.hidden = false;
      if (payload.next_url) nextLink.href = payload.next_url;
      nextLink.textContent = payload.final_delivery ? "打开最终交付页" : "进入第 " + (day + 1) + " 天";

      var state = document.querySelector(".journey-day-state");
      if (state) {
        state.classList.remove("is-active", "is-locked");
        state.classList.add("is-completed");
        state.querySelector("span").textContent = "已交付";
        state.querySelector("b").textContent =
          (payload.complete_count != null ? payload.complete_count : day) + " / " + 7;
      }
      var mapStep = document.querySelector('.journey-build-map a.is-active');
      if (mapStep) {
        mapStep.classList.remove("is-current", "is-active");
        mapStep.classList.add("is-completed");
        mapStep.querySelector("i").textContent = "✓";
      }
    }
  }

  function handlePayload(payload, submitting) {
    if (!payload.ok) {
      if (payload.error === "请先登录") {
        goToLogin();
      } else if (payload.locked) {
        CMaster.toast(payload.error || "前一天还没有完成", "bad");
      } else {
        CMaster.toast(payload.error || "请求失败", "bad");
      }
      return;
    }

    CMaster.renderResult(payload.result, resultArea);
    prependRoundSummary(payload, submitting);
    if (submitting) updateRecord(payload);

    if (payload.result.status === "AC" && submitting) {
      CMaster.toast(payload.final_delivery ? "项目全部交付" : "本日交付完成，下一关已解锁", "ok");
    } else if (payload.result.status === "SE") {
      CMaster.toast("判题服务暂时不可用，代码已保留", "bad");
    } else if (!submitting) {
      CMaster.toast("可见用例运行完成，接着检查差异", payload.result.status === "AC" ? "ok" : "bad");
    }
    resultArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function request(kind) {
    if (busy) return;
    var code = editor.getValue();
    if (!code.trim()) {
      CMaster.toast("先写一点代码", "bad");
      return;
    }
    saveDraft();
    if (kind === "submit" && !data.loggedIn) {
      goToLogin();
      return;
    }

    setBusy(true, kind);
    CMaster.setJudging(
      resultArea,
      true,
      kind === "run" ? "运行可见用例" : "检查全部边界用例"
    );
    var endpoint = "/api/journey/" + encodeURIComponent(day) +
      (kind === "run" ? "/run" : "/submit");
    CMaster.api(endpoint, {
      code: code,
      accountId: kind === "submit" ? data.storageId : undefined
    })
      .then(function (payload) {
        handlePayload(payload, kind === "submit");
      })
      .catch(function (error) {
        CMaster.toast(error.message || "网络错误，请稍后重试", "bad");
      })
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
  resetButton.addEventListener("click", function () {
    editor.setValue(data.starter);
    saveDraft();
    editor.focus();
    CMaster.toast("已恢复到这一天的起始代码", "ok");
  });

  var hints = Array.prototype.slice.call(document.querySelectorAll("#journey-hints li"));
  var nextHint = document.getElementById("journey-next-hint");
  var hintCounter = document.getElementById("journey-hint-counter");
  var revealed = 0;
  nextHint.addEventListener("click", function () {
    if (revealed >= hints.length) return;
    hints[revealed].hidden = false;
    revealed += 1;
    hintCounter.textContent = revealed + " / " + hints.length;
    if (revealed < hints.length) {
      nextHint.textContent = "解锁第 " + (revealed + 1) + " 条线索";
    } else {
      nextHint.textContent = "线索已全部展开";
      nextHint.disabled = true;
    }
  });
})();
