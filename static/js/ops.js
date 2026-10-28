/* ============================================================================
   CMaster —— 运行状态页（/ops）
   ----------------------------------------------------------------------------
   分工：
     metrics.py  算指标：分位数、直方图分桶、健康判据，全在服务端
     ops.html    首屏由服务端把快照渲染进 DOM（刷新时不会先白一下）
     ops.js      只做三件事：把快照变成图、把「哪些数变了」标出来、按需轮询

   为什么分位数不在前端算：
     400 条记录里每条又有若干用例，几万个数字在前端重算一遍既慢，又会和
     Python 侧算出不一样的结果——两边各写一份分位数的线性插值，迟早在某个
     边界样本上分叉，而「面板显示 812ms、接口说 790ms」这种分歧根本没人能
     查出原因。所以接口只传算好的数，前端一个减法都不做。

   为什么只有这一页轮询：
     另外两页（分析 / 图谱）的数据是「学习进度」，用户自己改的，改完会刷新；
     这一页的数据是「机器状态」，用户不动它也会变。没有第二个东西会变，就
     不给全站加心跳。
   ============================================================================ */

(function () {
  "use strict";

  /* ============================================================
     一、常量
     ============================================================ */

  /* 5 秒。判题的 P95 在几百毫秒量级，5 秒足够让「刚交的那道题」出现在
     曲线右端；再快就是拿 CPU 换一个没人看的数字。 */
  var POLL_MS = 5000;

  /* 连续失败几次就停掉自动刷新。不停的话，后端挂了之后这个页面会每 5 秒
     在控制台和网络面板留一条记录，一晚上就是一万多条——而用户看到的只是
     「数字不动了」，根本不知道已经坏了。 */
  var FAIL_LIMIT = 3;

  /* 占比条里一段的可用像素低于这个值就不写文字。见 applyPhase 里的说明。 */
  var PH_MIN_PX = 62;

  var CHART_DUR = 720;

  /* 全站只有这一处用 JS 拼业务路由。改 app.py 的 problems_page 路由时
     记得同步这里——空态里的那个链接是 JS 生成的。 */
  var PATH_PROBLEMS = "/problems";

  /* 三段的显示名。other 不叫「进程开销」：起进程的时间在 run_once 里
     就被计时器圈进去了，这一段只剩建目录、读输出、比对、清理。 */
  var PH_LABEL = { compile: "编译", run: "运行", other: "建目录与比对" };

  /* 空态文案。和 ops.html 里 {% else %} 分支那段保持一致——
     两边都只在「一次判题都没有」时出现，改一处就得改另一处。 */
  var EMPTY_HTML =
    '<p class="ops-empty">还没有判题记录。去<a href="' + PATH_PROBLEMS + '">做一道题</a>，' +
    '或者跑 <code>python tools/loadtest.py</code> 造一批真实流量。</p>';

  /* 单系列图表不显示图例。图例的作用是「区分」，只有一项时它只是占地方，
     而且会把本来就不高的画布再压掉 40px。 */
  var LEGEND_H = 40;

  /* ============================================================
     二、小工具
     ============================================================ */

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /* 读页面里那份首屏快照。
     用 script[type=application/json] 而不是让 JS 再请求一次：首屏就有图，
     少一次往返；而且这份数据是服务端算的，和 HTML 上的数字必然一致。 */
  function readJSON(id) {
    var el = $(id);
    if (!el) return null;
    var raw = el.textContent || el.innerText || "";
    if (!raw.trim()) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      /* 解析失败吞掉会变成「页面有数字但一张图都没有」，那种 bug 最难查。
         明确打出来，而且返回 null 让调用方走降级路径。 */
      console.error("[ops] 快照解析失败：", e);
      return null;
    }
  }

  /* 按 "a.b.c" 取值。指标卡的 data-k 就是这么写的，
     加一张卡只要在 HTML 里写路径，不用改这里。 */
  function getPath(obj, path) {
    var cur = obj;
    var parts = String(path).split(".");
    for (var i = 0; i < parts.length; i++) {
      if (cur == null) return null;
      cur = cur[parts[i]];
    }
    return cur;
  }

  /* ------------------------------------------------------------
     颜色
     ------------------------------------------------------------
     全部从 :root 的 CSS 变量读，不在这里写死色值——改主题时这一页
     一行都不用动。chart.js 里有一份同样的函数，但没导出，而它只有
     四行，复制一份比往引擎的公开 API 里加东西干净。
     ------------------------------------------------------------ */

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement)
        .getPropertyValue(name)
        .trim();
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function tone() {
    return {
      accent: cssVar("--accent", "#58a6ff"),
      ok: cssVar("--ok", "#3fb950"),
      warn: cssVar("--warn", "#d29922"),
      warm: cssVar("--warm", "#e3b341"),
      bad: cssVar("--bad", "#f85149"),
      purple: cssVar("--purple", "#bc8cff"),
      cyan: cssVar("--cyan", "#39c5cf"),
      dim: cssVar("--text-dim", "#93a1b3"),
      mute: cssVar("--text-mute", "#6b7a8d")
    };
  }

  function parseHex(hex) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return null;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16)
    ];
  }

  /* 往黑或白混。pct > 0 变亮，< 0 变暗。
     不用 alpha 来做「同族不同档」：环形图的扇区是并排的，两个半透明色
     叠在深色底上会都往底色偏，最后看起来是同一个颜色。 */
  function shade(color, pct) {
    var rgb = parseHex(color);
    if (!rgb) return color;
    var t = Math.max(-1, Math.min(1, pct));
    var target = t >= 0 ? 255 : 0;
    var k = Math.abs(t);
    return "rgb(" + [
      Math.round(rgb[0] + (target - rgb[0]) * k),
      Math.round(rgb[1] + (target - rgb[1]) * k),
      Math.round(rgb[2] + (target - rgb[2]) * k)
    ].join(",") + ")";
  }

  /* 两个色对半混。TLE 的橙就是这么来的：黄和红各一半，
     比直接挑一个橙色更能保证「和 WA、RE 都不同族」。 */
  function mix(a, b, t) {
    var ca = parseHex(a);
    var cb = parseHex(b);
    if (!ca || !cb) return a;
    var k = t == null ? 0.5 : t;
    return "rgb(" + [
      Math.round(ca[0] + (cb[0] - ca[0]) * k),
      Math.round(ca[1] + (cb[1] - ca[1]) * k),
      Math.round(ca[2] + (cb[2] - ca[2]) * k)
    ].join(",") + ")";
  }

  /* ------------------------------------------------------------
     裁决配色
     ------------------------------------------------------------
     七个裁决值塞进一个环，如果七种颜色各挑一个色相，环会变成调色板，
     而且和全站的判题语义色对不上。

     所以按「族」分色，族内用亮度区分：
       AC / OK        绿色族：通过。OK 压暗——它只是游乐场跑通了，
                      没有正确性含义，用同样亮的绿会误导人。
       WA             黄色：答案不对。
       TLE            橙色：超时。黄和红对半混出来的，传统上超时就是橙，
                      它既不完全是「答错」也不完全是「崩了」。
       CE             蓝色：编译没过，代码根本没跑起来。用蓝是因为
                      「编译」在耗时构成那一段就是蓝色，两处刻意对上。
       RE             红色：运行时崩了。
       SE             紫红：判题机自己出错。这个颜色只在维护者眼里有意义，
                      所以它是唯一一个不属于前四族的。
     ------------------------------------------------------------ */

  function verdictColor(v, T) {
    switch (String(v).toUpperCase()) {
      case "AC":  return T.ok;
      case "OK":  return shade(T.ok, -0.34);
      case "WA":  return T.warm;
      case "TLE": return mix(T.warm, T.bad, 0.5);
      case "CE":  return T.accent;
      case "RE":  return T.bad;
      case "SE":  return mix(T.bad, T.purple, 0.55);
      default:    return T.mute;
    }
  }

  /* ============================================================
     三、状态
     ============================================================ */

  var state = {
    snap: null,
    charts: { latency: null, verdict: null, hist: null, days: null },
    sig: null,        // 图表数据的指纹，没变就不重绘
    slowSig: null,    // 表格的指纹
    poll: { timer: 0, on: false, busy: false, fails: 0 }
  };

  /* ============================================================
     四、指标卡
     ============================================================ */

  /* 更新一个数字，并决定要不要闪一下。
     el 里可能带一个 <i> 单位节点（ms / % / /3），那个节点要复用——
     重建它会让文字基线在动画期间抖一下，而且没必要。 */
  function setNum(el, value) {
    var text = String(value == null ? "—" : value);
    if (el.__raw === text) return false;

    var had = el.__raw != null;
    el.__raw = text;

    var unit = el.querySelector("i");
    el.textContent = "";
    el.appendChild(document.createTextNode(text));
    if (unit) el.appendChild(unit);

    /* 首次设值不闪：页面刚打开，每个数字都是「新的」，全闪一遍等于
       什么都没标出来。只有后续刷新改掉的才闪。

       只闪卡片主数值，不闪脚注里那些小字。一次判题会同时改掉十几个数
       （p50/p99、编译均值、排队峰值、三段秒数……），全闪就是满屏闪，
       眼睛反而抓不到「哪个是主要的」。主数值闪一下，脚注安静地变。 */
    if (had && el.classList.contains("ops-stat-value")) flash(el);
    return true;
  }

  function flash(el) {
    /* 减少动态效果时不闪，而且必须**显式跳过**：CSS 里那条规则只是把
       动画时长去了，animationend 就不会触发，is-tick 会永远留在元素上，
       连带 padding / 负 margin 一起留下。 */
    if (window.CMTween && window.CMTween.reducedMotion()) return;

    el.classList.remove("is-tick");
    /* 强制回流。同一帧里「移除再加」浏览器会认为没变化，动画不重播——
       连续两次刷新都改了这个数时就会看起来只闪了第一次。 */
    void el.offsetWidth;
    el.classList.add("is-tick");

    /* animationend 不一定会来：元素可能不在布局里、动画可能被别的
       样式规则关掉、浏览器也可能整帧跳过。所以配一个看门狗定时器，
       到点无论如何把类摘掉——否则 is-tick 会永久留在元素上，连带
       padding 和负 margin 一起，看上去就是这个数被挤歪了。 */
    var guard = 0;
    function done() {
      if (guard) clearTimeout(guard);
      el.removeEventListener("animationend", done);
      el.classList.remove("is-tick");
    }
    guard = setTimeout(done, 900);
    el.addEventListener("animationend", done);
  }

  /* 首屏那些数字的初始值记下来。不记的话第一次轮询会把每个数都当成
     「变了」而全闪一遍——服务端渲染的数字和接口返回的其实一模一样。 */
  function seedStats(snap) {
    var nodes = document.querySelectorAll("[data-k]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var v = getPath(snap, el.dataset.k);
      if (v != null) el.__raw = String(v);
    }
  }

  function applyStats(snap) {
    var nodes = document.querySelectorAll("[data-k]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var v = getPath(snap, el.dataset.k);
      if (v != null) setNum(el, v);
    }
  }

  /* 卡里那两条细横条。宽度是从别的数算出来的，不是快照里的字段，
     所以单独一张表说明「哪个条由哪个数决定」。 */
  var BAR_FN = {
    "rates.ac": function (s) {
      return s.rates.ac || 0;
    },
    "queue.inflight": function (s) {
      var cap = s.queue.capacity || 1;
      return 100 * s.queue.inflight / cap;
    }
  };

  function applyBars(snap) {
    var nodes = document.querySelectorAll("[data-bar]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var fn = BAR_FN[el.dataset.bar];
      if (!fn) continue;
      el.style.width = Math.max(0, Math.min(100, fn(snap))) + "%";
    }
  }

  /* ============================================================
     五、三段占比条
     ============================================================ */

  function applyPhase(snap) {
    var bar = $("ops-phase");
    if (!bar) return;
    var p = snap.phases || {};

    /* 先量总宽，再改子元素宽度。反过来量到的是过渡动画中间的值——
       那会让 is-tiny 的判断每隔几次刷新就抖一下。 */
    var total = bar.clientWidth || 0;

    ["compile", "run", "other"].forEach(function (key) {
      var seg = bar.querySelector(".ph-" + key);
      if (!seg) return;
      var pct = Number(p[key + "_pct"]) || 0;

      seg.style.width = pct + "%";
      seg.title = PH_LABEL[key] + " " + pct + "%";
      var label = seg.querySelector("i");
      if (label) label.textContent = pct + "%";
      bar.dataset[key] = pct;

      /* 要不要藏文字，看「这一段实际能拿到多少像素」，不看百分比。
         同一个 8%，1100px 宽是 88px（放得下「建目录与比对」），
         400px 宽只有 32px（放不下，会溢出成「建…」）。
         总宽量不到时（隐藏 / 还没布局）保持原状，别乱改。 */
      if (total > 0) {
        seg.classList.toggle("is-tiny", total * pct / 100 < PH_MIN_PX);
      }
    });
  }

  /* ------------------------------------------------------------
     超时归因条。和阶段条同一个套路，但有一个区别：段数会变。
     某一类这次是 0 就不画（服务端首屏也是这么画的），所以刷新时
     要做的是「有则改宽、无则摘掉」——只改宽度的话，0% 的段会留
     一个 0 宽但仍在 flex 里的空壳，段间隙把剩下几段挤歪。
     ------------------------------------------------------------ */
  function applyTle(snap) {
    var bar = $("ops-tle");
    var t = snap && snap.tle;
    if (!bar || !t) return;

    var total = bar.clientWidth || 0;
    var names = t.labels || {};

    ["cpu", "wall", "wait", "unknown"].forEach(function (k) {
      var seg = bar.querySelector('[data-tle="' + k + '"]');
      var pct = Number(t[k + "_pct"]) || 0;

      if (!pct) {
        if (seg) seg.parentNode.removeChild(seg);
        return;
      }
      if (!seg) {
        /* 新出现的归因（比如第一次出现 wall 超时）。文字从快照里的
           labels 取，不在这里写死四份中文名——否则 verdicts.py 改了
           措辞，静态渲染和刷新后会是两套说法。 */
        seg = document.createElement("span");
        seg.className = "tle tle-" + k;
        seg.dataset.tle = k;
        seg.appendChild(document.createElement("b"));
        seg.appendChild(document.createElement("i"));
        bar.appendChild(seg);
      }
      seg.style.width = pct + "%";
      seg.querySelector("b").textContent = names[k] || k;
      seg.querySelector("i").textContent = pct + "%";
      seg.title = (names[k] || k) + " " + pct + "%";

      /* 太窄就把字藏掉。阈值比阶段条（62px）小一点：这里的字是
         「算得慢」这种四字词，比「建目录与比对」短。 */
      if (total > 0) {
        seg.classList.toggle("is-tiny", total * pct / 100 < 48);
      }
    });
  }

  /* ============================================================
     六、图表
     ============================================================ */

  /* 画布高度 = 卡片正文高度 - 图例占的高。
     不写死像素是因为窄屏下 .ops-card-body 会从 208 变 176，
     写死就会在窄屏上把图例挤出卡片。 */
  function bodyHeight(host, withLegend) {
    var h = host ? host.clientHeight : 0;
    return Math.max(120, (h || 208) - (withLegend ? LEGEND_H : 0));
  }

  function latencyConfig(snap) {
    var T = tone();
    var host = $("chart-latency");
    return {
      type: "line",
      labels: snap.series.labels,
      /* 总耗时用中性灰：它是「合计」，不是某一项。用彩色会让人以为
         它是第三个并列的分项，而它其实是另外两条加上没画出来的第三块。 */
      series: [
        { name: "总耗时", data: snap.series.total, color: T.dim, dashed: true },
        { name: "编译", data: snap.series.compile, color: T.accent },
        { name: "运行", data: snap.series.run, color: T.ok }
      ],
      height: bodyHeight(host, true),
      xRotate: 0,
      /* 60 个点不做降采样。降采样（每 N 个取平均）会把尖峰抹平，而这
         张图唯一的目的就是看尖峰——TLE 在 60 个点里只有一两根。 */
      emptyText: "还没有判题记录",
      duration: CHART_DUR
    };
  }

  function verdictConfig(snap) {
    var T = tone();
    var host = $("chart-verdict");
    var labels = snap.verdicts.labels || [];
    return {
      type: "donut",
      labels: labels,
      series: [{ name: "裁决", data: snap.verdicts.counts || [] }],
      /* 逐扇区配色走 colors，不是 series[0].color——
         环形图只有一个系列，按系列上色的话整个环是一个颜色。 */
      colors: labels.map(function (v) { return verdictColor(v, T); }),
      height: bodyHeight(host, true),
      donutInner: 0.58,
      emptyText: "还没有判题结果",
      duration: CHART_DUR
    };
  }

  function histConfig(snap) {
    var T = tone();
    var host = $("chart-hist");
    return {
      type: "bar",
      labels: snap.histogram.labels,
      series: [{ name: "判题次数", data: snap.histogram.counts, color: T.accent }],
      height: bodyHeight(host, false),
      showLegend: false,
      /* 桶名是 "0–50" "200–300" 这种，一段比一段长。横排会被引擎自动
         抽稀（它按「标签宽 × 数量 > 可用宽」决定步长），抽稀对直方图
         是可以接受的——要看的是形状，不是每一个刻度。 */
      emptyText: "还没有样本",
      duration: CHART_DUR
    };
  }

  function daysConfig(snap) {
    var T = tone();
    var host = $("chart-days");
    var days = snap.days || [];
    return {
      type: "bar",
      /* 后端给的是 2026-10-14，轴上只留 10-14。
         年份在这里没有信息量——环形缓冲只有两天的窗口，图上是 14 天，
         但跨年的情况不存在。 */
      labels: days.map(function (d) { return String(d.date).slice(5); }),
      series: [{ name: "判题次数", data: days.map(function (d) { return d.count; }), color: T.purple }],
      height: bodyHeight(host, false),
      showLegend: false,
      emptyText: "还没有按天的记录",
      duration: CHART_DUR
    };
  }

  /* 刷新时用 0 时长：数据自己变，不该每次轮询都重播一遍入场动画。
     那会让面板看起来一直在「加载」，而实际上它早就稳定了。 */
  function quiet(cfg) {
    var out = {};
    for (var k in cfg) {
      if (Object.prototype.hasOwnProperty.call(cfg, k)) out[k] = cfg[k];
    }
    out.duration = 0;
    return out;
  }

  function mountChart(key, host, cfg) {
    if (!host || !window.CMChart) return null;
    if (state.charts[key]) {
      state.charts[key].update(quiet(cfg));
    } else {
      state.charts[key] = window.CMChart.mount(host, cfg);
    }
    return state.charts[key];
  }

  function applyCharts(snap) {
    mountChart("latency", $("chart-latency"), latencyConfig(snap));
    mountChart("verdict", $("chart-verdict"), verdictConfig(snap));
    mountChart("hist", $("chart-hist"), histConfig(snap));
    mountChart("days", $("chart-days"), daysConfig(snap));
  }

  /* 图表数据的指纹。没变就整段跳过：
     连续两次轮询数据一样时（比如没人交题），重绘不会带来任何信息，
     但会让 tooltip 被意外关掉、让图例的隐藏状态被 reset。 */
  function signature(snap) {
    return JSON.stringify([
      snap.window.runs,
      snap.counts.total,
      snap.series.labels,
      snap.series.total,
      snap.series.compile,
      snap.series.run,
      snap.histogram.counts,
      snap.verdicts.labels,
      snap.verdicts.counts,
      snap.days
    ]);
  }

  /* ============================================================
     七、最慢的几次
     ============================================================ */

  function caseBars(caseMs) {
    if (!caseMs || !caseMs.length) return '<span class="ops-dim">—</span>';
    var peak = 0;
    for (var i = 0; i < caseMs.length; i++) {
      if (caseMs[i] > peak) peak = caseMs[i];
    }
    if (!peak) peak = 1;

    var out = [];
    for (var j = 0; j < caseMs.length; j++) {
      /* 4px 是底高：0ms 的用例也要看得见一个点，否则那一格会「缺一块」。
         归一化只在一行之内，跨行不比——不同题的用例不是一个量级。 */
      var h = Math.round(4 + 14 * (caseMs[j] / peak));
      out.push('<i style="height: ' + h + 'px" title="用例 ' + (j + 1) +
        " · " + esc(caseMs[j]) + ' ms"></i>');
    }
    return out.join("");
  }

  function renderSlow(rows) {
    var host = $("ops-slow-host");
    if (!host) return;

    if (!rows || !rows.length) {
      host.innerHTML = EMPTY_HTML;
      return;
    }

    var html = [
      '<div class="ops-table-wrap"><table class="ops-table">',
      "<thead><tr>",
      "<th>时间</th><th>题目</th><th>裁决</th>",
      '<th class="num">总耗时</th><th class="num">编译</th><th class="num">运行</th>',
      '<th class="num" title="排队 + 起进程 + 读输出 + 逐行比对">其余</th>',
      "<th>逐用例</th>",
      "</tr></thead><tbody>"
    ];

    rows.forEach(function (r) {
      /* 所有插进 DOM 的文本都过一遍 esc。qid 来自题目数据、verdict 来自
         judge.py 的常量，严格说都安全——但「安全」是这两处的实现细节，
         明天有人往 qid 里放用户输入，这里就会变成一个注入点。 */
      html.push("<tr>");
      html.push('<td class="mono">' + esc(r.label) + "</td>");
      html.push('<td class="mono">' + esc(r.qid) + "</td>");
      html.push('<td><span class="ops-v is-' +
        esc(String(r.verdict).toLowerCase()) + '">' + esc(r.verdict) + "</span></td>");
      html.push('<td class="num">' + esc(r.total_ms) + "</td>");
      html.push('<td class="num">' + esc(r.compile_ms) + "</td>");
      html.push('<td class="num">' + esc(r.run_ms) + "</td>");
      /* 其余 = 总 - 编译 - 运行。字段是服务端算好的，这里留一个兜底：
         万一连的是旧接口（没这个键），至少别在格子里写出 undefined。 */
      html.push('<td class="num">' + esc(r.other_ms != null
        ? r.other_ms
        : Math.max(0, r.total_ms - r.compile_ms - r.run_ms)) + "</td>");
      html.push('<td class="ops-cases">' + caseBars(r.case_ms) + "</td>");
      html.push("</tr>");
    });

    html.push("</tbody></table></div>");
    host.innerHTML = html.join("");
  }

  function slowSig(snap) {
    return JSON.stringify((snap.slowest || []).map(function (r) {
      return [r.ts, r.qid, r.verdict, r.total_ms, r.compile_ms, r.run_ms, r.case_ms];
    }));
  }

  /* ============================================================
     八、健康结论与数据时刻
     ============================================================ */

  function applyHealth(health) {
    var badge = $("ops-health");
    var line = $("ops-healthline");
    if (!health) return;

    if (badge) {
      badge.className = "ops-health is-" + health.level;
      badge.title = health.text;

      /* 只换文字，保留那个色点。色点上挂着呼吸动画，重建它会让
         「不正常」时的一次呼吸从零开始——看起来像卡了一下。 */
      var dot = badge.querySelector("i");
      badge.textContent = "";
      if (dot) badge.appendChild(dot);
      badge.appendChild(document.createTextNode(health.label));
    }

    if (line && line.textContent !== health.text) {
      /* role="status" 的区域，内容变了读屏器会念。
         文案没变就不动它，否则每次轮询都念一遍同样的话。 */
      line.textContent = health.text;
    }
  }

  function applyStamp(snap) {
    var el = $("ops-stamp");
    if (!el) return;
    /* 用后端的时间戳而不是 new Date()：轮询有往返延迟，而且浏览器和
       服务器不在一台机器上时（后面真部署了）本地钟可能偏。这一格要
       回答的问题是「这屏数据是服务器几点的」。 */
    var d = new Date(snap.now * 1000);
    el.textContent = pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
    el.title = snap.saved_label || el.textContent;
    el.classList.remove("is-bad");
  }

  function stampError(msg) {
    var el = $("ops-stamp");
    if (!el) return;
    el.textContent = "刷新失败";
    el.title = msg || "接口没有返回可用的快照";
    el.classList.add("is-bad");
  }

  /* ============================================================
     九、应用一份快照
     ============================================================ */

  function apply(snap) {
    if (!snap) return;
    state.snap = snap;

    applyStamp(snap);
    applyHealth(snap.health);
    applyStats(snap);
    applyBars(snap);
    applyPhase(snap);
    applyTle(snap);

    /* 图表和表格各自比指纹，变了才重画。 */
    var sig = signature(snap);
    if (sig !== state.sig) {
      state.sig = sig;
      applyCharts(snap);
    }

    var ssig = slowSig(snap);
    if (ssig !== state.slowSig) {
      state.slowSig = ssig;
      renderSlow(snap.slowest);
    }
  }

  /* ============================================================
     十、轮询
     ============================================================ */

  function setPollButton(on) {
    var btn = $("ops-refresh");
    if (!btn) return;
    /* 用 aria-pressed 表达开关态，不是加一个 .is-on 类。
       属性是事实，类名是样式——以后换成 tab 结构或者加个开关图标，
       这里的状态判断都不用改。 */
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "每 5 秒拉一次 /api/ops（点击停止）" : "点击开始每 5 秒自动刷新";
  }

  function startPoll() {
    if (state.poll.on) return;
    state.poll.on = true;
    state.poll.fails = 0;
    setPollButton(true);
    pollOnce();
    state.poll.timer = window.setInterval(pollOnce, POLL_MS);
  }

  function stopPoll(reason) {
    if (state.poll.timer) {
      window.clearInterval(state.poll.timer);
      state.poll.timer = 0;
    }
    state.poll.on = false;
    setPollButton(false);
    if (reason) stampError(reason);
  }

  function togglePoll() {
    if (state.poll.on) stopPoll("");
    else startPoll();
  }

  function pollOnce() {
    /* 上一次还没回来就不发下一次。服务端卡住时（比如判题机把 GIL 占满），
       不等回来就接着发会堆出一串请求，面板最后显示的是最早那次的结果。 */
    if (state.poll.busy) return;

    /* 标签页在后台不刷。这一页的开销不大，但「切到别的窗口去写代码，
       回来发现面板是新的」比「后台每秒烧一次 CPU」重要得多。 */
    if (document.hidden) return;

    state.poll.busy = true;

    /* 带 no-store：这个接口永远该是最新的，走缓存等于看历史。
       credentials 用 same-origin：/api/ops 要登录，会话过期时服务端会
       302 到登录页，fetch 跟过去拿到的是 HTML，r.json() 解析失败正好
       落进下面的 catch —— 不需要额外判断 status。 */
    fetch("/api/ops", {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        if (!j || !j.ok || !j.snapshot) {
          throw new Error((j && j.error) || "接口返回异常");
        }
        state.poll.fails = 0;
        apply(j.snapshot);
      })
      .catch(function (err) {
        state.poll.fails++;
        console.warn("[ops] 刷新失败（第 " + state.poll.fails + " 次）：", err);
        if (state.poll.fails >= FAIL_LIMIT) {
          stopPoll("连续 " + state.poll.fails + " 次刷新失败，已停止自动刷新");
        }
      })
      .then(function () {
        state.poll.busy = false;
      });
  }

  /* ============================================================
     十一、启动
     ============================================================ */

  function bind() {
    var btn = $("ops-refresh");
    if (btn) btn.addEventListener("click", togglePoll);

    /* 后台切回来时立刻补一次，不等下一个 5 秒。
       否则用户切回来盯着一个旧数字看最多 5 秒，会以为面板坏了。 */
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && state.poll.on) pollOnce();
    });

    /* 页面要走了就停掉定时器。单页应用里这是必须的，这里是整页刷新——
       浏览器本来也会清理，但显式停掉不花钱，而且万一以后改成局部导航
       （或者被 bfcache 存下来）时不会留一个孤儿 interval。 */
    window.addEventListener("pagehide", function () {
      if (state.poll.timer) {
        window.clearInterval(state.poll.timer);
        state.poll.timer = 0;
      }
    });
    window.addEventListener("beforeunload", function () {
      if (state.poll.timer) {
        window.clearInterval(state.poll.timer);
        state.poll.timer = 0;
      }
    });
  }

  function init() {
    if (!window.CMChart) {
      /* chart.js 没加载成功。这时候不该静默失败——页面上的数字还是对的，
         只是没有图，明确说出来比让用户以为「本来就没有图」好。 */
      console.error("[ops] 图表引擎没加载，跳过全部图表");
    }

    var snap = readJSON("ops-data");
    if (!snap) {
      stampError("页面里的快照数据缺失，请刷新");
      bind();
      return;
    }

    seedStats(snap);
    apply(snap);
    bind();

    /* 首屏就是「已开启」还是「关着」？
       关着。自动轮询是有代价的（每 5 秒一次请求），不该在用户没要求时
       默认打开；但首屏那份快照已经是最新的，用户点一下才开始持续更新。 */
    setPollButton(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
