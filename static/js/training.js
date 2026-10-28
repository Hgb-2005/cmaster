/* ============================================================
   training.js —— /training 训练模式

   三种操练，一套骨架
   ------------------
     trace  读代码说输出    输入是一段代码 + 一组 stdin，答案是它的 stdout
     cloze  填空补洞        输入是挖掉几个 token 的代码，答案是那些 token
     flash  概念快答        输入是知识点标题，答案是正文提炼的要点（自评）

   骨架是同一套：抽牌 → 计时 → 判 → 连击 → 错了回头 → 收尾结算。
   所以这里只有一个状态机，三种卡只是「怎么画」和「怎么判」不一样。
   三个独立状态机的话，连击、计时、回炉都要写三遍，三遍里必有两遍不一致。

   三条不能随手改的规则
   --------------------
   1. 归一化必须和 judge.py 的 _normalize 一模一样（见 normText）。
      这一页在学生本地判，判题器在服务端判；两边归一化口径一旦不同，
      就会出现「训练说对了、交上去 WA」——这比不给反馈更伤人。
   2. 计时归零**不等于判错**。归零只把这一张降级为「超时答对也不加分、
      不续连击」，仍允许提交。自动判错等于惩罚思考，而这个页面的目的是
      练熟，不是考试。
   3. 判分只用 textContent 写进 DOM，绝不拼 innerHTML。卡片内容来自题库，
      看着可信，但参考代码里有 `printf("%s")`、笔记里有尖括号，
      拼接迟早出一次「页面少了一半内容」的事故，且很难查。

   回炉（Leitner 的极简版）
   -----------------------
   答错的卡不是丢掉，而是插回队列里「隔三张之后」的位置。同一张卡最多回炉
   两次——不设上限的话，一张怎么都记不住的卡会让这一轮永远结束不了，
   学生会直接关掉页面，那连失败数据都拿不到。
   ============================================================ */

