# 接手指南（当前状态速查）

> 本文只写「接手继续干活需要知道的事」。想改内容看第四节，想改判题看第五节。
> 最后更新：2026-10-28
>
> 学习流程升级：训练卡片随仓库发布并支持缺失重建；完整用户名计算独立进度文件标识，
> 旧文件仅在归属唯一时复制迁移；系统错误保存代码但不扣学习记录；首页有继续学习路线；
> 做题页的草稿与历史恢复逻辑位于 `static/js/problem.js`。
> 第二轮：课程/题目收藏与私人笔记，`/notebook` 支持组合筛选；Markdown 笔记与 JSON 学习记录导出。
> 笔记写入必须携带账号标识和版本号；冲突返回 409，页面保留草稿供作者选择。
> 新回归命令：`python -m unittest discover -s tests -v`。

## 一、这是什么

`CMaster`：一个**能真的把 C 代码跑起来**的 C 语言学习平台，对着 PyMaster 的形态做的 C 版。

```
12 章 / 48 个知识点 / 60 道在线判题（157 个测试用例）
基础篇 · 语言地基    → 环境、变量、运算符、分支循环、数组
进阶篇 · 内存与抽象  → 函数、指针、指针进阶与动态内存、字符串
工程篇 · 写出能用的程序 → 结构体、文件操作、预处理与多文件编译
```

四件事同时成立，是它区别于"看视频学 C"的地方：

1. **每节都能立刻动手**——知识点页底部直接挂本章练习，不用跳转找题。
2. **判题是真的编译真的跑**——本机 GCC 编译、逐用例执行、输出比对，不是字符串匹配。
3. **零外部依赖**——没有数据库、没有 CDN、没有构建步骤，断网能用，整个文件夹拷走也能用。
4. **有一层像样的交互**——`/` 唤起站内搜索、阅读进度条、代码块一键复制、逐日学习热力图、
   九枚成就。这部分集中在 `ux.js` + `motion.css`，**全部是渐进增强**：
   脚本挂了站点照样能读能写能判题。

面向的使用场景：**简历项目**。所以设计上刻意保留了一些"可以聊"的点——门禁、并发闸门、
沙箱边界——见第七、九节。

## 二、怎么跑起来

双击 `start.cmd`（相对路径 `%~dp0`，整个项目搬到哪都能跑）。

首次启动会自动做三件事：建 `runtime/venv` → 装 Flask → 检测编译器。
服务在 **http://127.0.0.1:5001**（刻意避开 PyMaster 的 5000，两个可以同时开）。

手动启动：

```bash
runtime/venv/Scripts/python.exe app.py
CMASTER_PORT=5002 runtime/venv/Scripts/python.exe app.py   # 换端口
```

**编译器**：便携版 w64devkit 解压在 `runtime/w64devkit/`，不进系统 PATH。
如果没装，站点仍然能开、能读课程，只是判题报"编译器缺失"——首页右上角徽标会变成红色。

## 三、目录与职责

```
c-master/
├── start.cmd             唯一启动脚本（相对路径，可整体搬走）
├── app.py                Flask 主程序：路由 / 会话 / 进度 / 判题 API / 内存演示 / 运维快照 /
│                         管理台 / 健康检查（约 1670 行）
├── judge.py              判题引擎：找编译器 / 编译 / 跑用例 / 比对 / 裁决 / 退出码翻译（约 500 行）
├── sandbox.py            ★ 沙箱封装：构建宿主 / 探针 / 解析报告 / 五组自检 / 状态计数（约 790 行）
├── sandbox/
│   ├── runner_win.c      ★ Windows 宿主：Job Object（内存 / 进程数 / CPU / KILL_ON_JOB_CLOSE）（约 725 行）
│   └── runner_linux.c    ★ Linux 宿主：setrlimit + setgid(nobody) + NO_NEW_PRIVS + seccomp-bpf（约 740 行）
├── Dockerfile            容器镜像：非 root + 构建期编译宿主 + 存活探针
├── docker-compose.yml    部署参数：只绑 127.0.0.1 / 只读根 / cap_drop / 配额推导
├── docker-entrypoint.sh  启动时补种 data 卷 + 打印沙箱状态 + exec 交棒
├── .dockerignore         排除 runtime/w64devkit（200MB）与用户数据
├── metrics.py            指标采集：判题耗时打点、滚动窗口聚合、落盘 data/metrics.json（约 430 行）
├── requirements.txt      只有 Flask
├── tools/
│   ├── build_content.py  ★ 内容装配 + 题库门禁（构建入口）
│   ├── build_demos.py    ★ 内存演示生成器：算偏移 / 填充 / 字节，并自校验（约 970 行）
│   ├── build_training.py ★ 训练卡片生成器：从题库 / 参考答案 / 课程正文算卡（约 430 行）
│   ├── gate.py           独立门禁：拿 answers.json 真编译真跑（可只验指定题）
│   ├── crawl.py          全站爬取回归：134 页 + 29 个静态资源 + 4 个接口契约
│   ├── loadtest.py       并发压测（给 /ops 造数据的工具，不属于站点运行时）
│   ├── probe_tle.py      TLE 归因探针：五个程序打 wall/cpu/比值，断言档位，不符退出 1
│   ├── check_runners.py  两个 runner 的契约面对拍：宽限/配额/报告键/note/打点顺序
│   ├── md2html.py        Markdown→HTML + C 语法高亮（手写，离线可用）
│   ├── content_ch_a.py   课程第 1–6 章源数据
│   ├── content_ch_b.py   课程第 7–12 章源数据
│   ├── content_qb_a.py   题库第 1–6 章
│   └── content_qb_b.py   题库第 7–12 章
├── data/                 编译产物（courses.json / question_bank.json / answers.json）
│   ├── memory_demos.json 内存演示的 8 个场景 / 36 步（build_demos.py 生成，随仓走）
│   ├── training.json     训练卡片（build_training.py 生成，随仓走）
│   ├── metrics.json      判题耗时与裁决的滚动窗口（运行时写，可随时删）
│   ├── .secret           自动生成的会话密钥（gitignore）
│   ├── users.json        账号（gitignore）
│   └── progress/         每人一个进度文件（gitignore）
├── templates/            base / index / chapter / problems / problem / playground / login /
│                         profile / wrongbook / analytics / map / memory / ops / training / error
├── static/
│   ├── css/style.css     全站样式（深色，约 35KB）
│   ├── css/motion.css    动效层（约 21KB）：缓动变量 / 渐入 / 阅读进度条 / 判题反馈 / 徽章入场
│   ├── css/chart.css     Canvas 图表的图例 / tooltip / 容器（约 6KB）
│   ├── css/analytics.css 学习分析页版式（约 6KB）：指标卡 / 图表网格 / 薄弱点表
│   ├── css/graph.css     关系图「画布外面那层」（约 3KB）：容器 / 光标 / 浮动提示 / 焦点环
│   ├── css/map.css       图谱页版式（约 12KB）：工具栏 / 搜索建议 / 侧栏 / 图例 / 窄屏 / 打印
│   ├── css/ops.css       运维状态页版式（约 14KB）：健康徽标 / 四图网格 / 耗时构成条 / 慢请求表
│   ├── css/memory.css    内存演示页版式（约 26KB）：两栏骨架 / 格子 / 填充片段 / 箭头 / 图例
│   ├── css/training.css  训练模式版式（约 30KB）：两栏 / 计时条 / 代码卡 / 词库 / 翻转卡 / 逐行 diff
│   ├── js/editor.js      CMaster 前端模块：编辑器 / 结果渲染 / setJudging / api / toast
│   ├── js/ux.js          交互增强（约 22KB）：搜索 / 快捷键 / 代码块包裹 / 复制 / 渐入 / 阅读条
│   ├── js/app.js         只干一件事：启动时查编译器状态
│   ├── js/lib/tween.js   补间动画引擎（约 21KB）：Timeline / tween / stagger / 弹簧 / 缓动表
│   ├── js/lib/chart.js   Canvas 图表引擎（约 42KB）：折线 / 面积 / 柱状 / 雷达 / 环形
│   ├── js/lib/graph.js   Canvas 力导向关系图引擎（约 32KB）：布局 / 拾取 / 缩放 / 聚焦过滤
│   ├── js/lib/memview.js 内存布局渲染器（约 480 行）：格子用 DOM、箭头用 SVG，不认识「第几步」
│   ├── js/analytics.js   学习分析页：把后端聚合出来的数据喂给图表引擎
│   ├── js/map.js         图谱页：关系索引 / 侧栏详情 / 搜索 / 聚焦未读 / 双击进入
│   ├── js/ops.js         运维状态页：轮询 /api/ops、四张图、耗时构成条、慢请求表
│   ├── js/memory.js      内存演示页：场景切换 / 单步 / 自动播放 / 代码高亮 / 地址栏同步
│   ├── js/training.js    训练模式：三种操练共用一套骨架（抽牌→计时→判→连击→回炉→结算）
│   ├── favicon.svg       站点图标
│   └── vendor/codemirror CodeMirror 5.65.16 + clike 模式（全部本地）
└── runtime/              venv 与 w64devkit（gitignore，首启生成）
```

## 四、改内容：装配 + 门禁

