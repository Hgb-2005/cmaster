/* ============================================================================
   CMaster —— 知识点关系图页面逻辑
   ----------------------------------------------------------------------------
   分工：
     graph.js 管「怎么把一堆节点和边画成一张能拖能缩的图」，
     这个文件管「这张图对用户意味着什么」——谁是先修、谁是后继、
     点进去该去哪一页。

   为什么逻辑写在这里而不是塞进 graph.js：
     引擎不应该认识 /chapter/<cid>/<kid> 这种业务路由。哪天把图谱
     用到题目依赖图上，引擎一行都不用改。
   ============================================================================ */

(function () {
  "use strict";

  /* 路由约定。和 app.py 里的 url_map 一致：/chapter/<cid>、
     /chapter/<cid>/<kid>。全站只有这一处用 JS 拼章节地址，
     改路由时记得同步这里。 */
  var PATH_CHAPTER = "/chapter/";
  var PATH_KP = "/chapter/";

  /* 后端已经通过 m.kinds 传了中文名，这里留一份兜底：
     JSON 缺字段时不该让侧栏出现 "undefined"。 */
  var KIND_FALLBACK = {
    concept: "概念",
    syntax: "语法",
    pitfall: "易错点",
    practice: "练习"
  };

  /* 关系列表里一种节点最多列这么多。
     章节点的后继可能同时有四节内容加两条跨章依赖，全列出来
     侧栏会变成第二个目录，反而找不到「真正要先学的那一条」。 */
  var REL_MAX = 8;

  /* 同一条关系可能有两条边（比如章→节是 tree，节→下一节是 seq，
     两个节点之间既可能是父子又可能是先后）。只保留最强的那个类型：
     先修依赖 > 章节结构 > 阅读顺序。 */
  var EDGE_RANK = { dep: 3, tree: 2, seq: 1 };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 读页面里那份 JSON。
     用 script[type=application/json] 而不是 fetch：首屏就要画图，
     多一次请求就多一次白屏；而且断网时这份数据照样在。 */
  function readJSON(id) {
    var el = $(id);
    if (!el) return null;
    var raw = el.textContent || el.innerText || "";
    if (!raw.trim()) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      /* 解析失败多半是后端 tojson 出了岔子（比如塞了不可序列化的对象）。
         吞掉会变成「页面空白但没有报错」，那种 bug 最难查，
         所以这里明确打出来。 */
      console.error("[map] 图谱数据解析失败：", e);
      return null;
    }
  }

  var state = {
    data: null,
    graph: null,
    host: null,
    byId: {},
    relIn: {},      // id -> [{id, kind}] 谁指向我
    relOut: {},     // id -> [{id, kind}] 我指向谁
    kinds: KIND_FALLBACK,
    selId: null,
    focusTodo: false,
    todoCount: 0,
    sug: [],
    sugIdx: -1
  };

  /* ------------------------------------------------------------ 兜底 */

  /* 数据或引擎缺失时，不要留一块什么都没有的空白。
     用户看到空白只会以为「这页坏了」，看到一句话才知道该怎么办。 */
  function fail(msg) {
    var host = $("graph-host");
    if (host) {
      host.innerHTML = '<div class="map-fail">' + esc(msg) + "</div>";
    }
    var empty = $("map-empty");
    if (empty) empty.innerHTML = "<p>" + esc(msg) + "</p>";
  }

  /* ------------------------------------------------------------ 索引 */

  /* 把扁平的 links 数组翻成「进」和「出」两张邻接表。
     点击节点时侧栏要立刻列出先修和后继，现算 101 条边也行，
     但每次都算一遍没有意义——构建一次，之后都是查表。 */
  function indexRelations(data) {
    var byId = {};
    data.nodes.forEach(function (n) { byId[n.id] = n; });

    var relIn = {};
    var relOut = {};
    data.nodes.forEach(function (n) {
      relIn[n.id] = [];
      relOut[n.id] = [];
    });

    /* 去重：同一个 (来源, 目标) 只留 rank 最高的类型。
       用 Map 而不是对象是因为键里带 "|"，对象键也够，但 Map
       的语义更准——这里确实是「一对一映射」。 */
    var seen = new Map();

    function push(bucket, selfId, otherId, kind) {
      var key = bucket === relIn ? otherId + ">" + selfId : selfId + ">" + otherId;
      var prev = seen.get(key);
      if (prev) {
        if ((EDGE_RANK[kind] || 0) > (EDGE_RANK[prev] || 0)) prev.kind = kind;
        return;
      }
      var item = { id: otherId, kind: kind };
      seen.set(key, item);
      bucket[selfId].push(item);
    }

    data.links.forEach(function (l) {
      var a = byId[l.source];
      var b = byId[l.target];
      if (!a || !b) return;
      push(relOut, a.id, b.id, l.kind || "tree");
      push(relIn, b.id, a.id, l.kind || "tree");
    });

    /* 排序：依赖优先，然后按章号。
       不排序的话顺序取决于 links 数组的生成顺序，
       虽然稳定但对读者没有意义——「先修」列表应该从最该先看的开始。 */
    function sorter(list) {
      list.sort(function (x, y) {
        var rx = EDGE_RANK[x.kind] || 0;
        var ry = EDGE_RANK[y.kind] || 0;
        if (rx !== ry) return ry - rx;
        var nx = byId[x.id];
        var ny = byId[y.id];
        return (nx ? nx.cid : 0) - (ny ? ny.cid : 0) || (nx && nx.kid || 0) - (ny && ny.kid || 0);
      });
    }
    Object.keys(relIn).forEach(function (id) { sorter(relIn[id]); });
    Object.keys(relOut).forEach(function (id) { sorter(relOut[id]); });

    return { byId: byId, relIn: relIn, relOut: relOut };
  }

  /* ------------------------------------------------------------ 侧栏 */

  function hrefOf(n) {
    if (!n) return null;
    if (n.type === "chapter") return PATH_CHAPTER + n.cid;
    return PATH_KP + n.cid + "/" + n.kid;
  }

  /* 章节的阅读进度要从节点列表现算，后端没给。
     为什么不在后端算：这是纯展示用的聚合，一个 for 循环的事，
     放后端就要多一个字段、多一处要跟着进度更新。 */
  function chapterProgress(cid) {
    var total = 0;
    var done = 0;
    state.data.nodes.forEach(function (n) {
      if (n.type !== "kp" || n.cid !== cid) return;
      total++;
      if (n.done) done++;
    });
    return { total: total, done: done };
  }

  /* 章里第一个没读的小节，用来做「继续读」。
     全读完了返回 null，侧栏就不出这个按钮——出一个点了没反应的按钮
     比不出更糟。 */
  function firstUnread(cid) {
    var nodes = state.data.nodes;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.type === "kp" && n.cid === cid && !n.done) return n;
    }
    return null;
  }

  function kindName(kind) {
    return state.kinds[kind] || KIND_FALLBACK[kind] || "知识点";
  }

  function renderMeta(n) {
    var tags = [];
    if (n.type === "chapter") {
      tags.push('<span class="md-tag">第 ' + n.cid + " 章</span>");
    } else {
      tags.push('<span class="md-tag">第 ' + n.cid + " 章 · 第 " + n.kid + " 节</span>");
      tags.push('<span class="md-tag">' + esc(kindName(n.kind)) + "</span>");
    }

    /* 阶段名。stages 是后端按章出现顺序去重后的名字列表，
       node.stage 是它的下标——这里查不到就不显示，别硬凑。 */
    if (state.data.stages && state.data.stages[n.stage]) {
      tags.push('<span class="md-tag">' + esc(state.data.stages[n.stage]) + "</span>");
    }

    if (n.type === "chapter") {
      var p = chapterProgress(n.cid);
      tags.push('<span class="md-tag' + (p.done === p.total && p.total ? " is-done" : "") + '">' +
        "已读 " + p.done + "/" + p.total + "</span>");
    } else {
      tags.push('<span class="md-tag ' + (n.done ? "is-done" : "is-todo") + '">' +
        (n.done ? "已读" : "未读") + "</span>");
    }
    return tags.join("");
  }

  function renderActions(n) {
    var html = "";
    if (n.type === "chapter") {
      html += '<a href="' + hrefOf(n) + '">进入本章</a>';
      var next = firstUnread(n.cid);
      if (next) {
        html += '<a href="' + hrefOf(next) + '">继续：第 ' + next.kid + " 节</a>";
      }
    } else {
      html += '<a href="' + hrefOf(n) + '">去本节</a>';
      html += '<a href="' + PATH_CHAPTER + n.cid + '">看整章</a>';
    }
    return html;
  }

  function renderRel(list, emptyText) {
    if (!list || !list.length) {
      return '<li class="md-none">' + esc(emptyText) + "</li>";
    }
    var shown = list.slice(0, REL_MAX);
    var html = shown.map(function (it) {
      var n = state.byId[it.id];
      if (!n) return "";
      /* 这里的链接是「在图里跳过去」，不是跳页。
         在关系列表里点一下就直接离开图谱，等于每次探索都要后退一次，
         所以用 button 语义（a 但拦掉默认行为），真正跳页靠上面的按钮。 */
      return '<li><a href="#" data-goto="' + esc(it.id) + '" data-edge="' + esc(it.kind) + '"' +
        ' title="' + esc(n.label) + '">' + esc(n.short || n.label) + "</a></li>";
    }).join("");

    if (list.length > REL_MAX) {
      html += '<li class="md-none">还有 ' + (list.length - REL_MAX) + " 个，图里都连着</li>";
    }
    return html;
  }

  function renderDetail(n) {
    var detail = $("map-detail");
    var empty = $("map-empty");
    if (!detail || !n) return;

    $("md-title").textContent = n.label;
    $("md-meta").innerHTML = renderMeta(n);
    $("md-actions").innerHTML = renderActions(n);
    $("md-prev").innerHTML = renderRel(state.relIn[n.id], "没有前置要求，可以直接看");
    $("md-next").innerHTML = renderRel(state.relOut[n.id], "这里是终点，后面没有依赖它的内容");

    detail.hidden = false;
    if (empty) empty.hidden = true;

    /* 窄屏上侧栏在画布下面，点了节点如果不同时把侧栏带进视野，
       用户会以为「点了没反应」。block: "nearest" 保证已经在视野里时
       不产生多余滚动。 */
    if (window.matchMedia && window.matchMedia("(max-width: 880px)").matches) {
      var aside = $("map-aside");
      if (aside && aside.scrollIntoView) aside.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function clearDetail() {
    state.selId = null;
    var detail = $("map-detail");
    var empty = $("map-empty");
    if (detail) detail.hidden = true;
    if (empty) empty.hidden = false;
  }

  /* 选中一个节点：引擎里高亮 + 侧栏填内容。
     引擎的 focus() 只负责「把视线移过去」，不会抛事件——
     它不知道页面要不要显示详情，所以这一步由页面自己补。 */
  function selectNode(id, opts) {
    var n = state.byId[id];
    if (!n) return;
    state.selId = id;
    if (state.graph) state.graph.focus(id);
    renderDetail(n);
    if (opts && opts.fromSearch) {
      /* 搜索定位后把焦点交还画布，接着就能用方向键继续走 */
      var canvas = state.host && state.host.querySelector("canvas");
      if (canvas) canvas.focus();
    }
  }

  /* ------------------------------------------------------------ 提示行 */

  function note(msg) {
    var el = $("map-note");
    if (el) el.textContent = msg || "";
  }

  /* ------------------------------------------------------------ 搜索 */

  /* 搜索是「定位」不是「筛选」：
     图是空间记忆，按关键词把节点删掉、清空后恢复，等于让人重新
     找一遍位置。所以这里只移动视线，节点一个都不动。 */
  function initSearch() {
    var input = $("map-search");
    var box = $("map-suggest");
    if (!input || !box) return;

    function close() {
      box.hidden = true;
      state.sug = [];
      state.sugIdx = -1;
    }

    function pick(i) {
      var item = state.sug[i];
      if (!item) return;
      input.value = item.label;
      close();
      selectNode(item.id, { fromSearch: true });
      note("已定位：" + item.label);
      input.blur();
    }

    function match(q) {
      var lower = q.toLowerCase();
      var out = [];
      state.data.nodes.forEach(function (n) {
        var hitLabel = String(n.label).toLowerCase().indexOf(lower) >= 0;
        /* 允许只输章号：敲 "7" 应该能直接找到第 7 章，
           而不是把 48 个标题里带 7 的全列出来。 */
        var hitNum = n.type === "chapter" && String(n.cid).indexOf(q) === 0;
        if (!hitLabel && !hitNum) return;
        out.push(n);
      });
      /* 完全一致的排最前，其次章节点（章是更大的锚点），再按章号。 */
      out.sort(function (a, b) {
        var ea = a.label === q ? 0 : 1;
        var eb = b.label === q ? 0 : 1;
        if (ea !== eb) return ea - eb;
        if (a.type !== b.type) return a.type === "chapter" ? -1 : 1;
        return a.cid - b.cid || (a.kid || 0) - (b.kid || 0);
      });
      return out.slice(0, 8);
    }

    function render(list, q) {
      state.sug = list;
      state.sugIdx = list.length ? 0 : -1;
      if (!list.length) {
        box.innerHTML = '<li class="is-empty">没有匹配「' + esc(q) + "」的章节或知识点</li>";
        box.hidden = false;
        return;
      }
      box.innerHTML = list.map(function (n, i) {
        var suffix = n.type === "chapter"
          ? '<span class="sug-kind">第 ' + n.cid + " 章</span>"
          : '<span class="sug-kind">第 ' + n.cid + " 章 · " + esc(kindName(n.kind)) + "</span>";
        return '<li data-i="' + i + '"' + (i === 0 ? ' class="is-active"' : "") + ">" +
          esc(n.label) + suffix + "</li>";
      }).join("");
      box.hidden = false;
    }

    function highlight() {
      var items = box.querySelectorAll("li[data-i]");
      Array.prototype.forEach.call(items, function (li, i) {
        li.classList.toggle("is-active", i === state.sugIdx);
      });
      var act = items[state.sugIdx];
      /* 候选列表高度有限，方向键走到看不见的位置要自己滚进来 */
      if (act && act.scrollIntoView) act.scrollIntoView({ block: "nearest" });
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (!q) {
        close();
        note("");
        return;
      }
      render(match(q), q);
    });

    /* 用 pointerdown 而不是 click：click 之前 input 会先 blur，
       如果 blur 里关了候选列表，click 的目标就已经不存在了。
       preventDefault 阻止 input 失焦，选完再手动 blur。 */
    box.addEventListener("pointerdown", function (e) {
      var li = e.target.closest && e.target.closest("li[data-i]");
      if (!li) return;
      e.preventDefault();
      pick(parseInt(li.getAttribute("data-i"), 10));
    });

    input.addEventListener("keydown", function (e) {
      if (box.hidden) {
        if (e.key === "ArrowDown" && input.value.trim()) {
          render(match(input.value.trim()), input.value.trim());
          e.preventDefault();
        }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (state.sug.length) {
          state.sugIdx = (state.sugIdx + 1) % state.sug.length;
          highlight();
        }
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (state.sug.length) {
          state.sugIdx = state.sugIdx <= 0 ? state.sug.length - 1 : state.sugIdx - 1;
          highlight();
        }
      } else if (e.key === "Enter") {
        e.preventDefault();
        pick(state.sugIdx < 0 ? 0 : state.sugIdx);
      } else if (e.key === "Escape") {
        e.preventDefault();
        close();
        input.blur();
      }
    });

    input.addEventListener("blur", function () {
      /* 延迟关闭：pointerdown 已经 preventDefault 了，正常点击不会
         走到这里；但鼠标点页面别处时 blur 会先于任何点击发生，
         立刻关掉会让「点空白处收起」这个动作看起来像闪一下。 */
      window.setTimeout(close, 120);
    });
  }

  /* ------------------------------------------------------------ 聚焦未读 */

  /* 聚焦模式：把已经读过的知识点压暗，让没读的凸显出来。
     为什么章节点不跟着暗：只留一堆孤零零的小点，用户不知道
     它们属于哪一章——地图上把城市名隐掉只留红点是没用的。 */
  function initTodoFilter() {
    var btn = $("map-focus-todo");
    if (!btn) return;

    state.todoCount = state.data.nodes.filter(function (n) {
      return n.type === "kp" && !n.done;
    }).length;

    btn.addEventListener("click", function () {
      if (!state.todoCount) {
        note("48 个知识点都读完了，没有未读可以凸显。");
        btn.disabled = true;
        return;
      }
      state.focusTodo = !state.focusTodo;
      btn.setAttribute("aria-pressed", state.focusTodo ? "true" : "false");
      btn.textContent = state.focusTodo ? "取消聚焦" : "聚焦未读";

      if (state.focusTodo) {
        state.graph.setFocus(function (n) {
          return n.type === "chapter" || !n.done;
        });
        note("聚焦中：还剩 " + state.todoCount + " 个知识点没读（空心点）");
      } else {
        state.graph.setFocus(null);
        note("");
      }
    });

    /* 全读完了，按钮从一进来就该是禁用态，
       而不是等用户点了才告诉他「没东西可聚焦」 */
    if (!state.todoCount) {
      btn.disabled = true;
      btn.title = "所有知识点都已读，没有可聚焦的内容";
    }
  }

  /* ------------------------------------------------------------ 启动 */

  function boot() {
    var host = $("graph-host");
    var data = readJSON("map-data");

    if (!host) return;
    if (!data || !data.nodes || !data.nodes.length) {
      fail("图谱数据没加载出来，刷新页面试试。");
      return;
    }
    if (!window.CMGraph) {
      fail("图谱组件没加载出来（js/lib/graph.js），刷新页面试试。");
      return;
    }

    state.data = data;
    state.host = host;
    state.kinds = data.kinds || KIND_FALLBACK;

    var idx = indexRelations(data);
    state.byId = idx.byId;
    state.relIn = idx.relIn;
    state.relOut = idx.relOut;

    state.graph = window.CMGraph.mount(host, {
      nodes: data.nodes,
      links: data.links,
      /* 双击进入。引擎只调这个回调，具体跳哪个 URL 由页面决定。 */
      onActivate: function (n) {
        var href = hrefOf(n);
        if (href) window.location.href = href;
      }
    });

    /* 引擎通过事件告诉我们「用户选中了谁」，这里再翻译成侧栏内容。
       反过来页面调 selectNode 时不需要走事件——那是内部知道的事。 */
    host.addEventListener("cm:graph-select", function (e) {
      var d = e.detail;
      if (!d) {
        clearDetail();
        return;
      }
      state.selId = d.id;
      renderDetail(state.byId[d.id]);
    });

    /* 关系列表里的跳转：拦住默认行为，在图中定位。 */
    var detail = $("map-detail");
    if (detail) {
      detail.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("a[data-goto]");
        if (!a) return;
        e.preventDefault();
        selectNode(a.getAttribute("data-goto"));
      });
    }

    var reset = $("map-reset");
    if (reset) {
      reset.addEventListener("click", function () {
        state.graph.reset();
        note("视角已重置。拖动空白处平移，滚轮缩放。");
      });
    }

    initSearch();
    initTodoFilter();

    /* 首屏给一句引导：这张图的自解释能力比看上去弱——
       不告诉用户「空心是没读」，大多数人会以为是两种节点类型。 */
    if (data.stats && data.stats.kp_done === 0) {
      note("还没开始读：空心小点是没读过的知识点，双击可以直接进去。");
    } else if (data.stats) {
      note("已读 " + data.stats.kp_done + "/" + data.stats.kp_total +
        " 个知识点。空心点是还没读的，双击节点直接进去。");
    }

    /* 给控制台留一个调试入口。
       真出问题时能直接敲 CMMap.graph.k 看缩放、CMMap.graph.sim.alpha
       看模拟有没有收敛，比在源码里打断点快得多。 */
    window.CMMap = {
      graph: state.graph,
      data: state.data,
      select: selectNode,
      dump: function () {
        return {
          nodes: state.graph.nodes.length,
          links: state.graph.links.length,
          settled: state.graph._settled,
          alpha: state.graph.sim.alpha,
          zoom: state.graph.k
        };
      }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
