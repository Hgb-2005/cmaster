/* ============================================================
   CMaster —— 补间动画引擎
   ------------------------------------------------------------
   为什么要自己写一个，而不是继续用 CSS transition：

   1. CSS transition 只能动「能写成属性值」的东西。图表要动的是
      「42 个点的 y 坐标」「环形扇区的起止角」这类中间量，CSS 无解。
   2. 动画多了以后，CSS 那套 `transition-delay` 排交错要一个个写死延迟，
      改一个节奏就得改十条规则。这里 `stagger()` 一行解决。
   3. 需要「暂停 / 减速 / 跳到最后」这类控制。CSS 只有
      `animation-play-state`，粒度太粗，而且拿不到进度值。

   所以这里只做一件事：把「从 A 平滑走到 B」抽象成可组合的对象，
   Canvas 绘制、DOM 属性、数值滚动，全都走同一个循环。

   ------------------------------------------------------------
   三条自我约束（和 motion.css 一致）：

   · 一个 rAF 循环。不管页面上有多少个动画，都只有一次
     requestAnimationFrame 在跑，空闲时自动停机，不停着烧电。
   · 缓动只影响「进度 → 比例」的映射，不参与时间调度，
     所以换缓动不会让动画时长变。
   · 用户开了「减少动态效果」，duration 直接归零但**回调照常触发**。
     归零不是跳过——业务代码依赖 onComplete 收尾，跳过会留下永远
     停在初始态的图表。
   ============================================================ */

