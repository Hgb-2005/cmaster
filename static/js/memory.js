/* ============================================================
   memory.js —— /memory 页的播放逻辑

   分工：
     tools/build_demos.py  算布局、算字节、校验自洽
     lib/memview.js        把一份布局画出来（不知道有「第几步」这回事）
     memory.js             场景切换、单步、自动播放、代码高亮、地址栏同步

   为什么「逐步」值得单独做一个页面
   --------------------------------
   指针、数组退化、结构体填充、栈帧——这四件事光看代码是看不出来的，
   它们在代码里是一行，在内存里是一块。看动画比读十遍文字有效，
   而动画的唯一意义是「同一块内存，上一秒和下一秒差在哪」。
   所以每加一张图，都必须是同一个场景的下一步，而不是换一张图。

   两个状态装饰的区别
   ------------------
     state（数据里带的）  这一步这一格是什么身份：新增 / 改写 / 被读 / 有问题
     is-enter / is-flash  这一刻发生的变化：刚插进 DOM，或者值跟上一帧不同
   前者决定长期描边，后者只演一次。混在一起会导致「已经过去三步的格子还在闪」。
   ============================================================ */

(function () {
  "use strict";

  var PLAY_MS = 1800;          // 自动播放的步间隔。比 1 秒慢——每一步都要读完那句说明
  var STORE_KEY = "cm.mem.last";
  var REDUCE = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var dataEl = document.getElementById("mem-data");
  if (!dataEl) return;

  var DATA;
  try {
    DATA = JSON.parse(dataEl.textContent);
  } catch (e) {
    console.error("[memory] 场景数据解析失败", e);
    return;
  }
  if (!DATA || !DATA.scenarios || !DATA.scenarios.length) return;

  var BY_ID = {};
  for (var i = 0; i < DATA.scenarios.length; i++) BY_ID[DATA.scenarios[i].id] = DATA.scenarios[i];

  var $ = function (s) { return document.querySelector(s); };

  var stageHost = $("#mem-stage");
  var codeHost = $("#mem-code");
  var noteHost = $("#mem-note");
  var titleEl = $("#mem-title");
  var tagEl = $("#mem-tag");
  var introEl = $("#mem-intro");
  var posEl = $("#mem-pos");
  var railEl = $("#mem-rail");
  var btnPrev = $("#mem-prev");
  var btnNext = $("#mem-next");
  var btnPlay = $("#mem-play");
  var btnReset = $("#mem-reset");
  var listEl = $("#mem-list");

  var view = null;
  if (window.MemView) {
    view = new window.MemView(stageHost);
  } else {
    /* 没有渲染器的时候页面还是能用：代码和说明是服务端渲染的，
       只有内存图和箭头没了。明确说出来，比让人以为「这页本来就没图」好。 */
    console.error("[memory] MemView 没加载，内存图无法渲染");
    stageHost.innerHTML =
      '<p class="mem-fallback">内存渲染脚本没有加载，' +
      '下面的文字说明仍然按步骤更新。</p>';
  }

  var state = { sid: DATA.scenarios[0].id, step: 0, timer: 0, codeSig: "" };

  /* ============================================================
     一、代码面板

     自己分词的而不是接一个高亮库：要标的只有 C 的六类记号，
     加一个 20KB 的依赖只为染几个颜色不划算，而且这个页面
     整个项目里只有它用到。
     ============================================================ */

  var TOKEN = new RegExp([
    "(\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*)",       // 1 注释
    "(\"(?:\\\\.|[^\"\\\\])*\")",                    // 2 字符串
    "('(?:\\\\.|[^'\\\\])*')",                       // 3 字符
    "\\b(0x[0-9a-fA-F]+|\\d+)\\b",                   // 4 数字
    "\\b(int|char|double|float|void|struct|unsigned|signed|long|short" +
      "|return|if|else|for|while|do|switch|case|break|continue" +
      "|sizeof|const|static|typedef|NULL|malloc|free|printf|include" +
      "|define|main)\\b"                             // 5 关键字
  ].join("|"), "g");

  var TOKEN_CLASS = ["", "tok-com", "tok-str", "tok-chr", "tok-num", "tok-kw"];

  function highlight(text) {
    var frag = document.createDocumentFragment();
    var last = 0;
    var m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text)) !== null) {
      if (m.index > last) {
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      var cls = "";
      for (var g = 1; g <= 5; g++) {
        if (m[g] != null) { cls = TOKEN_CLASS[g]; break; }
      }
      if (cls) {
        var s = document.createElement("span");
        s.className = cls;
        s.textContent = m[0];
        frag.appendChild(s);
      } else {
        frag.appendChild(document.createTextNode(m[0]));
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) {
      frag.appendChild(document.createTextNode(text.slice(last)));
    }
    return frag;
  }

  function renderCode(sc) {
    /* 同一个场景切步骤时整块代码没变，只是高亮行变了。重建 DOM 会让
       横向滚动位置和选区一起丢，所以按场景 id 做一次比对。 */
    if (state.codeSig === sc.id) return;
    state.codeSig = sc.id;

    codeHost.innerHTML = "";
    var ol = document.createElement("ol");
    ol.className = "mem-code-list";
    for (var i = 0; i < sc.code.length; i++) {
      var li = document.createElement("li");
      li.className = "mem-code-line";
      li.dataset.line = String(i);
      li.appendChild(highlight(sc.code[i] || " "));
      ol.appendChild(li);
    }
    codeHost.appendChild(ol);
  }

  function markLine(n) {
    var lines = codeHost.querySelectorAll(".mem-code-line");
    for (var i = 0; i < lines.length; i++) {
      lines[i].classList.toggle("on", i === n);
    }
    /* 高亮行滚进视野。用 block:"nearest" 而不是 "center"：
       相邻上下步之间行号只差一行，居中会让整块代码每步都跳一下。 */
    var on = codeHost.querySelector(".mem-code-line.on");
    if (on) {
      on.scrollIntoView({
        block: "nearest",
        behavior: REDUCE ? "auto" : "smooth"
      });
    }
  }

  /* ============================================================
     二、进度轨与控件
     ============================================================ */

  function renderRail(sc, step) {
    railEl.innerHTML = "";
    for (var i = 0; i < sc.steps.length; i++) {
      var d = document.createElement("button");
      d.type = "button";
      d.className = "mem-dot" +
        (i < step ? " is-done" : "") +
        (i === step ? " is-on" : "");
      d.title = "第 " + (i + 1) + " 步";
      d.setAttribute("aria-label", "跳到第 " + (i + 1) + " 步");
      d.dataset.step = String(i);
      railEl.appendChild(d);
    }
  }

  function renderControls(sc, step) {
    posEl.textContent = (step + 1) + " / " + sc.steps.length;
    btnPrev.disabled = step === 0;
    btnNext.disabled = step >= sc.steps.length - 1;
  }

  function setPlay(on) {
    state.playing = on;
    btnPlay.setAttribute("aria-pressed", on ? "true" : "false");
    btnPlay.textContent = on ? "暂停" : "自动播放";
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = 0;
    }
    if (!on) return;

    state.timer = setInterval(function () {
      var sc = BY_ID[state.sid];
      if (state.step >= sc.steps.length - 1) {
        setPlay(false);
        return;
      }
      go(state.step + 1);
    }, PLAY_MS);
  }

  /* ============================================================
     三、走一步
     ============================================================ */

  function go(step) {
    var sc = BY_ID[state.sid];
    if (!sc) return;
    if (step < 0) step = 0;
    if (step > sc.steps.length - 1) step = sc.steps.length - 1;
    state.step = step;

    var st = sc.steps[step];
    if (view) view.setStep(st);

    markLine(st.line);
    renderRail(sc, step);
    renderControls(sc, step);

    noteHost.innerHTML = "";
    noteHost.appendChild(highlight(st.note));

    syncHash();
    remember();
  }

  function selectScenario(sid, step) {
    if (!BY_ID[sid]) return;
    var changed = sid !== state.sid;
    state.sid = sid;
    var sc = BY_ID[sid];

    titleEl.textContent = sc.title;
    tagEl.textContent = sc.tag;
    tagEl.dataset.tag = sc.tag;
    introEl.textContent = sc.intro;

    var items = listEl.querySelectorAll("[data-sid]");
    for (var i = 0; i < items.length; i++) {
      var on = items[i].dataset.sid === sid;
      items[i].classList.toggle("on", on);
      if (on) items[i].setAttribute("aria-current", "true");
      else items[i].removeAttribute("aria-current");
    }

    /* 换场景时先关自动播放：上一个场景的节奏没道理延续到下一个，
       而且三步的场景播到「最后一步」会卡在那儿不动。 */
    if (changed) setPlay(false);
    renderCode(sc);
    go(step == null ? 0 : step);
  }

  /* ============================================================
     四、地址栏

     深链接形如 #ptr-basic/3。不做这一步的话，每次刷新都回到第一个场景
     第一步——而「把这一步发给同学看」是这个页面最常见的用法。
     ============================================================ */

  function syncHash() {
    var want = "#" + state.sid + "/" + (state.step + 1);
    if (location.hash !== want) {
      /* replaceState 而不是给 location.hash 赋值：后者会往历史里塞一条，
         按十次「下一步」之后要按十次返回才能离开这个页面。 */
      try {
        history.replaceState(null, "", want);
      } catch (e) {
        location.hash = want;
      }
    }
  }

  function readHash() {
    var h = location.hash.replace(/^#/, "");
    if (!h) return null;
    var parts = h.split("/");
    var sid = parts[0];
    if (!BY_ID[sid]) return null;
    var n = parseInt(parts[1], 10);
    return { sid: sid, step: isNaN(n) ? 0 : Math.max(0, n - 1) };
  }

  function remember() {
    try {
      localStorage.setItem(STORE_KEY, state.sid);
    } catch (e) {
      /* 隐私模式下 localStorage 会抛。记住上次看到哪只是顺手的事，
         存不下就不存，不能因为这件事把页面搞挂。 */
    }
  }

  function recall() {
    try {
      var v = localStorage.getItem(STORE_KEY);
      return v && BY_ID[v] ? v : null;
    } catch (e) {
      return null;
    }
  }

  /* ============================================================
     五、事件
     ============================================================ */

  function bind() {
    btnPrev.addEventListener("click", function () {
      setPlay(false);
      go(state.step - 1);
    });
    btnNext.addEventListener("click", function () {
      setPlay(false);
      go(state.step + 1);
    });
    btnPlay.addEventListener("click", function () {
      setPlay(!state.playing);
    });
    btnReset.addEventListener("click", function () {
      setPlay(false);
      go(0);
    });

    railEl.addEventListener("click", function (e) {
      var d = e.target.closest(".mem-dot");
      if (!d) return;
      setPlay(false);
      go(parseInt(d.dataset.step, 10));
    });

    /* 场景列表用事件委托：列表是服务端渲染的，换一批场景不用重新绑。 */
    listEl.addEventListener("click", function (e) {
      var a = e.target.closest("[data-sid]");
      if (!a) return;
      e.preventDefault();
      selectScenario(a.dataset.sid, 0);
    });

    document.addEventListener("keydown", function (e) {
      /* 在输入框里按空格是打空格，不是播放。这个判断全站都有，
         但这里必须单独写一次——这个页面没有输入框，容易忘了加。 */
      var t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        setPlay(false);
        go(state.step + 1);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        setPlay(false);
        go(state.step - 1);
      } else if (e.key === " ") {
        e.preventDefault();
        setPlay(!state.playing);
      } else if (e.key === "Home") {
        e.preventDefault();
        setPlay(false);
        go(0);
      } else if (e.key === "End") {
        e.preventDefault();
        setPlay(false);
        go(BY_ID[state.sid].steps.length - 1);
      }
    });

    /* 手动改地址栏（或者从别处点进来一个 #sid/step）也要生效 */
    window.addEventListener("hashchange", function () {
      var h = readHash();
      if (!h) return;
      if (h.sid === state.sid && h.step === state.step) return;
      selectScenario(h.sid, h.step);
    });

    /* 切到别的标签页就暂停。自动播放是「边看边想」用的，
       页面看不见的时候继续走，回来刚好错过中间几步。 */
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && state.playing) setPlay(false);
    });

    /* 窗口宽度变了，箭头要重算。这是个纯几何问题，节流到一帧即可。 */
    var rt = 0;
    window.addEventListener("resize", function () {
      if (view && !rt) {
        rt = requestAnimationFrame(function () {
          rt = 0;
          view.drawArrows();
        });
      }
    });

    window.addEventListener("beforeunload", function () {
      if (state.timer) clearInterval(state.timer);
    });
  }

  /* ============================================================
     六、启动
     ============================================================ */

  function boot() {
    var h = readHash();
    var start = h ? h.sid : (recall() || DATA.scenarios[0].id);
    var step = h ? h.step : 0;
    selectScenario(start, step);
    bind();
  }

  boot();
})();
