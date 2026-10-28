/* ============================================================================
   CMGraph —— 力导向关系图引擎
   ----------------------------------------------------------------------------
   用在知识图谱页：12 个章节 + 48 个知识点，加三类连边，让「先学什么、
   后学什么、哪些是同一章」一眼看得出来。

   为什么自己写而不是引 d3-force：
     1. 项目原则是零外部依赖、断网能用。d3-force 单独抽出来也有 30KB，
        为了 60 个节点引一个库，收益不成比例。
     2. 我们只需要一种力模型（FR + 弹簧 + 向心）。d3 里那十几种力、
        事件系统、selection 抽象都用不上，它们占的正是体积。
     3. 配色和动画要跟项目打通。d3 的力是「数据驱动」的，
        要跟 CMTween 的时钟合到一起反而要垫一层适配。

   为什么不做 Barnes-Hut 四叉树：
     n=60 时，全对计算是 60*59/2 = 1770 次距离平方运算。一帧 1770 次
     乘加，在现在的 CPU 上是微秒级，比维护四叉树的递归和对象分配便宜。
     四叉树要到 n 上千才有意义。这里刻意不写——知道「什么时候不该优化」
     比会写高级算法更值钱。真到节点上千，把 _repel 换成四叉树即可，
     其余代码不用动。
   ============================================================================ */