**不要手改 `data/*.json`**，会被下次构建覆盖。源在 `tools/content/`，改完重新生成：

```bash
python tools/build_content.py              # 装配 + 跑门禁，全 AC 才写文件
python tools/build_content.py --no-gate    # 只装配（改排版文案时用，快）
python tools/build_content.py --gate-only  # 只跑门禁（验证用）
```

门禁做的是**用参考答案跑一遍自己的测试用例**，任何一题非 AC 就构建失败、不写文件，
并打印编译输出 + 每个用例的 input/expected/actual。理由很直接：这类项目最恶劣的故障不是
页面崩了，而是**学生写对了却判错**——那会让人怀疑自己，比报错更伤。所以宁可构建失败。

当前全量门禁约 **24 秒**，60/60 AC。

两个细节：

- 题库落盘时**剥离 `answer`**，单独写 `answers.json`，只留在服务端。前端拿不到答案。
- 所有写盘走 `os.replace()` 原子替换，避免前端读到半截 JSON。

## 五、判题是怎么做的

```
代码 → gcc -std=c11 -O2 -Wall -Wextra -fno-asm -o a.exe
     → 逐用例：独立临时目录 + stdin 喂入 + 3 秒超时
     → 输出归一化（CRLF→LF、去行尾空白、去首尾空行）后比对
     → 首个失败用例即返回，附 input/expected/actual/stderr
```

裁决码：`AC` / `WA` / `TLE` / `RE` / `CE` / `SE`；游乐场用 `OK`（那里没有"正确"可言）。

四个容易踩的点，改判题前先看：

1. **gcc 必须能自己找到 `cc1`/`as`/`ld`**。所以调子进程时要把编译器**自己那个目录**
   放进 PATH，只给绝对路径不够——否则报 "cannot execute cc1"。
2. **输出必须归一化再比**。Windows 下学生代码打 `\r\n`、标准答案写 `\n`，
   不归一化会稳定 WA，而且看起来像逻辑错，极难自查。
3. **`run_freeform` 也要带 `status`**。游乐场和判题页共用一套前端渲染，
   少了这个字段前端直接抛异常。
4. **并发要限流**。判题是本地 CPU 密集型，多人同时交会把机器打满。
   `threading.Semaphore` 默认 3，`CMASTER_CONCURRENCY` 可调。

## 六、前端约定

脚本加载顺序（`base.html` 末尾，别调换）：`editor.js` → `ux.js` → `app.js`。
`chart.js` / `graph.js` 是**按页引的**，不挂在全站：只有 `/analytics` 引 `chart.js`，
只有 `/map` 引 `graph.js` + `map.js`。

- `editor.js` **必须排在 `app.js` 之前**。`app.js` 要用它导出的 `CMaster` 查编译器状态；
  之前只在判题页引且在后面，导致徽标永远停在"检测中"。
- **`app.config['TEMPLATES_AUTO_RELOAD'] = True`**。Flask 默认只在 `debug=True` 时
  重载模板，而我们是 `debug=False` 起的（本地判题，不想让 Werkzeug 的重载器去反复重启
  判题子进程池）。不开这个，改一行 HTML 都要重启服务，而且**不重启时 `curl` 拿到的是
  旧 HTML，会让人误判成「JS 没生效」**。Jinja 的重载只比 mtime，开销可以忽略。
- **容器宽度做成 `{% block wrap_class %}`**。正文页要窄栏好读，图谱页要宽才铺得开；
  只给需要的那一页加 `.wrap-wide`，不为了一个页面把全站改宽。
  别在子模板里覆盖 `{% block layout %}`——里面再定义一次 `{% block content %}` 会撞
  `block 'content' defined twice`。
- `createEditor()` 在 CodeMirror 没加载时会**降级成原生 textarea**。宁可丑，不能让人写不了字。
- 代码草稿进 `localStorage`（key `cmaster:code:v2:<账号标识>:<qid>`），AC 后保留。
  通过代码与最近 10 次提交保存在进度文件的 `solutions` 中，支持新浏览器恢复。
- 收藏与笔记保存在进度文件 `notebook["lesson:<cid>:<kid>" | "problem:<qid>"]`。
  `static/js/notebook.js` 同时在 `sessionStorage` 中保留每个标签页的草稿，在 `localStorage`
  中保留最近草稿；保存请求携带 `X-CMaster-Account` 和 `version`，服务器在进度锁内比较版本。
  取消收藏且清空笔记后仍保留版本号，避免过期页面覆盖。笔记按纯文本渲染，最多 8000 字。
  `/api/notebook/export` 下载 Markdown，`/api/progress/export` 下载当前账号的学习数据 JSON；
  两者都要求登录且返回 `Cache-Control: no-store`。这些下载链接带 `download`，跳过页面离场动画。
  浏览器回归：`node tests/notebook.cjs`（先启动 `python tests/browser_fixture.py`）。
- 判题页用 `{% block layout %}` 覆盖成宽布局（左题面右编辑器），其它页用默认窄栏。
- 未标注语言的围栏代码块**不按 C 高亮**——内容里大量 ASCII 示意图混着中文，
  强行高亮会把箭头和中文标识符搞乱。

### 6.1 `ux.js`：每个功能先探测元素再执行

全站引入，但内部每个 `init*()` 都先 `document.querySelector` 判断目标是否存在，
不存在就直接返回。首页没有正文，就不该有阅读进度条；错题本空着，就不该有渐入候选。
**这样一份脚本可以安全地挂在所有页面。**

| 能力 | 触发 | 实现要点 |
| --- | --- | --- |
| 站内搜索 | `/` 或 `Ctrl+K` | command palette；`AbortController` + 130ms 防抖；`lastQ` 丢弃过期响应 |
| 代码块壳 | 页面加载 | 把 `md2html` 产出的 `<pre class="md-code">` 在 DOM 里包一层 `.code-block` + 语言标签 + 复制按钮 |
| 复制 | 点按钮 | `navigator.clipboard` 失败退 `execCommand`；成功变「已复制」加 `.done` |
| 阅读进度条 | 有 `.md` / `.q-desc` / `.play-layout` | `rafThrottle` 节流，只写 CSS 变量 |
| 滚动渐入 | `.reveal` 元素 | 首屏内不加动画；2 秒兜底强制显形，防 IntersectionObserver 失效留白 |
| 快捷键 | `/` `Ctrl+K` `?` `j` `k` `Esc` | `isTyping()` 守卫，CodeMirror 内不抢按键 |
| 页面转场 | 站内链接 | `body.cm-leaving` 淡出 145ms；`pageshow` 清理残留类（防 BFCache 白屏） |

**为什么代码块壳放在 JS 而不是 `md2html.py`**：改构建脚本要重跑 60 题门禁（24 秒），
纯表现层的需求不值得付那个代价。改动只影响一个页面时，优先动 CSS/JS。

搜索排序：标题命中权重 100 起、正文 40 起、开头命中 +30，再按 `(_GROUP_ORDER, -score)`
排序让同类结果成组。120 条数据直接线性扫，不做倒排索引——**在这个量级上索引是纯负担**。

### 6.2 `motion.css`：三条自定规则

写任何动效前先看这三条，别破例：

1. **只动 `transform` 和 `opacity`**。动 `width`/`top` 会触发 layout，滚动时必掉帧。
2. **动效必须可被打断**。`animation` 而不是长 `transition`，用户换页时不用等它演完。
3. **`prefers-reduced-motion` 全量尊重**。文件末尾有一整块 `@media` 把所有动效降级成
   `0.01ms`，别在新写规则里漏掉。

两个坑已经踩过，别重犯：

- 顶部阅读条的 `width` **不能加 `transition`**，滚动时会出现拖尾，看着像卡顿。
- CSS 里写好的动画要确认 JS 真的加了那个类。`is-pass` / `is-fail` / `is-judging`
  三个类曾经 CSS 写了但 JS 从没加过，绿光、抖动、呼吸全是死代码。
  排查方式：`grep` 一遍类名，看 JS 侧有没有 `classList.add`。

## 六之二、个人中心的数据模型

`data/progress/u_<完整用户名SHA256>.json` 里与学习轨迹相关的四个键：

| 键 | 结构 | 谁写 |
| --- | --- | --- |
| `read` | `[kp_id, ...]` | 打开知识点即记，带 dirty 标记批量落盘 |
| `solved` | `[qid, ...]` | 判题 AC 时写入 |
| `days` | `["2026-10-27", ...]` | `_touch_day()`，只记「有没有学」 |
| `acts` | `{"2026-10-27": 3, ...}` | 同上，记**次数**。热力图分深浅靠它；超过 400 天自动裁旧 |

三个派生视图都是**现算不落盘**：

- `learning_streak(data)` —— 连续天数。**今天没学时从昨天起算**，否则早起打开就显示归零，
  没人受得了这个。
- `compute_achievements(data)` —— 九枚成就由 `read`/`solved`/`streak` 实时推导。
  不落盘是为了避免"数据变了徽章没变"这类对不上的故障。
- `activity_grid(data, weeks=26)` —— 26 列 × 7 行的热力图。`level=-1` 是未来日期，
  渲染成透明占位而不是空格子，否则表格右边缘会缺一块。
  **老数据没有 `acts` 时退化成查 `days` 集合**，只显示「学过/没学过」两档，不会报错。