(function (global) {
  "use strict";

  var PI = Math.PI;
  var HALF_PI = PI / 2;

  /* ============================================================
     一、缓动函数
     ------------------------------------------------------------
     签名统一为 (t) => eased，t ∈ [0,1]，返回值**可以**越界
     （back / elastic 那几条会冲过头再回来，这正是它们的价值）。

     命名沿用 Robert Penner 那套，方便对照：
     easeIn 起步慢、easeOut 收尾慢、easeInOut 两头慢。
     ============================================================ */

  var Easing = {
    linear: function (t) { return t; },

    /* --- 二次 --- */
    quadIn: function (t) { return t * t; },
    quadOut: function (t) { return t * (2 - t); },
    quadInOut: function (t) {
      return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    },

    /* --- 三次 --- */
    cubicIn: function (t) { return t * t * t; },
    cubicOut: function (t) {
      var p = t - 1;
      return p * p * p + 1;
    },
    cubicInOut: function (t) {
      return t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1;
    },

    /* --- 四次 --- */
    quartIn: function (t) { return t * t * t * t; },
    quartOut: function (t) {
      var p = t - 1;
      return 1 - p * p * p * p;
    },
    quartInOut: function (t) {
      return t < 0.5
        ? 8 * t * t * t * t
        : 1 - 8 * (t - 1) * (t - 1) * (t - 1) * (t - 1);
    },

    /* --- 五次 ---
       图表里「生长」类动画用 quintOut 最顺手：
       前段冲得快，尾巴拖得长，看着像有质量。 */
    quintIn: function (t) { return t * t * t * t * t; },
    quintOut: function (t) {
      var p = t - 1;
      return 1 + p * p * p * p * p;
    },
    quintInOut: function (t) {
      if (t < 0.5) return 16 * t * t * t * t * t;
      var p = 2 * t - 2;
      return 1 + p * p * p * p * p / 2;
    },

    /* --- 正弦 ---
       最容易「看不出来在动」的一组，适合位移很小的情况。 */
    sineIn: function (t) { return 1 - Math.cos(t * HALF_PI); },
    sineOut: function (t) { return Math.sin(t * HALF_PI); },
    sineInOut: function (t) { return 0.5 * (1 - Math.cos(PI * t)); },

    /* --- 指数 ---
       expo 的起步极慢，用来做「从静止突然启动」很有力。
       注意 t=0 时要手动兜住：Math.pow(2, -10) 不是 0，差一点点，
       在像素坐标里看不出来，但归一化进度会露出 0.0009 的脏值。 */
    expoIn: function (t) { return t === 0 ? 0 : Math.pow(2, 10 * (t - 1)); },
    expoOut: function (t) { return t === 1 ? 1 : 1 - Math.pow(2, -10 * t); },
    expoInOut: function (t) {
      if (t === 0) return 0;
      if (t === 1) return 1;
      if (t < 0.5) return 0.5 * Math.pow(2, 20 * t - 10);
      return 1 - 0.5 * Math.pow(2, -20 * t + 10);
    },

    /* --- 圆弧 --- */
    circIn: function (t) { return 1 - Math.sqrt(1 - t * t); },
    circOut: function (t) { return Math.sqrt(1 - (t - 1) * (t - 1)); },
    circInOut: function (t) {
      return t < 0.5
        ? 0.5 * (1 - Math.sqrt(1 - 4 * t * t))
        : 0.5 * (Math.sqrt(1 - (2 * t - 2) * (2 * t - 2)) + 1);
    },

    /* --- 回弹 ---
       overshoot 是冲过头的比例：1.70158 大致等于「多走 10%」。
       数字滚动、徽章入场的「啪」一下全靠它。 */
    backIn: function (t) {
      var s = 1.70158;
      return t * t * ((s + 1) * t - s);
    },
    backOut: function (t) {
      var s = 1.70158;
      var p = t - 1;
      return p * p * ((s + 1) * p + s) + 1;
    },
    backInOut: function (t) {
      var s = 1.70158 * 1.525;
      if (t < 0.5) {
        var q = 2 * t;
        return 0.5 * (q * q * ((s + 1) * q - s));
      }
      var p = 2 * t - 2;
      return 0.5 * (p * p * ((s + 1) * p + s) + 2);
    },

    /* --- 弹性 ---
       振幅衰减的正弦。period 控制「抖几下」，
       0.3 大约抖三下，数值越小抖得越久。 */
    elasticOut: function (t) {
      if (t === 0 || t === 1) return t;
      var period = 0.3;
      var s = period / 4;
      return Math.pow(2, -10 * t) * Math.sin((t - s) * (2 * PI) / period) + 1;
    },
    elasticIn: function (t) {
      if (t === 0 || t === 1) return t;
      var period = 0.3;
      var s = period / 4;
      var p = t - 1;
      return -Math.pow(2, 10 * p) * Math.sin((p - s) * (2 * PI) / period);
    },

    /* --- 弹跳 ---
       分段抛物线，模拟落地。注意它**不越界**，最高点始终 ≤ 1。 */
    bounceOut: function (t) {
      if (t < 1 / 2.75) {
        return 7.5625 * t * t;
      } else if (t < 2 / 2.75) {
        var a = t - 1.5 / 2.75;
        return 7.5625 * a * a + 0.75;
      } else if (t < 2.5 / 2.75) {
        var b = t - 2.25 / 2.75;
        return 7.5625 * b * b + 0.9375;
      }
      var c = t - 2.625 / 2.75;
      return 7.5625 * c * c + 0.984375;
    },
    bounceIn: function (t) { return 1 - Easing.bounceOut(1 - t); },
    bounceInOut: function (t) {
      return t < 0.5
        ? (1 - Easing.bounceOut(1 - 2 * t)) * 0.5
        : Easing.bounceOut(2 * t - 1) * 0.5 + 0.5;
    }
  };

  /* ============================================================
     二、插值器
     ------------------------------------------------------------
     一个值可能长什么样：数字、数组、普通对象、颜色字符串。
     这里先按类型挑一个插值函数，再统一成 (a, b, t) => 中间值。
     ============================================================ */

  /* #rgb / #rrggbb / rgb() / rgba() → [r,g,b,a]
     解析失败返回 null，交给调用方降级成「到点切换」。 */
  var COLOR_RE = /^#([0-9a-f]{3,8})$/i;
  var RGBA_RE = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i;

  function parseColor(str) {
    if (typeof str !== "string") return null;
    var s = str.trim();

    var m = RGBA_RE.exec(s);
    if (m) {
      return [
        parseFloat(m[1]),
        parseFloat(m[2]),
        parseFloat(m[3]),
        m[4] == null ? 1 : parseFloat(m[4])
      ];
    }

    m = COLOR_RE.exec(s);
    if (m) {
      var hex = m[1];
      if (hex.length === 3) {
        return [
          parseInt(hex[0] + hex[0], 16),
          parseInt(hex[1] + hex[1], 16),
          parseInt(hex[2] + hex[2], 16),
          1
        ];
      }
      if (hex.length === 6) {
        return [
          parseInt(hex.slice(0, 2), 16),
          parseInt(hex.slice(2, 4), 16),
          parseInt(hex.slice(4, 6), 16),
          1
        ];
      }
      if (hex.length === 8) {
        return [
          parseInt(hex.slice(0, 2), 16),
          parseInt(hex.slice(2, 4), 16),
          parseInt(hex.slice(4, 6), 16),
          parseInt(hex.slice(6, 8), 16) / 255
        ];
      }
    }
    return null;
  }

  function colorToCss(c) {
    var r = Math.round(c[0]);
    var g = Math.round(c[1]);
    var b = Math.round(c[2]);
    var a = c[3];
    if (a >= 1) return "rgb(" + r + "," + g + "," + b + ")";
    return "rgba(" + r + "," + g + "," + b + "," + Math.round(a * 1000) / 1000 + ")";
  }

  /* 深拷贝一层就够：我们的目标值都是扁平结构（数字/数组/一层对象），
     真嵌套对象在动画里也没有意义——没人需要同时插值两棵不同的树。 */
  function cloneValue(v) {
    if (v == null || typeof v !== "object") return v;
    if (Array.isArray(v)) return v.slice();
    var out = {};
    for (var k in v) {
      if (Object.prototype.hasOwnProperty.call(v, k)) out[k] = v[k];
    }
    return out;
  }

  function isColorish(v) {
    return typeof v === "string" && (v.charAt(0) === "#" || /^rgba?\(/i.test(v));
  }

  /* 数值插值。注意这里**不做** clamp：
     back / elastic 需要冲过头，clamp 了就没那个手感了。 */
  function lerpNum(from, to, t) {
    return from + (to - from) * t;
  }

  /* 混合两个值，类型按 to 决定。返回新对象，绝不改 from/to —— 调用方
     常常把同一个配置对象复用给多个 Tween，改坏了会串味。 */
  function mix(from, to, t) {
    if (typeof to === "number") {
      var a = typeof from === "number" ? from : 0;
      return lerpNum(a, to, t);
    }

    if (typeof to === "string") {
      if (isColorish(to) && isColorish(from)) {
        var c1 = parseColor(from);
        var c2 = parseColor(to);
        if (c1 && c2) {
          return colorToCss([
            lerpNum(c1[0], c2[0], t),
            lerpNum(c1[1], c2[1], t),
            lerpNum(c1[2], c2[2], t),
            lerpNum(c1[3], c2[3], t)
          ]);
        }
      }
      /* 非颜色字符串（比如 "translateX(10px)"）没法插值，
         只能在过半时整体切换。这是 CSS 动画本身就有的限制，
         不是这里的偷懒。 */
      return t < 0.5 ? from : to;
    }

    if (Array.isArray(to)) {
      var src = Array.isArray(from) ? from : [];
      var out = new Array(to.length);
      for (var i = 0; i < to.length; i++) {
        out[i] = mix(src[i], to[i], t);
      }
      return out;
    }

    if (to && typeof to === "object") {
      var base = (from && typeof from === "object" && !Array.isArray(from)) ? from : {};
      var res = {};
      for (var key in to) {
        if (!Object.prototype.hasOwnProperty.call(to, key)) continue;
        res[key] = mix(base[key], to[key], t);
      }
      return res;
    }

    return t < 0.5 ? from : to;
  }

  /* ============================================================
     三、全局时钟
     ------------------------------------------------------------
     全站只有这一个 rAF 循环。所有 Tween 挂到 _actives 里，
     没有活跃动画时循环自动停掉。

     为什么不用 setTimeout/setInterval：
     · setInterval 16ms 和屏幕刷新不同步，长期跑会漂，画面会顿。
     · 后台标签页里 rAF 会被浏览器降到 1Hz 甚至暂停，这正是我们想要的
       （省电、不烧 CPU）；setInterval 不会。
     ============================================================ */

  var _actives = [];
  var _rafId = 0;
  var _lastTime = 0;
  var _paused = false;
  var _speed = 1;
  var _reduced = false;

  /* 系统级「减少动态效果」偏好。这里读一次之后要监听变化——
     用户可能中途打开这个开关，不能只读启动时的值。 */
  function readReducedMotion() {
    if (!global.matchMedia) return false;
    try {
      return global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  _reduced = readReducedMotion();

  if (global.matchMedia) {
    try {
      var mq = global.matchMedia("(prefers-reduced-motion: reduce)");
      var onChange = function (e) { _reduced = e.matches; };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    } catch (e2) { /* 老浏览器不支持监听，用启动时那次的值 */ }
  }

  function now() {
    return (global.performance && global.performance.now)
      ? global.performance.now()
      : Date.now();
  }

  function tick(time) {
    _rafId = 0;

    /* 第一帧没有 _lastTime，用当前时间垫上。
       否则会算出「距 1970 年过了 56 年」的巨型 delta，
       所有动画在第一帧直接结束。 */
    var delta = _lastTime ? (time - _lastTime) : 16.7;
    _lastTime = time;

    /* 切回前台时 delta 可能是几分钟。
       钳到 100ms：宁可让动画「跳一下」，也不能让它一帧走完。 */
    if (delta > 100) delta = 100;
    if (delta < 0) delta = 0;

    var scaled = delta * _speed;

    /* 倒着遍历：onComplete 里常常会启动新动画（_actives.push），
       正着遍历会让新加的在同一帧就跑，节奏错乱。 */
    for (var i = _actives.length - 1; i >= 0; i--) {
      var tw = _actives[i];
      if (tw._done) {
        _actives.splice(i, 1);
        continue;
      }
      tw._step(scaled);
      if (tw._done) _actives.splice(i, 1);
    }

    if (_actives.length) {
      _rafId = global.requestAnimationFrame(tick);
    } else {
      _lastTime = 0;
    }
  }

  function ensureRunning() {
    if (_rafId || _paused) return;
    _lastTime = 0;
    _rafId = global.requestAnimationFrame(tick);
  }

  /* ============================================================
     四、Tween
     ------------------------------------------------------------
     用法：
       Tween.to(obj, 600, { x: 100, opacity: 0.4 }, { easing: "cubicOut" })
     直接改 obj 本身。图表里 obj 常常是「这一帧要画的中间状态」，
     原地改比返回新对象省事得多。

     也有 Tween.value(0, 100, 600, fn)，只关心数字流。
     ============================================================ */

  function Tween(target, duration, props, opts) {
    opts = opts || {};
    this.target = target || {};
    this.duration = Math.max(0, duration || 0);
    this.delay = opts.delay || 0;
    this.easing = resolveEasing(opts.easing);
    this.repeat = opts.repeat || 0;      // 额外重复几次（0 = 不重复）
    this.yoyo = !!opts.yoyo;             // 来回走
    this.onUpdate = opts.onUpdate || null;
    this.onComplete = opts.onComplete || null;
    this.onStart = opts.onStart || null;

    this._from = {};
    this._to = props || {};
    this._elapsed = -this.delay;
    this._cycle = 0;
    this._forward = true;
    this._done = false;
    this._started = false;
    this._backup = null;   // 需要恢复「起始值」时用（yoyo 反向）

    /* 减少动态效果：时长归零，但回调照常跑。
       见文件头第三条约束。 */
    if (_reduced) this.duration = 0;
  }

  Tween.prototype._step = function (delta) {
    if (this._done) return;

    this._elapsed += delta;

    // 延迟阶段：还没开始，但也不用做什么
    if (this._elapsed < 0) return;

    if (!this._started) {
      this._started = true;
      // 起始值在**真正开始**那一刻才抓。提前抓的话，
      // 如果目标对象在延迟期间被别处改了，动画会从旧值跳回来。
      for (var k in this._to) {
        if (!Object.prototype.hasOwnProperty.call(this._to, k)) continue;
        this._from[k] = cloneValue(this.target[k]);
      }
      if (this.onStart) this.onStart(this.target);
    }

    var raw = this.duration === 0
      ? 1
      : Math.min(1, this._elapsed / this.duration);

    var t = this._forward ? raw : 1 - raw;
    var e = this.easing(t);

    for (var key in this._to) {
      if (!Object.prototype.hasOwnProperty.call(this._to, key)) continue;
      var from = this._forward ? this._from[key] : this._to[key];
      var to = this._forward ? this._to[key] : this._from[key];
      this.target[key] = mix(from, to, e);
    }

    if (this.onUpdate) this.onUpdate(this.target, raw);

    if (raw >= 1) {
      /* yoyo 的去程走完了：掉头，从 0 重新计时往回走。
         注意此时**不判断 repeat**，因为一个完整往返才算一轮，
         去程结束就扣次数的话，「repeat=1」会变成只去不回。 */
      if (this.yoyo && this._forward) {
        this._forward = false;
        this._elapsed = 0;
        return;
      }

      if (this.repeat > 0) {
        this.repeat--;
        this._elapsed = 0;
        this._forward = true;
        /* 回到起点再播一遍。这一步会「跳」一下，但它就是 repeat 的
           标准语义，调用方如果想要无缝循环应该用 yoyo。 */
        for (var k3 in this._from) {
          if (!Object.prototype.hasOwnProperty.call(this._from, k3)) continue;
          this.target[k3] = cloneValue(this._from[k3]);
        }
        return;
      }

      this._finish();
    }
  };

  Tween.prototype._finish = function () {
    if (this._done) return;
    this._done = true;

    /* 终值按方向决定，不能一律写 _to：
       yoyo 往返结束后停在**起点**，这时写 _to 会让图形永远停在
       终态，下一次重绘看到的是上一次动画的最后一帧。 */
    var finals = this._forward ? this._to : this._from;
    for (var k in this._to) {
      if (!Object.prototype.hasOwnProperty.call(this._to, k)) continue;
      // 硬写终值：浮点误差会让最后停在 99.9997，
      // 图表上就是「差一个像素没对齐」，很显眼
      this.target[k] = cloneValue(finals[k]);
    }
    if (this.onUpdate) this.onUpdate(this.target, 1);
    if (this.onComplete) this.onComplete(this.target);
  };

  /* 立刻跳到终点，不触发 onUpdate 的中间帧。
     页面切换、元素被移除时用得上。 */
  Tween.prototype.finish = function () {
    if (this._done) return this;
    this._elapsed = this.duration;
    this._forward = true;
    if (!this._started) {
      this._started = true;
      for (var k in this._to) {
        if (!Object.prototype.hasOwnProperty.call(this._to, k)) continue;
        this._from[k] = cloneValue(this.target[k]);
      }
    }
    this._finish();
    return this;
  };

  /* 中途停下，停在当前值。 */
  Tween.prototype.stop = function () {
    this._done = true;
    return this;
  };

  Tween.prototype.play = function () {
    if (this._done) return this;
    if (_actives.indexOf(this) < 0) _actives.push(this);
    ensureRunning();
    return this;
  };

  function resolveEasing(easing) {
    if (typeof easing === "function") return easing;
    if (typeof easing === "string" && Easing[easing]) return Easing[easing];
    // 默认用 cubicOut：绝大多数 UI 动画的「正确答案」都是它
    return Easing.cubicOut;
  }

  /* ============================================================
     五、Timeline
     ------------------------------------------------------------
     组合多个 Tween。两种模式：
       .then(t)  上一个完了再开始（串行）
       .and(t)   和上一个同时开始（并行）

     为什么不用 Promise 串：Promise 的 then 里有微任务调度，
     一帧内能连跳好几步，画面会「瞬移」。这里用累计时间轴，
     每一步的开始时间在添加时就定死，节奏可预测。
     ============================================================ */

  function Timeline() {
    this._steps = [];
    this._cursor = 0;      // 当前串行位置的时间偏移
    this._lastStart = 0;   // 上一个 then/and 的开始时间
    this._done = false;
  }

  /* 所有 Tween 都是在同一个时刻 play 的，各自独立计时。
     所以「串行」的实现很朴素：把后一个的 delay 设成前一个的结束时间。
     不需要真的去监听 onComplete 再启动——那样每步都会有
     一帧的调度缝隙，串几个就有肉眼可见的卡顿。 */
  Timeline.prototype.then = function (tween) {
    this._lastStart = this._cursor;
    tween.delay = this._lastStart;
    this._cursor = this._lastStart + tween.duration;
    this._steps.push(tween);
    return this;
  };

  Timeline.prototype.and = function (tween) {
    tween.delay = this._lastStart;
    var end = this._lastStart + tween.duration;
    if (end > this._cursor) this._cursor = end;
    this._steps.push(tween);
    return this;
  };

  /* 在时间轴上留一段空白。 */
  Timeline.prototype.wait = function (ms) {
    this._cursor += ms;
    this._lastStart = this._cursor;
    return this;
  };

  Timeline.prototype.play = function () {
    var self = this;
    this._steps.forEach(function (tw, i) {
      tw.onComplete = (function (orig) {
        return function (target) {
          if (orig) orig(target);
          if (i === self._steps.length - 1) self._done = true;
        };
      })(tw.onComplete);
      tw.play();
    });
    return this;
  };

  Timeline.prototype.stop = function () {
    this._steps.forEach(function (tw) { tw.stop(); });
    this._done = true;
    return this;
  };

  Timeline.prototype.finish = function () {
    this._steps.forEach(function (tw) { tw.finish(); });
    this._done = true;
    return this;
  };

  /* ============================================================
     六、公共 API
     ============================================================ */

  /* 最常用的一条：改 target 的属性。 */
  function to(target, duration, props, opts) {
    return new Tween(target, duration, props, opts).play();
  }

  /* 从某组值「走回」目标当前的属性。
     实现方式是先把 target 设成 from，再 to 到原值。 */
  function from(target, duration, props, opts) {
    var original = {};
    for (var k in props) {
      if (!Object.prototype.hasOwnProperty.call(props, k)) continue;
      original[k] = cloneValue(target[k]);
      target[k] = cloneValue(props[k]);
    }
    return new Tween(target, duration, original, opts).play();
  }

  /* 只要数字流，不要对象。返回一个可 stop 的句柄。 */
  function value(fromVal, toVal, duration, onUpdate, opts) {
    var box = { v: fromVal };
    var tw = new Tween(box, duration, { v: toVal }, {
      easing: (opts && opts.easing) || "cubicOut",
      delay: (opts && opts.delay) || 0,
      yoyo: !!(opts && opts.yoyo),
      repeat: (opts && opts.repeat) || 0,
      onUpdate: function (t) { if (onUpdate) onUpdate(t.v, t); },
      onComplete: opts && opts.onComplete
    });
    tw.play();
    return tw;
  }

  /* 一组元素错开出场。
     摆卡片、刷柱子、点亮成排的格子，全都用它。

     交错不用 Timeline 的 then 来串——那是「等前一个走完」，
     元素多了会变成一条很长的队列。这里所有 Tween 同时 play，
     只让 delay 递增，所以总时长是 duration + (n-1)*step，
     和元素个数线性相关，不平方。 */
  function stagger(items, duration, makeProps, opts) {
    opts = opts || {};
    var step = opts.step == null ? 45 : opts.step;
    var baseDelay = opts.delay || 0;
    var steps = [];

    items.forEach(function (item, i) {
      var props = typeof makeProps === "function"
        ? makeProps(item, i)
        : makeProps;
      var tw = new Tween(item, duration, props, {
        easing: opts.easing || "cubicOut",
        onUpdate: opts.onUpdate,
        onComplete: opts.onComplete
      });
      tw.delay = baseDelay + i * step;
      steps.push(tw);
    });

    var tl = new Timeline();
    tl._steps = steps;
    tl._cursor = steps.length
      ? steps[steps.length - 1].delay + duration
      : 0;
    tl.play();
    return tl;
  }

  var TweenLib = {
    Easing: Easing,
    mix: mix,
    parseColor: parseColor,
    colorToCss: colorToCss,
    cloneValue: cloneValue,
    Tween: Tween,
    Timeline: Timeline,
    to: to,
    from: from,
    value: value,
    stagger: stagger,
    now: now,

    /* 全局开关。调试慢动作时 speed(0.25) 很有用。 */
    speed: function (v) {
      if (v == null) return _speed;
      _speed = Math.max(0.05, Math.min(8, v));
      return _speed;
    },

    pause: function () {
      _paused = true;
      if (_rafId) {
        global.cancelAnimationFrame(_rafId);
        _rafId = 0;
      }
    },

    resume: function () {
      _paused = false;
      if (_actives.length) ensureRunning();
    },

    /* 当前活跃动画数。性能面板会显示它，
       正常情况应该一直是 0 或个位数。 */
    activeCount: function () { return _actives.length; },

    /* 一次性清空。路由切换时用，避免上个页面的图表动画还在改已卸载的 canvas。 */
    clear: function () {
      _actives.length = 0;
      if (_rafId) {
        global.cancelAnimationFrame(_rafId);
        _rafId = 0;
      }
      _lastTime = 0;
    },

    reducedMotion: function () { return _reduced; }
  };

  global.CMTween = TweenLib;

  /* 兼容用 AMD 的包管理场景（本项目其实没有，但导出去不花钱） */
  if (typeof module !== "undefined" && module.exports) {
    module.exports = TweenLib;
  }
})(typeof window !== "undefined" ? window : this);
