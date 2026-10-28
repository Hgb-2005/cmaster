/* ============================================================
   CMaster 前端公共模块
   - 编辑器初始化（CodeMirror 5 + C 模式，全部本地资源）
   - 判题结果渲染
   - 一点通用工具（fetch 封装、toast、忙碌态）
   ============================================================ */

(function (global) {
  "use strict";

  var VERDICT_TEXT = {
    AC: "通过",
    OK: "运行完成",
    WA: "答案错误",
    TLE: "运行超时",
    RE: "运行时错误",
    CE: "编译错误",
    SE: "系统错误"
  };

  /* ---------------------------------------------------------- 工具 */

  function toast(message, kind) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.className = "toast"; }, 2600);
  }

  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () {
        throw new Error("服务器返回了非 JSON 内容（HTTP " + r.status + "）");
      });
    });
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      btn._label = btn._label || btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>' + (label || "判题中");
    } else {
      btn.disabled = false;
      if (btn._label) btn.innerHTML = btn._label;
    }
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ---------------------------------------------------------- 编辑器 */

  function createEditor(textarea, options) {
    options = options || {};
    if (typeof CodeMirror === "undefined") {
      // 极端情况下静态资源没加载出来，退回纯 textarea，至少不让人写不了字
      textarea.style.display = "";
      return {
        getValue: function () { return textarea.value; },
        setValue: function (v) { textarea.value = v; },
        focus: function () { textarea.focus(); }
      };
    }

    var cm = CodeMirror.fromTextArea(textarea, {
      mode: "text/x-csrc",
      theme: "material-darker",
      lineNumbers: true,
      indentUnit: 4,
      tabSize: 4,
      indentWithTabs: false,
      smartIndent: true,
      matchBrackets: true,
      autoCloseBrackets: true,
      styleActiveLine: true,
      lineWrapping: false,
      viewportMargin: Infinity,
      extraKeys: {
        Tab: function (editor) {
          if (editor.somethingSelected()) {
            editor.indentSelection("add");
          } else {
            editor.replaceSelection("    ", "end");
          }
        },
        "Shift-Tab": function (editor) { editor.indentSelection("subtract"); },
        "Ctrl-Enter": function () {
          if (options.onRun) options.onRun("run");
        },
        "Ctrl-S": function () {
          if (options.onRun) options.onRun("submit");
          return false;
        }
      }
    });

    cm.setValue(options.value || "");
    return cm;
  }

  /* ---------------------------------------------------------- 结果渲染 */

  function ioBlock(label, text, cls) {
    var box = el("div", cls || "");
    box.appendChild(el("span", "io-label", label));
    var pre = el("pre");
    pre.textContent = text && text.length ? text : "（空）";
    box.appendChild(pre);
    return box;
  }

  function renderResult(result, target) {
    target.innerHTML = "";

    var status = result.status || "SE";
    // is-pass / is-fail 是 motion.css 里绿光与抖动的开关，别漏了，
    // 否则那段动画写了等于没写
    var box = el("div", "result " + (status === "AC" ? "is-pass" : "is-fail"));
    var head = el("div", "result-head");
    var badge = el("span", "verdict " + status, status + " · " + (VERDICT_TEXT[status] || status));
    head.appendChild(badge);

    if (result.total) {
      head.appendChild(el("span", "", "通过 " + result.passed + " / " + result.total + " 个用例"));
    }
    if (result.time_ms != null) {
      head.appendChild(el("span", "meta", result.time_ms + " ms"));
    }
    box.appendChild(head);

    var body = el("div", "result-body");

    if (result.message) {
      body.appendChild(el("div", "result-msg", result.message));
    }

    // 编译错误：整段编译器输出贴出来，学生要靠它定位
    if (result.status === "CE") {
      var ce = el("pre", "stderr");
      ce.textContent = result.compile_msg || result.message || "编译失败，但没有输出";
      body.appendChild(ce);
    }

    // 逐用例反馈
    if (result.cases && result.cases.length) {
      result.cases.forEach(function (c) {
        var item = el("div", "case-item");
        var ch = el("div", "case-head");
        ch.appendChild(el("span", "st", c.status, c.status));
        ch.appendChild(el("span", "", c.name || ("用例 " + c.idx)));
        if (c.time_ms != null) ch.appendChild(el("span", "t", c.time_ms + " ms"));
        item.appendChild(ch);

        var grid = el("div", "io-grid");
        grid.appendChild(ioBlock("输入", c.input));
        grid.appendChild(ioBlock("期望输出", c.expected, "exp"));
        grid.appendChild(ioBlock("实际输出", c.actual, "act"));
        item.appendChild(grid);

        if (c.stderr) {
          var se = el("pre", "stderr");
          se.textContent = c.stderr;
          item.appendChild(se);
        }
        body.appendChild(item);
      });
    }

    box.appendChild(body);
    target.appendChild(box);
    return box;
  }

  /* 自由运行的结果（无测试用例） */
  function renderRunResult(result, target) {
    target.innerHTML = "";
    var status = result.status || "SE";
    // 自由运行只有「跑通了」和「没跑通」两种：OK 不抖，别的抖一下
    var box = el("div", "result" + (status === "OK" ? "" : " is-fail"));
    var head = el("div", "result-head");
    head.appendChild(el("span", "verdict " + status,
      status + " · " + (VERDICT_TEXT[status] || status)));
    if (result.time_ms != null) head.appendChild(el("span", "meta", result.time_ms + " ms"));
    box.appendChild(head);

    var body = el("div", "result-body");
    if (result.status === "CE") {
      var ce = el("pre", "stderr");
      ce.textContent = result.compile_msg || result.message || "编译失败";
      body.appendChild(ce);
    } else {
      body.appendChild(el("div", "io-label", "stdout"));
      var out = el("pre", "stdout-raw");
      out.textContent = (result.stdout && result.stdout.length) ? result.stdout : "（没有任何输出）";
      body.appendChild(out);

      if (result.stderr && result.stderr.length) {
        body.appendChild(el("div", "io-label", "stderr"));
        var err = el("pre", "stderr");
        err.textContent = result.stderr;
        body.appendChild(err);
      }
      if (result.message) body.appendChild(el("div", "result-msg", result.message));
    }

    box.appendChild(body);
    target.appendChild(box);
    return box;
  }

  /* ---------------------------------------------------------- 编译器徽标 */

  function checkCompiler() {
    var badge = document.getElementById("compiler-badge");
    if (!badge) return;
    fetch("/api/compiler", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          badge.className = "badge badge-ok";
          badge.textContent = "GCC 就绪";
          badge.title = d.version;
        } else {
          badge.className = "badge badge-bad";
          badge.textContent = "编译器缺失";
          badge.title = d.hint || "找不到 gcc，判题不可用";
        }
      })
      .catch(function () {
        badge.className = "badge badge-bad";
        badge.textContent = "状态未知";
      });
  }

  /* 判题/运行期间占位：让等待有个看得见的东西，而不是只变按钮文字。
     编译加上跑用例，慢的时候能到一秒多，空白区会让人以为没点上。 */
  function setJudging(target, on, label) {
    if (!target) return;
    target.classList.toggle("is-judging", !!on);
    if (!on) return;
    target.innerHTML =
      '<div class="result is-judging">' +
      '<div class="result-head"><span class="verdict">' +
      (label || "判题中") +
      "</span></div>" +
      '<div class="result-body"><div class="result-msg">正在编译并运行全部测试用例…</div></div>' +
      "</div>";
  }

  global.CMaster = {
    createEditor: createEditor,
    renderResult: renderResult,
    renderRunResult: renderRunResult,
    setJudging: setJudging,
    checkCompiler: checkCompiler,
    api: api,
    toast: toast,
    setBusy: setBusy,
    verdictText: VERDICT_TEXT
  };
})(window);