九枚成就图标是页面顶部 `<defs>` 里的 `<symbol>`，下面用 `<use href="#ach-xxx">` 复用。
全部 `stroke="currentColor"`，所以锁定态只要调 `color` 就自动灰显，不用两套图。

## 六之三、可视化三层（`lib/tween` → `lib/chart` → `lib/graph`）

自己写而不是引 ECharts / D3，不是重复造轮子，是这三件事都太窄：
图表只要五种、关系图只有 60 个节点、动画只要「进场 + 两个数字滚动」。
**在这种规模上，通用库的抽象成本（配置项 + 生命周期 + 包体）比手写高。**

三层是自下而上依赖的，别跨层调：

```
tween.js   只管「一段时间内把一个值从 a 变成 b」     —— 不认识图表，不认识节点
chart.js   只管「一组数画成一条线」                 —— 不认识章节，不认识进度
graph.js   只管「一堆点和线怎么摆、怎么点」          —— 不认识 /chapter/<cid>/<kid>
```

### 6.3.1 `tween.js`：单一 rAF

整个页面只有**一个** `requestAnimationFrame` 循环，所有补间挂在 `_actives` 里。

- 遍历 `_actives` 必须**倒序**。正序时回调里 `kill()` 自己会让后面的元素前移，
  发生跳帧——这个 bug 表现是「偶发卡一下」，最难查。
- delta 钳在 **100ms**。切标签页回来时 `now` 会跳几千毫秒，不钳的话动画瞬间跳到终点，
  等于白做。
- `prefers-reduced-motion` 时把 **duration 归零但回调照发**。只停动画不回调，
  依赖 `then` 收尾的代码（比如「动画结束后显示结果」）就永远不执行。
- 没有活动补间时 `_actives` 空了就**停 rAF**。不停的话空闲页面一直在空转，
  笔记本风扇能听出来。

### 6.3.2 `chart.js`：四张图 + 两个必做细节

`Chart.line / area / bar / radar / donut`，图例和 tooltip 用 DOM（不是 canvas 里画），
这样 hover 高亮和读屏器都能走正常 DOM 路径。

必须做的两件事，漏了就是「看起来能跑其实糊」：

1. **DPR 上限 2**。按真实 `devicePixelRatio` 铺位图在 3x 屏上会让内存翻倍，
   而肉眼看不出差别。`setTransform(dpr,…)` 一次搞定，别逐点乘。
2. **`+0.5` 像素对齐**。1px 的线画在整数坐标上会跨两个物理像素，
   渲染成 2px 的灰线；`Math.round(x) + 0.5` 才是清晰的一像素。

其余几个决定：`niceScale` 把轴刻度收成 1/2/5×10ⁿ；折线用 Catmull-Rom（t=0.18）
而不是纯二次贝塞尔——后者在拐点会冲出数据范围；`xRotate` 自动加 `pad.bottom`，
否则斜排的标签会被裁掉；环形图有扇区级的 `_sliceHidden`，图例点一下少一块，
不是整张图重画。

### 6.3.3 `graph.js`：力导向关系图

参数是拿「12 章 48 节」这组真实数据调出来的，换数据要重调：

| 参数 | 值 | 为什么是这个数 |
| --- | --- | --- |
| `repulsion` | 2600 | 再小章节点会叠在一起，再大边缘的节会被甩出视野 |
| `springK` | 0.085 | 弹簧太硬会抖，太软图会散成几团 |
| `gravity` | 0.012 | 只够把所有节点往中间轻推，防止孤岛飘走 |
| `damping` | 0.86 | 低于 0.8 停得太快像卡住，高于 0.92 永远不收敛 |
| `maxStep` | 18 | **必需**。退火初期一步能走出几百像素，看着像爆炸 |
| `minDist` | 6 | **必需**。两个节点重合时 1/d² 会算出 `Infinity`，整张图瞬移 |

几个不显然的：

- **不做 Barnes-Hut。** n=60 全对比才 1770 次距离平方，微秒级；
  八叉树在这个规模上是纯负担。
- **标签在屏幕空间画**，不在缩放矩阵里画。世界里画的话字会跟着 `k` 缩放，
  缩到 0.5 就糊成一团黑点。
- **`_fade` 自己算，不用 `tween`。** 渲染循环里的透明度要跟帧同步，
  走补间的异步回调会晚一帧，入场时能看见闪一下。
- **收敛后停机**（`_settled && _fade>=1 && !hover && !_drag` 才 return）。
  不停机就是空转 CPU，风扇能听出来。
- **`setFocus(predicate)` 是压暗不是移除。** 图是空间记忆，节点一被删掉，
  用户再看到会当成换了一张图，得重新找一遍位置。
- **边的过滤要求两端都在焦点里。** 只判一端会留一片亮线，
  看起来像「亮着的那半边才是重点」，正好反了。
- **过滤维度 0.07、标签 0.12 是两个不同的值。** 节点压到 0.07 还能看见轮廓，
  标签压到 0.07 就是个脏点。
- **双击 = 进入，单击 = 选中。** 单击成本低（只想看看连着谁）；
  单击就跳页，用户就再也不敢点节点了，这张图就废了。
- **命中半径随缩放折算**（`Math.max(n.r, 10/k)`）。5.5px 的点用鼠标很难瞄，
  但把视觉做大会糊掉整张图。分开处理：手感是准的，画面是干净的。
- **引擎不认识业务路由。** 双击只发 `cm:graph-select`（CustomEvent，
  `detail` 里带 `{id,type,cid,kid,label}`），跳哪个 URL 由页面决定。
  哪天把这张图用到「题目依赖图」上，引擎一行都不用改。

### 6.3.4 `/map` 页面：引擎不用改，页面自己组装

- 数据走 `<script type="application/json">` **不走 fetch**。首屏就要画，
  多一次请求就多一次白屏，断网时这份数据也照样在。
- **后端只聚合不决定样式。** `map_data()` 输出 `kind` / `stage` 这类语义字段，
  不输出 `#58a6ff`。后端一旦吐颜色，做浅色主题就硬了。
- **`aria-pressed` 当开关态选择器**（`.map-btn[aria-pressed="true"]`）。
  属性是「事实」，类名是「样式」，样式跟着事实走就不会出现「看着开了其实没开」。
- **状态行 `min-height` 固定占位。** 不占位的话第一句反馈出现时整页下顶 20px，
  画布 resize 会触发图重排——用户点个按钮，图自己动了。
- **搜索候选用 `pointerdown` 而不是 `click`。** `click` 之前 input 会先 `blur`，
  候选列表在点击生效前就关了。
- 三种边三种视觉：`tree` 短暗骨架、`seq` 虚线阅读顺序、`dep` 暖色真依赖。
  **同一条边只留 rank 最高的类型**（`dep > tree > seq`），否则两个节点之间画两条线。

## 六之四、`/memory`：把内存画出来

指针、数组步长、字符串末尾的 `\0`、结构体填充、递归栈帧——这五件事在代码里
各是一行，在内存里各是一块。光读代码看不出来，所以单独做了一页，
**同一块内存一步一步地变**。

### 6.4.1 三方分工（别把它们混在一起）

| 谁 | 管什么 | 不管什么 |
| --- | --- | --- |
| `tools/build_demos.py` | 算偏移、算填充、算每个字节、算地址 | 不画 |
| `static/js/lib/memview.js` | 把**一份**布局画出来 | 不知道有「第几步」 |
| `static/js/memory.js` | 场景切换、单步、播放、代码高亮、地址栏 | 不碰字节 |

偏移和字节**一律由生成脚本算，不许手写，也不许前端复算**。手写几百个十六进制
必然写出「偏移 4 的 int 只占 2 格」这种自相矛盾的数据，而页面看着完全正常，
只是数字对不上——这是最难发现的一类错。所以前端连一次地址都不算。

`build_demos.py` 跑完会调 `validate()` 自校验，不过就不写盘。

### 6.4.2 数据层的三条不变量（都曾经被踩过）

1. **帧的基址由「帧的身份」决定，不能由记忆化条目的序号决定。**
   同一个 `main` 每步的变量数都不一样（变量是一个一个加上去的），
   按 `len(self._memo)` 分配基址会让 `main` 每步整体下移 `0x40`——
   于是「`c` 的地址」每步都在变，而这一步根本没碰过 `c`。
   这个 bug 曾经真的存在：**每一步单独看都自洽**，页面上完全看不出异常，
   只有把三步的地址并排抄下来才暴露。现在 `validate()` 会查基址稳定性。
2. **栈帧和堆帧的起始地址字段都叫 `base`。** 堆帧当初写的是 `addr`，
   前端读不到就抛异常，**整个堆场景整片空白**，而校验只遍历格子不看帧，一路全绿。
3. **「什么都不画」的一步必须有 `ghost` 文案。** 否则右侧一块空面板，看着像加载失败。

### 6.4.3 两条状态装饰，别混用

- `state`（`new` / `chg` / `read` / `bad`）：**数据自带的长期身份**，描边 + 极淡底色。
- `is-enter` / `is-flash`：**这一刻发生的变化**，只演一次，420/620ms 后自己摘掉。

