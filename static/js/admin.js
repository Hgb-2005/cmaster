/* ============================================================================
   CMaster —— 系统管理台（/admin）前端
   ----------------------------------------------------------------------------
   这一页的 JS 只有一个职责：把 /api/admin/panel 的一份快照**按路径**贴回页面。

   为什么不在服务端把整页重渲染一遍然后替换 innerHTML：
     · 每 5 秒替换一次整页会让「选中文字」「滚动位置」「按钮焦点」全部丢失；
     · 自检按钮按下去之后正在跑，整页替换会把 disabled 状态覆盖掉，
       用户以为没点动，就会连点三次。

   所以：
     · 纯数字/文本 → 按 data-k 路径赋值（DOM 不动，只改 textContent）
     · 结构会变的部分（自检五项、直方图十根柱子）→ 单独重渲染那一个小容器
   ============================================================================ */

(function () {
  "use strict";

  var POLL_MS = 5000;        /* 常规轮询间隔 */
  var POLL_FAST_MS = 1500;   /* 自检进行中时的轮询间隔 */

  var auto = false;
  var timer = null;
  var fastTimer = null;
  var selftestBusy = false;

  /* ------------------------------------------------------------ 工具 */

  function $(sel) { return document.querySelector(sel); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* 按 "a.b.c" 从对象里取値。取不到返回 undefined——
     调用方据此决定「跳过」而不是「填个 0 进去」。 */
  function pick(obj, path) {
    var cur = obj;
    var parts = path.split(".");
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  function fmtBytes(n, unit) {
    var div = unit === "mb" ? 1048576 : (unit === "kb" ? 1024 : 1);
    var v = Math.round(Number(n) / div);
    return String(isFinite(v) ? v : "-");
  }

  function fmtUptime(s) {
    s = Number(s) || 0;
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (h >= 24) return Math.floor(h / 24) + " 天 " + (h % 24) + " 小时";
    return h + " 小时 " + m + " 分";
  }

  /* ------------------------------------------------------------ 数值回填 */

  /* 每个带 data-k 的元素按路径取值。
     两类特殊格式：
       data-k-fmt="mb"/"kb"  → 数值换算后再写，单位 <i> 保留原样不动
       元素里有 <i> 子节点      → 只改前面的数字，不碰单位标签
     这两条是因为「256 MB」里的 MB 是模板写死的，重写整个 textContent
     会把单位吃掉——第一版就是这么丢的。 */
  function applyValues(panel) {
    var nodes = document.querySelectorAll("[data-k]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var v = pick(panel, node.getAttribute("data-k"));
      if (v === undefined || v === null) continue;

      var unit = node.getAttribute("data-k-fmt");
      var text = unit ? fmtBytes(v, unit) : String(v);

      var iTag = node.querySelector("i");
      if (iTag) {
        node.firstChild && node.firstChild.nodeType === 3
          ? (node.firstChild.nodeValue = text)
          : node.insertBefore(document.createTextNode(text), iTag);
      } else {
        node.textContent = text;
      }
    }
  }

  /* ------------------------------------------------------------ 健康 */

  function applyHealth(panel) {
    var h = panel.health || { level: "ok", label: "-", text: "" };
    var badge = $("#adm-health");
    if (badge) {
      badge.className = "adm-health is-" + (h.level || "ok");
      badge.innerHTML = "";
      badge.appendChild(el("i", "dot"));
      badge.appendChild(document.createTextNode(h.label || ""));
      badge.title = h.text || "";
    }
    var line = $("#adm-healthline");
    if (line) line.textContent = h.text || "";

    var stamp = $("#adm-stamp");
    if (stamp) {
      stamp.textContent = panel.stamp || "--:--:--";
      stamp.classList.remove("is-bad");
      /* 顺手把「进程跑了多久」停在环境那一格的开头，避免又写一个轮询 */
      var up = document.querySelector("#adm-env .adm-env-uptime");
      if (up) up.textContent = fmtUptime(panel.uptime_s);
    }
  }

  /* ------------------------------------------------------------ 沙箱 */

  function applySandbox(panel) {
    var sb = panel.sandbox || {};
    var av = sb.available || {};

    var badge = $("#adm-backend");
    if (badge) {
      badge.textContent = av.backend || "unknown";
      badge.className = "adm-backend " + (av.ok ? "is-ok" : "is-warn");
    }

    /* 初始化失败计数只有在 > 0 时才变红。理由：这个数字是 0 的时候
       它和旁边五个计数没有区别，一旦变红就代表「沙箱本该可用却挂了」，
       是唯一需要立刻动手的信号。常驻红色会让人对这种红脱敏。 */
    var cnts = document.querySelectorAll("#adm-counters .adm-counter");
    if (cnts.length >= 6) {
      var fail = (sb.stats && sb.stats.init_failed) || 0;
      cnts[5].classList.toggle("is-hot", fail > 0);
    }
  }

  /* ------------------------------------------------------------ 自检 */

  function renderSelftest(panel) {
    var box = $("#adm-selftest-list");
    var meta = $("#adm-selftest-meta");
    if (!box) return;

    var st = panel.sandbox && panel.sandbox.selftest;

    if (panel.sandbox && panel.sandbox.selftest_running) {
      if (meta) meta.textContent = "正在跑……（真编译五个程序，约 5-10 秒）";
      if (!box.querySelector(".adm-empty")) {
        box.innerHTML = "";
        box.appendChild(el("p", "adm-empty", "自检进行中，页面会自己刷新出结果。"));
      }
      return;
    }

    if (!st || !st.items || !st.items.length) {
      if (meta) meta.textContent = "还没跑过";
      if (!box.querySelector(".adm-empty")) {
        box.innerHTML = "";
        box.appendChild(el("p", "adm-empty",
          "还没跑过自检。点右上角「重跑自检」，或者等页面自动触发一次。"));
      }
      return;
    }

    var pass = st.items.filter(function (it) { return it.status === "pass"; }).length;
    if (meta) {
      meta.textContent = pass + "/" + st.items.length + " 通过 · " + st.took_ms + "ms";
    }

    /* 内容没变就不重渲染：每 5 秒重建一遍 DOM 会让用户正在读的那一行
       文字被选中状态清掉，也会让 :hover 闪一下。 */
    if (box.getAttribute("data-sig") === st.at + ":" + pass) return;
    box.setAttribute("data-sig", st.at + ":" + pass);

    box.innerHTML = "";
    st.items.forEach(function (it) {
      var row = el("div", "adm-case is-" + it.status);
      row.appendChild(el("i", "dot"));

      var main = el("div", "adm-case-main");
      var head = el("div", "adm-case-head");
      head.appendChild(el("b", "", it.name));
      head.appendChild(el("span", "adm-case-ms", it.ms + "ms"));
      main.appendChild(head);
      main.appendChild(el("p", "adm-case-desc", it.desc || ""));
      if (it.detail) main.appendChild(el("p", "adm-case-detail", it.detail));

      row.appendChild(main);
      box.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ 直方图 */

  function renderHist(panel) {
    var box = $("#adm-hist");
    if (!box) return;
    var h = (panel.ops && panel.ops.histogram) || {};
    var labels = h.labels || [];
    var counts = h.counts || [];
    if (!labels.length) return;

    var sig = counts.join(",");
    if (box.getAttribute("data-sig") === sig) return;
    box.setAttribute("data-sig", sig);

    var hmax = Math.max.apply(null, counts.concat([1]));
    box.innerHTML = "";
    labels.forEach(function (label, i) {
      var c = counts[i] || 0;
      var col = el("div", "adm-hist-col");
      col.title = label + " ms：" + c + " 次";
      var bar = el("b");
      /* 有数据但比例极小时给 2% 保底，否则那一格看起来像「没有数据」，
         而「有 1 次」和「一次都没有」在运维上是两件事。 */
      bar.style.height = c ? Math.max(2, Math.round(c * 100 / hmax)) + "%" : "0";
      col.appendChild(bar);
      col.appendChild(el("span", "", label));
      box.appendChild(col);
    });
  }

  /* ------------------------------------------------------------ 三段构成 */

  function applyPhases(panel) {
    var p = (panel.ops && panel.ops.phases) || {};
    var bar = $("#adm-phases .adm-phase-bar");
    if (!bar) return;
    var segs = [["compile", p.compile_pct], ["run", p.run_pct], ["other", p.other_pct]];
    segs.forEach(function (pair, i) {
      var b = bar.children[i];
      if (b) b.style.width = (Number(pair[1]) || 0) + "%";
    });
  }

  /* ------------------------------------------------------------ 拉数据 */

  function refresh() {
    return fetch("/api/admin/panel", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d.ok || !d.panel) throw new Error("响应格式不对");
        var p = d.panel;
        applyHealth(p);
        applyValues(p);
        applySandbox(p);
        renderSelftest(p);
        renderHist(p);
        applyPhases(p);

        /* 自检还在跑就切到快轮询，跑完自动退回慢轮询。
           这不是「多发几次请求」的偷懒，是因为自检要 5-10 秒，
           5 秒一次的话用户要盯着一个不动的页面等两轮。 */
        if (p.sandbox && p.sandbox.selftest_running) startFast();
        else stopFast();
        return p;
      })
      .catch(function (err) {
        /* 失败必须显式显示：面板上的数字如果已经过期，最危险的是
           「看起来一切正常」。把时刻标红，比在控制台留一行 warning 有用。 */
        var stamp = $("#adm-stamp");
        if (stamp) {
          stamp.textContent = "刷新失败：" + (err && err.message ? err.message : err);
          stamp.classList.add("is-bad");
        }
      });
  }

  /* ------------------------------------------------------------ 轮询控制 */

  function startAuto() {
    stopAuto();
    auto = true;
    var btn = $("#adm-refresh");
    if (btn) btn.setAttribute("aria-pressed", "true");
    timer = setInterval(function () {
      /* 页面在后台时不轮询：这一页的读者常常开着它去干别的，
         十个标签页各 5 秒一次能把服务端日志刷满。 */
      if (!document.hidden) refresh();
    }, POLL_MS);
  }

  function stopAuto() {
    auto = false;
    if (timer) { clearInterval(timer); timer = null; }
    var btn = $("#adm-refresh");
    if (btn) btn.setAttribute("aria-pressed", "false");
  }

  function startFast() {
    if (fastTimer) return;
    fastTimer = setInterval(refresh, POLL_FAST_MS);
  }

  function stopFast() {
    if (fastTimer) { clearInterval(fastTimer); fastTimer = null; }
    if (selftestBusy) {
      selftestBusy = false;
      var btn = $("#adm-selftest");
      if (btn) { btn.disabled = false; btn.textContent = "重跑自检"; }
    }
  }

  /* ------------------------------------------------------------ 自检按钮 */

  function runSelftest() {
    var btn = $("#adm-selftest");
    if (!btn || btn.disabled) return;

    btn.disabled = true;
    btn.textContent = "自检中…";
    selftestBusy = true;

    fetch("/api/admin/selftest", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.reason || "服务端拒绝了这次自检");
        /* 立刻拉一次，让「正在跑」的状态马上出来；
           之后由 refresh() 里的快轮询接手。 */
        startFast();
        return refresh();
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.textContent = "重跑自检";
        selftestBusy = false;
        stopFast();
        var meta = $("#adm-selftest-meta");
        if (meta) meta.textContent = "触发失败：" + (err && err.message ? err.message : err);
      });
  }

  /* ------------------------------------------------------------ 启动 */

  document.addEventListener("DOMContentLoaded", function () {
    var refreshBtn = $("#adm-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        if (auto) { stopAuto(); return; }
        startAuto();
        refresh();
      });
    }

    var stBtn = $("#adm-selftest");
    if (stBtn) stBtn.addEventListener("click", runSelftest);

    /* 首屏由服务端渲染（打开就是完整内容，不闪空白）。
       这里只在「页面显示出来之后」拉一次新鲜的：
       服务端那份是请求进来那一刻的，和浏览器真正看到它可能差几百毫秒。 */
    refresh();

    /* 第一次进这一页、且从没跑过自检 → 自动触发一次。
       为什么不放在进程启动时跑：那会让每次 `python app.py` 都等 7 秒
       才能开始响应，而自检的结果只有在有人看的时候才有价值。 */
    fetch("/api/admin/panel", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var sb = d && d.panel && d.panel.sandbox;
        if (sb && !sb.selftest && !sb.selftest_running) runSelftest();
      })
      .catch(function () { /* 拉不到就算了，用户还能手点 */ });
  });
})();
