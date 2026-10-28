/* ============================================================
   CMaster —— 交互层
   负责「页面活了」的部分：搜索、快捷键、代码复制、滚动渐入、
   阅读进度、回到顶部、页面转场。

   和 editor.js 的分工：
   - editor.js 管「代码编辑器 + 判题结果渲染」，只在需要的页面起作用
   - ux.js 管「全站通用交互」，每个页面都跑，但每项都先探测元素存在再干活

   所有功能都是增量增强（progressive enhancement）：
   这段脚本加载失败，站点仍然能读能写能判题，只是少了动效和快捷键。
   ============================================================ */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- 工具 */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /** 正在输入框 / 编辑器里？这类状态下不能抢用户按键。 */
  function isTyping(e) {
    var t = e.target;
    if (!t) return false;
    var tag = (t.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (t.isContentEditable) return true;
    if (t.closest && t.closest(".CodeMirror")) return true;
    return false;
  }

  /** 节流到每帧最多执行一次，滚动事件直接绑会拖慢低端机。 */
  function rafThrottle(fn) {
    var queued = false;
    return function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        fn();
      });
    };
  }

  var ICON_COPY =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/>' +
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var ICON_DONE =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" ' +
    'stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  /* ---------------------------------------------------------- 顶栏状态 */

  function initTopbar() {
    var bar = $(".topbar");
    if (!bar) return;
    var onScroll = rafThrottle(function () {
      bar.classList.toggle("scrolled", window.scrollY > 8);
    });
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------------------------------------------------------- 阅读进度 */

  function initReadbar() {
    // 只在长文档页出现：短页面显示进度条毫无意义，反而分心
    var article = $(".content .md") || $(".q-desc") || $(".play-layout");
    if (!article) return;

    var bar = document.createElement("div");
    bar.className = "readbar";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);

    var onScroll = rafThrottle(function () {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var pct = max > 40 ? Math.min(1, window.scrollY / max) : 0;
      bar.style.width = (pct * 100).toFixed(2) + "%";
    });
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
  }

  /* ---------------------------------------------------------- 滚动渐入 */

  var REVEAL_SELECTOR = [
    ".stage-head",
    ".stage-desc",
    ".grid > .chapter-card",
    ".chapter-exercises",
    ".q-row",
    ".stat-card",
    ".progress-row",
    ".wrong-item",
    ".pane",
    ".code-block",
    ".md > h2",
    ".md > h3",
    ".md > table",
    ".md > blockquote",
    ".kp-nav"
  ].join(",");

  function initReveal() {
    if (reduceMotion || !("IntersectionObserver" in window)) return;

    var vh = window.innerHeight;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });

    var groups = new Map();
    $$(REVEAL_SELECTOR).forEach(function (el) {
      var rect = el.getBoundingClientRect();
      // 首屏内的元素直接可见，不加 reveal —— 免得用户一进来先看到空白再浮现
      if (rect.top < vh * 0.92) return;
      if (el.closest(".cmdk-mask, .kbd-mask, .CodeMirror")) return;

      var parent = el.parentElement;
      var idx = groups.get(parent) || 0;
      groups.set(parent, idx + 1);

      el.style.setProperty("--i", String(Math.min(idx, 7)));
      el.classList.add("reveal");
      io.observe(el);
    });

    // 兜底：两秒后还没进视口的（比如在很长的页底）也强制显形，
    // 防止布局计算出错导致内容永久隐身
    setTimeout(function () {
      $$(".reveal:not(.in)").forEach(function (el) {
        var r = el.getBoundingClientRect();
        if (r.top < window.innerHeight * 1.6) el.classList.add("in");
      });
    }, 2000);
  }

  /* ---------------------------------------------------------- 卡片光斑 */

  function initCardGlow() {
    if (reduceMotion) return;
    $$(".chapter-card").forEach(function (card) {
      card.addEventListener("mousemove", function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
        card.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
      });
    });
  }

  /* ---------------------------------------------------------- 数字滚动 */

  function initCountUp() {
    if (reduceMotion) return;
    $$(".hero-stat b").forEach(function (b) {
      // 只处理「整块就是一个数字」的，带 /48 小字的复合结构跳过
      if (b.childNodes.length !== 1) return;
      var node = b.firstChild;
      if (!node || node.nodeType !== 3) return;

      var target = parseInt(String(node.nodeValue).trim(), 10);
      if (!isFinite(target) || target <= 0) return;

      var start = performance.now();
      var dur = 850;
      node.nodeValue = "0";

      requestAnimationFrame(function tick(now) {
        var p = Math.min(1, (now - start) / dur);
        var eased = 1 - Math.pow(1 - p, 3);
        node.nodeValue = String(Math.round(target * eased));
        if (p < 1) requestAnimationFrame(tick);
      });
    });
  }

  /* ---------------------------------------------------------- 代码块 */

  function copyText(text) {
    // clipboard API 在 http 非 localhost 下不可用，所以要留一条老路
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error("copy failed"));
    });
  }

  function initCodeBlocks() {
    $$("pre.md-code").forEach(function (pre) {
      if (pre.parentElement && pre.parentElement.classList.contains("code-block")) return;

      var lang = (pre.getAttribute("data-lang") || "text").toLowerCase();
      var isFigure = (lang === "text" || lang === "txt" || lang === "");

      var wrap = document.createElement("div");
      wrap.className = "code-block" + (isFigure ? " is-figure" : "");
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);

      var bar = document.createElement("div");
      bar.className = "code-bar";

      var label = document.createElement("span");
      label.className = "code-lang";
      label.textContent = isFigure ? "图示" : lang;
      bar.appendChild(label);

      var spacer = document.createElement("span");
      spacer.className = "spacer";
      bar.appendChild(spacer);

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.innerHTML = ICON_COPY + "<span>复制</span>";
      btn.setAttribute("aria-label", "复制代码");
      bar.appendChild(btn);

      wrap.insertBefore(bar, pre);

      var timer = null;
      btn.addEventListener("click", function () {
        var codeEl = pre.querySelector("code") || pre;
        copyText(codeEl.innerText.replace(/\n$/, "")).then(function () {
          btn.classList.add("done");
          btn.innerHTML = ICON_DONE + "<span>已复制</span>";
          clearTimeout(timer);
          timer = setTimeout(function () {
            btn.classList.remove("done");
            btn.innerHTML = ICON_COPY + "<span>复制</span>";
          }, 1600);
        }).catch(function () {
          btn.innerHTML = "<span>复制失败</span>";
          clearTimeout(timer);
          timer = setTimeout(function () {
            btn.innerHTML = ICON_COPY + "<span>复制</span>";
          }, 1600);
        });
      });
    });
  }

  /* ---------------------------------------------------------- 回到顶部 */

  function initToTop() {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "to-top";
    btn.setAttribute("aria-label", "回到顶部");
    btn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>';
    document.body.appendChild(btn);

    btn.addEventListener("click", function () {
      window.scrollTo({
        top: 0,
        behavior: reduceMotion ? "auto" : "smooth"
      });
    });

    var onScroll = rafThrottle(function () {
      btn.classList.toggle("on", window.scrollY > 700);
    });
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------------------------------------------------------- 搜索 */

  var searchState = {
    built: false,
    items: [],
    sel: 0,
    lastQ: "",
    timer: null,
    ctrl: null
  };

  function buildSearch() {
    if (searchState.built) return;
    searchState.built = true;

    var mask = document.createElement("div");
    mask.className = "cmdk-mask";
    mask.setAttribute("role", "dialog");
    mask.setAttribute("aria-modal", "true");
    mask.setAttribute("aria-label", "站内搜索");
    mask.innerHTML =
      '<div class="cmdk">' +
        '<div class="cmdk-input-row">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/>' +
            '<path d="m21 21-4.3-4.3"/></svg>' +
          '<input class="cmdk-input" type="text" autocomplete="off" spellcheck="false" ' +
            'placeholder="搜索章节、知识点、题目…" aria-label="搜索关键词">' +
          '<span class="cmdk-esc">ESC</span>' +
        '</div>' +
        '<div class="cmdk-results"></div>' +
        '<div class="cmdk-foot">' +
          '<span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>' +
          '<span><kbd>↵</kbd> 打开</span>' +
          '<span><kbd>Esc</kbd> 关闭</span>' +
          '<span class="spacer"></span>' +
          '<span class="cmdk-count"></span>' +
        '</div>' +
      '</div>';
    document.body.appendChild(mask);

    var input = $(".cmdk-input", mask);
    var results = $(".cmdk-results", mask);

    input.addEventListener("input", function () {
      var q = input.value.trim();
      clearTimeout(searchState.timer);
      if (!q) {
        searchState.items = [];
        searchState.sel = 0;
        renderShortcuts(results);
        return;
      }
      // 每敲一个字就请求太浪费，等一下再发
      searchState.timer = setTimeout(function () { doSearch(q, results); }, 130);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveSel(1, results);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        moveSel(-1, results);
      } else if (e.key === "Enter") {
        e.preventDefault();
        var cur = searchState.items[searchState.sel];
        if (cur) go(cur.url);
      }
    });

    mask.addEventListener("mousedown", function (e) {
      if (e.target === mask) closeSearch();
    });

    renderShortcuts(results);
  }

  function renderShortcuts(container) {
    var shortcuts = [
      { icon: "章", title: "全部课程", meta: "12 章", url: "/" },
      { icon: "题", title: "题库 · 60 道练习", meta: "60", url: "/problems" },
      { icon: "跑", title: "游乐场 · 随便写点 C", meta: "", url: "/playground" }
    ];
    var me = document.body.dataset.loggedIn === "1";
    if (me) {
      shortcuts.push({ icon: "错", title: "错题本", meta: "", url: "/wrongbook" });
      shortcuts.push({ icon: "我", title: "学习进度", meta: "", url: "/profile" });
    }

    container.textContent = "";
    var g = document.createElement("div");
    g.className = "cmdk-group";
    g.textContent = "快速前往";
    container.appendChild(g);

    searchState.items = shortcuts;
    searchState.sel = 0;

    shortcuts.forEach(function (item, i) {
      container.appendChild(makeItem(item, i, "", container));
    });
    var count = $(".cmdk-count");
    if (count) count.textContent = "";
  }

  function makeItem(item, i, q, container) {
    var row = document.createElement("div");
    row.className = "cmdk-item" + (i === 0 ? " sel" : "");
    row.setAttribute("role", "option");
    row.dataset.idx = String(i);

    var icon = document.createElement("span");
    icon.className = "ci-icon";
    icon.textContent = item.icon || "·";
    row.appendChild(icon);

    var title = document.createElement("span");
    title.className = "ci-title";
    paintHighlight(title, item.title, q);
    row.appendChild(title);

    if (item.meta) {
      var meta = document.createElement("span");
      meta.className = "ci-meta";
      meta.textContent = item.meta;
      row.appendChild(meta);
    }

    row.addEventListener("mouseenter", function () {
      $$(".cmdk-item", container).forEach(function (n) { n.classList.remove("sel"); });
      row.classList.add("sel");
      searchState.sel = i;
    });
    row.addEventListener("click", function () { go(item.url); });

    return row;
  }

  /** 只把命中的片段用 <mark> 包起来。全程 textContent，不拼 innerHTML。 */
  function paintHighlight(el, text, q) {
    el.textContent = "";
    if (!q) { el.textContent = text; return; }

    var lower = text.toLowerCase();
    var ql = q.toLowerCase();
    var idx = lower.indexOf(ql);
    if (idx < 0) { el.textContent = text; return; }

    el.appendChild(document.createTextNode(text.slice(0, idx)));
    var m = document.createElement("mark");
    m.textContent = text.slice(idx, idx + q.length);
    el.appendChild(m);
    el.appendChild(document.createTextNode(text.slice(idx + q.length)));
  }

  function doSearch(q, container) {
    searchState.lastQ = q;

    if (searchState.ctrl) searchState.ctrl.abort();
    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    searchState.ctrl = ctrl;

    fetch("/api/search?q=" + encodeURIComponent(q), {
      credentials: "same-origin",
      signal: ctrl ? ctrl.signal : undefined
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (searchState.lastQ !== q) return;  // 回来晚了，已经有更新的查询
        var items = (d && d.results) || [];

        container.textContent = "";
        searchState.items = items;
        searchState.sel = 0;

        if (!items.length) {
          var empty = document.createElement("div");
          empty.className = "cmdk-empty";
          empty.textContent = "没有找到「" + q + "」，换个词试试";
          container.appendChild(empty);
        } else {
          var lastType = null;
          var visibleIdx = 0;
          items.forEach(function (item) {
            if (item.group && item.group !== lastType) {
              lastType = item.group;
              var g = document.createElement("div");
              g.className = "cmdk-group";
              g.textContent = item.group;
              container.appendChild(g);
            }
            container.appendChild(makeItem(item, visibleIdx, q, container));
            visibleIdx++;
          });
          searchState.sel = 0;
        }

        var count = $(".cmdk-count");
        if (count) count.textContent = items.length ? items.length + " 条结果" : "";
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        container.textContent = "";
        var e2 = document.createElement("div");
        e2.className = "cmdk-empty";
        e2.textContent = "搜索出错了，稍后再试";
        container.appendChild(e2);
      });
  }

  function moveSel(delta, container) {
    var items = $$(".cmdk-item", container);
    if (!items.length) return;
    var next = (searchState.sel + delta + items.length) % items.length;
    items.forEach(function (n) { n.classList.remove("sel"); });
    items[next].classList.add("sel");
    items[next].scrollIntoView({ block: "nearest" });
    searchState.sel = next;
  }

  function go(url) {
    closeSearch();
    // 搜索跳转也走淡出，和普通链接一致
    document.body.classList.add("cm-leaving");
    setTimeout(function () { location.href = url; }, 130);
  }

  function openSearch() {
    buildSearch();
    var mask = $(".cmdk-mask");
    if (!mask) return;
    mask.classList.add("on");
    document.body.style.overflow = "hidden";
    var input = $(".cmdk-input", mask);
    input.value = "";
    renderShortcuts($(".cmdk-results", mask));
    setTimeout(function () { input.focus(); }, 30);
  }

  function closeSearch() {
    var mask = $(".cmdk-mask");
    if (!mask) return;
    mask.classList.remove("on");
    document.body.style.overflow = "";
  }

  function isSearchOpen() {
    var mask = $(".cmdk-mask");
    return !!(mask && mask.classList.contains("on"));
  }

  /* ---------------------------------------------------------- 快捷键面板 */

  function buildKbd() {
    if ($(".kbd-mask")) return;
    var rows = [
      { keys: ["/"], desc: "打开搜索" },
      { keys: ["Ctrl", "K"], desc: "打开搜索（同 /）" },
      { keys: ["?"], desc: "显示这份快捷键表" },
      { keys: ["J"], desc: "下一节 · 下一个知识点" },
      { keys: ["K"], desc: "上一节" },
      { keys: ["Esc"], desc: "关闭弹层" }
    ];
    var mask = document.createElement("div");
    mask.className = "kbd-mask";
    mask.innerHTML = '<div class="kbd-panel"><h3>键盘快捷键</h3><div class="kbd-list"></div></div>';
    document.body.appendChild(mask);

    var list = $(".kbd-list", mask);
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "kbd-row";
      var keys = document.createElement("span");
      keys.className = "keys";
      r.keys.forEach(function (k) {
        var kb = document.createElement("kbd");
        kb.textContent = k;
        keys.appendChild(kb);
      });
      row.appendChild(keys);
      var desc = document.createElement("span");
      desc.textContent = r.desc;
      row.appendChild(desc);
      list.appendChild(row);
    });

    mask.addEventListener("mousedown", function (e) {
      if (e.target === mask) closeKbd();
    });
  }

  function openKbd() {
    buildKbd();
    var mask = $(".kbd-mask");
    mask.classList.add("on");
    document.body.style.overflow = "hidden";
  }

  function closeKbd() {
    var mask = $(".kbd-mask");
    if (mask) mask.classList.remove("on");
    document.body.style.overflow = "";
  }

  /* ---------------------------------------------------------- 键盘总控 */

  function initKeys() {
    document.addEventListener("keydown", function (e) {
      // Esc 优先级最高，任何弹层都能关
      if (e.key === "Escape") {
        if (isSearchOpen()) { e.preventDefault(); closeSearch(); return; }
        if ($(".kbd-mask.on")) { e.preventDefault(); closeKbd(); return; }
      }

      if (isTyping(e)) return;

      var metaK = (e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K");
      if (metaK) { e.preventDefault(); openSearch(); return; }

      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "/") { e.preventDefault(); openSearch(); return; }
      if (e.key === "?") { e.preventDefault(); openKbd(); return; }

      // 章节页 J/K 走上下节，对齐 Vim 习惯，也不用摸方向键
      if (e.key === "j" || e.key === "J") { e.preventDefault(); navStep("next"); return; }
      if (e.key === "k" || e.key === "K") { e.preventDefault(); navStep("prev"); return; }
    });
  }

  function navStep(dir) {
    var nav = $(".kp-nav");
    if (!nav) return;
    var link = dir === "next" ? $("a.next", nav) : $("a:not(.next)", nav);
    if (link && link.href) go(link.href);
  }

  /* ---------------------------------------------------------- 页面转场 */

  function initTransition() {
    if (reduceMotion) return;

    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

      var a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      if (a.target && a.target !== "_self") return;
      if (a.hasAttribute("download")) return;

      // 锚点跳转是页内行为，不该走离场动画
      var raw = a.getAttribute("href") || "";
      if (!raw || raw.charAt(0) === "#") return;

      if (a.host && a.host !== location.host) return;
      if (/^(mailto|tel|javascript):/i.test(raw)) return;

      e.preventDefault();
      document.body.classList.add("cm-leaving");
      setTimeout(function () { location.href = a.href; }, 145);
    });

    // 浏览器前进/后退回来时可能还带着离场类，要清掉
    window.addEventListener("pageshow", function () {
      document.body.classList.remove("cm-leaving");
    });
  }

  /* ---------------------------------------------------------- 启动 */

  function boot() {
    initCodeBlocks();   // 先包代码块，后面的 reveal 选择器才认 .code-block
    initTopbar();
    initReadbar();
    initReveal();
    initCardGlow();
    initCountUp();
    initToTop();
    initKeys();
    initTransition();

    var trigger = $("#search-trigger");
    if (trigger) {
      trigger.addEventListener("click", function (e) {
        e.preventDefault();
        openSearch();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // 给别的脚本（比如判题页）复用
  window.CMUX = {
    openSearch: openSearch,
    closeSearch: closeSearch,
    openKbd: openKbd
  };
})();