混在一起就会出现「已经过去三步的格子还在闪」。端到端专门有一条断言：
走完 36 步后 `.is-enter` / `.is-flash` 一个都不许残留。

### 6.4.4 几何硬约定（`memory.css` 文件头也写了）

- **`.mem-stage` 是箭头的坐标系**，`memview.js` 用 `getBoundingClientRect()`
  相减算 px。所以 `svg` 必须正好等于 `.mem-stage` 的 padding box，
  **不能用 `viewBox` 缩放**——一缩放，「算出来的 120px」和「画出来的 120px」
  就不是一回事。端到端有断言比 `svg.clientWidth === stage.clientWidth`。
- `.mem-stage { padding-right: 92px }` 是给同区拱形留的路（`bulge` 最大 26+13×2）。
  改 JS 的 `bulge` 公式时这里要一起改。
- `.mem-stage { width: max-content }`：宁可让 `.mem-scroll` 横滚，也不要压扁格子。
  压扁之后 14 字符的地址行会折行，整块高度就乱了。
- **格子宽度有两个下限**：按字节数算出来的，和「地址行 / 值行放得下」算出来的，
  取大的那个。只按字节数定宽的话，`char` 的格子内宽只有 64px，地址会被截成
  `0x7ffd5c2a4…`——被截掉的恰好是区分变量的那两三位。

### 6.4.5 交互上几个「不做就会难受」的细节

- 地址栏用 `history.replaceState`，**不是**给 `location.hash` 赋值：
  后者按十次「下一步」就要按十次返回才能离开。
- 高亮行滚进视野用 `block: "nearest"`，不是 `"center"`：相邻步只差一行，
  居中会让代码块每步都跳一下。
- `visibilitychange` 时暂停自动播放：看不见的时候继续走，回来刚好错过中间几步。
- 场景列表**服务端渲染**：它不会变，没必要等 JS 再画一遍让首屏先空一下。
- 代码高亮是自己分词的（一个正则五组捕获）。全项目只有这一页用，
  加 20KB 依赖只为染几个颜色不划算。

## 六之五、`/ops`：给自己看的运维页

`metrics.py` 在判题的关键路径上打点，落盘 `data/metrics.json`（滚动窗口）。
`/ops` 把这份快照画成四张图 + 一条耗时构成条 + 一张慢请求表。

- **首屏服务端渲染，轮询再更新**：`/api/ops` 和模板渲染的是**同一份快照**，
  所以端到端有一条「`data-k` 与 `/api/ops` 逐项一致」的漂移断言。
  两条数据源分别算一遍，迟早会不一样，而且不一样的时候两处都「看着对」。
- **计时器不能套在 `subprocess` 外面。** `/ops` 的 `other` 曾经恒为 0，
  真因就是只在外面记了一次总耗时，编译和运行各自没打点。
- 有新判题进窗口时数字闪一下，闪完要**摘掉类名**，否则 padding 留在元素上。

### 6.5.1 失败分两堆：`verdicts.py` 的 owner 三分法

`/ops` 上真正的结论不是「AC 率多少」，而是**这次失败是谁的锅**：

| owner | 谁 | 有哪些 | 该谁去修 |
| --- | --- | --- | --- |
| `user` | 学生写错了 | AC / WA / TLE / RE / CE | 不修，本来就是教学效果 |
| `system` | 判题机坏了 | **只有 SE** | 我，去 `/admin` 看是哪一项 |
| `none` | 没有对错语义 | OK（游乐场跑通） | 谁也不修 |

裁决码的数字借自 judge0 的公开状态码（AC=3 / WA=4 / TLE=5 / CE=6 / RE=11 / SE=13），
只借心智模型，不实现它的协议——面试时说「我的裁决码和 judge0 对齐」比说
「我自己编了六个字母」可信。定义只在 `verdicts.py` 一处，`judge.py` 出裁决、
`metrics.py` 分责任、模板上色，三方读的是同一张表。

**AC 率的分母是 `scored`，不是总次数**：`scored = 全部 − owner=="none"` 的条数。
把游乐场那次 `OK` 放进分母，数学上没错、语义上错——「试了 20 次 Hello World」
能把 AC 率稀释掉一半。`_health()` 里 CE/TLE 的判据分母也必须一起改成 `scored`，
否则同一页上会出现「健康判据说编译失败 60%、下面的卡片写 45%」。

**账本不可追溯改写**：`metrics.record()` 落盘时就把 `fault`（归属）写死，
而不是聚合时按裁决码现查。只有老记录没有这个字段才回退反查——回退是兼容，
不是设计，别把新逻辑写进回退分支里。

### 6.5.2 `/api/verdicts`：契约要能被 GET

裁决码表同时挂在 `/api/verdicts`（不要登录，里面没有一个用户数据）。
理由和 judge0 把状态码做成公开常量一样：契约**能被取到**，才谈得上三方读同一份。
`tools/crawl.py` 会去 GET 它并验字段——页面全绿不代表接口没坏，
这个接口加进来的那一轮就是「整站全 200（当时 133 个页面，现在多了 `/admin`，是 134）、
它自己 500」。**页数会变，这个教训不会变**：坏在契约上，没有任何页面会露出痕迹。

## 六之六、`/training`：训练模式

和「做题」不是一回事。做题考「会不会写」，训练考「手比脑子快不快」：
三分钟里连着答十几张，答错立刻告诉你差在哪，答错的隔三张再问一遍。
数据结构只有一份 `data/training.json`（`build_training.py` 生成，166 张）。

三种操练：**trace**（看代码写输出）/ **cloze**（从词库补洞）/ **flash**（概念卡自评）。

### 6.6.1 三种操练共用一套骨架，不要拆成三个状态机

`renderCard` 分出去三支，但 `step / settle / advance / requeue / finish` 只有一份。
理由：这三件事（连击、计时、回炉）如果各写三遍，三遍里必有两遍不一致，
而且不一致的时候两边都「看着对」。加第四种操练时只写 `renderX + judgeX`。

### 6.6.2 卡片全部是算出来的，一张也没有手写

- `trace` / `cloze` 的题面来自 `answers.json` 的参考代码 + `question_bank.json` 的用例
- `flash` 的要点来自 `courses.json` 的正文段落
- 挖空**不随机**，按 p1→p8 优先级挑 token：`->` / 格式符 / 比较运算 / `sizeof` /
  `NULL` / 标准库函数 / `&` / `*`。每行最多一个洞，同一 token 最多挖两次，
  三个洞全同词就弃卡（否则「三个空都填 printf」能靠排除法蒙对）
- 构建必须可复现：自己写 PRNG（FNV-1a 播种 + LCG），不用 `random.shuffle`。
  同一份源数据每次生成同一份 `training.json`，否则「改了哪张卡」永远说不清

挖空产出的是**分段结构** `[{t:"text"},{t:"blank",i:0,answer:"->"}]`，
不是 `__BLANK_0__` 占位符——占位符要靠字符串替换，一旦参考代码里本来就有下划线就完蛋。

### 6.6.3 判分：归一化只做三件事

前端 `normText` 与 `judge.py::_normalize` 逐条对齐：CRLF→LF、去行尾空白、去首尾空行。
**不折叠行内空白，不改大小写**——`printf("%d", x)` 和 `printf("%d",x)` 的输出对判题
其实等价，但训练的目的是让人背准，放宽了就是在教错的写法。

- **判分只用 `textContent` 写 DOM，绝不拼 `innerHTML`。** 参考代码里有 `%s`，
  笔记里有尖括号，拼字符串进去就是一处 XSS 或一处被吃掉的标签。
- **计时归零 ≠ 判错。** 归零不锁输入、不写错误反馈，只在提交时降级为
  「连击冻结 + XP 减半」，反馈走 `tr-fb-slow`（黄）而不是 `tr-fb-bad`（红）。
  把超时直接判错，人会开始乱写，训练就变成抽奖了。
- **自评不给连击倍率。** 倍率会让人为了分数谎报「记得」，那张卡就再也回不来了，
  回炉机制等于被自己关掉。「模糊」算没答对（连击断），但给 3 XP（忘了是 0 XP），
  所以自评说真话仍然划算。
- **回炉 = Leitner 极简版**：插回「隔三张」的位置（`idx + 1 + REQUEUE_GAP`），
  同一张最多回炉两次。立刻重问是在考短期记忆，隔太远等于没回炉。

### 6.6.4 这一页踩过的坑（每条都是真 bug，不是设想）

| 现象 | 真因 | 现在怎么做 |
| --- | --- | --- |
| 答错一次就卡死，Enter 没反应 | 判断「Enter 继续」时要求焦点不在输入框，可答错后焦点一直在 textarea 里 | 判完把输入 `readOnly`，Enter 一律继续（按钮上的「（Enter）」才是真的） |
| 答错后立刻跳下一张，红字一闪而过 | 同一个 Ctrl+Enter 判完分继续冒泡到 document，又触发了一次 `advance` | `e.defaultPrevented` 先挡一道；焦点在按钮上时交给原生 click，避免连翻两张 |
| 逐行 diff 从来没显示过 | `settle` 里的 `setFeedback` 整块清空反馈区，把刚铺好的对比表擦了 | `setFeedback` 只清文字、留着 `.tr-diff` 节点；`setFeedback("")` 才连表一起清 |
| 错题清单和落盘的错题空着 | 回炉那题后来答对，把 `results[id]` 从 `bad` 覆盖成 `ok` | `results`（最近一次，进度点用）与 `missed`（错过就留名）分开记 |
| 填空题报 `TypeError: appendChild` | 一行是「文本段 + input + 文本段」的混合数组，`appendChild` 只收 Node | 逐项判 `typeof`，字符串走 `createTextNode` |
| 计时条走成弹簧 | 给 `#tr-timer-fill` 加了 CSS `transition`，和 rAF 互相追 | 只用 `transform: scaleX()`，且 CSS 侧**刻意不加 transition** |

