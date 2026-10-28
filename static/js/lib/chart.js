/* ============================================================
   CMaster —— Canvas 图表引擎
   ------------------------------------------------------------
   为什么自己写，而不用 Chart.js / ECharts：

   1. 这个站点的原则是「零外部依赖、断网能用」。引一个 200KB 的
      图表库，只为了画四条折线和一个环，不划算，也违背项目定位。
   2. 需要的图很固定：折线、柱状、雷达、环形。四种，不多。
      通用库为了覆盖几十种图，配置项复杂得像另一门语言。
   3. 配色要跟着 CSS 变量走（深色主题 + 未来要做的多主题）。
      外部库换主题要重新 hydrate，这里读一次 getComputedStyle 就够。

   ------------------------------------------------------------
   设计取舍：

   · 图例和 tooltip 是 DOM，不是 canvas 画的。理由：文字要可选中、
     可访问、可被 CSS 主题接管，而且不用每次重绘。canvas 只画图形。
   · 动画统一走 CMTween。柱子生长、折线描边、环形扫过，都是同一个
     对象在不同属性上跑。
   · 命中检测用「最近点」而不是「多边形包含」。图表是给人看的，
     鼠标指向哪里是想看哪个数据，容差大一点比精确更重要。
   ============================================================ */

(function (global) {
  "use strict";

  var CMTween = global.CMTween;

  /* ============================================================
     一、小工具
     ============================================================ */

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function clamp(v, lo, hi) {
    return v < lo ? lo : (v > hi ? hi : v);
  }

  /* 从 CSS 变量取色。canvas 的 fillStyle 不认 var(--x)，
     必须先解析成真实颜色值。 */
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

  /* 半透明版本。只处理 #rgb / #rrggbb / rgb()，够用了。 */
  function withAlpha(color, alpha) {
    if (!color) return "rgba(255,255,255," + alpha + ")";
    var c = CMTween && CMTween.parseColor ? CMTween.parseColor(color) : null;
    if (c) {
      return "rgba(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," +
        Math.round(c[2]) + "," + alpha + ")";
    }
    return color;
  }

  /* 把数字格式化成人看得懂的样子：1200 → 1.2k。
     图表轴标签宽度有限，几千的数字横着放不下。 */
  function shortNum(v) {
    var abs = Math.abs(v);
    if (abs >= 1000000) return trimZero(v / 1000000) + "M";
    if (abs >= 1000) return trimZero(v / 1000) + "k";
    if (abs >= 1) return trimZero(v);
    if (abs === 0) return "0";
    return trimZero(v, 2);
  }

  function trimZero(v, digits) {
    var d = digits == null ? 1 : digits;
    var s = v.toFixed(d);
    // "3.0" → "3"，"0.50" → "0.5"
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s;
  }

  /* ------------------------------------------------------------
     刻度计算。
     ------------------------------------------------------------
     「nice numbers」算法：把原始范围扩到「整」的边界，
     让刻度落在 1/2/5 × 10^n 上。

     不做这个的话，y 轴会出现 0.0 / 13.37 / 26.74 这种刻度，
     一眼看过去不知道量级，图就白画了。
     ------------------------------------------------------------ */

  function niceNum(range, round) {
    if (range <= 0) return 1;
    var exp = Math.floor(Math.log(range) / Math.LN10);
    var f = range / Math.pow(10, exp);
    var nf;
    if (round) {
      if (f < 1.5) nf = 1;
      else if (f < 3) nf = 2;
      else if (f < 7) nf = 5;
      else nf = 10;
    } else {
      if (f <= 1) nf = 1;
      else if (f <= 2) nf = 2;
      else if (f <= 5) nf = 5;
      else nf = 10;
    }
    return nf * Math.pow(10, exp);
  }

  function niceScale(min, max, maxTicks) {
    if (!isNum(min) || !isNum(max)) { min = 0; max = 1; }
    if (min === max) {
      // 全是同一个值（比如所有用例都 0ms）：给个可视的上下浮动，
      // 否则线会贴在边框上，看着像没画
      if (min === 0) { max = 1; }
      else { min = min * 0.9; max = max * 1.1; }
    }
    if (min > max) { var t = min; min = max; max = t; }

    var ticks = Math.max(2, maxTicks || 5);
    var range = niceNum(max - min, false);
    var step = niceNum(range / (ticks - 1), true);
    var niceMin = Math.floor(min / step) * step;
    var niceMax = Math.ceil(max / step) * step;

    // 步长太细会产生几十条网格线，钳一下
    var out = [];
    var guard = 0;
    for (var v = niceMin; v <= niceMax + step * 0.5 && guard < 40; v += step) {
      // 累加会攒浮点误差，round 到 step 的精度上
      out.push(Math.round(v / step) * step);
      guard++;
    }

    return { min: niceMin, max: niceMax, step: step, ticks: out };
  }

  /* ============================================================
     二、Canvas 尺寸与 DPR
     ------------------------------------------------------------
     高分屏上 canvas 必须放大 backing store，否则线是糊的。
     做法：CSS 尺寸按逻辑像素，画布像素乘 dpr，然后 ctx.scale(dpr,dpr)。
     之后所有绘图代码都只用逻辑像素，不用关心 dpr。
     ============================================================ */

  function fitCanvas(canvas, cssW, cssH) {
    var dpr = global.devicePixelRatio || 1;
    // dpr 上限 2：4K 屏上 dpr=3 会让画布像素翻九倍，
    // 而图表这种线框图在 2x 之后肉眼已经分辨不出差别
    dpr = Math.min(dpr, 2);

    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);

    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  /* ============================================================
     三、Chart 实例
     ============================================================ */

  function Chart(host, config) {
    this.host = host;
    this.cfg = normalizeConfig(config);
    this.hover = null;         // {si: 系列下标, pi: 数据点下标}
    this._raf = 0;
    this._destroyed = false;
    this._anim = 0;            // 0..1，入场进度

    /* 环形图专用的扇区隐藏开关。
       折线/柱状图靠 series[i].hidden 就够了，因为「一条线」就是一个
       可开关的单位；环形图不是——它只有一个系列，可开关的单位是
       「一个扇区」。两套状态不能混用，所以单独开一个数组，
       下标对齐 series[0].data 的下标。 */
    this._sliceHidden = [];

    this._buildDom();
    this._layout();
    this._bindEvents();

    /* 入场动画：不是「画出来再淡入」，而是数据从 0 长到目标值。
       后者更像「图表在算」，前者只是「图表在出现」。
       减少动态效果时直接置 1。 */
    if (CMTween && !CMTween.reducedMotion()) {
      var self = this;
      this._tween = CMTween.value(0, 1, this.cfg.duration, function (v) {
        self._anim = v;
        self._render();
      });
    } else {
      this._anim = 1;
      this._render();
    }
  }

  function normalizeConfig(c) {
    c = c || {};
    var type = c.type || "line";
    if (["line", "area", "bar", "radar", "donut"].indexOf(type) < 0) {
      type = "line";
    }

    var series = (c.series || []).map(function (s, i) {
      return {
        name: s.name || ("系列 " + (i + 1)),
        data: (s.data || []).map(function (v) {
          return isNum(v) ? v : 0;
        }),
        color: s.color || null,
        fill: s.fill !== false,
        dashed: !!s.dashed,
        hidden: !!s.hidden
      };
    });

    return {
      type: type,
      labels: c.labels || [],
      series: series,
      /* 逐元素配色。环形图每个扇区一个颜色，靠它指定；
         折线图一般用 series[i].color，不用这个。 */
      colors: c.colors || null,
      height: c.height || 200,
      /* 留白。右侧默认大一点：最后一个 x 标签常常要半个标签宽度。 */
      pad: c.pad || { top: 14, right: 14, bottom: 26, left: 40 },
      yFrom0: c.yFrom0 !== false,
      yMax: c.yMax,
      yMin: c.yMin,
      smooth: c.smooth !== false,
      showGrid: c.showGrid !== false,
      showLegend: c.showLegend !== false,
      showXAxis: c.showXAxis !== false,
      showYAxis: c.showYAxis !== false,
      maxYTicks: c.maxYTicks || 5,
      /* X 轴标签旋转角度（度，正数表示逆时针抬起）。
         标签密集时横排放不下，抽样又会漏信息，转 35–45 度是最常见解法。
         底边留白会根据最长标签自动算，见 _layout。 */
      xRotate: c.xRotate || 0,
      /* 值格式化。tooltip 和轴标签共用。 */
      format: c.format || shortNum,
      tooltipFormat: c.tooltipFormat || c.format || shortNum,
      duration: c.duration == null ? 720 : c.duration,
      /* 雷达图的网格层数 */
      radarRings: c.radarRings || 4,
      /* 环形图内径比例 */
      donutInner: c.donutInner == null ? 0.62 : c.donutInner,
      emptyText: c.emptyText || "暂无数据",
      onPointClick: c.onPointClick || null
    };
  }

  /* ---------------------------------------------------------- DOM */

  Chart.prototype._buildDom = function () {
    var cfg = this.cfg;

    /* 如果 host 已经是 .cm-chart 就直接用，否则包一层。
       这样调用方可以自己写结构，也可以什么都不管。 */
    if (!this.host.classList.contains("cm-chart")) {
      var wrap = document.createElement("div");
      wrap.className = "cm-chart";
      this.host.appendChild(wrap);
      this.host = wrap;
    }

    this.canvas = document.createElement("canvas");
    this.canvas.className = "cm-chart-canvas";
    this.canvas.setAttribute("role", "img");
    this.host.appendChild(this.canvas);

    if (cfg.showLegend && cfg.type !== "radar") {
      this.legendEl = document.createElement("div");
      this.legendEl.className = "cm-chart-legend";
      this.host.appendChild(this.legendEl);
    }

    this.tipEl = document.createElement("div");
    this.tipEl.className = "cm-chart-tip";
    this.tipEl.setAttribute("aria-hidden", "true");
    this.host.appendChild(this.tipEl);

    this.ctx = this.canvas.getContext("2d");
  };

  /* 图例：点一下可以隐藏/显示该系列。
     这不是花哨功能——三条线叠在一起时，想单独看一条是刚需。

     环形图要特殊处理：它只有一个 series，可开关的单位是「扇区」。
     如果按 series 渲染，图例上只有一行「题目」，点一下整个环消失，
     等于没有图例。所以这里按 labels 逐扇区生成。 */
  Chart.prototype._renderLegend = function () {
    if (!this.legendEl) return;
    var self = this;
    this.legendEl.innerHTML = "";

    if (this.cfg.type === "donut") {
      var data = (this.cfg.series[0] || {}).data || [];
      this.cfg.labels.forEach(function (label, i) {
        var zero = !(data[i] > 0);

        var item = document.createElement("button");
        item.type = "button";
        item.className = "cm-chart-legend-item" +
          (self._sliceHidden[i] ? " is-off" : "") +
          (zero ? " is-zero" : "");
        item.dataset.si = i;

        var dot = document.createElement("i");
        dot.className = "cm-chart-dot";
        /* 用扇区自己的颜色，而不是 _seriesColor(0)——
           否则三个圆点全是一个颜色，图例跟环对不上。 */
        dot.style.background = self._seriesColor(i);

        var txt = document.createElement("span");
        txt.textContent = label;

        item.appendChild(dot);
        item.appendChild(txt);

        if (zero) {
          /* 值为 0 的扇区在环上占不到角度，点它什么也不会发生。
             与其让用户以为是坏的，不如直接禁用并说明原因。 */
          item.disabled = true;
          item.title = "这一档没有题目";
        } else {
          item.title = self._sliceHidden[i] ? "点击显示" : "点击隐藏";
          item.addEventListener("click", function () {
            self._sliceHidden[i] = !self._sliceHidden[i];
            /* 隐藏扇区后剩下的扇区要重新铺满整圈，所以总占比变了，
               入场动画不能重播（数据没变，重播会让环突然缩回去）。
               直接 _render 一次即可。 */
            self._renderLegend();
            self._render();
          });
        }

        self.legendEl.appendChild(item);
      });
      return;
    }

    this.cfg.series.forEach(function (s, i) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "cm-chart-legend-item" + (s.hidden ? " is-off" : "");
      item.dataset.si = i;

      var dot = document.createElement("i");
      dot.className = "cm-chart-dot";
      dot.style.background = self._seriesColor(i);

      var label = document.createElement("span");
      label.textContent = s.name;

      item.appendChild(dot);
      item.appendChild(label);
      item.title = s.hidden ? "点击显示" : "点击隐藏";

      item.addEventListener("click", function () {
        s.hidden = !s.hidden;
        self._renderLegend();
        self._render();
      });

      self.legendEl.appendChild(item);
    });
  };

  Chart.prototype._seriesColor = function (i) {
    /* 三种来源，优先级从高到低：
       1. series[i].color —— 折线/柱状里「第 i 条线」自己的颜色
       2. cfg.colors[i]  —— 环形图用：它的「第 i 个可视元素」是一个扇区，
          而不是一个系列（series 只有一个）。没有这条，环形图所有扇区
          会共用一个颜色，因为 series[0].color 一存在就被无差别返回。
       3. 默认调色板 */
    var s = this.cfg.series[i];
    if (s && s.color) return s.color;
    if (this.cfg.colors && this.cfg.colors[i]) return this.cfg.colors[i];

    /* 默认调色板。顺序是按「相邻区分度」排的：
       蓝 → 绿 → 黄 → 紫 → 青，色相间隔足够大，
       色盲也能靠亮度区分前两个。 */
    var palette = [
      cssVar("--accent", "#58a6ff"),
      cssVar("--ok", "#3fb950"),
      cssVar("--warm", "#e3b341"),
      cssVar("--purple", "#bc8cff"),
      cssVar("--cyan", "#39c5cf"),
      cssVar("--bad", "#f85149")
    ];
    return palette[i % palette.length];
  };

  /* ---------------------------------------------------------- 布局 */

  Chart.prototype._layout = function () {
    var cfg = this.cfg;
    var rect = this.host.getBoundingClientRect();
    // host 还没进 DOM 时宽度是 0，退化成 320 免得算出 NaN
    var w = Math.max(120, Math.round(rect.width || 320));
    var h = cfg.height;

    this.w = w;
    this.h = h;
    this.ctx = fitCanvas(this.canvas, w, h);

    var pad = {
      top: cfg.pad.top,
      right: cfg.pad.right,
      bottom: cfg.pad.bottom,
      left: cfg.pad.left
    };

    /* 旋转标签要额外留高：竖直分量 = 最长标签宽 × sin(角度)。
       不算这个的话，45 度标签的下半截会被 canvas 边缘直接切掉，
       而 canvas 不会报错——只是标签默默没了，很难查。 */
    if (cfg.xRotate && cfg.labels.length) {
      var ctx0 = this.ctx;
      ctx0.save();
      ctx0.font = "11px " + cssVar("--sans", "sans-serif");
      var maxW = 0;
      cfg.labels.forEach(function (t) {
        var mw = ctx0.measureText(String(t)).width;
        if (mw > maxW) maxW = mw;
      });
      ctx0.restore();

      var rad = Math.abs(cfg.xRotate) * Math.PI / 180;
      var extra = Math.ceil(maxW * Math.sin(rad) + 12);
      /* 只在自动值更大时覆盖：调用方显式给了更大的 pad.bottom
         可能是有别的用途（比如底部放图例），不该被压小。 */
      if (extra > pad.bottom) pad.bottom = extra;
    }

    this.plot = {
      x: pad.left,
      y: pad.top,
      w: Math.max(10, w - pad.left - pad.right),
      h: Math.max(10, h - pad.top - pad.bottom)
    };
    this._pad = pad;
  };

  /* ---------------------------------------------------------- 数值范围 */

  /* 把所有可见系列的极值收出来，喂给 niceScale。 */
  Chart.prototype._valueRange = function () {
    var cfg = this.cfg;
    var min = Infinity;
    var max = -Infinity;

    cfg.series.forEach(function (s) {
      if (s.hidden) return;
      s.data.forEach(function (v) {
        if (v < min) min = v;
        if (v > max) max = v;
      });
    });

    if (min === Infinity) { min = 0; max = 1; }

    // 柱状图必须从 0 起：柱子长度 = 数值，不从 0 起就是骗人
    if (cfg.yFrom0 || cfg.type === "bar") {
      if (min > 0) min = 0;
      if (max < 0) max = 0;
    }
    if (isNum(cfg.yMin)) min = cfg.yMin;
    if (isNum(cfg.yMax)) max = cfg.yMax;

    return { min: min, max: max };
  };

  /* ============================================================
     四、渲染主流程
     ============================================================ */

  Chart.prototype._render = function () {
    if (this._destroyed) return;

    var ctx = this.ctx;
    var cfg = this.cfg;
    var plot = this.plot;

    ctx.clearRect(0, 0, this.w, this.h);

    if (cfg.type === "radar") {
      this._renderRadar();
      return;
    }
    if (cfg.type === "donut") {
      this._renderDonut();
      return;
    }

    var range = this._valueRange();
    var sc = niceScale(range.min, range.max, cfg.maxYTicks);
    this.scale = sc;

    if (cfg.showGrid) this._drawGrid(sc);
    if (cfg.showYAxis) this._drawYLabels(sc);
    if (cfg.showXAxis) this._drawXLabels();

    /* 绘制顺序：折线在下、柱子在上。
       反过来的话柱子会把线上的点盖住。 */
    var self = this;
    cfg.series.forEach(function (s, i) {
      if (s.hidden) return;
      if (cfg.type === "bar") self._drawBars(s, i, sc);
    });
    cfg.series.forEach(function (s, i) {
      if (s.hidden) return;
      if (cfg.type === "line" || cfg.type === "area") self._drawLine(s, i, sc);
    });

    if (this.hover) this._drawHover(sc);

    /* 没有任何数据时给一句话，不要留一片空白让人以为图坏了 */
    var hasData = cfg.series.some(function (s) {
      return !s.hidden && s.data.length;
    });
    if (!hasData) {
      ctx.save();
      ctx.fillStyle = cssVar("--text-mute", "#6b7a8d");
      ctx.font = "13px " + cssVar("--sans", "sans-serif");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(cfg.emptyText, this.w / 2, this.h / 2);
      ctx.restore();
    }

    if (this.legendEl) this._renderLegend();
  };

  /* 坐标换算：数值 → 画布 y */
  Chart.prototype._y = function (v, sc) {
    var plot = this.plot;
    var span = sc.max - sc.min || 1;
    return plot.y + plot.h - ((v - sc.min) / span) * plot.h;
  };

  /* 下标 → 画布 x。折线用「均分」，柱子用「每格中心」。 */
  Chart.prototype._x = function (i, count) {
    var plot = this.plot;
    if (count <= 1) return plot.x + plot.w / 2;
    if (this.cfg.type === "bar") {
      return plot.x + (plot.w / count) * (i + 0.5);
    }
    return plot.x + (plot.w / (count - 1)) * i;
  };

  /* ---------------------------------------------------------- 网格 */

  Chart.prototype._drawGrid = function (sc) {
    var ctx = this.ctx;
    var plot = this.plot;
    var line = cssVar("--border-soft", "#1e2634");

    ctx.save();
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;

    sc.ticks.forEach(function (v) {
      /* +0.5 对齐到物理像素。不加的话 1px 线会落在两个像素中间，
         被抗锯齿摊成 2px 的灰边，看着脏。 */
      var y = Math.round(plot.y + plot.h - ((v - sc.min) / (sc.max - sc.min || 1)) * plot.h) + 0.5;
      if (y < plot.y - 0.5 || y > plot.y + plot.h + 0.5) return;
      ctx.beginPath();
      ctx.moveTo(plot.x, y);
      ctx.lineTo(plot.x + plot.w, y);
      ctx.stroke();
    });

    ctx.restore();
  };

  Chart.prototype._drawYLabels = function (sc) {
    var ctx = this.ctx;
    var plot = this.plot;
    var cfg = this.cfg;

    ctx.save();
    ctx.fillStyle = cssVar("--text-mute", "#6b7a8d");
    ctx.font = '11px ' + cssVar("--mono", "monospace");
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    var fmt = cfg.format;
    sc.ticks.forEach(function (v) {
      var y = plot.y + plot.h - ((v - sc.min) / (sc.max - sc.min || 1)) * plot.h;
      if (y < plot.y - 1 || y > plot.y + plot.h + 1) return;
      ctx.fillText(fmt(v), plot.x - 8, y);
    });

    ctx.restore();
  };

  Chart.prototype._drawXLabels = function () {
    var cfg = this.cfg;
    var ctx = this.ctx;
    var plot = this.plot;
    var labels = cfg.labels;
    if (!labels.length) return;

    var count = labels.length;

    ctx.save();
    ctx.fillStyle = cssVar("--text-mute", "#6b7a8d");
    ctx.font = "11px " + cssVar("--sans", "sans-serif");

    /* 旋转模式：全部标签都画，不再抽样。
       角度超过 30 度时用右对齐，让文字从锚点向左上长，
       这样每个标签的「根部」都贴着对应的刻度。 */
    if (cfg.xRotate) {
      var rad = cfg.xRotate * Math.PI / 180;
      ctx.textAlign = Math.abs(cfg.xRotate) > 30 ? "right" : "center";
      ctx.textBaseline = "middle";
      for (var k = 0; k < count; k++) {
        ctx.save();
        ctx.translate(this._x(k, count), plot.y + plot.h + 9);
        ctx.rotate(-rad);
        ctx.fillText(String(labels[k]), 0, 0);
        ctx.restore();
      }
      ctx.restore();
      return;
    }

    ctx.textBaseline = "top";

    /* 标签太密就抽样显示。判据是「估算的标签宽度 × 数量 > 可用宽度」。
       中文按 11px 算宽，这个估算不精确，但只为决定要不要抽样，
       差几个像素无所谓。 */
    var maxLabelW = 0;
    ctx.textAlign = "left";
    labels.forEach(function (t) {
      var w = ctx.measureText(String(t)).width;
      if (w > maxLabelW) maxLabelW = w;
    });

    var slot = cfg.type === "bar"
      ? plot.w / count
      : plot.w / Math.max(1, count - 1);
    var stride = Math.max(1, Math.ceil((maxLabelW + 10) / slot));

    for (var i = 0; i < count; i += stride) {
      var x = this._x(i, count);
      var text = String(labels[i]);
      /* 首尾两个标签改成左/右对齐，否则会被画布边缘裁掉半个字。
         这是所有手写图表都该处理的一件事。 */
      if (i === 0 && cfg.type !== "bar") {
        ctx.textAlign = "left";
      } else if (i >= count - stride && cfg.type !== "bar") {
        ctx.textAlign = "right";
      } else {
        ctx.textAlign = "center";
      }
      ctx.fillText(text, x, plot.y + plot.h + 8);
    }

    ctx.restore();
  };

  /* ---------------------------------------------------------- 折线 / 面积 */

  /* Catmull-Rom 转三次贝塞尔。
     直接用「中点连线」的简易平滑会在拐点处鼓出来，
     真正的 Catmull-Rom 更贴合原始点。 */
  function smoothPath(ctx, pts) {
    if (pts.length < 2) return;
    ctx.moveTo(pts[0].x, pts[0].y);
    if (pts.length === 2) {
      ctx.lineTo(pts[1].x, pts[1].y);
      return;
    }
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i];
      var p1 = pts[i];
      var p2 = pts[i + 1];
      var p3 = pts[i + 2] || p2;

      var t = 0.18;   // 张力。0.5 是标准 Catmull-Rom，但会过冲得厉害
      var c1x = p1.x + (p2.x - p0.x) * t;
      var c1y = p1.y + (p2.y - p0.y) * t;
      var c2x = p2.x - (p3.x - p1.x) * t;
      var c2y = p2.y - (p3.y - p1.y) * t;

      ctx.bezierCurveTo(c1x, c1y, c2x, c2y, p2.x, p2.y);
    }
  }

  Chart.prototype._drawLine = function (s, si, sc) {
    var ctx = this.ctx;
    var cfg = this.cfg;
    var count = s.data.length;
    if (!count) return;

    var color = this._seriesColor(si);
    var anim = this._anim;

    /* 生长动画：所有点的值按 anim 从基线插到目标。
       这样动画过程中线是「长出来」的，而不是淡入。 */
    var baseY = this._y(cfg.yFrom0 ? Math.max(0, sc.min) : sc.min, sc);
    var pts = s.data.map(function (v, i) {
      var yv = cfg.yFrom0 ? Math.max(0, sc.min) + (v - Math.max(0, sc.min)) * anim : v * anim;
      return {
        x: this._x(i, count),
        y: this._y(yv, sc),
        v: v
      };
    }, this);

    // 面积填充画在折线下面
    if (cfg.type === "area" && s.fill) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(pts[0].x, baseY);
      if (cfg.smooth) {
        // 先把路径建到第一个点上，再接着走平滑
        ctx.lineTo(pts[0].x, pts[0].y);
        for (var i = 0; i < pts.length - 1; i++) {
          var p0 = pts[i - 1] || pts[i];
          var p1 = pts[i];
          var p2 = pts[i + 1];
          var p3 = pts[i + 2] || p2;
          var t = 0.18;
          ctx.bezierCurveTo(
            p1.x + (p2.x - p0.x) * t, p1.y + (p2.y - p0.y) * t,
            p2.x - (p3.x - p1.x) * t, p2.y - (p3.y - p1.y) * t,
            p2.x, p2.y
          );
        }
      } else {
        pts.forEach(function (p) { ctx.lineTo(p.x, p.y); });
      }
      ctx.lineTo(pts[pts.length - 1].x, baseY);
      ctx.closePath();

      var grad = ctx.createLinearGradient(0, this.plot.y, 0, this.plot.y + this.plot.h);
      grad.addColorStop(0, withAlpha(color, 0.28 * anim));
      grad.addColorStop(1, withAlpha(color, 0.01));
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.restore();
    }

    // 折线本体
    ctx.save();
    ctx.beginPath();
    if (cfg.smooth) smoothPath(ctx, pts);
    else {
      ctx.moveTo(pts[0].x, pts[0].y);
      pts.forEach(function (p, i) { if (i) ctx.lineTo(p.x, p.y); });
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    if (s.dashed) ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.restore();

    /* 数据点：只在点少的时候画。点多了每个都画圆圈会糊成一条粗线。
       悬停时那个点单独高亮，所以不画也不影响交互。 */
    if (count <= 16) {
      ctx.save();
      var r = 3;
      pts.forEach(function (p) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = cssVar("--bg-elev", "#151a23");
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = color;
        ctx.stroke();
      });
      ctx.restore();
    }
  };

  /* ---------------------------------------------------------- 柱状 */

  Chart.prototype._drawBars = function (s, si, sc) {
    var ctx = this.ctx;
    var cfg = this.cfg;
    var count = s.data.length;
    if (!count) return;

    var color = this._seriesColor(si);
    var plot = this.plot;
    var slot = plot.w / count;

    /* 全部系列都显示时并排，只有一条时占满。
       并排宽度 = 槽宽 × 0.7 / 系列数，剩下的 30% 做间隙。 */
    var visible = cfg.series.filter(function (x) { return !x.hidden; });
    var vi = visible.indexOf(s);
    var n = visible.length || 1;
    var groupW = slot * 0.72;
    var barW = Math.max(2, groupW / n - (n > 1 ? 2 : 0));
    var offset = -groupW / 2 + vi * (barW + 2);

    var zeroY = this._y(clamp(0, sc.min, sc.max), sc);

    ctx.save();
    for (var i = 0; i < count; i++) {
      var v = s.data[i] * this._anim;
      var cx = this._x(i, count) + offset;
      var y = this._y(v, sc);
      var top = Math.min(y, zeroY);
      var hgt = Math.max(0.5, Math.abs(zeroY - y));

      /* 圆角只给「远离零轴」的那一端。柱子贴地那一头应该是方的，
         两头都圆看着像药丸，不像柱子。 */
      var r = Math.min(3, barW / 2, hgt / 2);
      roundRect(ctx, cx - barW / 2, top, barW, hgt, v >= 0 ? [r, r, 0, 0] : [0, 0, r, r]);

      var grad = ctx.createLinearGradient(0, top, 0, top + hgt);
      grad.addColorStop(0, withAlpha(color, 0.95));
      grad.addColorStop(1, withAlpha(color, 0.5));
      ctx.fillStyle = grad;
      ctx.fill();
    }
    ctx.restore();
  };

  /* 支持每角不同半径的圆角矩形。 */
  function roundRect(ctx, x, y, w, h, radii) {
    var r = Array.isArray(radii) ? radii : [radii, radii, radii, radii];
    var tl = clamp(r[0] || 0, 0, Math.min(w, h) / 2);
    var tr = clamp(r[1] || 0, 0, Math.min(w, h) / 2);
    var br = clamp(r[2] || 0, 0, Math.min(w, h) / 2);
    var bl = clamp(r[3] || 0, 0, Math.min(w, h) / 2);

    ctx.beginPath();
    ctx.moveTo(x + tl, y);
    ctx.lineTo(x + w - tr, y);
    if (tr) ctx.arcTo(x + w, y, x + w, y + tr, tr);
    ctx.lineTo(x + w, y + h - br);
    if (br) ctx.arcTo(x + w, y + h, x + w - br, y + h, br);
    ctx.lineTo(x + bl, y + h);
    if (bl) ctx.arcTo(x, y + h, x, y + h - bl, bl);
    ctx.lineTo(x, y + tl);
    if (tl) ctx.arcTo(x, y, x + tl, y, tl);
    ctx.closePath();
  }

  /* ---------------------------------------------------------- 雷达 */

  Chart.prototype._renderRadar = function () {
    var cfg = this.cfg;
    var ctx = this.ctx;
    var plot = this.plot;
    var n = cfg.labels.length;
    if (!n) return;

    var cx = plot.x + plot.w / 2;
    var cy = plot.y + plot.h / 2;
    // 留出标签位置：半径取较窄那边的一半
    var R = Math.min(plot.w, plot.h) / 2 - 26;
    if (R < 10) return;

    /* 雷达图的「一个轴」是 0..max，所有轴共用同一个值域。
       否则各部门满分的形状会变成锯齿，没法比。 */
    var range = this._valueRange();
    var top = Math.max(range.max, 1);

    var angleOf = function (i) {
      // 从 12 点方向开始，顺时针
      return -Math.PI / 2 + (Math.PI * 2 / n) * i;
    };

    // 蛛网
    ctx.save();
    ctx.strokeStyle = cssVar("--border-soft", "#1e2634");
    ctx.lineWidth = 1;

    for (var ring = 1; ring <= cfg.radarRings; ring++) {
      var rr = R * (ring / cfg.radarRings) * this._anim;
      ctx.beginPath();
      for (var i = 0; i < n; i++) {
        var a = angleOf(i);
        var px = cx + Math.cos(a) * rr;
        var py = cy + Math.sin(a) * rr;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.stroke();
    }

    // 轴辐
    for (var j = 0; j < n; j++) {
      var a2 = angleOf(j);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(a2) * R * this._anim, cy + Math.sin(a2) * R * this._anim);
      ctx.stroke();
    }
    ctx.restore();

    // 各系列多边形
    var self = this;
    cfg.series.forEach(function (s, si) {
      if (s.hidden) return;
      var color = self._seriesColor(si);

      ctx.save();
      ctx.beginPath();
      for (var k = 0; k < n; k++) {
        var v = clamp((s.data[k] || 0) / top, 0, 1) * self._anim;
        var ang = angleOf(k);
        var x = cx + Math.cos(ang) * R * v;
        var y = cy + Math.sin(ang) * R * v;
        if (k === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = withAlpha(color, 0.22);
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
    });

    // 轴标签
    ctx.save();
    ctx.fillStyle = cssVar("--text-dim", "#93a1b3");
    ctx.font = "11.5px " + cssVar("--sans", "sans-serif");
    for (var t = 0; t < n; t++) {
      var ta = angleOf(t);
      var lx = cx + Math.cos(ta) * (R + 16);
      var ly = cy + Math.sin(ta) * (R + 16);
      // 按象限决定对齐方式，不然标签会盖到蛛网上
      ctx.textAlign = Math.abs(Math.cos(ta)) < 0.25
        ? "center"
        : (Math.cos(ta) > 0 ? "left" : "right");
      ctx.textBaseline = Math.abs(Math.sin(ta)) < 0.25
        ? "middle"
        : (Math.sin(ta) > 0 ? "top" : "bottom");
      ctx.fillText(String(cfg.labels[t]), lx, ly);
    }
    ctx.restore();

    if (this.legendEl) this._renderLegend();
  };

  /* ---------------------------------------------------------- 环形 */

  Chart.prototype._renderDonut = function () {
    var cfg = this.cfg;
    var ctx = this.ctx;
    var plot = this.plot;

    /* 环形图的数据来源有点特殊：它只有「一个系列」的概念，
       每一段是一个数据点。所以这里把 series[0].data 当扇区值，
       labels 当扇区名。 */
    var s = cfg.series[0];
    if (!s) return;
    var data = s.data;

    /* 被隐藏的扇区从「总量」里扣掉，剩下的重新铺满整圈。
       另一种做法是保留角度占位、在环上留一道缺口——但那样没意义：
       用户点掉「未开始 51 题」就是想看「已通过 vs 待攻克」的比例，
       留个占 85% 的空洞，剩下的两块还是挤在角落里看不清。 */
    var visibleTotal = 0;
    for (var v0 = 0; v0 < data.length; v0++) {
      if (this._sliceHidden[v0]) continue;
      visibleTotal += Math.max(0, data[v0]);
    }
    var total = visibleTotal;

    var cx = plot.x + plot.w / 2;
    var cy = plot.y + plot.h / 2;
    var R = Math.min(plot.w, plot.h) / 2 - 6;

    if (total <= 0) {
      ctx.save();
      ctx.strokeStyle = cssVar("--border", "#263041");
      ctx.lineWidth = Math.max(6, R * (1 - cfg.donutInner));
      ctx.beginPath();
      ctx.arc(cx, cy, R * (1 - (1 - cfg.donutInner) / 2), 0, Math.PI * 2);
      ctx.stroke();
      /* 全隐藏时中心什么都不写会像个坏掉的图，写上 0 至少说明
         「你现在看不到任何一档」。数据本来就全 0 时也走这里。 */
      ctx.fillStyle = cssVar("--text-mute", "#6b7a8d");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "600 18px " + cssVar("--mono", "monospace");
      ctx.fillText("0", cx, cy - 6);
      if (s.name) {
        ctx.font = "11px " + cssVar("--sans", "sans-serif");
        ctx.fillText(s.name, cx, cy + 14);
      }
      ctx.restore();
      return;
    }

    var inner = cfg.donutInner;
    var start = -Math.PI / 2;   // 从 12 点开始，顺时针

    for (var i = 0; i < data.length; i++) {
      if (this._sliceHidden[i]) continue;

      var frac = Math.max(0, data[i]) / total;
      var sweep = frac * Math.PI * 2 * this._anim;
      if (sweep <= 0.0001) { start += sweep; continue; }

      /* 颜色按「原始下标」取，不按可见顺序重排。
         否则隐藏「已通过」之后，「待攻克」会从黄色变成绿色——
         同一档数据的颜色不该因为藏了别的档而改变。 */
      var color = this._seriesColor(i);

      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, R, start, start + sweep);
      ctx.arc(cx, cy, R * inner, start + sweep, start, true);
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();

      /* 扇区之间留一道缝：深色背景下两块同色系颜色挨着会糊成一整块。
         缝隙用背景色描边实现，比算角度便宜。 */
      ctx.strokeStyle = cssVar("--bg-elev", "#151a23");
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();

      start += sweep;
    }

    // 中心文字
    if (s.name) {
      ctx.save();
      ctx.fillStyle = cssVar("--text", "#dfe6ef");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "600 18px " + cssVar("--mono", "monospace");
      ctx.fillText(cfg.format(total), cx, cy - 6);
      ctx.fillStyle = cssVar("--text-mute", "#6b7a8d");
      ctx.font = "11px " + cssVar("--sans", "sans-serif");
      ctx.fillText(s.name, cx, cy + 14);
      ctx.restore();
    }

    if (this.legendEl) this._renderLegend();
  };

  /* ---------------------------------------------------------- 悬停 */

  Chart.prototype._drawHover = function (sc) {
    var hv = this.hover;
    var cfg = this.cfg;
    var ctx = this.ctx;
    var plot = this.plot;

    if (cfg.type === "radar" || cfg.type === "donut") return;

    var count = cfg.labels.length || (cfg.series[0] && cfg.series[0].data.length) || 0;
    if (!count) return;

    var x = this._x(hv.pi, count);

    // 竖直准线
    ctx.save();
    ctx.strokeStyle = withAlpha(cssVar("--text-mute", "#6b7a8d"), 0.45);
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, plot.y);
    ctx.lineTo(Math.round(x) + 0.5, plot.y + plot.h);
    ctx.stroke();
    ctx.restore();

    // 每个系列在那个 x 上的点
    var self = this;
    cfg.series.forEach(function (s, si) {
      if (s.hidden || si !== hv.si) return;
      var v = s.data[hv.pi];
      if (!isNum(v)) return;
      var y = self._y(v * self._anim, sc);

      ctx.save();
      ctx.beginPath();
      ctx.arc(x, y, 5.5, 0, Math.PI * 2);
      ctx.fillStyle = cssVar("--bg", "#0d1017");
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = self._seriesColor(si);
      ctx.stroke();
      ctx.restore();
    });
  };

  /* tooltip 是 DOM，定位到光标附近。
     内容结构：标签行 + 每个系列一行（色点 + 名字 + 值）。 */
  Chart.prototype._showTip = function (pi, clientX, clientY) {
    var cfg = this.cfg;
    if (!this.tipEl) return;

    var rows = [];
    var label = cfg.labels[pi] != null ? String(cfg.labels[pi]) : "";

    if (cfg.type === "donut") {
      var s0 = cfg.series[0];
      if (!s0) return;
      /* 占比的分母是「当前可见扇区之和」——环按这个总量铺满，
         tooltip 写 100% 才和肉眼看到的角度一致。 */
      var total = 0;
      for (var k0 = 0; k0 < s0.data.length; k0++) {
        if (this._sliceHidden[k0]) continue;
        total += Math.max(0, s0.data[k0]);
      }
      var v0 = s0.data[pi] || 0;
      var pct = total ? (v0 / total * 100) : 0;
      rows.push({
        color: this._seriesColor(pi),
        name: label,
        value: cfg.tooltipFormat(v0) + " · " + pct.toFixed(1) + "%"
      });
      label = s0.name || "";
    } else {
      cfg.series.forEach(function (s, si) {
        if (s.hidden) return;
        var v = s.data[pi];
        if (!isNum(v)) return;
        rows.push({
          color: cfg.series.length > 1 ? cfg.series[si].color || null : null,
          si: si,
          name: s.name,
          value: cfg.tooltipFormat(v)
        });
      });
    }

    if (!rows.length) { this._hideTip(); return; }

    var html = "";
    if (label) {
      html += '<div class="cm-tip-head">' + escapeHtml(label) + "</div>";
    }
    rows.forEach(function (r) {
      var dot = "";
      if (r.si != null) {
        dot = '<i class="cm-tip-dot" style="background:' +
          escapeHtml(r.color || "#888") + '"></i>';
      } else if (r.color) {
        dot = '<i class="cm-tip-dot" style="background:' +
          escapeHtml(r.color) + '"></i>';
      }
      html += '<div class="cm-tip-row">' + dot +
        '<span class="cm-tip-name">' + escapeHtml(r.name) + "</span>" +
        '<b class="cm-tip-val">' + escapeHtml(String(r.value)) + "</b></div>";
    });

    this.tipEl.innerHTML = html;
    this.tipEl.classList.add("show");

    /* 定位：默认在光标右上方偏移 12px。
       靠近右/上边缘时翻到另一侧，避免被视口裁掉。 */
    var rect = this.tipEl.getBoundingClientRect();
    var hostRect = this.host.getBoundingClientRect();
    var x = clientX - hostRect.left + 14;
    var y = clientY - hostRect.top - rect.height - 12;

    if (x + rect.width > hostRect.width - 4) {
      x = clientX - hostRect.left - rect.width - 14;
    }
    if (x < 4) x = 4;
    if (y < 4) y = clientY - hostRect.top + 18;

    this.tipEl.style.left = x + "px";
    this.tipEl.style.top = y + "px";
  };

  Chart.prototype._hideTip = function () {
    if (this.tipEl) this.tipEl.classList.remove("show");
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* 命中检测：把光标 x 映射到最近的数据点下标。
     只按 x 判，不看 y——用户指向某一列是想看那一列的整组数据。 */
  Chart.prototype._hitTest = function (offsetX, offsetY) {
    var cfg = this.cfg;
    var count = cfg.labels.length || (cfg.series[0] && cfg.series[0].data.length) || 0;
    if (!count) return null;

    if (cfg.type === "donut") {
      return this._hitDonut(offsetX, offsetY, count);
    }

    var plot = this.plot;
    if (offsetX < plot.x - 8 || offsetX > plot.x + plot.w + 8) return null;
    if (offsetY < plot.y - 8 || offsetY > plot.y + plot.h + 8) return null;

    var best = 0;
    var bestDist = Infinity;
    for (var i = 0; i < count; i++) {
      var d = Math.abs(this._x(i, count) - offsetX);
      if (d < bestDist) { bestDist = d; best = i; }
    }

    /* 找最近的系列：拿鼠标 y 和每个系列在该点的 y 比。
       都不在容差内就选第一条可见的。 */
    var si = -1;
    var cursorY = offsetY;
    var bestY = Infinity;
    var self = this;
    cfg.series.forEach(function (s, k) {
      if (s.hidden) return;
      var v = s.data[best];
      if (!isNum(v)) return;
      var py = self._y(v * self._anim, self.scale);
      var dy = Math.abs(py - cursorY);
      if (dy < bestY) { bestY = dy; si = k; }
    });

    return { pi: best, si: si < 0 ? 0 : si };
  };

  Chart.prototype._hitDonut = function (offsetX, offsetY, count) {
    var plot = this.plot;
    var cx = plot.x + plot.w / 2;
    var cy = plot.y + plot.h / 2;
    var dx = offsetX - cx;
    var dy = offsetY - cy;
    var dist = Math.sqrt(dx * dx + dy * dy);
    var R = Math.min(plot.w, plot.h) / 2 - 6;
    var inner = R * this.cfg.donutInner;

    if (dist < inner || dist > R) return null;

    /* atan2 返回 -π..π，起点在 3 点方向。我们的环从 12 点开始，
       所以加 π/2 再归一化到 0..2π。 */
    var ang = Math.atan2(dy, dx) + Math.PI / 2;
    while (ang < 0) ang += Math.PI * 2;
    while (ang >= Math.PI * 2) ang -= Math.PI * 2;

    var s0 = this.cfg.series[0];
    if (!s0) return null;

    /* 命中检测必须和渲染用同一套「可见总量」，否则鼠标落在哪个扇区
       会和画出来的角度对不上——环被隐藏的扇区重新铺满之后，
       用原始总量算角度就是错的。 */
    var total = 0;
    for (var j = 0; j < s0.data.length; j++) {
      if (this._sliceHidden[j]) continue;
      total += Math.max(0, s0.data[j]);
    }
    if (total <= 0) return null;

    var acc = 0;
    var lastVisible = -1;
    for (var i = 0; i < count; i++) {
      if (this._sliceHidden[i]) continue;
      var sweep = (Math.max(0, s0.data[i]) / total) * Math.PI * 2;
      if (ang >= acc && ang < acc + sweep) {
        return { pi: i, si: 0 };
      }
      acc += sweep;
      lastVisible = i;
    }
    /* 兜底：角度累加有浮点误差，落在末尾极窄的缝里就归给最后一个
       可见扇区。不能直接写 count - 1——那一项可能正好被隐藏了。 */
    return lastVisible >= 0 ? { pi: lastVisible, si: 0 } : null;
  };

  /* ---------------------------------------------------------- 事件 */

  Chart.prototype._bindEvents = function () {
    var self = this;

    function onMove(e) {
      var rect = self.canvas.getBoundingClientRect();
      var ox = e.clientX - rect.left;
      var oy = e.clientY - rect.top;

      var hit = self._hitTest(ox, oy);
      var changed = !hit !== !self.hover ||
        (hit && self.hover && (hit.pi !== self.hover.pi || hit.si !== self.hover.si));

      self.hover = hit;
      if (changed) self._render();

      if (hit) self._showTip(hit.pi, e.clientX, e.clientY);
      else self._hideTip();
    }

    function onLeave() {
      if (!self.hover) return;
      self.hover = null;
      self._hideTip();
      self._render();
    }

    function onClick(e) {
      if (!self.cfg.onPointClick || !self.hover) return;
      self.cfg.onPointClick(self.hover.pi, self.hover.si, e);
    }

    this.canvas.addEventListener("mousemove", onMove);
    this.canvas.addEventListener("mouseleave", onLeave);
    this.canvas.addEventListener("click", onClick);

    /* 触摸：手指按下等价于悬停。移动端没有 hover 这个概念，
       不处理的话手机上永远看不到 tooltip。 */
    this.canvas.addEventListener("touchstart", function (e) {
      if (!e.touches.length) return;
      var t = e.touches[0];
      var rect = self.canvas.getBoundingClientRect();
      var hit = self._hitTest(t.clientX - rect.left, t.clientY - rect.top);
      self.hover = hit;
      if (hit) {
        self._render();
        self._showTip(hit.pi, t.clientX, t.clientY);
      }
    }, { passive: true });

    this.canvas.addEventListener("touchend", function () {
      self.hover = null;
      self._hideTip();
      self._render();
    });

    /* 容器尺寸变化要重算。用 ResizeObserver 而不是 window.resize：
       侧边栏折叠、内容区变宽这类变化 window 是不知道的。 */
    if (global.ResizeObserver) {
      this._ro = new ResizeObserver(function () {
        /* 回调可能在同一帧触发多次，用 rAF 合并。
           不合并的话拖动窗口会疯狂重绘。 */
        if (self._raf) return;
        self._raf = requestAnimationFrame(function () {
          self._raf = 0;
          if (self._destroyed) return;
          var r = self.host.getBoundingClientRect();
          if (Math.abs(r.width - self.w) < 1) return;
          self._layout();
          self._render();
        });
      });
      this._ro.observe(this.host);
    }

    this._onMove = onMove;
    this._onLeave = onLeave;
    this._onClick = onClick;
  };

  /* ============================================================
     五、公开 API
     ============================================================ */

  /* 更新数据。不重建 DOM，只重跑入场动画——
     切换图表数据时如果整块重建，会闪一下白。 */
  Chart.prototype.update = function (config) {
    if (this._destroyed) return this;
    var next = normalizeConfig(config);
    this.cfg = next;
    this.hover = null;

    /* 换了数据就清空扇区隐藏状态。
       留着旧数组会有两种后果：新旧长度不一致时，多出来的下标指向
       不存在的扇区；长度一致时更糟——用户上次藏起来的扇区在新数据
       里莫名其妙还是藏着的，看起来像数据丢了。 */
    this._sliceHidden = [];

    if (this._tween) this._tween.stop();
    this._anim = 0;

    if (CMTween && !CMTween.reducedMotion()) {
      var self = this;
      this._tween = CMTween.value(0, 1, next.duration, function (v) {
        self._anim = v;
        self._render();
      });
    } else {
      this._anim = 1;
      this._render();
    }
    return this;
  };

  Chart.prototype.resize = function () {
    if (this._destroyed) return this;
    this._layout();
    this._render();
    return this;
  };

  Chart.prototype.destroy = function () {
    this._destroyed = true;
    if (this._tween) this._tween.stop();
    if (this._ro) this._ro.disconnect();
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this.canvas && this._onMove) {
      this.canvas.removeEventListener("mousemove", this._onMove);
      this.canvas.removeEventListener("mouseleave", this._onLeave);
      this.canvas.removeEventListener("click", this._onClick);
    }
    return this;
  };

  var CMChart = {
    create: function (host, config) {
      if (!host) return null;
      return new Chart(host, config);
    },
    /* 挂一个实例到元素上，方便后续取。重复挂会先销毁旧的。 */
    mount: function (host, config) {
      if (!host) return null;
      if (host._cmChart) host._cmChart.destroy();
      host._cmChart = new Chart(host, config);
      return host._cmChart;
    },
    /* 内部函数导出一点，测试要用 */
    _internal: {
      niceScale: niceScale,
      niceNum: niceNum,
      shortNum: shortNum,
      roundRect: roundRect,
      withAlpha: withAlpha
    }
  };

  global.CMChart = CMChart;
})(typeof window !== "undefined" ? window : this);
