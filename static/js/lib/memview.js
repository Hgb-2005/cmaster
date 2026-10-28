/* ============================================================
   MemView —— 内存布局渲染器

   只做一件事：把 build_demos.py 算好的一份布局画出来。
   它不知道「第几步」、不知道播放、不知道场景切换——那些在 memory.js。

   为什么格子用 DOM、箭头用 SVG
   ----------------------------
   格子里的文字（变量名、十六进制字节、地址）需要能选中、能被读屏器念出来，
   而且「新出现的格子淡入」这种过渡只有 DOM 才有。Canvas 里画字既糊又不可访问。
   箭头不需要任何一项，反而需要任意曲线，所以交给 SVG。

   key 的约定
   -----------
   数据里的每个格子和片段都带一个全局唯一的 key（形如 "main:p" / "main:a#1"），
   由生成脚本拼好。渲染器只按 key 做增删和查找，不在前端拼字符串——
   一旦前端也拼一遍，两边的拼法迟早会不一样。
   ============================================================ */

(function (global) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  /* 一格要留多宽。不是按字节等比放大：一个 char 和一个 double 都要放得下
     一行 'A' 或 3.5，也要放得下 8 个十六进制字节。所以给一个基础宽度
     加一点随字节数的增量，再上下钳住。 */
  function pieceWidth(size) {
    return Math.max(44, Math.min(112, 26 + 9 * size));
  }

  /* 标量格子有两个硬下限，都不能只按字节数算：

       地址行  0x7ffd5c2a4eff 是 14 个字符，9.5px 等宽下量出来 78px，
               加上左右 padding 14px 就是 93。
       值行    指针的值也是同样长的地址，但它是 12px，每字符约 7.2px。

     只按字节数定宽的话，char 的格子宽 78、内宽 64，地址会被截成
     "0x7ffd5c2a4…"——而被截掉的恰好是区分不同变量的那两三位，
     这一格存在的意义就没了。所以宁可让格子宽一点，也不让地址断头。
     值的宽度封在 150：再长（"{a='A', b=2.5, c='B'}"）就靠 title 兜底。 */
  var CELL_MIN = 93;
  var VALUE_CH = 7.4;
  var VALUE_MAX = 150;

  function textFloor(v) {
    if (v == null) return 0;
    return Math.min(VALUE_MAX, Math.round(14 + VALUE_CH * String(v).length));
  }

  function cellWidth(c) {
    var floor = Math.max(CELL_MIN, textFloor(c.value));
    if (c.pieces && c.pieces.length) {
      var w = 0;
      for (var i = 0; i < c.pieces.length; i++) w += pieceWidth(c.pieces[i].size);
      /* 片段总宽一般远大于下限（最小的是 struct P 的 150），这里的 max
         只是兜底：真出现一个单片段的 char[]，也不至于把地址截掉。 */
      return Math.max(w, floor);
    }
    return Math.max(floor, pieceWidth(c.size));
  }

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  /* 地址行必须是完整地址：这是这个页面唯一「不可近似」的数字。
     9.5px 等宽下 14 个字符正好落在上面 CELL_MIN 算出来的内宽里，
     所以正常情况下不会被裁；真放不下时由 title 兜底，不做四舍五入。 */
  function hexAddr(a) {
    return "0x" + a.toString(16);
  }

  /* ============================================================
     MemView
     ============================================================ */

  function MemView(host) {
    this.host = host;
    this.stage = h("div", "mem-stage");
    this.arrowSvg = document.createElementNS(SVG_NS, "svg");
    this.arrowSvg.setAttribute("class", "mem-arrows");
    this.arrowSvg.setAttribute("aria-hidden", "true");
    this._defs(this.arrowSvg);

    this.stackRegion = this._region("stack", "栈", "向低地址增长");
    this.heapRegion = this._region("heap", "堆", "malloc 出来的");
    this.heapRegion.root.hidden = true;

    this.stage.appendChild(this.stackRegion.root);
    this.stage.appendChild(this.heapRegion.root);
    this.stage.appendChild(this.arrowSvg);
    host.appendChild(this.stage);

    this.blocks = {};      // 帧 id -> { root, cellsEl, cells: {key: row} }
    this.cellEls = {};     // 追加 key -> 元素（格子和片段都在里面）
    this.cellData = {};    // 追加 key -> 数据
    this.ownerOf = {};     // 片段的 key -> 所属格子的 key
    this.arrows = [];      // 上一次画出来的箭头，重排时要重画

    var self = this;
    this._onResize = function () { self.drawArrows(); };
    global.addEventListener("resize", this._onResize);
  }

  MemView.prototype._region = function (kind, title, hint) {
    var root = h("div", "mem-region mem-region-" + kind);
    var head = h("div", "mem-region-head");
    head.appendChild(h("b", null, title));
    head.appendChild(h("span", "mem-region-hint", hint));
    var cells = h("div", "mem-region-body");
    root.appendChild(head);
    root.appendChild(cells);
    return { root: root, body: cells };
  };

  /* 箭头形状只定义一次。orient="auto" 让三角自己跟着路径切线转，
     这样同一份 marker 能同时用于「指向右」和「指向左」两种箭头。 */
  MemView.prototype._defs = function (svg) {
    var defs = document.createElementNS(SVG_NS, "defs");
    var m = document.createElementNS(SVG_NS, "marker");
    m.setAttribute("id", "mem-arrow-head");
    m.setAttribute("viewBox", "0 0 8 8");
    m.setAttribute("refX", "7");
    m.setAttribute("refY", "4");
    m.setAttribute("markerWidth", "6");
    m.setAttribute("markerHeight", "6");
    m.setAttribute("orient", "auto");
    var p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", "M0 0 L8 4 L0 8 z");
    p.setAttribute("class", "mem-arrow-head");
    m.appendChild(p);
    defs.appendChild(m);
    svg.appendChild(defs);
  };

  /* ============================================================
     渲染一步
     ============================================================ */

  MemView.prototype.setStep = function (step) {
    var self = this;
    var want = {};
    var i;

    for (i = 0; i < step.frames.length; i++) want[step.frames[i].id] = "stack";
    for (i = 0; i < step.heap.length; i++) want[step.heap[i].id] = "heap";

    /* 先删。顺序很重要：先删再建，否则新建的块会被后面那次清理扫掉。 */
    Object.keys(this.blocks).forEach(function (fid) {
      if (!want[fid]) {
        self.blocks[fid].root.remove();
        delete self.blocks[fid];
      }
    });

    this.cellEls = {};
    this.cellData = {};
    this.ownerOf = {};

    var ghost = this.stage.querySelector(".mem-ghost");
    if (ghost) ghost.remove();

    /* 一步里什么都不画，右边就是一块空面板，看着像加载失败。
       但「空」本身常常正是这一步要讲的事（帧划出来了还没变量、
       或者帧刚被回收），所以空的时候画一句占位说明，
       文案由生成脚本给（step.ghost），不让渲染器自己编。 */
    if (!step.frames.length && !step.heap.length) {
      this.stackRegion.body.appendChild(
        h("p", "mem-ghost", step.ghost || "这一步内存里没有东西"));
    }

    for (i = 0; i < step.frames.length; i++) {
      this._syncBlock(step.frames[i], "stack", i);
    }
    this.heapRegion.root.hidden = step.heap.length === 0;
    for (i = 0; i < step.heap.length; i++) {
      this._syncBlock(step.heap[i], "heap", i);
    }

    this.drawArrows();
  };

  MemView.prototype._syncBlock = function (f, regionKind, index) {
    var region = regionKind === "stack" ? this.stackRegion : this.heapRegion;
    var block = this.blocks[f.id];

    if (!block) {
      var root = h("div", "mem-frame");
      var head = h("div", "mem-frame-head");
      var name = h("b", "mem-frame-name");
      var addr = h("span", "mem-frame-addr");
      var tip = h("i", "mem-frame-tip");
      head.appendChild(name);
      head.appendChild(addr);
      head.appendChild(tip);
      var cellsEl = h("div", "mem-cells");
      root.appendChild(head);
      root.appendChild(cellsEl);
      region.body.appendChild(root);
      block = { root: root, cellsEl: cellsEl, cells: {},
                nameEl: name, addrEl: addr, tipEl: tip };
      this.blocks[f.id] = block;
    }

    block.nameEl.textContent = f.name;
    /* 帧的起始地址。这里做一次类型判断而不是直接 hexAddr(f.base)：
       一个帧读不到 base，整块内存图会当场抛异常、整页空白——为了
       一个字段名撑不住整个页面不值得。真出现的话控制在控制台里说清楚。
       （这个坑踩过一次：堆帧当初写的是 addr。） */
    if (typeof f.base === "number") {
      block.addrEl.textContent = hexAddr(f.base);
      block.addrEl.hidden = false;
    } else {
      block.addrEl.textContent = "地址缺失";
      block.addrEl.hidden = false;
      console.error("[memview] 帧 " + f.id + " 没有 base 字段", f);
    }
    block.tipEl.textContent = f.tip || "";
    block.tipEl.hidden = !f.tip;
    block.root.classList.toggle("is-freed", !!f.freed);
    block.root.classList.toggle("is-heap", regionKind === "heap");

    /* 顺序用 appendChild 保证，而不是 CSS order。读屏器按 DOM 顺序念，
       用 order 会念出一个和画面对不上的顺序。 */
    region.body.appendChild(block.root);

    var seen = {};
    var i;
    for (i = 0; i < f.cells.length; i++) {
      seen[f.cells[i].id] = 1;
      this._syncCell(block, f.cells[i], index, i);
    }
    Object.keys(block.cells).forEach(function (key) {
      if (!seen[key]) {
        block.cells[key].remove();
        delete block.cells[key];
      }
    });
  };

  MemView.prototype._syncCell = function (block, c, blockIndex, cellIndex) {
    var row = block.cells[c.id];
    var fresh = false;

    if (!row) {
      row = h("div", "mem-cell");
      row.dataset.ck = c.id;
      block.cells[c.id] = row;
      block.cellsEl.appendChild(row);
      fresh = true;
    }

    /* 宽度每步都可能变（比如 p 从 int* 变成空指针），所以每步都写。
       用 style.width 而不是 CSS 变量，因为箭头要读 getBoundingClientRect。 */
    row.style.width = cellWidth(c) + "px";

    var prevValue = row.__value;
    row.__value = c.value;
    var valueChanged = !fresh && prevValue != null && prevValue !== c.value;

    row.className = "mem-cell" +
      (c.state ? " s-" + c.state : "") +
      (c.ptr_to ? " is-ptr" : "") +
      (fresh ? " is-enter" : "") +
      (valueChanged ? " is-flash" : "");

    row.innerHTML = "";
    row.appendChild(h("div", "mem-cell-addr", hexAddr(c.addr)));

    var head = h("div", "mem-cell-head");
    head.appendChild(h("b", null, c.name));
    head.appendChild(h("span", "mem-cell-type", c.type));
    row.appendChild(head);

    row.appendChild(h("div", "mem-cell-value", c.value));

    if (c.pieces && c.pieces.length) {
      var wrap = h("div", "mem-pieces");
      for (var i = 0; i < c.pieces.length; i++) {
        var p = c.pieces[i];
        var pe = h("div", "mem-piece" + (p.pad ? " is-pad" : ""));
        pe.dataset.ck = p.key;
        pe.style.width = pieceWidth(p.size) + "px";
        pe.title = p.pad ? "填充字节（不对应任何成员）"
                         : (p.label ? p.label + " = " : "") + p.value;
        pe.appendChild(h("span", "mem-piece-label", p.label || "\u00a0"));
        pe.appendChild(h("span", "mem-piece-value",
                         p.pad ? "" : p.value));
        var bytes = h("span", "mem-piece-bytes");
        for (var b = 0; b < p.bytes.length; b++) {
          bytes.appendChild(h("i", null, p.bytes[b]));
        }
        pe.appendChild(bytes);
        wrap.appendChild(pe);

        this.cellEls[p.key] = pe;
        this.cellData[p.key] = p;
        this.ownerOf[p.key] = c.id;
      }
      row.appendChild(wrap);
    } else {
      var bytes2 = h("div", "mem-cell-bytes");
      for (var k = 0; k < c.bytes.length; k++) {
        bytes2.appendChild(h("i", null, c.bytes[k]));
      }
      row.appendChild(bytes2);
    }

    row.setAttribute("aria-label",
      c.name + " " + c.type + " = " + c.value +
      "，地址 " + hexAddr(c.addr) + "，" + c.size + " 字节");

    /* 格子的宽度是按字节数定的（8 字节的 double 也只有 98px），
       而值可能是 "{a='A', b=2.5, c='B'}" 这种长串，CSS 会裁掉。
       补一个 title，裁掉的半句还能看到——比再放大字号把整块撑开好。 */
    row.title = c.name + " = " + c.value;

    this.cellEls[c.id] = row;
    this.cellData[c.id] = c;

    /* 入场和闪动都是一次性的：类摘掉之后，下一次重排不会重播。
       用 setTimeout 而不是 animationend，因为这两条动画都很短（≤ 0.4s），
       而 animationend 在「元素刚插入就改类」时偶尔会不来。 */
    if (fresh) {
      setTimeout(function () { row.classList.remove("is-enter"); }, 420);
    }
    if (valueChanged) {
      setTimeout(function () { row.classList.remove("is-flash"); }, 620);
    }
  };

  /* ============================================================
     箭头

     两种路由，因为目标可能在两个地方：
       同区  —— 从源格子右边出来，绕到目标格子右边进去（拱形）。走右侧
                是因为左边是地址列，中间一列全是格子，从左边绕必然穿过格子。
       跨区  —— 栈和堆是左右并排的两块，直接横着连过去，从目标左边进去。
     ============================================================ */

  MemView.prototype.drawArrows = function () {
    var svg = this.arrowSvg;
    while (svg.lastChild && svg.lastChild.nodeName !== "defs") {
      svg.removeChild(svg.lastChild);
    }

    var stageRect = this.stage.getBoundingClientRect();
    if (!stageRect.width) return;

    /* 先把所有目标的高亮清掉，再按这一帧的指针重新点。不清的话，
       上一步被指过的格子会一直亮着。 */
    var marked = this.stage.querySelectorAll(".is-target");
    for (var m = 0; m < marked.length; m++) marked[m].classList.remove("is-target");

    var keys = Object.keys(this.cellEls);
    var arrows = [];
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      var c = this.cellData[key];
      if (!c || !("ptr_to" in c)) continue;

      var srcEl = this.cellEls[key];
      var tgtKey = c.ptr_to;
      if (!tgtKey) continue;                    // 空指针不画
      var tgtEl = this.cellEls[tgtKey];
      if (!tgtEl) continue;
      arrows.push({ src: srcEl, tgt: tgtEl, tgtKey: tgtKey,
                    from: key, data: c });
    }

    /* 同一个目标被多个指针指着的时候，拱高要错开，否则两条线重叠成一条，
       看起来像只有一个指针。 */
    var depth = {};
    for (var a = 0; a < arrows.length; a++) {
      var t = arrows[a];
      var d = depth[t.tgtKey] || 0;
      depth[t.tgtKey] = d + 1;
      t.lane = d;
    }

    for (var j = 0; j < arrows.length; j++) {
      this._oneArrow(arrows[j], stageRect);
    }
  };

  MemView.prototype._oneArrow = function (a, stageRect) {
    var sr = a.src.getBoundingClientRect();
    var tr = a.tgt.getBoundingClientRect();

    /* 箭头指向的是「片段」时（比如 char *pc 指进 int 数组的第二个字节），
       几何上连到片段，但视觉上从属于那个格子——所以片段本身描边，
       线还是画到片段的位置，让人一眼看出指的到底是哪几个字节。 */
    var sameRegion = this._regionOf(a.src) === this._regionOf(a.tgt);

    var sx, sy, tx, ty, d;
    var bulge = 26 + 13 * a.lane;

    if (sameRegion) {
      sx = sr.right - stageRect.left;
      sy = sr.top - stageRect.top + sr.height / 2;
      tx = tr.right - stageRect.left;
      ty = tr.top - stageRect.top + tr.height / 2;
      d = "M" + sx + " " + sy +
          " C" + (sx + bulge) + " " + sy +
          "," + (tx + bulge) + " " + ty +
          "," + tx + " " + ty;
    } else {
      sx = sr.right - stageRect.left;
      sy = sr.top - stageRect.top + sr.height / 2;
      tx = tr.left - stageRect.left;
      ty = tr.top - stageRect.top + tr.height / 2;
      var mx = (sx + tx) / 2;
      d = "M" + sx + " " + sy +
          " C" + mx + " " + sy + "," + mx + " " + ty +
          "," + tx + " " + ty;
    }

    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("class", "mem-arrow");
    path.setAttribute("marker-end", "url(#mem-arrow-head)");
    this.arrowSvg.appendChild(path);

    /* 目标描边。片段和整格都能描，取决于 ptr_to 落在哪一层。 */
    a.tgt.classList.add("is-target");

    /* 标签放在贝塞尔中点上：三次贝塞尔 t=0.5 的点是
       (P0 + 3P1 + 3P2 + P3) / 8，不是两端的中点。
       用两端中点会让文字偏到线的一侧去。 */
    var cx1 = sameRegion ? sx + bulge : (sx + tx) / 2;
    var cx2 = sameRegion ? tx + bulge : (sx + tx) / 2;
    var mxp = (sx + 3 * cx1 + 3 * cx2 + tx) / 8;
    var myp = (sy + 3 * sy + 3 * ty + ty) / 8;

    var label = this._arrowLabel(a);
    if (label) {
      var t = document.createElementNS(SVG_NS, "text");
      t.setAttribute("x", mxp);
      t.setAttribute("y", myp - 6);
      t.setAttribute("class", "mem-arrow-label");
      t.setAttribute("text-anchor", "middle");
      t.textContent = label;
      this.arrowSvg.appendChild(t);
    }
  };

  MemView.prototype._regionOf = function (el) {
    return el.closest(".mem-region-stack") ? "stack" : "heap";
  };

  /* 箭头标签用「指到哪儿」而不是「存了什么」：地址本身已经写在格子里了，
     再念一遍 0x7ffd… 只是噪音。真正难看出来的是「指进了数组的第几个」。 */
  MemView.prototype._arrowLabel = function (a) {
    var v = String(a.data.value);
    if (/^0x0$/.test(v)) return "";
    var t = this.cellData[a.tgtKey];
    if (!t) return "";
    if (t.label != null) return (t.label || "") + (t.pad ? "" : "");
    return t.name || "";
  };

  MemView.prototype.resize = function () { this.drawArrows(); };

  MemView.prototype.destroy = function () {
    global.removeEventListener("resize", this._onResize);
    this.host.innerHTML = "";
    this.blocks = {};
    this.cellEls = {};
    this.cellData = {};
  };

  global.MemView = MemView;
})(window);