### 6.6.5 版式硬约定（`training.css` 文件头也写了）

- `.tr-stage` / `.tr-actions` / `.tr-feedback` 各钉一个 `min-height`（420/38/46px）。
  不钉的话 trace 卡和 flash 卡差 300px，连答几张眼睛每次都要重找提交按钮。
- `.tr-layout` 的右栏必须 `minmax(0, 1fr)`。写 `1fr` 会被代码块的最小内容宽度顶出横滚
  ——`1fr` 的最小值是 `auto`，等于「不小于内容」。
- `.tr-code-no` 用 `position: sticky; left: 0` 且给不透明底色：横滚时行号跟着滚出去，
  人就开始数格子。
- `.tr-summary[hidden] { display: none; }` 必须显式写：author 样式的 `display: grid`
  优先级高于 UA 的 `[hidden]`，不写就是「点了 Esc 覆盖层还挂着」。
- 正确率环用 `conic-gradient` + `::after` 挖洞，不用 `mask`：
  部分 Windows 版 Edge 上 `mask` 会导致整块不渲染。
- `100vh` → `calc(100vh - 92px)`，给左栏吸顶留出顶栏高度。

## 六之七、判题沙箱（`sandbox.py` + `sandbox/runner_*.c`）

### 6.7.1 三层结构，各自只干一件事

```
judge.py       编译、跑用例、比对、裁决 —— 它不知道沙箱怎么实现
   ↓ 只认一个 dict
sandbox.py     平台无关的封装：构建宿主 / 探针 / 解析报告 / 自检 / 计数
   ↓ 只走一份 report 文本
runner_win.c   Job Object                ┐ 输出格式严格一致
runner_linux.c setrlimit + setuid + seccomp ┘ 平台差异不外泄
```

**「同一份 report 格式」是这套东西能维护的唯一原因。** 两个 runner 输出完全相同的
`key=value` 文本（`exit_code` / `timed_out` / `output_limited` / `wall_ms` / `cpu_ms` /
`peak_job_mem` / `peak_proc_mem` / `total_procs` / `limit_*` / `note`），
`sandbox.py` 只写一份解析器。想加一个指标，两边一起加，`sandbox.py` 一行不动。

### 6.7.2 超时只能看 `timed_out`，不能看退出码

runner 自己用 124 表示墙钟超时，而用户程序 `return 124` 是完全合法的写法。
早年按退出码判断，把一道参考答案正好 `return 124` 的题误判成 TLE。
现在判据只有 report 里的 `timed_out` 字段。

同类的坑还有一处：**Windows 异常码必须先于 POSIX 信号码翻译**。
`0xC0000005` 存成 int 是 `-1073741819`，还原成无符号后同样 `>= 256`，
若先按信号解释会得到「信号 3221225221」——一个不存在的信号号，比不翻译更糟。

### 6.7.3 `-O2` 下 gcc 的除零不是 `0xC0000094`

它把「除数可能为 0」的除法编成一个条件 + `ud2`（非法指令），跑起来是 `0xC000001D`。
`_WIN_EXC` 表里必须同时有这两条，否则学生会看到「非法指令」并以为编译器坏了。

### 6.7.4 自检是五组实验，不是检查版本号

`_SELFTEST_CASES` 每一项都带 `key/name/desc/src`，真编译、真跑、真读报告：

| 组 | 判据（可证伪的那种） |
| --- | --- |
| baseline | 输出 `ok`、退出码 0。**对照组不过，下面四项的结论全是假的** |
| loop | `timed_out=True` 且 `cpu_ms` 接近上限 |
| bomb | 峰值内存 ≤ 配额 × 1.25，**且**程序自述里没有「全部成功」 |
| shell | 逃逸文件**不存在**，**且** `system()` 没有返回 0 |
| spam | `output_limited=True` 且实际写出量贴着上限 |

内存炸弹不能只看退出码——它可能因为别的原因退出；命令逃逸不能只看进程数——
实测正常程序的 `total_procs` 也是 2（Job Object 把 runner 自己算进去了），
这个判据一度想用，查清后放弃了，改成看文件副作用。

### 6.7.5 TLE 再分三档：算得慢 / 算等参半 / 在等

TLE 这一个徽章对学生几乎没信息量，而「算得慢」和「在等」的改法是两回事：
前者改复杂度和常数，后者去查 `scanf` 是不是在等一个永远不会来的输入。
所以 `sandbox.attribute_timeout()` 会再给一次归因，落在 `tle_kind` 上——
注意它**不是裁决码**，TLE 仍然只有一个，`/ops` 拿它做分布。

判据只有一个比值：**CPU 时间 ÷ 墙钟**（`wall_ms` 是实测，不是配置推出来的）。

| `tle_kind` | 判据 | 面板上的说法 |
| --- | --- | --- |
| `cpu` | 比值 ≥ 0.85，或 report 里写着「CPU 时间超限」 | 算得慢 |
| `wait` | 比值 ≤ 0.10 **且** CPU < 150ms | 在等 |
| `wall` | 其余（算了一部分，也等了一部分） | 算等参半 |
| `""` | 没超时 / 降级路径拿不到 CPU 时间 / `wall_ms` 记到 0 | 未归因 |

绝对值下限 150ms 不能省：一次 200ms 的正常程序 CPU 只有 15ms 也很正常
（起进程、动态链接本身就要几十毫秒），占比 8% 说明不了它在等。

这一节记三个真 bug，都是「数字对得上、含义是错的」那类：

1. **拿 CPU 时间比「CPU 配额」是死判据。** 配额本身 = 1.5×墙钟 + 1000ms，
   墙钟永远先到，`cpu >= 配额*0.9` 在默认配置下**永不成立**。第一版就这么写的，
   结果一个纯 CPU 死循环落进了「算等参半」档——面板上最该分开的两档合成了一个。
   判据的参照系必须是不依赖配置的实测值。
2. **`wall_ms` 曾经记在收尸之后。** Windows 侧在 join 读线程、`WaitForSingleObject`
   收尸之后才打点，多记了约 180ms；Linux 侧记在 `wait4` 之后。于是 3 秒的题
   报告上写 3.6~3.7 秒，四个用例差值稳定得可疑。现在两边都在「进程结束/被判
   超时」那一刻定格。**归因拿 wall 当分母，分母错一点，结论就反了**——
   300ms 的时限里 180ms 的启动开销能让「算得慢」变成「在等」。
3. **宽限 500ms 让判据和承诺对不上。** 题面写 3 秒、报告写「3 秒内没结束」，
   实际 3.5 秒才砍，跑了 3.3 秒的程序判通过。现在两边统一
   `LIMIT_GRACE_MS 150`（两个 runner 同名同值，否则同一份代码在 Windows 上 AC、
   在容器里 TLE）。实测 TLE 的 `wall_ms` 稳定落在 3150~3165ms。

验收用的探针是 `tools/probe_tle.py`：五个程序（纯死循环 / `Sleep(9000)` /
先算 1.5 秒再睡 / 正常小程序 / **四线程烧 CPU**），打印 `wall_ms`、`cpu_ms`、比值和归因，
**并断言每项落在预期档，不符就退出码 1**。第一版只有 print——判据要是退回
「比 CPU 配额」，它照样五行数字、照样退出 0，那是看板不是闸门。
**先有数字再定阈值**——靠感觉定的阈值，第一次遇到真超时就会分错档，
而那时判断对错的依据已经被改掉了。连跑两次的实测（写区间不写单值：这四项
每次都会漂几十毫秒，写死一个数会让人以为它是恒定值）：

```
cpu-spin  wall=3151~3161 cpu=3031~3125 ratio=0.96~0.99 -> cpu
sleep     wall=3152~3162 cpu=15~46     ratio=0.005~0.015 -> wait
both      wall=3152~3163 cpu=1484~1515 ratio=0.47       -> wall
quick     wall=23~33    cpu=15~31      （没超时，不归因）
mt        wall=3153     cpu=12421      ratio=3.94       -> cpu
```

`mt` 那一行是新加的，`ratio` 大于 1 是正常的：四个线程同时烧，CPU 累计时间是
墙钟的四倍。它挡的是一个真实存在过的记账 bug——job 的 CPU 配额触发时，内核用
`STATUS_QUOTA_EXCEEDED`（`0xC0000044`）结束进程，`timed_out` 若不认这个码，
四线程程序就以 `exit=-1073741756` 落进「运行时错误」：面板上写「你的程序崩了」，
真相是「你的程序太慢」。这两句话的改法完全不同。