(function () {
  "use strict";

  /* ============================================================
     一、常量
     ============================================================ */

  // 一张卡的思考窗口。25 秒是「读完一段十行代码并口算输出」的合理上限，
  // 再长就变成拖时间，再短会逼人瞎猜。
  var TIME_LIMIT = 25000;

  // 连击倍率：每连一张 +0.1，封顶 ×2。封顶是必要的——
  // 不封顶的话老手练同一套卡能刷到 ×20，XP 曲线立刻失去意义。
  var COMBO_STEP = 0.1;
  var COMBO_CAP = 2.0;

  var XP_TRACE = 10;       // trace 全对的基础分
  var XP_CLOZE_PER = 4;    // cloze 每空
  var XP_CLOZE_BONUS = 8;  // cloze 全对额外
  var XP_FLASH = { know: 6, fuzzy: 3, forgot: 0 };

  var MAX_ROUNDUP = 2;     // 同一张卡最多回炉两次
  var REQUEUE_GAP = 3;     // 回炉后隔三张再出现
  var ADVANCE_OK_MS = 720; // 答对后自动翻页的停留时间（够看完「对」的反馈）

  var OPT_KEY = "cm.train.opt";
  var BEST_KEY = "cm.train.best:v2:" + document.body.dataset.storageId;

  var REDUCE = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ============================================================
     二、小工具
     ============================================================ */

  function $(s, root) { return (root || document).querySelector(s); }

  /* 建元素。所有文本都走 textContent，不给 innerHTML 留口子。 */
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function pad2(n) { return n < 10 ? "0" + n : "" + n; }

  function fmtClock(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + pad2(s % 60);
  }

  function store(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* 隐私模式 */ }
  }

  function recall(key) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  /* 和 judge.py 的 _normalize 逐条对齐：CRLF → LF、去行尾空白、去首尾空行。
     刻意不折叠行内空白、不折叠大小写——判题器也不折叠，这里折叠就成了
     「训练比考试宽松」，练出来的手感在真判题时会被打回去。 */
  function normText(t) {
    if (t == null) return "";
    var s = String(t).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    var lines = s.split("\n").map(function (ln) {
      return ln.replace(/[ \t]+$/, "");
    });
    while (lines.length && !lines[0]) lines.shift();
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    return lines.join("\n");
  }

  /* 空的答案归一化：允许大小写无关吗？不允许。`Null` 不是 `NULL`。 */
  function normBlank(t) { return String(t == null ? "" : t).trim(); }

  function shuffled(list) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  /* ============================================================
     三、状态
     ============================================================ */

  var BOOT = readBoot();
  if (!BOOT) return;

  var state = {
    mode: null,
    size: 12,
    /* 卡池：按模式懒加载，加载过就常驻。一轮只用到十几张，
       但「再来一轮」要立刻换牌，所以整套留在内存里。 */
    pool: {},
    queue: [],
    idx: 0,
    card: null,
    phase: "idle",            // idle | asking | judged | done
    repeats: {},              // cardId -> 回炉次数
    results: {},              // cardId -> 最近一次的 "ok" | "bad"（进度点用）
    /* 与 results 分开记：「错过一次」这件事不会因为回炉时做对了就消失。
       合成一张表的话，回炉答对的卡会把 bad 覆盖成 ok，
       结算页的错题清单和落盘的错题统计就都空了——而那正是要复盘的东西。 */
    missed: {},               // cardId -> 1（只要错过就留名）
    right: 0, wrong: 0,
    combo: 0, maxCombo: 0, xp: 0,
    startedAt: 0,
    cardStartedAt: 0,
    deadline: 0,
    pausedAt: 0,
    rafId: 0,
    advanceTimer: 0,
    finished: false,
    dirty: false              // 有未结算的会话（给 beforeunload 用）
  };

  /* ============================================================
     四、DOM 句柄
     ============================================================ */

  var stage = $("#tr-stage");
  var actions = $("#tr-actions");
  var feedback = $("#tr-feedback");
  var dotsHost = $("#tr-dots");
  var posHost = $("#tr-pos");
  var comboHost = $("#tr-combo");
  var xpHost = $("#tr-xp");
  var timerFill = $("#tr-timer-fill");
  var modeDesc = $("#tr-mode-desc");
  var summary = $("#tr-summary");

  if (!stage) return;

  /* ============================================================
     五、卡池加载
     ============================================================ */

  function apiCards(mode) {
    return fetch("/api/training/cards?mode=" + encodeURIComponent(mode), {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      if (!d || !d.ok || !Array.isArray(d.cards)) throw new Error("数据格式不对");
      return d.cards;
    });
  }

  /* 加载失败要给人一条出路：训练页除了这套卡没有别的内容，
     静默失败等于一个白屏。所以错误态里带一个重试按钮。 */
  function loadPool(mode, onDone) {
    if (state.pool[mode]) { onDone(null, state.pool[mode]); return; }
    clear(stage);
    var box = el("div", "tr-loading");
    box.appendChild(el("span", null, "正在准备卡片…"));
    stage.appendChild(box);

    apiCards(mode).then(function (cards) {
      state.pool[mode] = cards;
      onDone(null, cards);
    }).catch(function (err) {
      console.error("[training] 卡片加载失败", err);
      clear(stage);
      var bad = el("div", "tr-error");
      bad.appendChild(el("b", null, "卡片没取到"));
      bad.appendChild(el("p", null, "检查一下服务是不是还在跑，然后重试。"));
      var again = el("button", "tr-btn tr-btn-primary", "重试");
      again.type = "button";
      again.addEventListener("click", function () { loadPool(mode, onDone); });
      bad.appendChild(again);
      stage.appendChild(bad);
      onDone(err);
    });
  }

  /* ============================================================
     六、开始一轮
     ============================================================ */

  function startRound() {
    state.finished = false;
    state.dirty = false;
    state.queue = [];
    state.idx = 0;
    state.repeats = {};
    state.results = {};
    state.missed = {};
    state.right = 0;
    state.wrong = 0;
    state.combo = 0;
    state.maxCombo = 0;
    state.xp = 0;
    state.startedAt = performance.now();
    state.phase = "idle";

    summary.hidden = true;
    document.body.classList.remove("tr-modal-open");

    loadPool(state.mode, function (err, cards) {
      if (err) return;
      /* 一轮的取牌：打乱后取前 size 张。
         这里用 Math.random 而不是构建期的确定性洗牌——每轮换一批才有练的必要，
         同一套顺序反复做只会背下答案的位置。 */
      var n = Math.min(state.size, cards.length);
      state.queue = shuffled(cards).slice(0, n);
      renderDots();
      renderStats();
      step();
    });
  }

  function step() {
    if (state.idx >= state.queue.length) { finish(); return; }
    state.card = state.queue[state.idx];
    state.phase = "asking";
    state.cardStartedAt = performance.now();
    state.deadline = 0;
    clearTimeout(state.advanceTimer);
    stopClock();

    clear(stage);
    clear(actions);
    setFeedback("");

    renderPos();
    renderCard(state.card);

    /* 时钟起点放在渲染之后：卡还没画出来就开始计时，那几百毫秒
       会白白吃掉，而且是在学生还没看到题目的时候。 */
    state.deadline = performance.now() + TIME_LIMIT;
    startClock();
  }

  /* ============================================================
     七、卡片渲染 —— 顺藤摸瓜
     ============================================================ */

  function renderCard(card) {
    if (state.mode === "trace") return renderTrace(card);
    if (state.mode === "cloze") return renderCloze(card);
    return renderFlash(card);
  }

  function cardHead(card, extra) {
    var head = el("div", "tr-card-head");
    var left = el("div", "tr-card-kicker");
    left.appendChild(el("span", "tr-chip", "第 " + card.chapter + " 章"));
    (card.tags || []).slice(0, 2).forEach(function (t) {
      left.appendChild(el("span", "tr-chip tr-chip-dim", t));
    });
    if (extra) left.appendChild(el("span", "tr-chip tr-chip-dim", extra));
    head.appendChild(left);
    head.appendChild(el("h2", "tr-card-title", card.title));
    return head;
  }

  /* 代码块：行号 + 等宽正文。行号不是装饰——「第 7 行那个循环」
     是复盘时最自然的定位方式，没有行号就只能说「中间那行」。 */
  function codeBlock(lines) {
    var box = el("div", "tr-code");
    for (var i = 0; i < lines.length; i++) {
      var row = el("div", "tr-code-line");
      row.appendChild(el("span", "tr-code-no", String(i + 1)));
      var body = el("span", "tr-code-body");
      if (typeof lines[i] === "string") {
        body.textContent = lines[i] || "\u00a0";
      } else {
        /* 填空卡的一行是「文本段 + 输入框 + 文本段」的混合数组。
           不能直接 appendChild(数组)——它不是 Node，会抛 TypeError，
           而且只在填空题里抛、只在有行内数组时抛。所以逐项加。 */
        for (var j = 0; j < lines[i].length; j++) {
          var piece = lines[i][j];
          body.appendChild(typeof piece === "string"
            ? document.createTextNode(piece) : piece);
        }
        if (!body.firstChild) body.textContent = "\u00a0";
      }
      row.appendChild(body);
      box.appendChild(row);
    }
    return box;
  }

  function renderTrace(card) {
    stage.appendChild(cardHead(card));

    var grid = el("div", "tr-trace-grid");

    var codePane = el("div", "tr-pane");
    codePane.appendChild(el("div", "tr-pane-head", "这段代码"));
    codePane.appendChild(codeBlock(card.code.split("\n")));
    grid.appendChild(codePane);

    var inPane = el("div", "tr-pane tr-pane-in");
    inPane.appendChild(el("div", "tr-pane-head", "它的输入（stdin）"));
    var stdin = el("pre", "tr-stdin");
    stdin.textContent = card.stdin ? card.stdin : "（没有输入）";
    inPane.appendChild(stdin);
    if (card.hint) inPane.appendChild(el("p", "tr-hintline", "提示：" + card.hint));
    grid.appendChild(inPane);

    stage.appendChild(grid);

    var ask = el("div", "tr-ask");
    ask.appendChild(el("label", "tr-ask-label", "它会输出什么？一行一行写，行尾空格不用管"));
    var ta = el("textarea", "tr-input");
    ta.id = "tr-answer";
    ta.rows = Math.max(3, Math.min(8, normText(card.expect).split("\n").length + 1));
    ta.spellcheck = false;
    ta.setAttribute("autocapitalize", "off");
    ta.setAttribute("autocorrect", "off");
    ta.placeholder = "按 Ctrl+Enter 提交";
    ask.appendChild(ta);
    stage.appendChild(ask);

    var submit = el("button", "tr-btn tr-btn-primary", "提交（Ctrl+Enter）");
    submit.type = "button";
    submit.addEventListener("click", function () { judgeTrace(ta.value); });
    actions.appendChild(submit);

    var peek = el("button", "tr-btn", "看答案");
    peek.type = "button";
    peek.addEventListener("click", function () {
      if (state.phase !== "asking") return;
      judgeTrace(ta.value, true);
    });
    actions.appendChild(peek);

    ta.addEventListener("keydown", function (e) {
      if (state.phase !== "asking") return;
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        judgeTrace(ta.value);
      }
    });

    if (!REDUCE) ta.focus({ preventScroll: true });
  }

  /* ============================================================
     八、卡片渲染 —— 填空补洞
     ============================================================ */

  /* 分段 → 按行铺开。
     难点在于一个 text 段可能跨好几行，而洞是行内的 inline 元素：
     直接按段渲染，行号就对不上代码的真实行数了。所以这里把文本段
     再按 \n 切开，逐行累积，遇到洞就往当前行塞一个 input。 */
  function clozeLines(card) {
    var lines = [[]];
    function pushText(txt) {
      var parts = txt.split("\n");
      for (var i = 0; i < parts.length; i++) {
        if (i > 0) lines.push([]);
        if (parts[i]) lines[lines.length - 1].push(parts[i]);
      }
    }
    card.segments.forEach(function (seg) {
      if (seg.t === "text") { pushText(seg.v); return; }
      var answer = seg.answer;
      var inp = el("input", "tr-blank");
      inp.type = "text";
      inp.spellcheck = false;
      inp.setAttribute("autocomplete", "off");
      inp.setAttribute("autocapitalize", "off");
      inp.setAttribute("data-blank", String(seg.i));
      inp.setAttribute("aria-label", "第 " + (seg.i + 1) + " 个空，共 " + card.blanks.length + " 个");
      /* 宽度按答案长度给，最少 4ch：两个字符的洞给两个字符宽，
         光标都看不见，学生会以为那是个不能输入的分隔符。 */
      inp.style.width = "calc(" + Math.max(4, answer.length + 1) + "ch + 8px)";
      lines[lines.length - 1].push(inp);
    });
    return lines;
  }

  function renderCloze(card) {
    stage.appendChild(cardHead(card, card.blanks.length + " 个空"));

    stage.appendChild(codeBlock(clozeLines(card)));

    var bankWrap = el("div", "tr-bank");
    bankWrap.appendChild(el("div", "tr-bank-head", "词库（点一下填进当前光标所在的空）"));
    var bank = el("div", "tr-bank-list");
    card.bank.forEach(function (word) {
      var chip = el("button", "tr-word", word);
      chip.type = "button";
      chip.addEventListener("click", function () { fillFocused(word, chip); });
      bank.appendChild(chip);
    });
    bankWrap.appendChild(bank);
    stage.appendChild(bankWrap);

    var hintLine = el("p", "tr-hintline tr-hint-cloze",
      "填完按 Ctrl+Enter。卡住了可以用「提示」看每个空的首字母，每个空扣 2 XP。");
    stage.appendChild(hintLine);

    var submit = el("button", "tr-btn tr-btn-primary", "提交（Ctrl+Enter）");
    submit.type = "button";
    submit.addEventListener("click", function () { judgeCloze(false); });
    actions.appendChild(submit);

    var peek = el("button", "tr-btn", "看答案");
    peek.type = "button";
    peek.addEventListener("click", function () { judgeCloze(true); });
    actions.appendChild(peek);

    var hint = el("button", "tr-btn", "提示首字母");
    hint.type = "button";
    hint.addEventListener("click", giveHint);
    actions.appendChild(hint);

    var inputs = stage.querySelectorAll(".tr-blank");
    for (var i = 0; i < inputs.length; i++) {
      inputs[i].addEventListener("keydown", onBlankKey);
      inputs[i].addEventListener("focus", markFocused);
    }
    if (inputs.length && !REDUCE) inputs[0].focus({ preventScroll: true });
  }

  function markFocused(e) {
    var all = stage.querySelectorAll(".tr-blank");
    for (var i = 0; i < all.length; i++) all[i].classList.remove("is-focus");
    e.currentTarget.classList.add("is-focus");
  }

  function focusBlank() {
    var a = stage.querySelector(".tr-blank.is-focus");
    if (a) return a;
    var all = stage.querySelectorAll(".tr-blank");
    for (var i = 0; i < all.length; i++) {
      if (!all[i].dataset.filled) return all[i];
    }
    return all[0] || null;
  }

  function fillFocused(word, chip) {
    if (state.phase !== "asking") return;
    var target = focusBlank();
    if (!target) return;
    target.value = word;
    /* 词库点过就置灰：同一个词被用掉两次时（比如两个 %d），
       学生需要知道「这个词还能再用吗」。这里不禁止重复使用，
       只做提示——禁止的话，重复的洞会变成猜谜。 */
    target.dataset.filled = "1";
    target.classList.add("is-filled");
    target.dispatchEvent(new Event("input", { bubbles: true }));
    chip.classList.add("is-used");
    var all = stage.querySelectorAll(".tr-blank");
    for (var i = 0; i < all.length; i++) {
      if (all[i] === target) {
        var next = all[i + 1];
        if (next) next.focus();
        break;
      }
    }
  }

  function onBlankKey(e) {
    /* 判过之后这里什么都不做：Enter 要能继续下一张，
       而下面两条都会 preventDefault，把「继续」挡在门外。 */
    if (state.phase !== "asking") return;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      judgeCloze(false);
      return;
    }
    if (e.key === "Enter") { e.preventDefault(); judgeCloze(false); }
  }

  function giveHint() {
    if (state.phase !== "asking") return;
    var all = stage.querySelectorAll(".tr-blank");
    var target = null;
    for (var i = 0; i < all.length; i++) {
      if (!all[i].value.trim()) { target = all[i]; break; }
    }
    if (!target) return;
    var card = state.card;
    var ans = card.blanks[Number(target.dataset.blank)];
    target.value = ans.charAt(0);
    target.classList.add("is-hinted");
    state.xp = Math.max(0, state.xp - 2);
    renderStats();
    if (!REDUCE) {
      target.classList.remove("tr-pop");
      /* 强制重排让动画能重放：同一个元素连续两次加同一个类，浏览器
         不会重新触发 animation，看起来就是「点了没反应」。 */
      void target.offsetWidth;
      target.classList.add("tr-pop");
    }
  }

  /* ============================================================
     九、卡片渲染 —— 概念快答
     ============================================================ */

  function renderFlash(card) {
    var head = el("div", "tr-card-head");
    var left = el("div", "tr-card-kicker");
    left.appendChild(el("span", "tr-chip", "第 " + card.chapter + " 章"));
    left.appendChild(el("span", "tr-chip tr-chip-dim", card.chapter_title));
    head.appendChild(left);
    stage.appendChild(head);

    /* 卡是 3D 翻转的：正面只放标题，背面放要点。
       结构和样式分离——翻转靠 .is-flipped 一个类，JS 只管状态。 */
    var flip = el("div", "tr-flip");
    var inner = el("div", "tr-flip-inner");

    var front = el("div", "tr-flip-face tr-flip-front");
    front.appendChild(el("span", "tr-flip-tag", "正面 · 这个知识点讲什么"));
    front.appendChild(el("h2", "tr-flip-title", card.title));
    front.appendChild(el("p", "tr-flip-tip", "先在心里说一遍要点，再翻。"));
    inner.appendChild(front);

    var back = el("div", "tr-flip-face tr-flip-back");
    back.appendChild(el("span", "tr-flip-tag", "背面 · 要点"));
    var ul = el("ul", "tr-points");
    card.points.forEach(function (p) {
      ul.appendChild(el("li", null, p));
    });
    back.appendChild(ul);
    inner.appendChild(back);

    flip.appendChild(inner);
    stage.appendChild(flip);
    state.flip = flip;

    var show = el("button", "tr-btn tr-btn-primary", "翻过来（空格）");
    show.type = "button";
    show.addEventListener("click", function () { flipCard(); });
    actions.appendChild(show);
  }

  function flipCard() {
    if (state.phase !== "asking" || !state.flip) return;
    state.flip.classList.add("is-flipped");
    state.phase = "revealed";
    clear(actions);
    var opts = [
      ["know", "记得", "说得出来"],
      ["fuzzy", "模糊", "想起来一半"],
      ["forgot", "忘了", "基本没印象"]
    ];
    opts.forEach(function (o, i) {
      var b = el("button", "tr-btn tr-grade tr-grade-" + o[0]);
      b.type = "button";
      b.appendChild(el("b", null, (i + 1) + ". " + o[1]));
      b.appendChild(el("span", null, o[2]));
      b.addEventListener("click", function () { judgeFlash(o[0]); });
      actions.appendChild(b);
    });
  }

  /* ============================================================
     十、判分
     ============================================================ */

  function judgeTrace(text, reveal) {
    if (state.phase !== "asking") return;
    var card = state.card;
    var want = normText(card.expect);
    var got = normText(text);

    if (reveal) {
      /* 看答案 = 这一张算错，但不进回炉队列的统计方式要和直接答错区分开：
         两者都是「没记住」，可「看答案」的人至少读了正确答案。 */
      showTraceDiff(want, got, false);
      settle({
        right: false, xp: 0, kind: "peek",
        note: "答案已经摊开了。这一张算没答上。",
        answer: want
      });
      return;
    }

    var ok = got === want;
    showTraceDiff(want, got, ok);
    var slow = !ok ? false : isSlow();
    if (ok) {
      settle({
        right: true, xp: score(XP_TRACE, slow), kind: slow ? "slow" : "ok",
        note: slow ? "对了，但超时了——连击不加，XP 减半。" : "对。",
        answer: want,
        /* holdCombo：连击停在原地，不清零也不 +1。
           清零是惩罚（人只是慢，不是不会）；照加又和提示文案对不上。 */
        holdCombo: slow
      });
    } else {
      settle({
        right: false, xp: 0, kind: "bad",
        note: firstDiffNote(want, got),
        answer: want
      });
    }
  }

  /* 逐行对比给出「第几行开始不一样」，这是这一页最有用的反馈：
     只说「错」学生要自己一行一行找，多半就放弃了。 */
  function firstDiffNote(want, got) {
    var w = want ? want.split("\n") : [];
    var g = got ? got.split("\n") : [];
    for (var i = 0; i < Math.max(w.length, g.length); i++) {
      if (w[i] !== g[i]) {
        if (g[i] == null) {
          return "第 " + (i + 1) + " 行开始少了：应该是「" + w[i] + "」。";
        }
        if (w[i] == null) {
          return "第 " + (i + 1) + " 行多出来了：「" + g[i] + "」。";
        }
        return "第 " + (i + 1) + " 行开始不一样：你写的「" + g[i] + "」，应该是「" + w[i] + "」。";
      }
    }
    return "输出对上了，但有看不见的差别（多半是行尾空白）。";
  }

  function showTraceDiff(want, got, same) {
    clear(feedback);
    /* 全对不铺这张表：两列一模一样，除了把反馈区撑高、让「下一张」
       按钮上下跳之外没有信息量。它存在的意义只有「哪一行不同」。 */
    if (same) return;
    var box = el("div", "tr-diff");
    var w = want ? want.split("\n") : [];
    var g = got ? got.split("\n") : [];
    var n = Math.max(w.length, g.length, 1);
    for (var i = 0; i < n; i++) {
      var row = el("div", "tr-diff-row");
      row.appendChild(el("span", "tr-diff-no", String(i + 1)));
      var mine = el("span", "tr-diff-mine", g[i] == null ? "（少了这一行）" : (g[i] || "␣"));
      var real = el("span", "tr-diff-real", w[i] == null ? "（不该有这一行）" : (w[i] || "␣"));
      if (w[i] !== g[i]) {
        row.classList.add("is-bad");
      } else {
        row.classList.add("is-good");
      }
      row.appendChild(mine);
      row.appendChild(real);
      box.appendChild(row);
    }
    var legend = el("div", "tr-diff-legend");
    legend.appendChild(el("span", "tr-diff-key tr-diff-key-mine", "你写的"));
    legend.appendChild(el("span", "tr-diff-key tr-diff-key-real", "应该是"));
    box.insertBefore(legend, box.firstChild);
    feedback.appendChild(box);
  }

  function judgeCloze(reveal) {
    if (state.phase !== "asking") return;
    var card = state.card;
    var inputs = stage.querySelectorAll(".tr-blank");
    var got = [];
    for (var i = 0; i < inputs.length; i++) got.push(inputs[i].value);

    var hits = 0;
    for (var k = 0; k < card.blanks.length; k++) {
      var okBlank = !reveal && normBlank(got[k]) === normBlank(card.blanks[k]);
      if (okBlank) hits++;
      var inp = inputs[k];
      inp.classList.remove("is-good", "is-bad");
      inp.classList.add(okBlank ? "is-good" : "is-bad");
      inp.readOnly = true;
      if (!okBlank) {
        /* 错的洞把正确答案写在旁边，而不是替换掉学生写的：
           替换掉的话，学生复盘时看不到自己原来写的是什么，
           「我写的是 . 不是 ->」这个信息就丢了——而那正是要记住的点。 */
        var tag = el("span", "tr-blank-fix");
        tag.textContent = card.blanks[k];
        inp.parentNode.insertBefore(tag, inp.nextSibling);
      }
    }

    var all = hits === card.blanks.length;
    var slow = all && isSlow();
    var xp = 0;
    if (all) xp = XP_CLOZE_PER * hits + XP_CLOZE_BONUS;
    else if (!reveal) xp = XP_CLOZE_PER * hits;
    if (slow) xp = Math.round(xp / 2);

    settle({
      right: all,
      xp: score(xp, slow),
      kind: all ? (slow ? "slow" : "ok") : (reveal ? "peek" : "bad"),
      note: all ? (slow ? "全对，但超时了——连击不加，XP 减半。" : "全对。")
        : (reveal ? "答案摊开了。这一张算没答上。"
          : "对了 " + hits + " / " + card.blanks.length + " 个。"),
      answer: card.blanks.join("  "),
      holdCombo: slow
    });
  }

  function judgeFlash(grade) {
    if (state.phase !== "revealed") return;
    var xp = XP_FLASH[grade] || 0;
    var right = grade === "know";
    /* 自评不给连击倍率：倍率会让人为了分数谎报「记得」，
       那这张卡就再也回不来了，回炉机制等于被玩家关掉。 */
    settle({
      right: right,
      xp: xp,
      kind: grade === "know" ? "ok" : (grade === "fuzzy" ? "fuzzy" : "bad"),
      /* 「模糊」不当答对：连击的定义是连续答对，模糊不是答对。
         这里不冻结连击——冻结的话，一路点「模糊」的人连击永不中断，
         这个数字就彻底没有意义了（也不能给人这种刷分的路子）。
         但模糊比忘了值钱（3 XP vs 0），且都要回炉，所以自评时说真话仍然划算。 */
      note: grade === "know" ? "记住了。"
        : (grade === "fuzzy" ? "模糊——这一张过几张还会回来，连击断了。"
          : "忘了——这一张过几张会回来。"),
      answer: null,
      requeue: grade !== "know"
    });
  }

  function isSlow() {
    return state.deadline > 0 && performance.now() > state.deadline;
  }

  function score(base, slow) {
    if (base <= 0) return 0;
    var mult = Math.min(COMBO_CAP, 1 + state.combo * COMBO_STEP);
    var v = Math.round(base * mult);
    return slow ? Math.round(v / 2) : v;
  }

  /* ============================================================
     十一、结算一张卡
     ============================================================ */

  function settle(r) {
    if (state.phase !== "asking" && state.phase !== "revealed") return;
    state.phase = "judged";
    state.dirty = true;
    stopClock();

    /* 判完就把输入锁上。留一个还能改的框在那儿，人会以为刚才没提交成功，
       改完再提交一次——而 diff 早按旧值算好了，两边说法就打架。
       顺带解决另一件事：锁上之后 Enter 在框里没有别的含义，只能是「继续」。 */
    var fields = stage.querySelectorAll("textarea, .tr-blank");
    for (var f = 0; f < fields.length; f++) fields[f].readOnly = true;

    var card = state.card;
    state.results[card.id] = r.right ? "ok" : "bad";

    if (r.right) {
      state.right++;
      if (r.holdCombo) {
        /* 连击冻结：慢而对的，或者自评「模糊」的，既不清零（人不是不会，
           清零是惩罚）也不 +1（没干脆利落地答上来，白送一次连击就是自欺）。 */
      } else {
        state.combo++;
        if (state.combo > state.maxCombo) state.maxCombo = state.combo;
      }
    } else {
      state.wrong++;
      state.missed[card.id] = 1;
      state.combo = 0;
    }
    if (r.xp) state.xp += r.xp;

    if (r.note) setFeedback(r.note + (r.xp ? "  +" + r.xp + " XP" : ""), r.kind);
    renderStats();
    renderDots();

    if (r.requeue !== false && !r.right) requeue(card);

    /* 下一张的入口：答对自动走，答错停下来让人看完反馈。
       答错也自动走的话，红字一闪而过，等于没反馈。 */
    clear(actions);
    if (r.right) {
      state.advanceTimer = setTimeout(advance, REDUCE ? 1 : ADVANCE_OK_MS);
    } else {
      var btn = el("button", "tr-btn tr-btn-primary", "下一张（Enter）");
      btn.type = "button";
      btn.addEventListener("click", advance);
      actions.appendChild(btn);
      /* 焦点交给按钮：Enter 的作用因此是看得见的（按钮带焦点框），
         键盘用户也不用先 Tab 一次才知道还能继续。 */
      if (!REDUCE) btn.focus({ preventScroll: true });
    }
  }

  function requeue(card) {
    var n = state.repeats[card.id] || 0;
    if (n >= MAX_ROUNDUP) return;
    state.repeats[card.id] = n + 1;
    var at = state.idx + 1 + REQUEUE_GAP;
    if (at >= state.queue.length) {
      state.queue.push(card);
    } else {
      state.queue.splice(at, 0, card);
    }
  }

  function advance() {
    clearTimeout(state.advanceTimer);
    if (state.phase === "done") return;
    state.phase = "idle";
    state.idx++;
    step();
  }

  function setFeedback(text, kind) {
    /* diff 表格是「错在哪」的主体，note 只是它上面的一句话。
       所以有话说的时候只清文字、留着 diff 节点——早先是整块清空，
       showTraceDiff 刚铺好的逐行对比立刻被这一句擦掉，
       学生只看到「第 3 行开始不一样」，看不到那两行到底差在哪。
       没有话说（换下一张）就是清场，连 diff 一起走。 */
    var diff = text ? feedback.querySelector(".tr-diff") : null;
    clear(feedback);
    if (diff) feedback.appendChild(diff);
    if (!text) return;
    var line = el("div", "tr-fb-line" + (kind ? " tr-fb-" + kind : ""));
    line.textContent = text;
    feedback.insertBefore(line, diff || null);
  }

  /* ============================================================
     十二、计时
     ============================================================ */

  function startClock() {
    stopClock();
    var tick = function () {
      if (!state.deadline) return;
      if (state.pausedAt) { state.rafId = requestAnimationFrame(tick); return; }
      var left = state.deadline - performance.now();
      var ratio = clamp(left / TIME_LIMIT, 0, 1);
      /* transform 而不是 width：每帧改 width 会触发布局，
         而这一条横贯整页，重排代价不小。scaleX 只走合成。 */
      timerFill.style.transform = "scaleX(" + ratio + ")";
      timerFill.classList.toggle("is-low", ratio < 0.3);
      if (left <= 0) {
        timerFill.style.transform = "scaleX(0)";
        timerFill.classList.add("is-out");
        return;
      }
      state.rafId = requestAnimationFrame(tick);
    };
    state.rafId = requestAnimationFrame(tick);
  }

  function stopClock() {
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = 0;
    timerFill.classList.remove("is-out", "is-low");
  }

  /* 切到别的标签页还继续倒计时是不讲理的：人不在，表在走。
     所以隐藏时把剩余时间冻住，回来再补上流逝的那段。 */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      state.pausedAt = performance.now();
    } else if (state.pausedAt && state.deadline) {
      state.deadline += performance.now() - state.pausedAt;
      state.pausedAt = 0;
    }
  });

  /* ============================================================
     十三、进度与统计
     ============================================================ */

  function renderPos() {
    var total = state.queue.length;
    var now = Math.min(state.idx + 1, total);
    var extra = total - Math.min(state.size, total);
    var txt = "第 " + now + " / " + total + " 张";
    if (extra > 0) txt += "（含回炉 " + extra + " 张）";
    posHost.textContent = txt;
  }

  /* 进度点：计划张数 = 实心点，回炉的卡 = 空心点追加在后面。
     用点而不是数字，是因为「还剩几个」这件事看一眼比读数字快；
     用点而不是进度条，是因为这里每一格都有独立结果（对/错），
     进度条只能表达「走了多少」，表达不了「对了几张」。 */
  function renderDots() {
    clear(dotsHost);
    var planned = Math.min(state.size, Math.max(1, state.queue.length));
    var extra = Math.max(0, state.queue.length - planned);
    var shown = Math.min(planned + extra, 26);

    for (var i = 0; i < shown; i++) {
      var isExtra = i >= planned;
      var dot = el("i", "tr-dot" + (isExtra ? " tr-dot-extra" : ""));
      var card = state.queue[i];
      if (card && state.results[card.id]) {
        dot.classList.add(state.results[card.id] === "ok" ? "is-ok" : "is-bad");
      }
      if (i === state.idx && state.phase !== "done") dot.classList.add("is-now");
      dotsHost.appendChild(dot);
    }
    if (planned + extra > shown) {
      dotsHost.appendChild(el("i", "tr-dot-more", "+" + (planned + extra - shown)));
    }
  }

  function renderStats() {
    $("#st-right").textContent = state.right;
    $("#st-wrong").textContent = state.wrong;
    $("#st-combo").textContent = state.combo;
    $("#st-max").textContent = state.maxCombo;
    $("#st-xp").textContent = state.xp;
    $("#st-time").textContent = fmtClock(performance.now() - state.startedAt);
    comboHost.textContent = "连击 ×" + state.combo;
    comboHost.classList.toggle("is-hot", state.combo >= 5);
    comboHost.classList.toggle("is-onfire", state.combo >= 10);
    xpHost.textContent = state.xp + " XP";
  }

  /* 用时每整秒刷一次。用 setInterval 而不是塞进 rAF：
     这个数字一秒才变一次，每帧写一次 DOM 是白烧 CPU。 */
  var statsTimer = setInterval(function () {
    if (state.startedAt && !state.finished) renderStats();
  }, 1000);

  /* ============================================================
     十四、结束一轮
     ============================================================ */

  function finish() {
    if (state.finished) return;
    state.finished = true;
    state.phase = "done";
    state.dirty = false;
    stopClock();
    clearTimeout(state.advanceTimer);
    clear(stage);
    clear(actions);

    var done = state.right + state.wrong;
    var pct = done ? Math.round(state.right / done * 100) : 0;
    var elapsed = performance.now() - state.startedAt;

    $("#sum-right").textContent = state.right;
    $("#sum-wrong").textContent = state.wrong;
    $("#sum-max").textContent = state.maxCombo;
    $("#sum-xp").textContent = state.xp;
    $("#sum-time").textContent = fmtClock(elapsed);
    $("#sum-avg").textContent = done ? Math.round(elapsed / done / 100) / 10 + "s" : "—";
    $("#tr-sum-pct").textContent = pct + "%";

    var ring = $("#tr-sum-ring");
    ring.style.setProperty("--pct", pct + "%");
    ring.classList.toggle("is-good", pct >= 80);
    ring.classList.toggle("is-bad", pct < 50);

    renderMissed();
    saveBest(pct);
    postRecord(done);

    summary.hidden = false;
    document.body.classList.add("tr-modal-open");
    var again = $("#sum-again");
    if (again) again.focus({ preventScroll: true });

    posHost.textContent = "本轮结束";
    setFeedback("");
    renderDots();
  }

  function renderMissed() {
    var host = $("#sum-missed");
    clear(host);
    var missed = [];
    state.queue.forEach(function (c) {
      if (state.missed[c.id] && missed.indexOf(c) < 0) missed.push(c);
    });
    if (!missed.length) {
      host.appendChild(el("p", "tr-missed-none", "这一轮没有错题。换更大的轮次试试。"));
      return;
    }
    host.appendChild(el("div", "tr-missed-head", "这几张没过（点标题回题里看）"));
    var ul = el("ul", "tr-missed-list");
    missed.slice(0, 10).forEach(function (c) {
      var li = el("li");
      var link = el("a", null, c.title);
      // trace/cloze 都能回到题目页；flash 没有对应题目，就不做链接
      link.href = c.qid ? "/problem/" + c.qid : "#";
      if (!c.qid) {
        link = el("span", null, c.title);
      }
      li.appendChild(link);
      li.appendChild(el("span", "tr-missed-tag", c.qid ? c.qid : "概念"));
      ul.appendChild(li);
    });
    host.appendChild(ul);
  }

  function saveBest(pct) {
    var best = recall(BEST_KEY) || {};
    var cur = best[state.mode] || { xp: 0, combo: 0, acc: 0, rounds: 0 };
    cur.xp = Math.max(cur.xp, state.xp);
    cur.combo = Math.max(cur.combo, state.maxCombo);
    cur.acc = Math.max(cur.acc, pct);
    cur.rounds += 1;
    best[state.mode] = cur;
    store(BEST_KEY, best);
  }

  /* 落盘。失败不打断总结页——成绩没了比「看着自己的结果被打断」轻得多。
     但要在页面上留一句，不能假装存上了。 */
  function postRecord(done) {
    var missed = [];
    for (var id in state.missed) missed.push(id);
    var payload = {
      mode: state.mode,
      size: done,
      right: state.right,
      wrong: state.wrong,
      combo: state.maxCombo,
      xp: state.xp,
      missed: missed
    };
    fetch("/api/training/record", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      if (!d || !d.ok) throw new Error("服务端没有接受这次记录");
      refreshCum(d.totals, d.modeStats);
    }).catch(function (err) {
      console.error("[training] 成绩没存上", err);
      var host = $("#sum-missed");
      if (host) host.appendChild(el("p", "tr-missed-warn",
        "这一轮的成绩没存上（服务可能重启了）。刷新页面还能接着练。"));
    });
  }

  function refreshCum(totals, modeStats) {
    var host = $("#tr-cum");
    if (!host || !totals) return;
    clear(host);
    host.appendChild(el("p", null,
      "共 " + totals.sessions + " 轮 · " + totals.xp + " XP"));
    if (modeStats) {
      var row = el("p", "tr-cum-row");
      var name = "";
      BOOT.modes.forEach(function (m) { if (m.id === state.mode) name = m.name; });
      row.appendChild(el("span", null, name));
      row.appendChild(el("span", null,
        "最长 " + modeStats.bestCombo + " 连 · " + modeStats.xp + " XP"));
      host.appendChild(row);
    }
  }

  /* ============================================================
     十五、会话中断
     ============================================================ */

  function quit(force) {
    if (state.finished) return;
    var done = state.right + state.wrong;
    if (!force && done > 0) {
      var sure = window.confirm("这一轮还有 " + (state.queue.length - done) +
        " 张没做，确定结束？已经做的会记账。");
      if (!sure) return;
    }
    if (done === 0) {
      /* 一张都没做就退出，不该在进度里留一条 sessions +1 的空记录 */
      state.finished = true;
      state.phase = "done";
      stopClock();
      clear(stage);
      clear(actions);
      stage.appendChild(el("div", "tr-loading", "这一轮没开始。点左边的模式随时重来。"));
      posHost.textContent = "准备中";
      setFeedback("");
      clear(dotsHost);
      return;
    }
    finish();
  }

  /* ============================================================
     十六、事件
     ============================================================ */

  function bindModes() {
    var host = $("#tr-modes");
    if (!host) return;
    host.addEventListener("click", function (e) {
      var btn = e.target.closest(".tr-mode");
      if (!btn || btn.classList.contains("on")) return;
      if (state.dirty && !window.confirm("换模式会丢掉这一轮，确定？")) return;
      var list = host.querySelectorAll(".tr-mode");
      for (var i = 0; i < list.length; i++) {
        list[i].classList.remove("on");
        list[i].setAttribute("aria-pressed", "false");
      }
      btn.classList.add("on");
      btn.setAttribute("aria-pressed", "true");
      setMode(btn.dataset.mode);
    });
  }

  function bindSizes() {
    var host = $("#tr-sizes");
    if (!host) return;
    host.addEventListener("click", function (e) {
      var btn = e.target.closest(".tr-size");
      if (!btn) return;
      var list = host.querySelectorAll(".tr-size");
      for (var i = 0; i < list.length; i++) {
        list[i].classList.remove("on");
        list[i].setAttribute("aria-pressed", "false");
      }
      btn.classList.add("on");
      btn.setAttribute("aria-pressed", "true");
      state.size = Number(btn.dataset.size) || 12;
      saveOpt();
      if (state.dirty && !window.confirm("改张数会重开一轮，确定？")) return;
      startRound();
    });
  }

  function bindKeys() {
    document.addEventListener("keydown", function (e) {
      var tag = (e.target.tagName || "").toLowerCase();
      var typing = tag === "textarea" || tag === "input";

      if (e.key === "Escape") {
        if (!summary.hidden) { closeSummary(); return; }
        quit(false);
        return;
      }
      if (!summary.hidden) return;

      if (state.phase === "judged" && e.key === "Enter") {
        /* 这一下 Enter 已经被卡片自己用掉了（提交）——它正在同一个事件里
           往上冒泡。不挡住的话，判完分紧接着就翻页，答错的人也看不到反馈。 */
        if (e.defaultPrevented) return;
        var field = e.target;
        var tag2 = (field.tagName || "").toLowerCase();
        /* 焦点在按钮上就交给按钮：Enter 触发原生 click，
           这里再 advance 一次会连翻两张。 */
        if (tag2 === "button") return;
        if ((tag2 === "textarea" || tag2 === "input") && !field.readOnly) return;
        e.preventDefault();
        advance();
        return;
      }
      if (state.phase === "asking" && state.mode === "flash" && e.key === " ") {
        e.preventDefault();
        flipCard();
        return;
      }
      if (state.phase === "revealed" && state.mode === "flash" && !typing) {
        if (e.key === "1") { e.preventDefault(); judgeFlash("know"); }
        else if (e.key === "2") { e.preventDefault(); judgeFlash("fuzzy"); }
        else if (e.key === "3") { e.preventDefault(); judgeFlash("forgot"); }
      }
      /* 题面里的 Ctrl+Enter 也在这里兜一层：焦点在按钮上时，
         卡片内部的 keydown 收不到。不过只对 trace 生效——
         cloze 的空有自己的 Enter 语义（跳下一个空/提交）。 */
      if (state.mode === "trace" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        var ta = $("#tr-answer");
        if (ta) { e.preventDefault(); judgeTrace(ta.value); }
      }
    });
  }

  function closeSummary() {
    summary.hidden = true;
    document.body.classList.remove("tr-modal-open");
    if (!state.finished) return;
    /* 关掉总结后留在「这一轮结束」的状态，给一个明确的「再来一轮」按钮，
       而不是自动重开——自动重开会让人来不及看清自己刚才错在哪。 */
    clear(stage);
    var box = el("div", "tr-done");
    box.appendChild(el("b", null, "这一轮做完了"));
    box.appendChild(el("p", null,
      "做对 " + state.right + " 张，最长连击 " + state.maxCombo + "，拿到 " + state.xp + " XP。"));
    var again = el("button", "tr-btn tr-btn-primary", "再来一轮");
    again.type = "button";
    again.addEventListener("click", startRound);
    box.appendChild(again);
    stage.appendChild(box);
    clear(actions);
  }

  function bindSummary() {
    $("#sum-again").addEventListener("click", function () {
      summary.hidden = true;
      document.body.classList.remove("tr-modal-open");
      startRound();
    });
    $("#sum-close").addEventListener("click", closeSummary);
    $("#tr-quit").addEventListener("click", function () { quit(false); });
  }

  window.addEventListener("beforeunload", function (e) {
    if (!state.dirty) return;
    e.preventDefault();
    e.returnValue = "";
    return "";
  });

  /* ============================================================
     十七、模式与偏好
     ============================================================ */

  function setMode(mode) {
    state.mode = mode;
    BOOT.modes.forEach(function (m) {
      if (m.id === mode) modeDesc.textContent = m.desc;
    });
    saveOpt();
    startRound();
  }

  function saveOpt() {
    store(OPT_KEY, { mode: state.mode, size: state.size });
  }

  function readBoot() {
    var node = document.getElementById("tr-boot");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      console.error("[training] 启动数据解析失败", e);
      return null;
    }
  }

  function boot() {
    var opt = recall(OPT_KEY) || {};
    var modes = BOOT.modes || [];
    if (!modes.length) {
      clear(stage);
      stage.appendChild(el("div", "tr-error", "没有可用的训练模式（data/training.json 可能是空的）"));
      return;
    }

    var ids = modes.map(function (m) { return m.id; });
    var mode = ids.indexOf(opt.mode) >= 0 ? opt.mode : ids[0];
    var size = (BOOT.sizes || []).indexOf(opt.size) >= 0 ? opt.size : 12;
    state.size = size;

    /* 左侧的「当前模式」「当前张数」按存下来的偏好回填，
       否则刷新后按钮显示 A、实际跑的是 B，人会以为选错了。 */
    var modeBtns = document.querySelectorAll(".tr-mode");
    for (var i = 0; i < modeBtns.length; i++) {
      var on = modeBtns[i].dataset.mode === mode;
      modeBtns[i].classList.toggle("on", on);
      modeBtns[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
    var sizeBtns = document.querySelectorAll(".tr-size");
    for (var j = 0; j < sizeBtns.length; j++) {
      var son = Number(sizeBtns[j].dataset.size) === size;
      sizeBtns[j].classList.toggle("on", son);
      sizeBtns[j].setAttribute("aria-pressed", son ? "true" : "false");
    }

    bindModes();
    bindSizes();
    bindKeys();
    bindSummary();
    setMode(mode);
  }

  boot();
})();