(function (global) {
  "use strict";

  var DEFAULTS = {
    /* 物理参数。这些值没有理论最优解，全是拿这一组真实数据（12 章 48 节）
       调出来的：太大了一张图糊成一团，太小了节点飘到画布外面。 */
    repulsion: 2600,      // 斥力强度
    springK: 0.085,       // 弹簧刚度
    gravity: 0.012,       // 向心力，防孤立节点飘走
    linkDistance: 62,     // 弹簧自然长度
    damping: 0.86,        // 每帧速度衰减
    alphaDecay: 0.019,    // 退火速度
    alphaMin: 0.008,      // 低于这个值就停止模拟
    alphaReheat: 0.55,    // 拖拽/交互后重新加热到的温度
    maxStep: 18,          // 单帧最大位移，防止初始炸开
    minDist: 6,           // 斥力的最小距离，防止 1/d² 爆炸
    collide: 3,           // 节点圆之间的最小间隙

    nodeRadius: { chapter: 15, kp: 5.5 },
    labelMinZoom: 0.62,   // 低于这个缩放只显示章名
    wheelStep: 0.0016,
    zoomMin: 0.35,
    zoomMax: 2.6,
    duration: 420          // 入场淡入时长
  };

  /* ---------------------------------------------------------------- 小工具 */

  function clamp(v, lo, hi) {
    return v < lo ? lo : (v > hi ? hi : v);
  }

  function cssVar(name, fallback) {
    if (!global.getComputedStyle) return fallback;
    var v = global.getComputedStyle(document.documentElement).getPropertyValue(name);
    v = (v || "").trim();
    return v || fallback;
  }

  /* 把 #rrggbb 拆成 r,g,b，给需要按透明度画边的地方用。
     不引 color 库，就这十几行。 */
  function parseHex(hex) {
    var h = String(hex || "").trim().replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return [136, 136, 136];
    return [
      parseInt(h.slice(0, 2), 16) || 0,
      parseInt(h.slice(2, 4), 16) || 0,
      parseInt(h.slice(4, 6), 16) || 0
    ];
  }

  function rgba(c, a) {
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }

  /* 按容器尺寸和技术分辨率准备画布。
     DPR 封顶 2：4K 屏 dpr=3 会让像素翻九倍，而图谱是线框图，
     2x 之后肉眼已经分不出来，帧率却掉一半。 */
  function fitCanvas(canvas, host) {
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var w = host.clientWidth || 600;
    var h = host.clientHeight || 400;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    return { w: w, h: h, dpr: dpr };
  }

  /* ---------------------------------------------------------------- 模拟 */

  /* 模拟器只管「算位置」，完全不知道 canvas 长什么样。
     这样它能在 Node 里被单元测试（见 tools/graph_test.js），
     也能被换到 WebGL 渲染下复用。 */
  function Sim(nodes, links, opts) {
    this.nodes = nodes;
    this.links = links;
    this.opt = opts || {};
    this.alpha = 1;
    this.iterations = 0;

    /* 边用对象引用而不是下标存，省掉每次 tick 的 id → 节点查表。
       60 个节点时这点开销无所谓，但结构更干净。 */
    this.links.forEach(function (l) {
      l.s = typeof l.source === "object" ? l.source : null;
      l.t = typeof l.target === "object" ? l.target : null;
    });
  }

  Sim.prototype.tick = function () {
    var o = this.opt;
    var nodes = this.nodes;
    var n = nodes.length;
    var alpha = this.alpha;
    var i, j, a, b, dx, dy, d2, d, f, fx, fy;

    /* ① 斥力：所有节点两两相斥，力 ∝ alpha / d²
       注意 minDist：两个节点恰好重合时 d=0，1/d² 是无穷大，
       乘上任何系数都会把它们弹到天边去。实际坐标几乎不可能完全相等，
       但初始布局是从小圆上撒点，早期很容易近似重合。 */
    for (i = 0; i < n; i++) {
      a = nodes[i];
      for (j = i + 1; j < n; j++) {
        b = nodes[j];
        dx = a.x - b.x;
        dy = a.y - b.y;
        d2 = dx * dx + dy * dy;
        if (d2 < o.minDist * o.minDist) {
          /* 完全重合时给一个确定性的小偏移，而不是随机：
             随机会让每次刷新的图形都不一样，调试时找不到基准。 */
          dx = (i - j) * 0.5;
          dy = (j - i) * 0.3;
          d2 = dx * dx + dy * dy;
        }
        d = Math.sqrt(d2);
        f = (o.repulsion * alpha) / d2;
        fx = (dx / d) * f;
        fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }

    /* ② 弹簧：把有边的节点拉到目标距离附近
       F = (d - L) * k  —— d 大于自然长度时是拉力，小于时是推力 */
    for (i = 0; i < this.links.length; i++) {
      var l = this.links[i];
      a = l.s; b = l.t;
      if (!a || !b) continue;
      dx = b.x - a.x;
      dy = b.y - a.y;
      d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      /* 不同类型的边给不同的自然长度：章-节是父子关系，要紧凑；
         章-章是依赖关系，拉远一点，否则三簇会糊在一起。 */
      var want = l.distance || o.linkDistance;
      f = (d - want) * (l.strength || o.springK) * alpha;
      fx = (dx / d) * f;
      fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }

    /* ③ 向心：防止只有一条边的叶子节点被斥力推到无穷远 */
    for (i = 0; i < n; i++) {
      a = nodes[i];
      a.vx -= a.x * o.gravity * alpha;
      a.vy -= a.y * o.gravity * alpha;
    }

    /* ④ 积分 + 阻尼 + 位移钳制
       钳制这一步是必需的不是保险：初始状态所有节点都在原点附近，
       第一帧的斥力方向几乎随机，不做钳制会瞬间炸到画布外，
       而且退火之后再也没机会回来（力随距离衰减，回不来了）。 */
    var maxStep = o.maxStep;
    for (i = 0; i < n; i++) {
      a = nodes[i];

      /* 被拖拽的节点直接固定在指针上，但不清零速度：
         松手那一刻会带着惯性冲一下，看起来像真的在解体。
         清掉的话松手是「啪」地贴住，很生硬。 */
      if (a.fx != null) {
        a.x = a.fx;
        a.y = a.fy;
        a.vx = 0;
        a.vy = 0;
        continue;
      }

      a.vx *= o.damping;
      a.vy *= o.damping;

      var sx = a.vx;
      var sy = a.vy;
      var step = Math.sqrt(sx * sx + sy * sy);
      if (step > maxStep) {
        sx = sx / step * maxStep;
        sy = sy / step * maxStep;
      }

      a.x += sx;
      a.y += sy;
    }

    this.alpha += (0 - this.alpha) * this.opt.alphaDecay;
    this.iterations++;
    return this.alpha > this.opt.alphaMin;
  };

  /* 重新加热。用 max 而不是直接赋值：
     连续拖动时每一帧都会调一次，直接赋值会让温度一直卡在高位，
     松手后图永远在抖。 */
  Sim.prototype.reheat = function (to) {
    this.alpha = Math.max(this.alpha, to == null ? this.opt.alphaReheat : to);
    return this;
  };

  /* ---------------------------------------------------------------- 图 */

  function Graph(host, config) {
    this.host = host;
    this.cfg = config || {};
    this.opt = {};
    for (var k in DEFAULTS) this.opt[k] = DEFAULTS[k];
    for (var k2 in this.opt) {
      if (this.cfg[k2] != null) this.opt[k2] = this.cfg[k2];
    }

    this.tx = 0;           // 平移
    this.ty = 0;
    this.k = 1;            // 缩放
    this.hover = null;
    this.selected = null;
    this._raf = 0;
    this._destroyed = false;
    this._settled = false; // 模拟是否已经收敛
    this._fade = 0;        // 入场淡入 0..1
    this._drag = null;
    this._pan = null;
    this._focusFn = null;   // 过滤谓词，见 setFocus()

    this._buildDom();
    this._layout();
    this._bindEvents();
    this._start();
  }

  Graph.prototype._buildDom = function () {
    var wrap = document.createElement("div");
    wrap.className = "cm-graph";
    this.host.appendChild(wrap);
    this.host = wrap;

    this.canvas = document.createElement("canvas");
    this.canvas.className = "cm-graph-canvas";
    /* role=img + aria-label：canvas 对读屏器是一片空白，
       不补这句等于整个图谱对无障碍用户不存在。 */
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label",
      "知识点关系图。" + (this.cfg.nodes || []).length + " 个节点，" +
      (this.cfg.links || []).length + " 条连线。可用键盘 Tab 选择节点。");
    this.canvas.tabIndex = 0;
    wrap.appendChild(this.canvas);

    this.tip = document.createElement("div");
    this.tip.className = "cm-graph-tip";
    this.tip.setAttribute("aria-hidden", "true");
    wrap.appendChild(this.tip);

    this.ctx = this.canvas.getContext("2d");
  };

  /* 建节点与边的运行时对象。
     每次 _layout 都从原始配置重建，所以窗口 resize / 数据更新
     都能安全重来，不会留下上一次的状态。 */
  Graph.prototype._layout = function () {
    var cfg = this.cfg;
    var box = fitCanvas(this.canvas, this.host);
    this.w = box.w;
    this.h = box.h;
    this.dpr = box.dpr;

    var isFirst = !this.nodes;
    var oldPos = {};
    if (!isFirst) {
      /* 重排时保留原有坐标，只补新节点。否则窗口一 resize
         整张图就重新炸开一次，用户的视觉记忆全丢。 */
      this.nodes.forEach(function (n) { oldPos[n.id] = { x: n.x, y: n.y }; });
    }

    var nodes = (cfg.nodes || []).map(function (raw, i) {
      var o = oldPos[raw.id];
      /* 初始位置撒在一个小圆上而不是纯随机：
         纯随机会偶尔出现两个节点重合，第一帧斥力爆炸，
         表现成「打开页面图就飞走了」。小圆上均匀分布不会有重合。 */
      var ang = (i / Math.max(1, (cfg.nodes || []).length)) * Math.PI * 2;
      var rad = 30 + (i % 7) * 6;
      return {
        id: raw.id,
        label: raw.label || raw.id,
        short: raw.short || raw.label || raw.id,
        type: raw.type || "kp",
        cid: raw.cid,
        kid: raw.kid,
        kind: raw.kind || "",
        done: !!raw.done,
        stage: raw.stage || 0,
        r: raw.r || (raw.type === "chapter" ? this.opt.nodeRadius.chapter : this.opt.nodeRadius.kp),
        x: o ? o.x : Math.cos(ang) * rad,
        y: o ? o.y : Math.sin(ang) * rad,
        vx: 0, vy: 0,
        fx: null, fy: null
      };
    }, this);

    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });

    var links = (cfg.links || []).map(function (l) {
      return {
        source: byId[l.source],
        target: byId[l.target],
        kind: l.kind || "tree",
        distance: l.distance,
        strength: l.strength
      };
    }).filter(function (l) { return l.source && l.target; });

    this.nodes = nodes;
    this.links = links;

    /* 邻接表，hover 高亮和 BFS 用。
       每次布局重建一次，比每次 hover 现算便宜得多。 */
    this.adj = {};
    nodes.forEach(function (n) { this.adj[n.id] = []; }, this);
    links.forEach(function (l) {
      this.adj[l.source.id].push(l.target.id);
      this.adj[l.target.id].push(l.source.id);
    }, this);

    this.sim = new Sim(nodes, links, this.opt);

    /* 首次进来把视线放在图形重心上。
       不居中会看到图缩在左上角——因为初始坐标是围绕原点撒的，
       而画布坐标原点在左上。 */
    if (isFirst) {
      this.tx = this.w / 2;
      this.ty = this.h / 2;
    }
    this._settled = false;
  };

  Graph.prototype._start = function () {
    var self = this;
    var last = 0;

    function frame(t) {
      self._raf = 0;
      if (self._destroyed) return;

      var delta = last ? (t - last) : 16.7;
      last = t;
      /* delta 钳到 100ms：切回前台时 delta 可能是几分钟，
         不钳的话一帧会跑上千次迭代，直接跳到最终态，看不到过程。 */
      if (delta > 100) delta = 100;

      /* 入场淡入用 CMTween 的时钟还是自己的？
         这里自己算：只有一个不透明度，为它挂一个 Tween 实例不划算，
         而且 Tween 停了 rAF 之后这里还得再起一个，反而更绕。 */
      if (self._fade < 1) {
        self._fade = Math.min(1, self._fade + delta / self.opt.duration);
      }

      /* 冷却后就不必每帧跑物理了。
         收敛后只跑渲染：拖拽、hover 会 reheat 把模拟唤醒。 */
      var alive = false;
      if (!self._settled) {
        /* 一帧跑 1 次而不是跑到收敛：物理模拟和渲染必须帧同步，
           否则节点位置在一个屏幕帧里跳好几次，视觉上是抖的。 */
        alive = self.sim.tick();
        if (!alive) self._settled = true;
      }

      self._render();

      /* 完全静止且淡入完成就停机。
         不停的话页面永远有个 rAF 在转，笔记本风扇会响——
         这是「看起来能用」和「做得干净」的区别。 */
      if (self._settled && self._fade >= 1 && !self.hover && !self._drag) {
        self._raf = 0;
        return;
      }
      self._raf = global.requestAnimationFrame(frame);
    }

    this._raf = global.requestAnimationFrame(frame);
    this._frame = frame;
  };

  /* 唤醒循环。静止态下的任何交互都要先调它，否则画面不会更新。 */
  Graph.prototype._wake = function () {
    if (this._destroyed) return;
    if (this._raf) return;
    this._raf = global.requestAnimationFrame(this._frame);
  };

  /* ---------------------------------------------------------------- 坐标 */

  Graph.prototype._toScreen = function (x, y) {
    return { x: this.tx + x * this.k, y: this.ty + y * this.k };
  };

  Graph.prototype._toWorld = function (sx, sy) {
    return { x: (sx - this.tx) / this.k, y: (sy - this.ty) / this.k };
  };

  /* ---------------------------------------------------------------- 渲染 */

  Graph.prototype._render = function () {
    var ctx = this.ctx;
    var dpr = this.dpr;
    var k = this.k;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    /* 一次矩阵搞定「DPR × 平移 × 缩放」。
       注意所有绘图坐标都要用世界坐标（节点位置），
       不需要自己乘 k——矩阵会处理。 */
    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * this.tx, dpr * this.ty);

    /* 画布外多留 80px 的边：刚从画布外滑进来的节点不该被硬切
       在边线上闪一下。 */
    var pad = 80;
    var view = {
      x0: (-this.tx - pad) / k,
      y0: (-this.ty - pad) / k,
      x1: (this.w - this.tx + pad) / k,
      y1: (this.h - this.ty + pad) / k
    };

    this._drawLinks(ctx, view);
    this._drawNodes(ctx, view);

    /* 标签必须换回屏幕坐标画。
       注意上面两个 _draw 都读 this._focusFn，见 setFocus()。
       如果在缩放矩阵里画，缩小到 0.4 时 12px 的字变成 5px，
       糊成一团黑点，缩放到 2 倍又大得盖住节点。
       标签是「读的」不是「看的」，字号该恒定。 */
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._drawLabels(ctx, view);

    if (this._fade < 1) {
      /* 淡入靠最后盖一层半透明背景色的膜实现。
         比给每个元素单独乘 alpha 便宜，也不会让节点和连线
         出现「各自淡入、层次错乱」的观感。 */
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var bg = cssVar("--bg-elev", "#151a23");
      ctx.globalAlpha = 1 - this._fade;
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, this.w, this.h);
      ctx.globalAlpha = 1;
    }
  };

  /* 连线。三种边用不同视觉：
       tree —— 章 → 节，短、暗，是结构骨架
       seq  —— 节 → 下一节，虚线，表示阅读顺序
       dep  —— 章 → 下一章，暖色，是真正的「先修依赖」
     不分层的话 100+ 条边会织成一张网，什么也看不出来。 */
  Graph.prototype._drawLinks = function (ctx, view) {
    var hot = this._hotSet();
    var base = parseHex(cssVar("--border", "#263041"));
    var warm = parseHex(cssVar("--warn", "#d29922"));
    var accent = parseHex(cssVar("--accent", "#58a6ff"));
    var k = this.k;
    var self = this;

    this.links.forEach(function (l) {
      var a = l.source;
      var b = l.target;
      if (!a || !b) return;

      /* 视口剔除。图谱缩放到 2.6 倍时大半节点都在画布外，
         不剔除就是白画。 */
      if (Math.max(a.x, b.x) < view.x0 || Math.min(a.x, b.x) > view.x1 ||
          Math.max(a.y, b.y) < view.y0 || Math.min(a.y, b.y) > view.y1) return;

      var isHot = hot && (hot[a.id] && hot[b.id]);
      /* 过滤模式下，两端都得在焦点里这条边才该亮。
         只判一端的话，未读节点和已读节点之间会留一片亮线，
         看起来像「亮着的那一半才是重点」，正好反了。 */
      var inFocus = !self._focusFn || (self._focusFn(a) && self._focusFn(b));
      var dim = (hot && !isHot) || !inFocus;

      var col, alpha, width;
      if (l.kind === "dep") {
        col = warm;
        alpha = dim ? 0.06 : 0.4;
        width = 1.6;
      } else if (l.kind === "seq") {
        col = base;
        alpha = dim ? 0.05 : 0.5;
        width = 1;
      } else {
        col = dim ? base : accent;
        alpha = dim ? 0.05 : 0.22;
        width = 1.2;
      }
      if (isHot) alpha = Math.min(1, alpha + 0.5);

      /* 线宽除以 k：不除的话放大 2 倍线也粗 2 倍，
         整张图会变成一团色块。1px 的骨架线在任何缩放下都该是 1px。 */
      ctx.lineWidth = width / k;
      ctx.strokeStyle = rgba(col, alpha);

      if (l.kind === "seq") {
        /* 虚线偏移按屏幕像素折算，保证缩放时虚线的疏密不发生跳变 */
        ctx.setLineDash([4 / k, 4 / k]);
      } else {
        ctx.setLineDash([]);
      }

      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    });
    ctx.setLineDash([]);
  };

  /* 节点。颜色编码「掌握程度」，形状编码「类型」：
       章节点   = 实心圆 + 外圈
       知识点   = 小圆点，已读填实、未读空心
     空心比降低透明度好认：一眼能数出还有多少个没读。 */
  Graph.prototype._drawNodes = function (ctx, view) {
    var hot = this._hotSet();
    var self = this;
    var accent = cssVar("--accent", "#58a6ff");
    var ok = cssVar("--ok", "#3fb950");
    var mute = cssVar("--text-mute", "#6b7a8d");
    var border = cssVar("--border", "#263041");
    var text = cssVar("--text", "#dfe6ef");
    var k = this.k;

    /* 知识点按 kind 上色，四种语义要对得上内容：
       concept=概念（蓝）、syntax=语法（紫）、pitfall=易错点（黄）、
       practice=练习（绿）。这样一眼能看出某章是不是全是坑。 */
    var kindColor = {
      concept: accent,
      syntax: cssVar("--purple", "#bc8cff"),
      pitfall: cssVar("--warn", "#d29922"),
      practice: ok
    };

    this.nodes.forEach(function (n) {
      if (n.x < view.x0 || n.x > view.x1 || n.y < view.y0 || n.y > view.y1) return;

      var isHot = !hot || hot[n.id];
      var isSel = self.selected && self.selected.id === n.id;
      /* 过滤模式：不在焦点里的节点压到 0.07 的不透明度。
         压暗而不是不画——图是空间记忆，节点一旦被删掉，
         用户再看到它时会当成「换了一张图」，得重新找一遍位置。 */
      var inFocus = !self._focusFn || self._focusFn(n);
      var col = n.type === "chapter" ? text : (kindColor[n.kind] || mute);

      ctx.globalAlpha = !inFocus ? 0.07 : (isHot ? 1 : 0.18);

      if (n.type === "chapter") {
        /* 章节点：外圈淡、内圈实。外圈是「有子节点」的视觉暗示 */
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 4 / k, 0, Math.PI * 2);
        ctx.fillStyle = self._hexA(col, 0.12);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = self._hexA(col, 0.9);
        ctx.fill();
      } else if (n.done) {
        /* 已读：填实 */
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
      } else {
        /* 未读：空心。填充背景色再描边，
           比只描边清楚——否则中间的连线会从洞里穿过去，很乱。 */
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = cssVar("--bg-elev", "#151a23");
        ctx.fill();
        ctx.lineWidth = 1.6 / k;
        ctx.strokeStyle = self._hexA(col, 0.75);
        ctx.stroke();
      }

      /* 选中环 */
      if (isSel) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 7 / k, 0, Math.PI * 2);
        ctx.lineWidth = 2 / k;
        ctx.strokeStyle = accent;
        ctx.stroke();
      }

      /* 命中区域比视觉半径大一圈：
         5.5px 的点用鼠标点很难瞄，但把视觉做大会糊掉整张图。
         分开处理，鼠标手感是准的、画面是干净的。 */
      if (isSel || n.type === "chapter") {
        ctx.beginPath();
        ctx.arc(n.x, n.y, Math.max(n.r, 9 / k), 0, Math.PI * 2);
        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";
        ctx.stroke();
      }
    });
    ctx.globalAlpha = 1;
  };

  /* 标签。屏幕空间绘制，字号恒定。
     缩放分级是为了解决 60 个标签必然互相压的问题：
     缩得小的时候只留章名（12 个），放大到 0.62 以上再显示全部。
     这不是「少画点省性能」，是「缩小时全画等于全看不清」。 */
  Graph.prototype._drawLabels = function (ctx, view) {
    var showAll = this.k >= this.opt.labelMinZoom;
    var hot = this._hotSet();
    var accent = cssVar("--accent", "#58a6ff");
    var text = cssVar("--text", "#dfe6ef");
    var mute = cssVar("--text-mute", "#6b7a8d");
    var bg = cssVar("--bg-elev", "#151a23");
    var k = this.k;
    var self = this;

    /* 先章后节：章名压在上面比被小节名盖住合理 */
    var ordered = this.nodes.slice().sort(function (a, b) {
      if (a.type === b.type) return 0;
      return a.type === "chapter" ? -1 : 1;
    });

    ordered.forEach(function (n) {
      if (n.x < view.x0 || n.x > view.x1 || n.y < view.y0 || n.y > view.y1) return;
      if (!showAll && n.type !== "chapter") return;

      var p = self._toScreen(n.x, n.y);
      var isHot = !hot || hot[n.id];
      var isSel = self.selected && self.selected.id === n.id;

      var fontSize = n.type === "chapter" ? 12 : 10.5;
      ctx.font = (isSel ? "600 " : "") + fontSize + "px " + cssVar("--sans", "sans-serif");

      var label = (isSel || isHot) && n.type === "kp" ? n.short : n.short;
      var w = ctx.measureText(label).width;
      var px = p.x + n.r * k + 6;
      var py = p.y + 4;

      /* 靠近右边界时把标签翻到左边。
         不翻的话右侧节点的标题会被画布边缘硬切掉半截。 */
      if (px + w > self.w - 6) px = p.x - n.r * k - 6 - w;

      /* 过滤模式下，不在焦点里的小节标签干脆不画：
         压暗到 12% 只剩一团灰影，比不画更脏。章名留着——
         它是定位用的地标，「第 7 章在这」本身就是有用信息。 */
      var inFocus = !self._focusFn || self._focusFn(n);
      if (!inFocus && n.type !== "chapter" && !isSel) return;

      ctx.globalAlpha = !inFocus ? 0.12 : (isHot ? 1 : 0.15);

      /* 标签底衬：图上一堆连线，纯文字贴上去读不出来。
         用半透明矩形垫一层，比给文字加描边便宜也更干净。 */
      if (isSel || isHot) {
        ctx.fillStyle = self._hexA(bg, 0.72);
        ctx.fillRect(px - 2, py - fontSize, w + 4, fontSize + 4);
      }

      ctx.fillStyle = isSel ? accent : (n.type === "chapter" ? text : mute);
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(label, px, py);
    });
    ctx.globalAlpha = 1;
  };

  Graph.prototype._hexA = function (hex, a) {
    var c = parseHex(hex);
    return rgba(c, a);
  };

  /* hover / 选中时该「点亮」哪些节点：自己 + 一层邻居。
     BFS 只走一层——走两层的话大半个图都亮着，高亮就没有意义了。 */
  Graph.prototype._hotSet = function () {
    var src = this.hover || this.selected;
    if (!src) return null;
    var set = {};
    set[src.id] = true;
    var nb = this.adj[src.id] || [];
    for (var i = 0; i < nb.length; i++) set[nb[i]] = true;
    return set;
  };

  /* ---------------------------------------------------------------- 交互 */

  Graph.prototype._hitTest = function (sx, sy) {
    var w = this._toWorld(sx, sy);
    var best = null;
    var bestD = Infinity;
    var k = this.k;

    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i];
      var dx = n.x - w.x;
      var dy = n.y - w.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      /* 命中半径随缩放折算到世界坐标，保证屏幕上永远有 10px 的容差。
         固定世界半径的话，缩小到 0.35 时几乎点不中任何东西。 */
      var pick = Math.max(n.r, 10 / k);
      if (d < pick && d < bestD) {
        bestD = d;
        best = n;
      }
    }
    return best;
  };

  Graph.prototype._bindEvents = function () {
    var self = this;
    var canvas = this.canvas;

    function local(e) {
      var r = canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    function onDown(e) {
      var p = local(e);
      var hit = self._hitTest(p.x, p.y);
      self._downAt = { x: p.x, y: p.y, node: hit };

      if (hit) {
        /* 记录按下时的世界坐标偏移。不记的话第一次移动节点会
           瞬移到指针正下方，看起来像「点了一下就跳走了」。 */
        var w = self._toWorld(p.x, p.y);
        self._drag = { node: hit, ox: hit.x - w.x, oy: hit.y - w.y, moved: 0 };
      } else {
        self._pan = { x: p.x, y: p.y, tx: self.tx, ty: self.ty };
        canvas.classList.add("is-panning");
      }
      canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId);
    }

    function onMove(e) {
      var p = local(e);

      if (self._drag) {
        var w = self._toWorld(p.x, p.y);
        var n = self._drag.node;
        n.fx = w.x + self._drag.ox;
        n.fy = w.y + self._drag.oy;
        self._drag.moved += Math.abs(p.x - (self._downAt ? self._downAt.x : p.x));
        self.sim.reheat(0.5);
        self._settled = false;
        self._wake();
        return;
      }

      if (self._pan) {
        self.tx = self._pan.tx + (p.x - self._pan.x);
        self.ty = self._pan.ty + (p.y - self._pan.y);
        self._wake();
        return;
      }

      var hit = self._hitTest(p.x, p.y);

      /* 命中检测每次 mousemove 都跑一遍，但只有结果变化才重绘。
         60 个节点线性扫不算贵，贵的是重绘整张图。 */
      if (hit !== self.hover) {
        self.hover = hit;
        self._updateTip(hit, p);
        canvas.style.cursor = hit ? "pointer" : "grab";
        self._wake();
      } else if (hit) {
        self._updateTip(hit, p);
      }
    }

    function onUp(e) {
      var p = local(e);

      if (self._drag) {
        var n = self._drag.node;
        var moved = self._drag.moved;
        n.fx = null;
        n.fy = null;
        self._drag = null;
        self.sim.reheat(0.35);

        /* 拖动和点击的区分：位移小于 4px 才算点击。
           不判断的话，手抖 1px 也会触发跳页，用户会以为页面乱跳。 */
        if (moved < 4) {
          self._select(n);
        }
        self._wake();
        return;
      }

      if (self._pan) {
        var dist = self._downAt ? Math.abs(p.x - self._downAt.x) + Math.abs(p.y - self._downAt.y) : 0;
        self._pan = null;
        canvas.classList.remove("is-panning");
        /* 空白处轻点 = 取消选中。这是拖动画布的副产品，
           用户点空白想要「取消选中」是很自然的期待。 */
        if (dist < 4 && self.selected) {
          self.selected = null;
          self._emitSelect(null);
          self._wake();
        }
        return;
      }
    }

    function onLeave() {
      if (self.hover) {
        self.hover = null;
        self._updateTip(null, null);
        self._wake();
      }
    }

    function onWheel(e) {
      /* 阻止默认滚动：图谱在页面中间，不拦的话滚轮会滚页面，
         用户以为自己放大了，实际是页面在动。 */
      e.preventDefault();

      var p = local(e);
      var before = self._toWorld(p.x, p.y);
      var factor = Math.exp(-e.deltaY * self.opt.wheelStep);
      var next = clamp(self.k * factor, self.opt.zoomMin, self.opt.zoomMax);
      if (next === self.k) return;

      self.k = next;
      /* 缩放锚点必须是光标位置：算出缩放前光标下的世界坐标，
         缩放后把平移调回去，让那个点仍然在光标下面。
         不锚定的话缩放总是围绕左上角，找一个节点要来回拖三遍。 */
      self.tx = p.x - before.x * self.k;
      self.ty = p.y - before.y * self.k;
      self._wake();
    }

    /* 双击 = 进入，单击 = 选中。
       两个动作必须分开：单击是「我想看看它连着谁」，成本很低；
       如果单击就跳页，用户就再也不敢点节点了，这张图也就废了。 */
    function onDblClick(e) {
      var p = local(e);
      var hit = self._hitTest(p.x, p.y);
      if (hit) self._activate(hit);
    }

    /* 键盘：Tab 聚焦后左右键在节点间移动，Enter 进章节。
       不做的的话这个组件对纯键盘用户完全不可用。 */
    function onKey(e) {
      if (!self.nodes.length) return;
      var idx = self.selected ? self.nodes.indexOf(self.selected) : -1;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        idx = (idx + 1) % self.nodes.length;
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        idx = idx <= 0 ? self.nodes.length - 1 : idx - 1;
      } else if (e.key === "Enter" || e.key === " ") {
        if (self.selected) {
          e.preventDefault();
          self._activate(self.selected);
        }
        return;
      } else if (e.key === "Escape") {
        self.selected = null;
        self._emitSelect(null);
        self._wake();
        return;
      } else {
        return;
      }
      e.preventDefault();
      self._select(self.nodes[idx]);
      self._wake();
    }

    this._h = { down: onDown, move: onMove, up: onUp, leave: onLeave, wheel: onWheel, key: onKey, dbl: onDblClick };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("keydown", onKey);
    canvas.addEventListener("dblclick", onDblClick);

    /* 容器尺寸变化重排。用 ResizeObserver 而不是 window.resize：
       侧边栏折叠不会触发 window 的事件。 */
    if (global.ResizeObserver) {
      this._ro = new ResizeObserver(function () {
        if (self._destroyed) return;
        if (self._roRaf) return;
        self._roRaf = requestAnimationFrame(function () {
          self._roRaf = 0;
          var w = self.host.clientWidth;
          if (Math.abs(w - (self.w || 0)) < 1) return;
          self._layout();
          self.sim.reheat(0.6);
          self._wake();
        });
      });
      this._ro.observe(this.host);
    }
  };

  Graph.prototype._updateTip = function (n, p) {
    if (!this.tip) return;
    if (!n) {
      this.tip.classList.remove("show");
      return;
    }
    var kindName = { concept: "概念", syntax: "语法", pitfall: "易错点", practice: "练习" };
    var html = '<div class="cm-gtip-title">' + this._esc(n.label) + "</div>";
    var meta;
    if (n.type === "chapter") {
      meta = "第 " + n.cid + " 章";
    } else {
      meta = "第 " + n.cid + " 章 · " + (kindName[n.kind] || "知识点");
    }
    html += '<div class="cm-gtip-meta">' + this._esc(meta) +
      (n.type === "kp" ? (n.done ? ' · <b class="is-done">已读</b>' : ' · <b class="is-todo">未读</b>') : "") +
      "</div>";
    this.tip.innerHTML = html;
    this.tip.classList.add("show");

    /* 定位：光标右上，靠边翻转。
       用 host 的矩形做参照，因为 tip 是 host 的子元素。 */
    var hr = this.host.getBoundingClientRect();
    var tr = this.tip.getBoundingClientRect();
    var x = p.x + 14;
    var y = p.y - tr.height - 10;
    if (x + tr.width > hr.width - 4) x = p.x - tr.width - 14;
    if (y < 4) y = p.y + 18;
    this.tip.style.left = x + "px";
    this.tip.style.top = y + "px";
  };

  Graph.prototype._esc = function (s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  Graph.prototype._select = function (n) {
    this.selected = n;
    this._emitSelect(n);
    /* 选中后把视线移过去：节点可能在画布外，尤其是缩放过之后 */
    this._wake();
  };

  /* 事件回调用 CustomEvent 抛出，业务层只监听、不管内部。
     好处是图谱引擎完全不认识「章节路由」这类业务概念。 */
  Graph.prototype._emitSelect = function (n) {
    if (!this.host || !this.host.dispatchEvent) return;
    var ev;
    try {
      ev = new CustomEvent("cm:graph-select", {
        bubbles: true,
        detail: n ? { id: n.id, type: n.type, cid: n.cid, kid: n.kid, label: n.label } : null
      });
    } catch (e) {
      ev = document.createEvent("CustomEvent");
      ev.initCustomEvent("cm:graph-select", true, false, n ? { id: n.id, type: n.type, cid: n.cid, kid: n.kid, label: n.label } : null);
    }
    this.host.dispatchEvent(ev);
  };

  Graph.prototype._activate = function (n) {
    if (typeof this.cfg.onActivate === "function") this.cfg.onActivate(n);
  };

  /* ---------------------------------------------------------------- API */

  Graph.prototype.focus = function (id) {
    var n = null;
    for (var i = 0; i < this.nodes.length; i++) {
      if (this.nodes[i].id === id) { n = this.nodes[i]; break; }
    }
    if (!n) return this;
    this.selected = n;
    /* 把节点移到视口中央，并确保缩放到能看清标签的程度 */
    this.k = Math.max(this.k, 1);
    this.tx = this.w / 2 - n.x * this.k;
    this.ty = this.h / 2 - n.y * this.k;
    this._wake();
    return this;
  };

  /* 过滤 / 聚焦模式。
     传一个谓词进来，返回 false 的节点会被压暗（不是移除）。
     引擎只认「这个节点算不算在焦点里」，不认「已读」「未读」这种
     业务概念——那是页面的事。传 null 退出过滤模式。 */
  Graph.prototype.setFocus = function (fn) {
    this._focusFn = typeof fn === "function" ? fn : null;
    this._wake();
    return this;
  };

  /* 重置视角 */
  Graph.prototype.reset = function () {
    /* 先算所有节点的包围盒，再按包围盒算缩放和中心。
       固定写 k=1 的话，图形收敛后的尺寸可能比画布大得多，
       重置完还是看不全。 */
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    this.nodes.forEach(function (n) {
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
    });
    if (!isFinite(minX)) return this;

    var pad = 60;
    var bw = Math.max(1, maxX - minX);
    var bh = Math.max(1, maxY - minY);
    var k = Math.min((this.w - pad * 2) / bw, (this.h - pad * 2) / bh);
    this.k = clamp(k, this.opt.zoomMin, this.opt.zoomMax);
    this.tx = this.w / 2 - ((minX + maxX) / 2) * this.k;
    this.ty = this.h / 2 - ((minY + maxY) / 2) * this.k;
    this._wake();
    return this;
  };

  Graph.prototype.update = function (cfg) {
    if (this._destroyed) return this;
    this.cfg = cfg || {};
    this.selected = null;
    this.hover = null;
    this._layout();
    this.sim.reheat(1);
    this._wake();
    return this;
  };

  Graph.prototype.destroy = function () {
    this._destroyed = true;
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this._roRaf) cancelAnimationFrame(this._roRaf);
    if (this._ro) this._ro.disconnect();
    var c = this.canvas;
    if (c && this._h) {
      c.removeEventListener("pointerdown", this._h.down);
      c.removeEventListener("pointermove", this._h.move);
      c.removeEventListener("pointerup", this._h.up);
      c.removeEventListener("pointercancel", this._h.up);
      c.removeEventListener("pointerleave", this._h.leave);
      c.removeEventListener("wheel", this._h.wheel);
      c.removeEventListener("keydown", this._h.key);
      c.removeEventListener("dblclick", this._h.dbl);
    }
    return this;
  };

  var CMGraph = {
    create: function (host, cfg) {
      if (!host) return null;
      return new Graph(host, cfg);
    },
    /* 挂到元素上，重复挂先销毁旧的 */
    mount: function (host, cfg) {
      if (!host) return null;
      if (host._cmGraph) host._cmGraph.destroy();
      host._cmGraph = new Graph(host, cfg);
      return host._cmGraph;
    },
    _internal: {
      Sim: Sim,
      clamp: clamp,
      parseHex: parseHex,
      rgba: rgba
    }
  };

  global.CMGraph = CMGraph;
})(typeof window !== "undefined" ? window : this);