四行的 `wall` 都贴着 3150（3000 时限 + 150 宽限），这是第 3 条的旁证；
`quick` 的 30ms 左右是第 2 条的旁证——改之前它显示 167ms。

顺带记一笔 Windows 的 CPU 配额有多**不可靠**，免得下次有人把它当主闸：
实测 500ms 的配额，单线程烧 CPU 跑到 `cpu_ms=5078` 都没被掐（墙钟先到），
四线程才会在墙钟之前被掐。所以它只能当「额外的早退信号」，判超时仍然靠墙钟；
它唯一必须做的事，是**被掐的时候别把账记错**。

## 六之八、`/admin`：和 `/ops` 的分工

读者不同，问题不同，所以是两页：

- `/ops`：**判题过程本身跑得怎样**——延迟、并发、失败率，全是「最近 400 次判题的统计量」。
- `/admin`：**这台机器和这个站点整体健不健康** + 沙箱是否真生效。

三条不妥协的约定：

1. **沙箱自检只能放 `/admin`。** 自检是「某一刻跑的五次实验」，是离散状态；
   放进 `/ops` 会让读者把「自检通过」当成「这一分钟的系统状态」——它不是。
2. **指标不重算。** `/admin` 直接切 `metrics.snapshot()` 的字段，
   两页上同一个 P95 必须一模一样。两份聚合逻辑一定会漂移，而面板一旦自相矛盾，
   读者就再也不信它了。
3. **自检异步点火。** Windows 上实测 7.7 秒，放请求里会让浏览器以为卡死；
   更重要的是——用户不耐烦按两次，两个自检会同时抢 CPU，而那恰恰是沙箱最不该被干扰的时候。
   接口只负责起线程并立刻返回，前端拿 `selftest_running` 双速轮询（5s / 1.5s）。

### 6.8.1 「没配白名单」必须写在页面上

`/admin` 能看到本机目录、内存、进程参数 (以及点一下就能真编译五个程序)。它的鉴权只有一条：

```
CMASTER_ADMIN=alice,bob      → 只有这两个账号能开；其余人拿 403
（不设）                      → 任何登录账号都能开，但页面顶部挂一条黄横幅说明这件事
```

两个决定的理由：

- **不设就明说，而不是不设就全开还不吭声。** 「没配白名单」是一个决定，不是默认值。
  只写在代码注释里等于没人知道；下一个人会以为这页本来就有权限控制，把横幅删掉
  就当加固过了——比一开始就没有更糟，因为它留下了一种「已经管过了」的印象。
- **403 而不是 404。** 这一页的存在是公开信息（首页就链着它）。用 404 假装不存在，
  只会让维护者去翻日志找「为什么这页没了」，而真正的原因（不在名单里）一句话就能说清。

`_admin_gate()` 返回 `None` / `"open"` / `"denied"` 三个值，把「放行**且**没配」
和「放行**因为**配了」分开——这两种状态在页面上要显示不同的话，
合成一个布尔值就一定会写成「登录了就是管理员」。

三个入口都过同一道 gate：`/admin`（403 用 `error.html` 渲染，不是白屏）、
`/api/admin/panel` 与 `/api/admin/selftest`（都回 `{"ok":false,"error":"forbidden"}`
+ 403）。后者尤其要挡——它会真的编译并跑五个程序，等于一个「远程烧 CPU」的按钮。

两条分支都真跑过，不是读完就算：

```
不设 CMASTER_ADMIN      → /admin 200，页面含 adm-acl 横幅 + adm-lede is-warn
CMASTER_ADMIN=someoneelse → /admin 403，crawl 记「预期拒绝 1」，退出码仍 0
```

第二行需要另起实例才有 403 可看（常驻开发实例不配白名单），
配方见第八节 `CMASTER_BASE` 那一段。

### 6.8.2 顺手踩到的一个 Jinja 坑

```jinja
{{ a.sandbox.selftest.items | length }}   {# ✗ TypeError #}
{{ a.sandbox.selftest['items'] | length }} {# ✓ #}
```

Jinja 取属性优先于取键，而 dict 自带 `items()` 方法，`.items` 拿到的是绑定方法。
阴险的地方在于：自检没跑过时这段在 `else` 分支里，**只有跑过一次之后页面才 500**。

## 七、安全边界（这一节在沙箱做完之后被整段重写过）

判题执行的是**陌生人写的 C 代码**。上一版这里写的是「只有四层保护，这不是系统调用级沙箱」，
那是诚实的，但也是认输的。现在做完了，边界重述如下。

### 7.1 隔离做了四层，每层挡的东西不一样

| 层 | 机制 | 挡什么 | 挡不住什么 |
| --- | --- | --- | --- |
| 资源量 | Windows Job Object / POSIX `setrlimit` | 死循环、内存炸弹、fork 炸弹、输出炸弹 | 写得很快但很规矩的代码 |
| 进程拓扑 | `KILL_ON_JOB_CLOSE` / `PR_SET_PDEATHSIG` + `setpgid` | 判题进程崩了留下孤儿进程 | 进程内部的死循环（那归超时管） |
| 权限 | 降权到普通用户 / `nobody` | 改系统文件、读别人的东西 | 同权限用户自己的文件 |
| 行为 | Linux `seccomp-bpf` 白名单 | `execve` / `socket` / `ptrace` / `mount` / `setuid` 等一整套 | **Windows 没有这一层**（见 7.3） |

关键在最后一列。**四层加起来的结论是「可以给一个班用」，不是「可以给公网用」。**

### 7.2 报告文件为什么必须放在工作目录外面

`workdir` 是用户程序的工作目录，它对那里**有写权限**。所以报告不能放那儿：
恶意代码可以抢先建一个同名文件（Windows 上还能用 `FILE_SHARE_NONE` 占住），
让判题器读到自己写的裁决依据。`sandbox.py` 里是单独 `mkdtemp(prefix="cmsb_")`
放 in/out/err/report，跑完 `rmtree`。

这条不是理论：**任何「把控制信息放进被控进程可写目录」的设计都是同一类错**。

### 7.3 Windows 侧的能力上限，写清楚免得吹过头

Job Object 能限内存、限进程数、限 CPU，但它**管不了行为**——挡不住读 `C:\Users\…`、
挡不住连外网、挡不住 `ptrace` 等价物。那边的对策是权限（普通用户 + 文件 ACL），
不是 syscall 过滤。所以在本机（Windows）上跑，能说的是「资源隔离」，
不能说「系统调用沙箱」。Linux 侧才有 `seccomp-bpf` 那层。

讲这个项目的时候，这个区别一定要主动讲出来——主动说清能力边界的可信度，
远高于一个「全栈安全」的形容词。

### 7.4 fail-closed：沙箱坏了就拒绝判题，不裸跑

`judge.py` 现在区分两种「没沙箱」：

- **本来就拿不到**（没编译器编不出 runner、平台不支持）→ 降级 `subprocess`，
  `sandboxed=False` 并计数，页面照常可用。
- **本该可用却初始化失败**（runner 返回 126 / note 含 `SANDBOX_SETUP_FAILED`）→
  **拒绝执行**，`error="sandbox_init_failed"`，判 `SE`（系统错误），不是 `RE`。

理由写在 `sandbox.py` 模块头：*沙箱坏了就裸跑用户代码，比一开始没有沙箱更糟，
因为它把降级藏起来了*。而判 `RE` 是错的——用户代码可能完全正确，
红字会指向错误的方向。

### 7.5 还没被证明的那一块

`runner_linux.c` **编写时手边没有 Linux 环境**（开发机是 Windows，WSL 被策略禁用，
没有 Docker）。这是已知事实，不藏着，补偿方式是让代码自己证伪：

- 镜像构建期就会编译它（`Dockerfile` 里那行 gcc）——**编不过就炸 build**，
  而不是变成运行期的一次静默降级。
- 首次部署后在 `/admin` 点「重跑自检」，五组实验（对照组 + 死循环 + 内存炸弹 +
  命令逃逸 + 输出炸弹）真编译真跑。四项全绿才算这个文件成立，红了按 `note` 改。

**编译证据已经拿到**（2026-10-27 补）。用 `zig cc` 在 Windows 上交叉编译到
`x86_64-linux-gnu`，命令与镜像里那条 gcc 的参数逐字对齐：

```
python -m ziglang cc -target x86_64-linux-gnu -O2 -std=gnu11 -Wall -Wextra \
    -o /tmp/runner_linux.elf sandbox/runner_linux.c
```

结果是 **`-Wall -Wextra` 下零告警**（不是「警告都无害」，是一条都没有），产物
46840 字节，ELF 头核对为 `64-bit LSB / EXEC / machine=0x3e`，动态解释器
`/lib64/ld-linux-x86-64.so.2`，要求的最高 glibc 符号是 `GLIBC_2.17`
（bookworm 是 2.36，可以满足）。二进制里能查到 `seccomp` / `prctl` /
`setrlimit` 三组字符串，说明三个过滤层都真的编进去了。

**这份证据的边界要说清楚，不然它会被当成比实际更强的结论**：

1. 它证明的是「这份 C 代码在 gnu11 + glibc 头文件下能编过、且结构完整」，
   **不证明 seccomp 过滤器在真内核上行为正确**——那只能真跑。
