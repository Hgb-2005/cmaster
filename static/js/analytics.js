/* ============================================================
   CMaster —— 学习分析页
   ------------------------------------------------------------
   这一层只做一件事：把后端算好的数字，翻译成图表配置。

   刻意不在后端决定颜色和图形类型。原因很实际：改一次配色不用
   重启 Flask，也不用担心 JSON 里存了一堆 CSS 变量名——
   后端如果输出 "#58a6ff"，将来做浅色主题时这里就硬了。

   数据通过页面里那个 <script type="application/json"> 传进来，
   不走 fetch。它是首屏就要画的东西，多一次请求就多一次白屏。
   ============================================================ */

(function (global) {
  "use strict";

  var CMChart = global.CMChart;

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement)
        .getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function pct(v) {
    return Math.round(v) + "%";
  }

  function readData() {
    var node = document.getElementById("analytics-data");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      return null;
    }
  }

  /* ------------------------------------------------------------
     五张图
     ------------------------------------------------------------ */

  function renderTrend(d) {
    var host = document.getElementById("chart-trend");
    if (!host) return;

    var total = d.trend.counts.reduce(function (a, b) { return a + b; }, 0);

    CMChart.mount(host, {
      type: "area",
      labels: d.trend.labels,
      series: [{
        name: "学习动作",
        data: d.trend.counts,
        color: cssVar("--accent", "#58a6ff")
      }],
      height: 210,
      /* 全零的 30 天不该画成一条贴底平线再配个 y 轴 0–1：
         那样看起来像「有数据但都是 0」。宁可就画一条平线，
         y 轴也保持 0–1，配合下面的空态提示。 */
      yMax: total ? undefined : 1,
      maxYTicks: 4,
      showLegend: false,
      smooth: true,
      format: function (v) {
        return v >= 1 ? String(Math.round(v)) : "0";
      },
      tooltipFormat: function (v) {
        return v === 0 ? "没有学习" : v + " 次";
      }
    });
  }

  function renderRadar(d) {
    var host = document.getElementById("chart-radar");
    if (!host) return;

    CMChart.mount(host, {
      type: "radar",
      labels: d.radar.labels,
      series: [{
        name: "完成度",
        data: d.radar.scores,
        color: cssVar("--cyan", "#39c5cf")
      }],
      height: 258,
      yMax: 100,
      radarRings: 4,
      showLegend: false,
      format: function (v) { return Math.round(v) + "%"; },
      tooltipFormat: pct
    });
  }

  function renderStatus(d) {
    var host = document.getElementById("chart-status");
    if (!host) return;

    var data = d.status.series[0].data;

    /* 环形图的三个扇区语义固定：绿=已通过、黄=待攻克、灰=未开始。
       不用默认调色板，因为默认顺序是蓝绿黄——蓝给「已通过」读起来
       不像成功，而「未开始」拿到黄色会显得在催人。 */
    var colors = [
      cssVar("--ok", "#3fb950"),
      cssVar("--warn", "#d29922"),
      cssVar("--border", "#263041")
    ];

    /* 未开始的扇区不要用纯背景色：深色背景下它会和卡片底糊成一体，
       看起来像一个缺口而不是一段数据。往上提一档到 --bg-elev-2。 */
    colors[2] = cssVar("--text-mute", "#6b7a8d");

    CMChart.mount(host, {
      type: "donut",
      labels: d.status.labels,
      series: [{
        name: "题目",
        data: data
      }],
      colors: colors,
      height: 222,
      donutInner: 0.62,
      showLegend: true,
      format: function (v) { return String(Math.round(v)); },
      tooltipFormat: function (v) { return v + " 题"; }
    });
  }

  function renderChapters(d) {
    var host = document.getElementById("chart-chapters");
    if (!host) return;

    CMChart.mount(host, {
      type: "bar",
      labels: d.chapters.labels,
      series: [{
        name: "完成度",
        data: d.chapters.percent,
        color: cssVar("--accent", "#58a6ff")
      }],
      height: 246,
      yMax: 100,
      maxYTicks: 5,
      /* 12 个章节标题横排必然挤，转 38 度是全显示的代价最小方案
         （抽样会丢掉一半章节，而那正是这张图要表达的东西） */
      xRotate: 38,
      showLegend: false,
      format: function (v) { return Math.round(v) + ""; },
      tooltipFormat: function (v) { return v + "% 完成"; },
      onPointClick: function (pi) {
        // 章节 id 就是顺序号的 1..12，和数组下标差 1
        global.location.href = "/chapter/" + (pi + 1);
      }
    });
  }

  function renderDifficulty(d) {
    var host = document.getElementById("chart-difficulty");
    if (!host) return;

    var series = d.difficulty.series.map(function (s, i) {
      return {
        name: s.name,
        data: s.data,
        color: i === 0
          ? cssVar("--ok", "#3fb950")
          : cssVar("--text-mute", "#6b7a8d")
      };
    });

    CMChart.mount(host, {
      type: "bar",
      labels: d.difficulty.labels,
      series: series,
      height: 196,
      yFrom0: true,
      maxYTicks: 4,
      showLegend: true,
      format: function (v) { return String(Math.round(v)); },
      tooltipFormat: function (v) { return v + " 题"; }
    });
  }

  /* ------------------------------------------------------------
     打印
     ------------------------------------------------------------ */

  /* canvas 上的颜色是「画上去的位图」，不是 CSS 属性。
     打印时把 :root 的变量改成浅色，屏幕上的元素会立刻变白，
     但 canvas 里已经烤进去的浅灰轴线和白字纹丝不动——白纸配白线，
     打出来就是一张空图。

     所以必须监听媒体切换事件，把每张图重画一遍。重画时 cssVar()
     重新读 computedStyle，才能拿到打印用的那一套浅色值。

     用 matchMedia 而不是 beforeprint：前者在 emulateMediaType 和
     真实打印两条路径上都会触发，后者只有真打印才派发。 */
  function watchPrint() {
    if (!global.matchMedia) return;
    var mq = global.matchMedia("print");

    function repaint() {
      var hosts = document.querySelectorAll(".chart-card-body[id^='chart-']");
      Array.prototype.forEach.call(hosts, function (host) {
        var c = host._cmChart;
        /* resize() 只做 _layout + _render，不重跑入场动画。
           _anim 已经是 1，所以重画出来就是最终形态，不会又长一遍。 */
        if (c && !c._destroyed && c.resize) c.resize();
      });
    }

    if (mq.addEventListener) mq.addEventListener("change", repaint);
    else if (mq.addListener) mq.addListener(repaint);   // 老 Safari
  }

  /* ------------------------------------------------------------
     启动
     ------------------------------------------------------------ */

  function boot() {
    if (!CMChart) {
      /* 图表引擎没加载出来（静态资源被拦、或者脚本顺序错了）。
         这时候不能默默留白，得告诉人这里本来有什么。 */
      document.querySelectorAll(".chart-card-body[id^='chart-']").forEach(function (el) {
        el.innerHTML = '<p class="an-empty">图表组件未加载，刷新试试。</p>';
      });
      return;
    }

    var d = readData();
    if (!d) return;

    renderTrend(d);
    renderRadar(d);
    renderStatus(d);
    renderChapters(d);
    renderDifficulty(d);

    watchPrint();

    /* 页面转场（ux.js 里的 cm-leaving）会在导航时让旧页面淡出。
       淡出期间图表不该继续跑动画，它们动的是已经看不见的 canvas。 */
    document.addEventListener("cm:leaving", function () {
      if (global.CMTween) global.CMTween.clear();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  global.CMAnalytics = {
    boot: boot,
    readData: readData
  };
})(window);