2. 产物是 zig 自带的 glibc 链出来的，**不能拿去部署**。镜像里由 bookworm 的
   自己的 gcc 现编（`Dockerfile` 那行），两者编译器不同。这份 ELF 唯一的用途
   是「证明源码可编译」，用完即弃，`.gitignore` 里也排除了同名的产物路径。
3. 顺带做了一次静态的**跨平台契约核对**（这类不一致在 Windows 上永远发现不了）：
   - `sandbox.py` 传的 13 个参数（`--limit-ms` / `--limit-mem` / `--limit-proc` /
     `--limit-cpu-ms` / `--limit-out` / `--limit-err` / `--in` / `--out` / `--err` /
     `--report` / `--cwd` / `--as` / `--no-seccomp`）与 `runner_linux.c::parse_args`
     里的 `strncmp` 前缀长度、偏移量逐个对上；
   - `runner_linux.c` 与 `runner_win.c` 写出的报告键**各 16 个，完全相同**；
     `sandbox.py` 读的 9 个键在两个 runner 里都存在。
     （`peak_proc_mem` 写了但没人读，留着无害，是下次清理的候选。）
   - 这一条从「手工 diff 过一次」升级成了 `tools/check_runners.py`：宽限、
     配额公式、16 个报告键、`CPU 时间超限` 这条 note、以及「墙钟在收尸之前
     定格」的顺序不变量，逐项断言，不符退出 1。手工 diff 的结论只活在那一次
     会话里；下次改任一侧，没有任何东西会提醒另一侧。

Windows 侧的五项自检和五种裁决已经在开发机实测通过（见第八节）。
**Linux 侧这份改动之后连编译证据都没有了**——本机无 docker、WSL 被策略禁用、
无 clang，而上一轮用的 zig 环境已不在（pip 装 ziglang 下载到 3.4MB 就超时）。
所以 `runner_linux.c` 现在只有两样东西：源码级对齐，和 `check_runners.py` 的
静态对拍。**编译与运行证据都要上 Linux 才有**，这句话要一直挂在这里，
直到在目标机器上跑过。

### 7.6 部署侧还剩什么

`docker-compose.yml` 里已经落地了：只绑 `127.0.0.1`、根文件系统只读、
`cap_drop: ALL`、`no-new-privileges`、`mem_limit` 与 `pids_limit`。
`/admin` 的账号白名单也有了（`CMASTER_ADMIN`，见 6.8.1），但它只是「一个 env」，
不是一套权限系统：**没有角色概念、没有审计日志、谁改的谁不知道**。
公网部署仍建议再加：TLS 终止、按 IP 限流、把 `/admin` 拆到内网或单独端口。


## 八、交付验证记录（2026-10-27）

**六条命令、十行结果**。每行都能被机器判对错，没有一行靠眼睛看：
`crawl.py` 一次覆盖全站 / 静态资源 / 接口契约三行，最后两行是 puppeteer 脚本
（要浏览器，不在那六条命令里）。**别把表里的行数当成命令数**——上一版这里写
「四条命令、九行结果」，而当时命令已经是五条（超时归因、前端语法、判题器编译
都是各自独立的命令）；再往前还有一版写「四条命令、六个检查项」，漏掉的是超时
归因和前端语法这两行（它们有命令、没进表，读的人无从核对）。这一版加进来的
是 `check_runners.py`：它挡的错编译器一个字都不会说。

`crawl.py` 需要一份登录态的 cookie jar（`%TEMP%\cmck.txt`），没有会直接退出码 3。
复现方式就是一条 curl：

```
curl -s -c "%TEMP%\cmck.txt" -d "username=verify01&password=cmaster2026" http://127.0.0.1:5001/login
```

`verify01` 是验收账号，口令写在文档里是故意的（**闸门得能被下一个人直接跑通**）。
它只该存在本地 `data/users.json`，公网部署前删掉或改口令。

`/admin` 的 403 分支在常驻实例上**走不到**：它只在配了 `CMASTER_ADMIN`、且当前账号
不在名单里时才触发，而本地开发实例恰恰不配。想让这份代码从「读过」变成「跑过」，
就换个端口起第二个实例（密钥落盘复用，cookie 不用重取）：

```
set CMASTER_PORT=5002
set CMASTER_ADMIN=someoneelse
python app.py

set CMASTER_BASE=http://127.0.0.1:5002
python tools/crawl.py
```

（`ADMIN_USERS` 解析时带 `strip()`，**别把这个 strip 去掉**：cmd 里写
`set CMASTER_ADMIN=someoneelse & ...`，空格会被算进值里，得到的是 `"someoneelse "`，
账号永远匹配不上，而 403 照样出现——看起来完全正常，查起来毫无线索。
上面一行一条地写是第二道保险，不是唯一一道。）

预期拿到 `admin=403` 与 `预期拒绝（CMASTER_ADMIN 生效）1`，且**退出码仍是 0**——
把「配了白名单」算成故障，运维会把一次加固读成一次事故，而这两件事的处置方向相反。
`CMASTER_BASE` 就是为这一条加的，别当调试残留删掉。

| 闸门 | 命令 | 当前结果 |
| --- | --- | --- |
| 题库门禁 | `python tools/gate.py` | **60/60 AC**，60 题 157 个用例，约 24 秒 |
| 全站爬取 | `python tools/crawl.py` | **134 页全部 200**，异常 0 / 模板残留 0 / 空页 0 / 预期拒绝 0 |
| 静态资源 | （含在 crawl 里） | **29 个全 200**，含 6 个 CodeMirror 文件 |
| 接口契约 | （含在 crawl 里） | `/healthz` `/readyz` `/api/verdicts` `/api/ops` 全 200 且字段齐 |
| 超时归因 | `python tools/probe_tle.py` | **5/5 落在预期档**，档错退出码 1 |
| 前端语法 | `node --check`（逐个） | **13 个 JS 全过**，含 `admin.js` 与 `lib/` 四个 |
| 判题器编译 | `gcc -Wall -Wextra -O2 -std=gnu11 -c sandbox/runner_win.c` | **零告警**（GCC 16.2.0 / w64devkit） |
| runner 契约面 | `python tools/check_runners.py` | **7/7 成立**，报告键 16 个两侧逐一相同 |
| 浏览器端到端 | puppeteer 脚本 | 全站 **61/61**、`/map` **65/65**、`/ops` **29/29**、`/memory` **73/73**、`/training` **67/67**，控制台错误 0、失败请求 0 |
| 内存几何审计 | 同上脚本单独一支 | 36 步全过：无格子重叠 / 无箭头越界 / 无地址被裁 |

`crawl.py` 的六个设计，别简化掉：

- **登录态必须先验，否则「全绿」是假的。** 未登录时 Flask 302 到 `/login`，
  `urllib` 跟着跳、拿到 200——所有页面全是同一份登录页，检测结果一片绿。
  这个假绿掩盖过 `/ops` 的 500 整整一轮。现在有 `PROTECTED` 名单 +
  `is_login_page()` 判据（页面里既有 `name="password"` 又提到 `/login`），
  命中就单独记 `denied` 并 `return 3`。**未登录时的全绿没有任何意义。**
- **403 是「预期拒绝」，不是异常。** 同一页在配了 `CMASTER_ADMIN` 时可以拒绝你，
  这和站点坏掉是两件事。现在单独记 `guarded` 并计入「预期拒绝 N」，不影响退出码。
- **接口也要扫。** 页面全 200 不代表接口没坏：`/api/verdicts` 刚加进来那一轮
  自己 500，没有任何页面会露出痕迹。现在是「200 + 能解析成 JSON + 关键字段在」。
- **读 curl 的 cookie jar，`#HttpOnly_` 是数据不是注释。** curl 给 HttpOnly 的
  cookie 行加这个前缀，Flask 的 session 恰好就是 HttpOnly 的。原先按
  `startswith("#")` 一律跳过的写法，会把 jar 里**唯一有用的那一行**悄悄扔掉。
  它不常露脸：jar 里只要还有别的非 HttpOnly cookie，请求照样带过去，看起来登录态
  有效；等重登过一次、jar 里只剩 session 一条，就突然报「7 个受保护页面被抓成了
  登录页」——第一反应会去查登录过期，而其实一行都没过期。这个 bug 是重做验收
  账号口令时才暴露的，说明**「顺便重登一次」本身就是一种测试**。
- **模板残留检测不能只搜 `{{`**。C 的二维数组初始化 `int a[2][2] = {{1,2},{3,4}};`
  长得一模一样，会稳定误报。现在的判据是「花括号内含字母且不含 HTML 标签」。
- **静态资源清单按模板里真实的 `url_for` 路径写**，别猜。之前猜了三个全假 404
  （codemirror 实际在 `vendor/codemirror/lib/`、favicon 在 `static/favicon.svg`）。

端到端覆盖：登录 → 首页进度环弧线 + 数字滚动归位 → `/` 搜索（空查询入口 / 命中高亮 /
方向键 / 回车跳转）→ 知识点页（代码块壳 / 语言标签 / 复制变「已复制」/ 渐入 / 阅读条随
滚动增长 / `?` 面板 / 回到顶部）→ 判题 AC + `is-pass` + 逐用例展开 → 个人中心
（九徽章 / 连击条 / 热力图 26 列 187 格 / 锁定态虚线）→ 其余页面抽样 → 390px 窄屏 0 溢出。

`/map` 专项额外覆盖：模拟收敛（alpha 收敛 + rAF 停机 + 淡入完成）→ 点节点填侧栏 →
侧栏关系内跳不离开页面 → 搜索定位 + 状态行反馈 + 无结果提示 → 聚焦未读（**定点采样节点
圆心 alpha 验证压暗**，不是数全画布墨量）→ 滚轮缩放锚定光标（漂移 0.19）→ 拖空白平移 →
重置 → hover 提示 → 键盘选中且焦点留在 canvas → 390px 单栏 → 双击进入。

三条 canvas 测试手法，都是踩坑换来的，**别再退回旧写法**：

- **验证「某个节点变暗了」必须定点采样。** 数全画布 alpha 总和只能当粗指标——
  章节点和章名标签按设计不参与压暗，12 个大圆 + 12 段文字占的墨量把几十个小节点
  的变化淹掉了（实测只降 5.6%，判失败）。正确做法是取该节点圆心 3×3 的 alpha 最大值
  （取 max 是防止正好落在一条连线上读到线的透明度）。
- **验证「平移」必须从空白处起手。** 缩放后画布中心很可能正好压着一个节点，
  拖下去命中的是节点不是画布，`Δtx` 恒为 0。先扫一个「26 世界像素内无节点」的屏幕点。
- **验证「双击」必须走 CDP 且给 `clickCount: 2`。** 两个坑：
  `page.mouse.click(x, y, { clickCount: 2 })` 只派发一次 `click`，不合成 `dblclick`；
  用两次 `page.mouse.click()` 也不行，puppeteer 每次都填 `clickCount: 1`。
  浏览器的 `dblclick` 由 `clickCount` 决定，只能直接喂 `Input.dispatchMouseEvent`。

还有一个选择器坑：**`querySelector` 遇到逗号列表返回的是「文档顺序第一个命中任意一条」
的元素，不是「先试第一条、没有再试第二条」。** 写过
`a[data-edge='tree'], a[data-goto]` 想表达优先级，结果永远拿到排在前面的 `dep` 边。
要优先级就得查两次。

`/training` 专项额外覆盖：trace 一整轮（首张故意错 → 逐行 diff / 红态 / 不自动翻页 →
Enter 才走 → 其余带 CRLF + 行尾空白提交，验证归一化）→ 回炉插在隔三张的位置 →
连击倍率 10/11/12 → 总结页九项（对+错=实际张数 / 正确率与 `--pct` 一致 / 错题清单 /
锁滚动）→ 落盘 `sessions+1` 与 `cards` 记名 → Esc 关总结停在完成态 → 再来一轮 →
`beforeunload` 拦截 → 退出二次确认；cloze（点词库填 / 已用标记 / 自动跳空 / 提示扣 2 XP /
错空旁边贴正确答案且**不覆盖原值** / 判过只读）；flash（空格翻面 / 三档自评 / 记得 +1 连击
且固定 6 XP / 模糊断连击 / 忘了清零并回炉）；**超时等满 26 秒**（计时条 `is-out`、
输入不锁、无错误反馈、提交后走 `tr-fb-slow`、连击冻结、XP 10→5）。

### 8.1 沙箱与管理台（2026-10-27 补测）

`python sandbox.py` 直接跑，Windows 侧五组自检全绿：

```
自检  : 全部通过（7709ms）
  [PASS] baseline stdout='ok' 退出码=0 耗时=178ms 峰值内存=3.3MB
  [PASS] loop     timed_out=True 墙钟=3585ms CPU=3421ms（上限 3000ms）
  [PASS] bomb     峰值内存=195.6MB（配额 256MB）程序自述='malloc 失败于第 3 轮'
  [PASS] shell    逃逸文件=不存在 程序自述='system() 返回 -1' 进程数=2
  [PASS] spam     output_limited=True 实际写出=69632B（上限 65536B）
```

`judge.py` 五种裁决逐个验证：AC（`cpu_ms=15` / `peak_mem=3465216` / `sandboxed=True`）、
TLE（`cpu=3515`）、RE（段错误 → 「访问违例（野指针、数组越界或段错误）（0xC0000005）」）、
WA、CE（`Assertion failed: 1==2, file main.c, line 2` —— 临时目录前缀已清掉）、
以及自由运行 OK。

管理台五个端点，起真服务用 curl 验的（不是读代码判断）：

| 端点 | 结果 |
| --- | --- |
| `/healthz` | 200 `ok`，未登录可访问 |
| `/readyz` | 200，五项 check 全绿（compiler / datadir / sandbox / disk / sandbox_init） |
| `/admin` | 未登录 302 到 `/login`；登录后 200；不在 `CMASTER_ADMIN` 名单里则 403（页面用 `error.html` 渲染，不是白屏） |
| `/api/admin/panel` | 21 个 `data-k` 路径**逐个按 JSON 走了一遍**，0 条解析失败 |
| `POST /api/admin/selftest` | 返回 `{started:true}`；并发第二次返回 `{started:false, reason:"自检已经在跑了"}` |

自检从触发到出结果：`running=true` → 约 11 秒后 `running=false`、`5/5 通过`、
五项明细渲染进页面。`/api/admin/panel`、`/api/ops`、`POST /api/admin/selftest`
未登录一律 401。

本轮实修的两个 bug：`admin.html` 的 `selftest.items`（Jinja 取属性优先，见 6.8.2）、
`runner_win.c` 的 `--in` 句柄不可继承（`CreateFileW` 的 `lpSecurityAttributes`
传 NULL 时句柄不可继承，`scanf` 一个都读不到，表现为「本机跑得好好的，交上去答案是乱的」）。


## 九、下一步（按性价比排序）

1. **章节式讲解播放器** —— 对标 PyMaster 的 `narration_player.js`（538 行）+
   `narration.css`（511 行）：把一章的知识点串成带旁白的自动播放，配时间轴与字幕。
   它补的是「第一次学」这条路径，现在页面全是「查阅式」的。
2. **AI 教练** —— 对标 PyMaster 的 `coach.js`（470 行）+ `coach.css`（503 行）：
   答错后按错误类型给一句针对性提示（不是给他答案）。这块要接模型，
   建议先把「错误分类 → 提示文案」的规则版做出来，模型只负责润色。
3. **内容扩容** —— 每章从 4 个知识点往 6–8 个扩，题目 60 → 120。
   纯体力活，适合边做边讲"我是怎么用门禁保证不写错答案的"。
4. **判题时间轴** —— 把「编译 / 逐个用例 / 比对」三段耗时画成一条横向时间条
   （`tween.js` 已经能做片段生长的补间）。比一个总数更能说明"慢在哪"。
5. **在真 Linux 上跑一次 `/admin` 自检** —— 见 7.5。这不是功能开发，是补证据：
   `runner_linux.c` 目前只有「编译通过」这一条，五组实验一次都没真跑过。
   顺手把结果截图存进仓库，那比任何形容词都硬。

已经做掉、别再往这里加回：

- **可观测性**：`metrics.py` 打点 + `/api/ops` + `/ops` 面板（第 6.5 节）。
- **训练模式**：`training.js` 约 1300 行 + `training.css` 约 1490 行，e2e 67 项全过。
- **判题沙箱**：`sandbox.py` + `sandbox/runner_win.c` + `sandbox/runner_linux.c`，
  Windows 侧五项自检全绿、五种裁决全部实测（第 6.7、七节）。
- **可观测性面板**：`/admin` + `/healthz` + `/readyz`（第 6.8 节）。
- **容器化**：`Dockerfile` + `docker-compose.yml` + `docker-entrypoint.sh`（第七节）。


## 十、已知限制

- 单机、单进程、JSON 存储。多用户高并发不合适（但那本来也不是它的目标）。
- 无邮箱验证、无找回密码、无管理后台。账号仅 pbkdf2 存盐哈希。
- 判题只比 stdout，不检查文件副作用、不看 stderr（除非 RE）。
- 移动端能看能读，编辑器体验一般——CodeMirror 5 在小屏上本来就局促。
- 端到端脚本（puppeteer，不在仓库内）需要测试账号登录。现在用两个：
  全站/`/map`/`/ops`/`/memory` 用 `uxtest`（需要一点历史进度才验得出成就与热力图，
  用 `python tools/loadtest.py --user uxtest --password test1234 -n 18 --no-tle` 现场喂），
  `/training` 用 `loadtest`。换机器跑之前先用同样的用户名/密码在 `/login` 注册一个即可。
  仓库里的 `gate.py` / `crawl.py` 不需要账号，随时可跑。
- `data/metrics.json` 是 `/ops` 的数据源，跑过 e2e 之后里面会有一堆压测留下的样本。
  它是运行产物，删掉无害（`metrics.py` 会重建），但别当成固定输入去断言具体数值。
- `/training` 的成绩写进账号独立进度文件的 `training` 字段，不另开文件——
  训练也要算「今天学了」，streak、热力图、成就都读同一份进度。
  落盘的 `training.cards` 是「累计错次」表，只增不清（超过 600 条才头部裁剪）。
