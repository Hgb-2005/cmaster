#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CMaster —— C 语言学习平台。

结构刻意保持扁平：Flask + JSON 数据文件 + Jinja 模板 + 本地静态资源，
没有数据库、没有构建步骤、没有 CDN。解压即跑。

安全边界（重要）
----------------
判题是把学生代码编译成可执行文件再在**沙箱**里执行，分层如下：

  编译期：干净环境变量 + 独立临时目录 + 20 秒超时（judge.compile_code）
  执行期：sandbox/runner_win.c（Windows，Job Object）或
          sandbox/runner_linux.c（Linux，setrlimit + seccomp-bpf），
          由 sandbox.py 统一分派，两套实现输出同一份报告格式

Windows 侧做的是**资源隔离**（内存/进程数/CPU/输出的内核级配额，
ActiveProcessLimit=1 直接让 system() 失败，KILL_ON_JOB_CLOSE 保证不留孤儿），
不做 syscall 过滤——Windows 没有 seccomp 的对等物，假装有才是问题。
Linux 侧是容器隔离的缩小版：setrlimit + 降权 65534 + no_new_privs +
seccomp 黑名单，把 execve/fork/socket/ptrace/mount/setuid 全部拦成 EPERM。

沙箱拿不到（没编译器编不出 runner / 平台不支持）时降级到普通 subprocess，
结果里 sandboxed=False，/admin 上有计数；但「沙箱本该可用却初始化失败」
**不降级**，直接判 SE 拒绝执行——沙箱坏了就裸跑用户代码，比一开始没有更糟。

即便如此，公网部署仍然需要外层隔离（容器 + 只读根 + 网络关掉 + cgroup），
因为沙箱挡的是「用户代码想越界」，挡不住「我们自己的代码有洞」。
Dockerfile 与 /readyz 见仓库根目录。
"""

import atexit
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from hashlib import pbkdf2_hmac, sha256
from functools import wraps

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

import judge
import metrics
import sandbox
import verdicts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(BASE_DIR, "data")
DATA_DIR = os.path.abspath(os.environ.get("CMASTER_DATA") or CONTENT_DIR)
PROGRESS_DIR = os.path.join(DATA_DIR, "progress")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

# 进程启动时刻。/admin 上显示「已经跑了多久」——重启是运维最关心的事件之一，
# 而「刚重启过」和「跑了三周」在看指标时是两种完全不同的心情。
_START_TS = time.time()

app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=365 * 24 * 3600,
    # 模板改动即时生效。Flask 默认只在 debug 下自动重载模板，
    # 而我们是 app.run(debug=False) 起的（本地跑判题，不要 Werkzeug
    # 的重载器去反复重启子进程池），结果就是改一行 HTML 都要重启。
    # Jinja 的重载只比对 mtime，开销可以忽略，开的收益远大于代价。
    TEMPLATES_AUTO_RELOAD=True,
)

# 判题并发闸门：编译很吃 CPU，同时跑太多会把机器拖死
MAX_CONCURRENT_JUDGE = int(os.environ.get("CMASTER_CONCURRENCY", "3"))
_judge_sem = threading.Semaphore(MAX_CONCURRENT_JUDGE)

# /admin 的账号白名单。**空集合 = 没配 = 单账号本地模式**，任何登录用户都能开，
# 但页面上会挂一条明晃晃的横幅说明这件事（见 admin_page 的注释）。
#
# 为什么不是「一律只允许第一个注册的账号」：本地开发时第一个账号是谁取决于
# 我先注册了哪个，写进代码就成了一个隐式约定，换台机器就不成立。env 是显式的：
# 配了就是配了，没配就是没配，页面上也照实说没配。
ADMIN_USERS = {u.strip() for u in os.environ.get("CMASTER_ADMIN", "").split(",")
               if u.strip()}

# 判题指标。/ops 页面读它。路径放在 data/ 下，和进度文件同级，
# 已经在 .gitignore 里——运行时数据不该进版本库。
metrics.init(os.path.join(DATA_DIR, "metrics.json"))
# 进程退出时把内存里还没落盘的那几秒指标补写一次。
# 不挂这个的话，最后 5 秒的判题记录会随进程一起消失。
atexit.register(metrics.flush, True)
_users_lock = threading.Lock()
_progress_lock = threading.RLock()


# ---------------------------------------------------------------- 启动期加载

def _load_or_make_secret():
    """没有环境变量就落一个到 data/.secret，重启后 session 不失效。"""
    path = os.path.join(DATA_DIR, ".secret")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        key = secrets.token_hex(32)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        return key
    except OSError:
        return secrets.token_hex(32)


app.config["SECRET_KEY"] = os.environ.get("CMASTER_SECRET") or _load_or_make_secret()
# Flask 3 起 JSON_AS_ASCII 已废弃，改成直接配 json provider
try:
    app.json.ensure_ascii = False
except AttributeError:
    app.config["JSON_AS_ASCII"] = False


def _load_json(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_json(path, data):
    """独立临时文件再替换，读者不会看到半份 JSON。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=os.path.dirname(path), suffix=".tmp") as f:
            pending = f.name
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(pending, path)
    finally:
        if pending and os.path.exists(pending):
            os.unlink(pending)


def _load_content(name, default):
    # 数据目录可独立挂载；空目录也可以读取随仓库发布的课程。
    for directory in dict.fromkeys((DATA_DIR, CONTENT_DIR)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return default


COURSES = _load_content("courses.json", {"stages": [], "chapters": []})
QUESTIONS = _load_content("question_bank.json", [])
ANSWERS = _load_content("answers.json", {})

# 内存演示的布局数据由 tools/build_demos.py 生成（偏移、填充、字节全在那边算完）。
# 这里只负责整份读进来：71KB，一次读完常驻，不为每次请求再解一遍 JSON。
# 页面不做任何二次推算——前端只要自己算一遍地址，就一定会和这里生成的对不上。
MEMORY_DEMOS = _load_content("memory_demos.json", {"scenarios": []})

CHAPTERS = {c["id"]: c for c in COURSES["chapters"]}
QMAP = {q["id"]: q for q in QUESTIONS}

# 题目按章节分组，章节页底部直接挂「本章练习」
QS_BY_CHAPTER = {}
for _q in QUESTIONS:
    QS_BY_CHAPTER.setdefault(_q["chapter"], []).append(_q)

TOTAL_KP = sum(len(c["knowledge_points"]) for c in CHAPTERS.values())
TOTAL_Q = len(QUESTIONS)

# 步数是页头那个「N 步」用的。在 Python 侧算一次，模板里不要写
# sum(attribute='steps') 那种表达式——Jinja 对 dict 取属性写起来容易踩空，
# 而且这个数在渲染前就定死了，没必要每渲染一次算一次。
MEMORY_STEPS = sum(len(s.get("steps", [])) for s in MEMORY_DEMOS.get("scenarios", []))

# 训练卡片由 tools/build_training.py 从题库、参考答案、课程正文里算出来。
# 三种模式共用一份文件，因为它们本来就是同一批内容的不同问法：
# trace/cloze 是同一段参考代码，flash 是同一个知识点的正文。
# 前端只按卡片字段画，不做任何「从代码里推断答案」的事——那等于把
# 构建期的规则在前端重写一遍，两份规则早晚会漂移。
def _load_training():
    try:
        doc = _load_content("training.json", None)
        if (isinstance(doc, dict) and doc.get("modes") and
                all(doc.get("cards", {}).get(m["id"]) for m in doc["modes"])):
            return doc
    except (OSError, ValueError, KeyError, TypeError):
        app.logger.warning("训练内容不可读，尝试从课程重新生成")
    try:
        from tools.build_training import build_document
        doc = build_document()
        try:
            _atomic_json(os.path.join(DATA_DIR, "training.json"), doc)
        except OSError:
            app.logger.warning("训练内容已生成，本次仅保存在内存中")
        return doc
    except (OSError, ValueError, KeyError, TypeError):
        app.logger.exception("训练内容暂不可用")
        return {"modes": [], "cards": {}}


TRAINING = _load_training()
TRAINING_MODES = TRAINING.get("modes", [])
TRAINING_CARDS = TRAINING.get("cards", {})
TRAINING_COUNT = {m["id"]: len(TRAINING_CARDS.get(m["id"], []))
                  for m in TRAINING_MODES}
# 每种模式单次会话能开几张：卡片不够就按实际张数封顶，
# 不然「选 20 张」在一共 8 张的模式里会开出一个走不满的进度条。
TRAINING_SIZES = (8, 12, 20)


# ---------------------------------------------------------------- 账号

def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"),
                         bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def _read_users():
    return _load_json("users.json", {})


def _write_users(users):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=1)
    os.replace(tmp, USERS_FILE)


def _safe_user(name):
    """仅用于定位旧版进度，新版不再把显示名称用作文件名。"""
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fa5]", "_", name)[:40]


def current_user():
    return session.get("user")


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "请先登录"}), 401
            return redirect(url_for("login_page", next=request.path))
        return fn(*a, **kw)
    return wrapper


# ---------------------------------------------------------------- 进度

def user_storage_id(username):
    # 完整用户名参与计算，避免特殊字符、截断和大小写文件系统导致串号。
    return "u_" + sha256(username.encode("utf-8")).hexdigest()


def _progress_path(username):
    return os.path.join(PROGRESS_DIR, user_storage_id(username) + ".json")


def _legacy_progress(username):
    legacy_name = _safe_user(username)
    legacy_path = os.path.join(PROGRESS_DIR, legacy_name + ".json")
    if not os.path.isfile(legacy_path):
        return {}
    # 旧文件本身没有归属信息。碰撞时保留原件，不能猜给哪一个账号。
    owners = [name for name in _read_users()
              if _safe_user(name).casefold() == legacy_name.casefold()]
    if owners != [username]:
        return {"migration_notice": "检测到旧版多个账号共用的进度。原始文件已保留，"
                "当前账号已使用独立存档；旧记录需要确认归属后恢复。"}
    with open(legacy_path, encoding="utf-8") as f:
        data = json.load(f)
    _atomic_json(_progress_path(username), data)
    return data


def load_progress(username=None):
    username = username or current_user()
    if not username:
        return {"read": [], "solved": {}, "attempts": {}, "wrong": {}}
    with _progress_lock:
        path = _progress_path(username)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = _legacy_progress(username)
        data.setdefault("read", [])
        data.setdefault("solved", {})
        data.setdefault("attempts", {})
        data.setdefault("wrong", {})
        dirty = False
        for qid, rec in list(data["wrong"].items()):
            if verdicts.owner(rec.get("status")) != "system":
                continue
            if rec.get("code") is not None:
                data.setdefault("solutions", {}).setdefault(qid, {}).setdefault("latest", {
                    "code": rec["code"], "status": "SE", "ts": rec.get("ts", 0)})
            del data["wrong"][qid]
            dirty = True
        if dirty:
            _atomic_json(path, data)
        return data


def save_progress(data, username=None):
    username = username or current_user()
    if not username:
        return
    with _progress_lock:
        _atomic_json(_progress_path(username), data)


def _touch_day(data):
    """记录「今天有学习行为」。

    timeline 只在首次 AC 时写，倒推不出完整的学习轨迹；
    这里单独存一个日期集合，streak 直接读它，逻辑简单也不容易算错。

    顺带按天记一次次数：热力图要分深浅才有意义，只存「有没有学」
    画出来是一张纯色块图，看不出哪天投入多。
    """
    today = date.today().isoformat()
    days = data.setdefault("days", [])
    if today not in days:
        days.append(today)
        # 存 400 天足够算 streak，也避免文件无限长
        days[:] = sorted(set(days))[-400:]

    acts = data.setdefault("acts", {})
    acts[today] = acts.get(today, 0) + 1
    if len(acts) > 400:
        for k in sorted(acts)[:-400]:
            del acts[k]


def learning_streak(data):
    days = set(data.get("days") or [])
    if not days:
        return 0
    # 今天还没学时从昨天算起：早上刚打开页面就看到 streak 归零，
    # 会让人以为记录丢了，实际只是今天还没开始
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def compute_achievements(data):
    """成就全部由现有进度实时算出，不单独存解锁状态。

    好处是永远不会出现「数据和徽章对不上」；代价是成就定义一改，
    老用户会突然多出或少掉几个——对这个规模的项目，这个取舍划算。
    """
    read = len(data.get("read") or [])
    solved = sum(1 for v in (data.get("solved") or {}).values()
                 if v.get("status") == "AC")
    streak = learning_streak(data)

    defs = [
        ("first_read", "开卷", "读完第一个知识点", read >= 1),
        ("ten_read", "十节", "累计读完 10 个知识点", read >= 10),
        ("all_read", "通读", "读完全部 %d 个知识点" % TOTAL_KP, read >= TOTAL_KP),
        ("first_ac", "开张", "通过第一道题", solved >= 1),
        ("ten_ac", "十斩", "通过 10 道题", solved >= 10),
        ("half_ac", "过半", "通过一半题目", solved >= TOTAL_Q / 2),
        ("all_ac", "全通", "通过全部 %d 道题" % TOTAL_Q, solved >= TOTAL_Q),
        ("streak_3", "三日", "连续学习 3 天", streak >= 3),
        ("streak_7", "一周", "连续学习 7 天", streak >= 7),
    ]
    return [{"id": a, "name": b, "desc": c, "unlocked": bool(d)}
            for a, b, c, d in defs]


def week_dots(data):
    """最近 7 天有没有学习，给个人中心那条连击条用。

    从 6 天前排到今天，返回 [{label, on, today}]。
    label 用周几的单字，够短，一行能放下七个。
    """
    days = set(data.get("days") or [])
    today = date.today()
    names = "一二三四五六日"
    out = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        out.append({"label": names[d.weekday()],
                    "on": d.isoformat() in days,
                    "today": i == 0})
    return out


def activity_grid(data, weeks=26):
    """最近半年的学习热力图，按周分列、周一在顶。

    返回 [[{level, date, n, title}] * 7] * weeks。
    level -1 表示「还没到那一天」（本周剩余的日子），渲染成透明占位，
    不然最后一个整列都是空格子，看着像漏了数据。
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    acts = data.get("acts") or {}
    days = set(data.get("days") or {})

    cols = []
    for w in range(weeks):
        col = []
        for d in range(7):
            day = first + timedelta(weeks=w, days=d)
            if day > today:
                col.append({"level": -1, "date": day.isoformat(), "n": 0, "title": ""})
                continue
            iso = day.isoformat()
            n = acts.get(iso)
            if n is None:
                # 老数据没有 acts，退化成「那天学过没有」
                n = 1 if iso in days else 0
            if n <= 0:
                level = 0
            elif n <= 2:
                level = 1
            elif n <= 4:
                level = 2
            elif n <= 8:
                level = 3
            else:
                level = 4
            col.append({"level": level, "date": iso, "n": n,
                        "title": ("%s · %d 次" % (iso, n)) if n else ("%s · 未学习" % iso)})
        cols.append(col)
    return cols


def chapter_progress(data):
    """给每个章节算「已读知识点 / 总知识点」和「已过题数」。"""
    read = set(data.get("read", []))
    solved = data.get("solved", {})
    out = {}
    for cid, ch in CHAPTERS.items():
        total_kp = len(ch["knowledge_points"])
        done_kp = sum(1 for kp in ch["knowledge_points"]
                      if "%d.%s" % (cid, kp["title"].split(" ")[0]) in read)
        qs = QS_BY_CHAPTER.get(cid, [])
        done_q = sum(1 for q in qs if solved.get(q["id"], {}).get("status") == "AC")
        out[cid] = {
            "kp_done": done_kp, "kp_total": total_kp,
            "q_done": done_q, "q_total": len(qs),
            "percent": int(round(100 * (done_kp + done_q) /
                                 max(1, total_kp + len(qs)))),
        }
    return out


# ---------------------------------------------------------------- 学习分析

DIFF_NAMES = {"easy": "简单", "medium": "中等", "hard": "困难"}

# 能力雷达的六个维度。
#
# 用「能力维度」而不是「每一章一个轴」，是因为 12 个轴的雷达图读不出来：
# 多边形成了锯齿星，一眼看不出哪块弱。归成 6 组，每组 1–3 章，
# 形状才有「这几块我都行、那块塌了」的直观感。
#
# 分组不是按章节顺序均分，而是按知识点性质：
# 语法/流程是读的，数组/函数/指针是写的，结构体/文件是工程习惯。
ABILITY_GROUPS = [
    ("语法基础", [1, 2, 3]),
    ("流程控制", [4]),
    ("数组与函数", [5, 6]),
    ("指针与内存", [7, 8]),
    ("字符串与结构体", [9, 10]),
    ("文件与工程", [11, 12]),
]


def _kp_key(cid, kp):
    """知识点在进度里的唯一键。规则和 chapter_progress 保持一致——
    两处一旦不一致，个人中心和分析页就会显示不同的完成度。"""
    return "%d.%s" % (cid, kp["title"].split(" ")[0])


def analytics_data(data):
    """把原始进度压成图表要的形状。

    这里刻意只做「取数 + 聚合」，不做任何样式决定。图的颜色、高度、
    平滑与否全在前端配置里——后端不该知道折线要不要圆角。
    """
    read = set(data.get("read") or [])
    solved = data.get("solved") or {}
    wrong = data.get("wrong") or {}
    attempts = data.get("attempts") or {}
    acts = data.get("acts") or {}

    # ---------- 1. 近 30 天活跃度 ----------
    #
    # 从 acts 取。老数据没有 acts 时退化成 days（那天学过记 1 次），
    # 这样曲线形状不精确但不会是一条全零的直线。
    days_set = set(data.get("days") or [])
    today = date.today()
    trend_labels, trend_values = [], []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        iso = d.isoformat()
        n = acts.get(iso)
        if n is None:
            n = 1 if iso in days_set else 0
        trend_labels.append("%d/%d" % (d.month, d.day))
        trend_values.append(n)

    # ---------- 2. 章节完成度 ----------
    cp = chapter_progress(data)
    ch_labels, ch_percent, ch_kp, ch_q = [], [], [], []
    for cid in sorted(CHAPTERS):
        ch = CHAPTERS[cid]
        # 树上 "认识 C 与开发环境" 太长，轴上放不下，截成 5 字
        title = ch["title"]
        ch_labels.append("%d·%s" % (cid, title[:4] if len(title) > 4 else title))
        ch_percent.append(cp[cid]["percent"])
        ch_kp.append(cp[cid]["kp_done"])
        ch_q.append(cp[cid]["q_done"])

    # ---------- 3. 能力雷达 ----------
    #
    # 每维的得分 = 该维度内「已读知识点 + 已过题」占「总知识点 + 总题」的比例。
    # 用比例而不是绝对数：各组的题量差很多（第 1 章 3 题、第 9 章 8 题），
    # 用绝对数会让题多的组天然得分高。
    radar_labels, radar_values = [], []
    for name, cids in ABILITY_GROUPS:
        total = 0
        done = 0
        for cid in cids:
            ch = CHAPTERS.get(cid)
            if not ch:
                continue
            for kp in ch["knowledge_points"]:
                total += 1
                if _kp_key(cid, kp) in read:
                    done += 1
            for q in QS_BY_CHAPTER.get(cid, []):
                total += 1
                if solved.get(q["id"], {}).get("status") == "AC":
                    done += 1
        radar_labels.append(name)
        radar_values.append(int(round(100 * done / max(1, total))))

    # ---------- 4. 题目状态分布 ----------
    #
    # 三档取互斥：已通过 / 待攻克（错过但还没过）/ 未开始。
    # 错题本里的题一定没通过（AC 会被移出），所以这两档不会重叠。
    ac = sum(1 for v in solved.values() if v.get("status") == "AC")
    stuck = len(wrong)
    untouched = max(0, TOTAL_Q - ac - stuck)
    status_series = [{
        "name": "题目",
        "data": [ac, stuck, untouched],
    }]
    status_labels = ["已通过", "待攻克", "未开始"]

    # ---------- 5. 难度掌握 ----------
    diff_total = {"easy": 0, "medium": 0, "hard": 0}
    diff_done = {"easy": 0, "medium": 0, "hard": 0}
    for q in QUESTIONS:
        d = q.get("difficulty") or "medium"
        if d not in diff_total:
            diff_total[d] = 0
            diff_done[d] = 0
        diff_total[d] += 1
        if solved.get(q["id"], {}).get("status") == "AC":
            diff_done[d] += 1

    diff_order = [k for k in ("easy", "medium", "hard") if k in diff_total]
    diff_labels = [DIFF_NAMES.get(k, k) for k in diff_order]
    diff_series = [
        {"name": "已通过", "data": [diff_done[k] for k in diff_order]},
        {"name": "总题数", "data": [diff_total[k] for k in diff_order], "dashed": True},
    ]

    # ---------- 6. 汇总指标 ----------
    total_attempts = sum(attempts.values())
    ac_attempts = sum(v.get("attempts", 0) for v in solved.values()
                      if v.get("status") == "AC")
    one_shot = sum(1 for v in solved.values()
                   if v.get("status") == "AC" and v.get("attempts") == 1)

    # 最弱的三章：只统计「开始过」的章节，全零的章不算弱项——
    # 那是还没学到，不是学不会，两种情况的建议完全不一样。
    weak = []
    for cid in sorted(CHAPTERS):
        row = cp[cid]
        if row["kp_done"] == 0 and row["q_done"] == 0:
            continue
        if row["percent"] >= 100:
            continue
        weak.append({
            "cid": cid,
            "title": CHAPTERS[cid]["title"],
            "percent": row["percent"],
            "kp_done": row["kp_done"], "kp_total": row["kp_total"],
            "q_done": row["q_done"], "q_total": row["q_total"],
        })
    weak.sort(key=lambda r: r["percent"])
    weak = weak[:3]

    # 两个键刻意不叫 "values"：Jinja 取属性时优先走 getattr，
    # dict 自带 .values 方法，`a.trend.values` 会拿到那个方法而不是
    # 我们存的数据，然后在模板里报「builtin_function_or_method
    # is not iterable」。换成 counts / scores 就不会撞名。
    return {
        "trend": {"labels": trend_labels, "counts": trend_values},
        "chapters": {"labels": ch_labels, "percent": ch_percent,
                     "kp": ch_kp, "q": ch_q},
        "radar": {"labels": radar_labels, "scores": radar_values},
        "status": {"labels": status_labels, "series": status_series},
        "difficulty": {"labels": diff_labels, "series": diff_series},
        "metrics": {
            "ac": ac,
            "total_q": TOTAL_Q,
            "pass_rate": int(round(100 * ac / max(1, TOTAL_Q))),
            "total_attempts": total_attempts,
            "avg_attempts": round(total_attempts / max(1, len(attempts)), 1),
            "one_shot_rate": int(round(100 * one_shot / max(1, ac))),
            "kp_read": len(read),
            "kp_total": TOTAL_KP,
        },
        "weak": weak,
    }



# ---------------------------------------------------------------- 页面

@app.route("/favicon.ico")
def favicon():
    """base.html 里已经用 <link rel="icon"> 指向了 SVG，这里只是兜底：
    有些客户端不认 link 标签，仍会直接来要 /favicon.ico，返回 404 会在
    控制台留一条红色报错，看着像出了故障。"""
    return send_from_directory(app.static_folder, "favicon.svg",
                               mimetype="image/svg+xml")


@app.route("/")
def index():
    data = load_progress() if current_user() else {}
    stats = chapter_progress(data) if data else {}
    total_q = len(QUESTIONS)
    solved_q = sum(1 for v in data.get("solved", {}).values() if v.get("status") == "AC")
    read_kp = len(data.get("read", []))
    # 总进度把「读过的知识点」和「通过的题」按等权算，权重怎么分都能吵，
    # 不如就用最直白的口径，用户自己一眼能对上
    overall = int(round(100.0 * (read_kp + solved_q) / max(1, TOTAL_KP + TOTAL_Q)))
    return render_template("index.html",
                           stages=COURSES["stages"],
                           chapters=CHAPTERS,
                           stats=stats,
                           total_q=total_q,
                           solved_q=solved_q,
                           total_kp=TOTAL_KP,
                           read_kp=read_kp,
                           overall=overall,
                           study_plan=study_plan(data) if current_user() else [],
                           migration_notice=data.get("migration_notice"),
                           streak=learning_streak(data) if data else 0)


def study_plan(data):
    """继续阅读、配套练习、错题复习，共用真实学习记录。"""
    lessons = [(cid, kid, kp) for cid, ch in sorted(CHAPTERS.items())
               for kid, kp in enumerate(ch["knowledge_points"], 1)]
    last = data.get("last_lesson") or {}
    lesson = next((item for item in lessons
                   if item[0] == last.get("cid") and item[1] == last.get("kid")), None)
    resumed = lesson is not None
    if lesson is None:
        lesson = next((item for item in lessons
                       if _kp_key(item[0], item[2]) not in data.get("read", [])), None)
    if lesson:
        cid, kid, kp = lesson
        learn = {"kind": "learn", "label": "继续阅读" if resumed else "开始学习",
                 "title": kp["title"], "description": "第 %d 章 · %s" % (cid, CHAPTERS[cid]["title"]),
                 "url": url_for("chapter_kp", cid=cid, kid=kid),
                 "action": "回到上次小节" if resumed else "打开小节"}
    else:
        cid = None
        learn = {"kind": "learn", "label": "巩固所学", "title": "课程已通读，来一轮训练",
                 "description": "用读代码、填空和概念快答检查理解。",
                 "url": url_for("training_page"), "action": "开始训练"}

    solved = data.get("solved", {})
    pending = [q for q in QUESTIONS if solved.get(q["id"], {}).get("status") != "AC"]
    question = next((q for q in pending if q["chapter"] == cid), pending[0] if pending else None)
    practice = {"kind": "practice", "label": "配套练习",
                "title": question["title"] if question else "全部练习已通过",
                "description": "第 %d 章 · 用代码验证刚学的内容。" % question["chapter"] if question
                               else "去游乐场自由写一段自己的程序。",
                "url": url_for("problem_page", qid=question["id"]) if question else url_for("playground_page"),
                "action": "动手写一题" if question else "自由编程"}
    wrong = [(qid, rec) for qid, rec in data.get("wrong", {}).items()
             if qid in QMAP and verdicts.owner(rec.get("status")) == "user"
             and solved.get(qid, {}).get("status") != "AC"]
    wrong.sort(key=lambda item: item[1].get("ts", 0), reverse=True)
    review = {"kind": "review", "label": "错题复习",
              "title": QMAP[wrong[0][0]]["title"] if wrong else "暂时没有待复习的错题",
              "description": "还有 %d 道待攻克，先从最近的一道开始。" % len(wrong) if wrong
                             else "做错的题会留在这里，通过后自动移出。",
              "url": url_for("problem_page", qid=wrong[0][0]) if wrong else url_for("training_page"),
              "action": "再试一次" if wrong else "做一轮巩固"}
    return [learn, practice, review]


@app.route("/chapter/<int:cid>")
def chapter_page(cid):
    ch = CHAPTERS.get(cid)
    if not ch:
        abort(404)
    return redirect(url_for("chapter_kp", cid=cid, kid=1))


@app.route("/chapter/<int:cid>/<int:kid>")
def chapter_kp(cid, kid):
    ch = CHAPTERS.get(cid)
    if not ch:
        abort(404)
    kps = ch["knowledge_points"]
    if not 1 <= kid <= len(kps):
        abort(404)

    key = "%d.%s" % (cid, kps[kid - 1]["title"].split(" ")[0])
    with _progress_lock:
        data = load_progress() if current_user() else {}
        read = set(data.get("read", []))
        if current_user():
            data["read"] = sorted(read | {key})
            data["last_lesson"] = {"cid": cid, "kid": kid, "ts": int(time.time())}
            _touch_day(data)
            save_progress(data)
            read.add(key)

    prev_ch = CHAPTERS.get(cid - 1)
    next_ch = CHAPTERS.get(cid + 1)
    return render_template("chapter.html",
                           chapter=ch, kp=kps[kid - 1], kid=kid,
                           key=key, read=read,
                           prev_ch=prev_ch, next_ch=next_ch,
                           questions=QS_BY_CHAPTER.get(cid, []),
                           solved=data.get("solved", {}),
                           chapters=CHAPTERS)


@app.route("/problems")
def problems_page():
    data = load_progress() if current_user() else {}
    return render_template("problems.html",
                           questions=QUESTIONS,
                           chapters=CHAPTERS,
                           solved=data.get("solved", {}))


@app.route("/problem/<qid>")
def problem_page(qid):
    q = QMAP.get(qid)
    if not q:
        abort(404)
    data = load_progress() if current_user() else {}
    solution = data.get("solutions", {}).get(qid, {})
    users = _read_users()
    allow_legacy_draft = (not users and not current_user()) or list(users) == [current_user()]
    return render_template("problem.html",
                           q=q, chapter=CHAPTERS.get(q["chapter"]),
                           solved=data.get("solved", {}).get(qid),
                           last_code=solution.get("latest", {}).get("code")
                               or (data.get("wrong", {}).get(qid) or {}).get("code")
                               or (data.get("solved", {}).get(qid) or {}).get("code"),
                           solution=solution, allow_legacy_draft=allow_legacy_draft,
                           chapters=CHAPTERS)


@app.route("/playground")
def playground_page():
    users = _read_users()
    return render_template("playground.html", chapters=CHAPTERS,
                           allow_legacy_draft=(not users and not current_user()) or list(users) == [current_user()])


@app.route("/wrongbook")
@login_required
def wrongbook_page():
    data = load_progress()
    wrong = data.get("wrong", {})
    items = []
    for qid, rec in wrong.items():
        q = QMAP.get(qid)
        if q:
            items.append({"q": q, "rec": rec})
    items.sort(key=lambda x: x["rec"].get("ts", 0), reverse=True)
    return render_template("wrongbook.html", items=items, chapters=CHAPTERS)


@app.route("/profile")
@login_required
def profile_page():
    data = load_progress()
    stats = chapter_progress(data)
    solved = data.get("solved", {})
    by_diff = {}
    for qid, rec in solved.items():
        if rec.get("status") != "AC":
            continue
        q = QMAP.get(qid)
        if q:
            by_diff[q["difficulty"]] = by_diff.get(q["difficulty"], 0) + 1
    heat = activity_grid(data)
    heat_days = sum(1 for col in heat for d in col if d["n"])
    return render_template("profile.html",
                           data=data, stats=stats, by_diff=by_diff,
                           total_q=TOTAL_Q,
                           total_kp=TOTAL_KP,
                           achievements=compute_achievements(data),
                           streak=learning_streak(data),
                           week=week_dots(data),
                           heat=heat,
                           heat_days=heat_days,
                           chapters=CHAPTERS)


@app.route("/analytics")
@login_required
def analytics_page():
    """学习分析。个人中心回答「我积累了什么」，这里回答「我哪里不行」。

    两页分开而不是合并：个人中心偏「鼓励」（成就、连击、热力图），
    分析页偏「诊断」（通过率、能力短板、难度分布）。放在一页里，
    用户看到一半被成就墙打断，就懒得往下滚到诊断部分了。
    """
    data = load_progress()
    return render_template("analytics.html",
                           a=analytics_data(data),
                           streak=learning_streak(data),
                           chapters=CHAPTERS)


# ---------------------------------------------------------------- 知识图谱

# 章节之间的先修依赖。
#
# 为什么不直接用 1→2→3→...→12 一条链：那是「目录顺序」，不是「依赖关系」。
# 真实情况是好几条路能通到同一章——学字符串之前你要会指针（7），
# 但也要见过数组（5）；学动态内存要数组（5）和指针（7）一起上。
# 把这些跨章依赖画出来，图谱才有信息量：它会告诉你「第 9 章学不动，
# 大概率是第 7 章没吃透」，而线性目录只会说「你卡在第 9 章」。
CHAPTER_DEPS = [
    (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7),
    (7, 8), (8, 9), (9, 10), (10, 11), (11, 12),
    (5, 7),    # 数组名退化就是指针，先见数组再看指针顺得多
    (5, 8),    # 动态内存拿到的是一块数组，先会数下标
    (7, 9),    # 字符串本质是指针 + '\0'
    (7, 10),   # 结构体指针是链表的前提
    (9, 11),   # 文件读写读写的都是字符串缓冲
    (6, 12),   # 多文件编译要先理解函数声明与定义
]

# 知识点的四个类型，图谱上按类型上色。
KP_KIND_NAMES = {
    "concept": "概念",
    "syntax": "语法",
    "pitfall": "易错点",
    "practice": "练习",
}


def _short_label(title):
    """把「1.1 从源码到可执行文件：编译的四个阶段」压成图上一个能读的短标签。

    规则按优先级：
      1. 去掉开头的编号（1.1 / 2.3）
      2. 有冒号就取冒号前——冒号后通常是展开说明，不是标题主体
      3. 还是太长就截断，加省略号

    截断放在最后而不是直接截前 N 字：直接截会得到
    「从源码到可执行文」这种半截词，加省略号至少说明「这里还有」。
    """
    t = (title or "").strip()
    parts = t.split(" ", 1)
    if len(parts) == 2 and parts[0].replace(".", "").isdigit():
        t = parts[1]
    if "：" in t:
        t = t.split("：", 1)[0]
    if ":" in t:
        t = t.split(":", 1)[0]
    if len(t) > 12:
        t = t[:11] + "…"
    return t


def map_data(data):
    """把课程结构 + 进度翻成力导向图要的 nodes / links。

    分层设计：
      章节点 12 个（大圆），知识点 48 个（小点），共 60 个节点。
      不把「节」再展开成更细的内容块——60 个节点已经是这张图
      在 900px 高度下能看清的上限，再多标签就必然糊在一起。
    """
    read = set(data.get("read", []))
    nodes = []
    links = []

    stage_index = {}
    for cid in sorted(CHAPTERS):
        ch = CHAPTERS[cid]
        stage_index.setdefault(ch["stage"], len(stage_index))

        nodes.append({
            "id": "c%d" % cid,
            "label": "%d %s" % (cid, _short_label(ch["title"])),
            "short": "%d %s" % (cid, _short_label(ch["title"])),
            "type": "chapter",
            "cid": cid,
            "stage": stage_index[ch["stage"]],
        })

        prev_kp = None
        for kid, kp in enumerate(ch["knowledge_points"], 1):
            kp_id = "k%d-%d" % (cid, kid)
            nodes.append({
                "id": kp_id,
                "label": kp["title"],
                "short": _short_label(kp["title"]),
                "type": "kp",
                "cid": cid,
                "kid": kid,
                "kind": kp.get("kind", ""),
                "done": _kp_key(cid, kp) in read,
                "stage": stage_index[ch["stage"]],
            })
            # 章 → 节：骨架边，短
            links.append({"source": "c%d" % cid, "target": kp_id, "kind": "tree"})
            # 节 → 下一节：阅读顺序，虚线
            if prev_kp:
                links.append({"source": prev_kp, "target": kp_id, "kind": "seq"})
            prev_kp = kp_id

    for a, b in CHAPTER_DEPS:
        if a in CHAPTERS and b in CHAPTERS:
            links.append({"source": "c%d" % a, "target": "c%d" % b, "kind": "dep"})

    # 只在后端做统计，样式全交给前端。
    # 后端一旦输出颜色值，将来做浅色主题就得改 Python。
    total_kp = sum(len(ch["knowledge_points"]) for ch in CHAPTERS.values())
    done_kp = len(read)
    return {
        "nodes": nodes,
        "links": links,
        "stats": {
            "chapters": len(CHAPTERS),
            "kp_total": total_kp,
            "kp_done": min(done_kp, total_kp),
            "deps": len(CHAPTER_DEPS),
        },
        "kinds": KP_KIND_NAMES,
        "stages": sorted(stage_index.keys()),
    }


@app.route("/map")
@login_required
def map_page():
    """知识点关系图。

    和 /analytics 的分工：分析页回答「我哪里不行」（诊断，看数字），
    图谱回答「我学的东西怎么连起来」（结构，看关系）。前者给结论，
    后者给地图——用户想找下一站该去哪，需要的是后者。
    """
    data = load_progress()
    return render_template("map.html",
                           m=map_data(data),
                           chapters=CHAPTERS)


@app.route("/ops")
@login_required
def ops_page():
    """判题机运行状态（运维视角）。

    三个页面的分工，别混：
      /analytics  学生视角，「我哪里不行」——看自己的掌握度
      /map        学生视角，「我学的东西怎么连起来」——看知识结构
      /ops        运维视角，「这台机器现在怎么样」——看延迟、并发、失败率

    为什么要单独做一页而不是塞进 /analytics：这个页面的读者不是学 C 的人，
    是维护它的人。混在一起两边都讲不清楚——一个页面只有一种读者。

    ⚠️ 真实部署时这个页面必须挂在 admin 鉴权后面（见页脚提示）。
    现在只要求登录，是因为本地单机、账号就一个。
    """
    return render_template("ops.html",
                           o=metrics.snapshot(concurrency=MAX_CONCURRENT_JUDGE),
                           # 裁决码表和超时归因文案都从 verdicts.py 拿，
                           # 模板里不写死任何状态名。加一个状态时只改一处。
                           vtable=verdicts.summary(),
                           # TLE_KINDS 的键是 cpu / wall / wait / ""（空串表示
                           # 未归因）。模板里要按 "unknown" 取，所以在这里先
                           # 解析成模板能直接用的四个键——别在模板里做
                           # 「空串映射成 unknown」这种事，那属于语义，
                           # 语义归 verdicts.py。
                           vk={k: verdicts.tle_kind(k)
                               for k in ("cpu", "wall", "wait", "unknown")},
                           chapters=CHAPTERS)


# ---------------------------------------------------------------- 管理台数据

def _fmt_bytes(n):
    """给人看的字节数。运维面板上 268435456 和 256MB 的信息量不一样。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f TB" % n


def _mem_info():
    """总内存与可用内存。

    刻意不依赖 psutil：这个项目的要求是「解压即跑」，多一个第三方包
    就多一次 pip 失败的可能，而这段代码只有二十行。Windows 走
    GlobalMemoryStatusEx（ctypes，系统自带），Linux 读 /proc/meminfo，
    两边都拿不到就返回 None——面板上显示「未知」比显示一个错的数字好。
    """
    try:
        if os.name == "nt":
            import ctypes

            class _MSX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MSX()
            st.dwLength = ctypes.sizeof(_MSX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return {"total": st.ullTotalPhys, "avail": st.ullAvailPhys}
            return None
        info = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = v.strip()
        # 都是 kB
        total = int(info["MemTotal"].split()[0]) * 1024
        avail = int(info.get("MemAvailable", info["MemFree"]).split()[0]) * 1024
        return {"total": total, "avail": avail}
    except Exception:  # noqa: BLE001
        return None


def _dir_size(path, depth=2):
    """目录占用（只看两层，够用就好）。

    不做全量递归：data/ 里可能有上万个小 JSON（每个用户一份进度），
    在请求路径上递归一遍会把 5 秒一次的轮询变成 IO 风暴。
    """
    total = 0
    base_depth = path.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(path):
        if root.count(os.sep) - base_depth >= depth:
            dirs[:] = []
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


_compiler_memo = {"at": 0.0, "info": None}


def _compiler_cached():
    """gcc --version 的结果缓存 60 秒。

    每 5 秒轮询一次面板、每次都起一个 gcc 进程问版本号，等于让运维面板
    自己变成负载源。而这台机器上换编译器的频率是「几乎为零」。
    """
    now = time.time()
    if _compiler_memo["info"] is None or now - _compiler_memo["at"] > 60:
        try:
            _compiler_memo["info"] = judge.compiler_info()
        except Exception as e:  # noqa: BLE001
            _compiler_memo["info"] = {"ok": False, "path": None,
                                      "version": "读取失败：%s" % e, "hint": ""}
        _compiler_memo["at"] = now
    return _compiler_memo["info"]


def _admin_env():
    """运行环境那一块。全是「看一眼就知道机器什么状态」的硬事实。"""
    mem = _mem_info()
    try:
        du = shutil.disk_usage(DATA_DIR)
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = None
    ci = _compiler_cached()
    # 只走一次目录：_dir_size 会遍历 data/，调两遍就是两次 IO。
    data_size = _dir_size(DATA_DIR)
    return {
        "python": sys.version.split()[0],
        "platform": ("Windows" if os.name == "nt" else "Linux") + " · " + sys.platform,
        "cpu": os.cpu_count() or 0,
        "mem_total": mem["total"] if mem else None,
        "mem_avail": mem["avail"] if mem else None,
        "mem_used_pct": (round((mem["total"] - mem["avail"]) * 100.0 / mem["total"], 1)
                         if mem and mem["total"] else None),
        "disk_total": disk["total"] if disk else None,
        "disk_free": disk["free"] if disk else None,
        "data_size": data_size,
        "data_size_h": _fmt_bytes(data_size),
        "concurrency": MAX_CONCURRENT_JUDGE,
        "compiler_ok": ci["ok"],
        "compiler_path": ci["path"],
        "compiler_version": ci["version"],
        "uptime_s": int(time.time() - _START_TS),
        "sandbox_env": os.environ.get("CMASTER_SANDBOX", ""),
    }


def _admin_panel():
    """把 /admin 需要的三块数据拼成一份。

    **指标那部分直接拿 metrics.snapshot() 的切片，不重算**：
    /ops 和 /admin 上同一个 P95 必须一模一样，一旦两页各算各的，
    迟早出现「这边 1200 那边 800」，然后这个面板就再也没人信了。
    """
    snap = metrics.snapshot(concurrency=MAX_CONCURRENT_JUDGE)
    sb = sandbox.status()

    # 队列：inflight 是「正在判的」，waiting 由信号量算——
    # threading.Semaphore 不暴露等待者数量，所以这里用「已经进闸门的人数」
    # 和容量比。waiting 只在有排队时才非零，而那正是我们想看的。
    queue = {
        "inflight": snap["queue"]["inflight"],
        "capacity": snap["queue"]["capacity"],
        "saturated": snap["queue"]["saturated"],
        "peak": snap["queue"]["peak"],
        "avg_ms": snap["latency"]["queue_avg"],
        "max_ms": snap["latency"]["queue_max"],
    }

    return {
        "at": int(time.time()),
        "stamp": time.strftime("%H:%M:%S"),
        "uptime_s": int(time.time() - _START_TS),
        "health": snap["health"],
        "sandbox": sb,
        "ops": {
            "window": snap["window"],
            "counts": snap["counts"],
            "rates": snap["rates"],
            "latency": snap["latency"],
            "phases": snap["phases"],
            "queue": queue,
            "histogram": snap["histogram"],
        },
        "env": _admin_env(),
    }


def _admin_gate():
    """这一页的鉴权：配置了白名单就只放白名单里的人，没配就说明没配。

    三种返回值：
        None        —— 放行（没配白名单，单账号本地模式）
        "denied"    —— 配了白名单，但当前账号不在里面
        "open"      —— 没配白名单（和 None 同义，单独叫出来是为了让调用点读得懂）

    为什么不干脆「没配就默认只允许第一个账号」：那是一个隐式约定，换台机器、
    换个人先注册，管理员就换人了，而且从代码里看不出来。env 是显式的。
    """
    user = session.get("user") or ""
    if not ADMIN_USERS:
        return "open"
    return None if user in ADMIN_USERS else "denied"


@app.route("/admin")
@login_required
def admin_page():
    """系统管理台（维护者视角）。

    和 /ops 的分工，一句话：/ops 回答「判题过程本身跑得怎样」，
    /admin 回答「这台机器和这个站点整体健不健康、沙箱有没有真的生效」。

    为什么沙箱自检不能放 /ops：/ops 的每一块都是「最近 400 次判题的统计量」，
    读者看到的是一个连续的分布；而自检是「某一刻跑的四次实验」，
    是一个离散的、可能五分钟前才跑过的状态。两种东西混在一页，
    读者会忍不住把「自检通过」当成「这一分钟的系统状态」——它不是。

    鉴权只做一件事：配了 `CMASTER_ADMIN`（逗号分隔的账号名单）就只放名单里的人，
    没配就**在页面上明说没配**。这一页能看到机器的目录、内存和进程参数，
    所以「没配」不能是悄悄发生的事——它是一个决定，得写在用户看得见的地方，
    否则下一个人会以为这页本来就有权限控制，把一条横幅删掉就当加固了。
    """
    gate = _admin_gate()
    if gate == "denied":
        # 403 而不是 404：这一页的存在是公开信息（首页就链着它），
        # 用 404 假装不存在只会让维护者去翻日志找「为什么这页没了」。
        return render_template("error.html", code=403,
                               message="这一页能看到机器的目录、内存和进程参数，"
                                       "只对 CMASTER_ADMIN 里列出的账号开放。"), 403
    return render_template("admin.html",
                           a=_admin_panel(),
                           admin_open=(gate == "open"),
                           chapters=CHAPTERS)


@app.route("/api/admin/panel")
@login_required
def api_admin_panel():
    """给 /admin 轮询用的整块数据：沙箱 + 指标 + 运行环境。

    一次请求把三块都给了。拆成三个接口看着更「REST」，但页面每 5 秒
    就要发三次请求，而这三块永远一起变——拆开只是多两个握手。
    """
    if _admin_gate() == "denied":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "panel": _admin_panel()})


@app.route("/api/admin/selftest", methods=["POST"])
@login_required
def api_admin_selftest():
    """触发一次沙箱自检。

    **不在请求里同步跑**：自检要真编译五个程序再各跑一遍，Windows 上
    实测 7 秒多，Linux 上因为要起 seccomp 进程还会更慢。放在请求里，
    浏览器会以为页面卡死了，而且一旦用户不耐烦按了两次，两个自检
    会同时抢 CPU——恰恰是沙箱最不该被干扰的时候。

    所以这里只负责「点火」：起一个守护线程，立刻返回。
    前端拿着 {started:true} 去轮询 /api/admin/panel，看 selftest_running
    变回 false 就有结果了。

    sandbox.selftest() 内部有自己的锁，重复点击只会得到「正在运行」，
    不会真的跑第二遍——这一层的保护放在数据源而不是接口，是因为
    以后可能还有别的入口（启动时预热、CLI）也要触发它。
    """
    if _admin_gate() == "denied":
        # 这个接口会真的编译并跑五个程序。让不在名单里的人也能点，
        # 等于给了一个「远程烧 CPU」的按钮——比看内存更值得挡。
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sandbox._selftest["running"]:  # noqa: SLF001
        return jsonify({"ok": True, "started": False, "reason": "自检已经在跑了"})

    def worker():
        try:
            sandbox.selftest(force=True)
        except Exception:  # noqa: BLE001
            # 自检自己炸了不该把线程异常打到控制台就完事——结果会在
            # /api/admin/panel 里显示成「上次自检」，失败也要留痕。
            import traceback
            traceback.print_exc()

    threading.Thread(target=worker, name="selftest", daemon=True).start()
    return jsonify({"ok": True, "started": True})


# ------------------------------------------------------------------ 健康检查
#
# 两个探针，语义**必须**分开，混成一个是最常见的部署错误：
#
#   /healthz  存活探针（liveness）：进程还在、还能响应 HTTP 就 200。
#             编排系统（docker/k8s）用它决定「要不要重启这个容器」。
#             如果它也去检查编译器，那么「gcc 没装」这种**重启治不好**的
#             问题会导致容器被反复重启，从一个故障变成一串故障。
#
#   /readyz   就绪探针（readiness）：能不能真的判题——编译器在不在、
#             data 目录写不写得了、沙箱有没有降级。不适合接流量时返回 503，
#             负载均衡把流量挪走，但**不重启**。
#
# 两个都不要求登录：探针不会带 cookie，要求登录等于永远探测失败。

@app.route("/healthz")
def healthz():
    return "ok\n", 200, {"Content-Type": "text/plain; charset=utf-8",
                         "Cache-Control": "no-store"}


@app.route("/readyz")
def readyz():
    """就绪检查：把「这台机器现在能不能判题」拆成几条可判定的条件。"""
    checks = []

    gcc = judge.find_compiler()
    checks.append({"key": "compiler", "ok": bool(gcc),
                   "detail": gcc or "找不到 C 编译器"})

    # data 目录要可写：进度、指标、密钥都往这儿落。只读文件系统上
    # 页面能打开但一判题就 500，这种「半死」状态最容易被漏掉。
    writable = False
    probe = os.path.join(DATA_DIR, ".readyz")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        writable = True
    except OSError as e:
        detail = "data 目录不可写：%s" % e
    else:
        detail = DATA_DIR
    checks.append({"key": "datadir", "ok": writable, "detail": detail})

    sb = sandbox.available()
    checks.append({"key": "sandbox", "ok": sb["ok"], "detail":
                   sb["backend"] if sb["ok"] else (sb["reason"] or "沙箱不可用")})

    # 磁盘：判题不落盘（输出走管道），但指标文件和进度文件要写。
    # 留 64MB 底线，低于此就认为「快满了」——满了之后 SQLite/JSON 写入
    # 失败的表现是乱码而不是报错，很难查。
    ok_disk = True
    try:
        du = shutil.disk_usage(DATA_DIR)
        free_mb = du.free // (1024 * 1024)
        ok_disk = free_mb > 64
        detail = "剩余 %d MB" % free_mb
    except OSError as e:
        detail = "读不到磁盘信息：%s" % e
    checks.append({"key": "disk", "ok": ok_disk, "detail": detail})

    # 沙箱降级不算「没就绪」：本机跑一下仍然能用。但如果沙箱**本该可用**
    # 却初始化失败了，那说明机器有问题（见 sandbox.stats 的 init_failed）。
    st = sandbox.stats()
    checks.append({"key": "sandbox_init", "ok": st["init_failed"] == 0,
                   "detail": "初始化失败 %d 次" % st["init_failed"]})

    ready = all(c["ok"] for c in checks)
    body = {"ok": ready, "checks": checks, "uptime_s": int(time.time() - _START_TS)}
    return jsonify(body), (200 if ready else 503)


@app.route("/api/ops")
@login_required
def api_ops():
    """给 /ops 页面做轮询用的快照。

    和页面走同一个 metrics.snapshot()——**不另写一份「精简版」**：
    两份聚合逻辑一定会漂移，而在运维面板上，「页面显示 P95=1200、
    接口显示 800」这种不一致会让人再也不敢相信这个面板。
    快照本身只有几 KB，没必要为它省带宽。
    """
    return jsonify({"ok": True, "snapshot": metrics.snapshot(
        concurrency=MAX_CONCURRENT_JUDGE)})


@app.route("/api/verdicts")
def api_verdicts():
    """裁决码表本身，做成接口。

    为什么值得单开一个端点而不是只放在 Python 里：这张表是**契约**——
    判题器按它出裁决、面板按它分责任、前端按它上色。契约能被 GET 到，
    才谈得上「三方读的是同一份」。judge0 把状态码做成公开常量也是这个
    理由：调用方不需要去读它的源码才知道 11 是 Runtime Error。

    不要求登录：里面没有一个用户的数据，只有定义。而它正好是
    「这个站怎么判题」最省事的说明文档。
    """
    return jsonify({
        "ok": True,
        "verdicts": verdicts.summary(),
        "tle_kinds": [dict(kind=k, **v) for k, v in verdicts.TLE_KINDS.items()],
    })


@app.route("/memory")
@login_required
def memory_page():
    """内存演示：把同一块内存一步一步画出来。

    为什么要单独一页，而不是在章节里放几张静态图：
    静态图只能画「结果」，而这个页面的全部价值是「上一秒和下一秒差在哪」——
    char c = 'A' 之后那个 41 是从哪冒出来的、p 自增之后为什么地址加了 4。
    这些只有同一块内存连着走才看得出来，换一张图就断了。

    数据和渲染的分工是刻意的：
      tools/build_demos.py  算地址/偏移/填充/字节，并校验自洽（偏移不连续、
                            片段之和不等于 sizeof、指针指向不存在的格子，都算错）
      模板 + memory.js      只负责把算好的东西按 steps 画出来，不做任何推算
    前端一旦也学会算地址，两边迟早算得不一样，而这种不一致是静默的——
    页面上看着一切正常，只是数字对不上。

    页头展示的步数来自 MEMORY_STEPS（启动期算好），模板不自己求和。
    """
    return render_template("memory.html", d=MEMORY_DEMOS,
                           steps_total=MEMORY_STEPS)


@app.route("/training")
@login_required
def training_page():
    """训练模式：三种操练，一套连击/回炉的骨架。

    和 /problems 的分工：
      /problems  写完整代码，判题器真编译真执行。重、慢、像考试。
      /training  只考一个点：这段代码输出什么、这个空补什么、这条概念记不记得。
                 轻、快、像热身。一个 12 张卡的会话两分钟能走完。

    为什么同一批内容要换三种问法：它们考的不是同一件事。
    「能读懂代码」和「能默写出关键写法」是两种能力，很多人卡在后者——
    看着都懂，一动键盘就 `->` 写成 `.`。填空补洞专门打这个。

    卡片数据**不在页面里内联**（166 张 / 170KB），走 /api/training/cards 按
    模式取。理由不是省流量，是这页其实只会用到一种模式的十几张卡；把三种
    全塞进 HTML，等于让学生每次开页都等一份他马上要扔掉的数据。
    代价是多一次请求——本地几毫秒，换来切换模式时的「秒开」缓存。
    """
    data = load_progress()
    return render_template("training.html",
                           modes=TRAINING_MODES,
                           counts=TRAINING_COUNT,
                           cards_total=sum(TRAINING_COUNT.values()),
                           sizes=TRAINING_SIZES,
                           stats=data.get("training") or {})


@app.route("/api/training/cards")
@login_required
def api_training_cards():
    """按模式取一整套卡片。

    一次给整套（58 张 / 45KB 左右）而不是按会话抽 12 张：会话结束点
    「再来一轮」要立刻换一批，抽牌在服务端做的话每轮都要等一个往返。
    整套给到前端，抽牌和去重都在内存里做，切换是零延迟。
    """
    if not TRAINING_MODES:
        return jsonify({"ok": False, "error": "训练内容暂不可用，请先阅读课程或练习题库。"}), 503
    mode = (request.args.get("mode") or "").strip()
    if mode not in TRAINING_CARDS:
        return jsonify({"ok": False, "error": "没有这个训练模式"}), 404
    return jsonify({"ok": True, "mode": mode, "cards": TRAINING_CARDS[mode]})


@app.route("/api/training/record", methods=["POST"])
@login_required
def api_training_record():
    """一次会话结束后落一次盘。

    存进 progress 的 `training` 字段，而不是另开一个文件：
    训练也要算「今天学了」——streak、热力图、成就都读同一份进度文件。
    单独存的话，学生练了半小时，个人中心显示「今天还没学」，
    那这个功能就活在另一个世界里了。

    入参全部来自前端，必须当成不可信数据夹范围。这里不追求精确的
    反作弊（本地单机、账号是自己的），只保证一条：不管前端发什么，
    进度文件的尺寸和取值范围都是有界的，不会因为这个接口被撑爆。
    """
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "")
    if mode not in TRAINING_CARDS:
        return jsonify({"ok": False, "error": "没有这个训练模式"}), 400

    def num(key, lo, hi, default=0):
        try:
            v = int(payload.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    right = num("right", 0, 999)
    wrong = num("wrong", 0, 999)
    combo = num("combo", 0, 999)
    xp = num("xp", 0, 100000)
    size = num("size", 0, 999)
    missed = payload.get("missed") or []
    if not isinstance(missed, list):
        missed = []
    # 只收「本模式里真实存在的卡片 id」，长度封顶 40。
    # 不校验的话，一次请求就能往进度文件里塞几千个垃圾 key。
    known = {c["id"] for c in TRAINING_CARDS[mode]}
    missed = [str(m) for m in missed if str(m) in known][:40]

    now = int(time.time())
    with _progress_lock:
        data = load_progress()
        tr = data.setdefault("training", {})
        tr.setdefault("totals", {"sessions": 0, "right": 0, "wrong": 0, "xp": 0})
        per = tr.setdefault("modes", {})

        totals = tr["totals"]
        totals["sessions"] += 1
        totals["right"] += right
        totals["wrong"] += wrong
        totals["xp"] += xp

        m = per.setdefault(mode, {"sessions": 0, "right": 0, "wrong": 0,
                                  "xp": 0, "bestCombo": 0, "lastTs": 0})
        m["sessions"] += 1
        m["right"] += right
        m["wrong"] += wrong
        m["xp"] += xp
        m["bestCombo"] = max(m["bestCombo"], combo)
        m["lastTs"] = now
        m["lastSize"] = size

        # 每张卡的答对/答错累计。这张表是「回炉」的长期依据：
        # 本次会话内靠内存里的队列就够了，跨会话要看这里。
        cards = tr.setdefault("cards", {})
        for cid in missed:
            rec = cards.setdefault(cid, {"r": 0, "w": 0, "ts": 0})
            rec["w"] += 1
            rec["ts"] = now
        # 只清最近 600 条，别让这个字典无界增长
        if len(cards) > 600:
            for k in list(cards)[:len(cards) - 600]:
                del cards[k]

        _touch_day(data)
        save_progress(data)

    return jsonify({"ok": True, "mode": mode,
                    "totals": data["training"]["totals"],
                    "modeStats": data["training"]["modes"][mode]})


# ---------------------------------------------------------------- 账号路由

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        mode = "register" if request.args.get("mode") == "register" else "login"
        return render_template("login.html", mode=mode,
                               chapters=CHAPTERS, next=request.args.get("next", "/"))

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    action = request.form.get("action", "login")
    nxt = request.form.get("next") or "/"

    if not username or len(password) < 6:
        return render_template("login.html", mode=action, chapters=CHAPTERS,
                               error="用户名不能为空，密码至少 6 位", next=nxt,
                               username=username)

    with _users_lock:
        users = _read_users()
        if action == "register":
            if username in users:
                return render_template("login.html", mode=action, chapters=CHAPTERS,
                                       error="这个用户名已经被注册了", next=nxt,
                                       username=username)
            salt, digest = _hash_password(password)
            users[username] = {"salt": salt, "hash": digest,
                               "created": int(time.time())}
            _write_users(users)
        else:
            rec = users.get(username)
            if not rec:
                return render_template("login.html", mode=action, chapters=CHAPTERS,
                                       error="用户名或密码不对", next=nxt,
                                       username=username)
            _, digest = _hash_password(password, rec["salt"])
            if not secrets.compare_digest(digest, rec["hash"]):
                return render_template("login.html", mode=action, chapters=CHAPTERS,
                                       error="用户名或密码不对", next=nxt,
                                       username=username)

    session.permanent = True
    session["user"] = username
    session["login_at"] = int(time.time())
    return redirect(nxt if nxt.startswith("/") else "/")


@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------- 搜索

def _strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html or "")


def _build_search_index():
    """12 章 + 48 个知识点 + 60 道题，一共 120 条，直接线性扫。

    这点数据量上倒排索引属于过度设计：全量扫一遍是微秒级，
    而维护索引（内容一改就得重建）反而多一个可能出错的地方。
    等题库过千条再换方案。
    """
    items = []
    for ch in COURSES["chapters"]:
        items.append({
            "group": "课程章节",
            "icon": "章",
            "title": "第 %d 章 %s" % (ch["id"], ch["title"]),
            "url": "/chapter/%d" % ch["id"],
            "meta": "%d 节" % len(ch["knowledge_points"]),
            "extra": ch.get("description") or "",
        })
        for i, kp in enumerate(ch["knowledge_points"], 1):
            items.append({
                "group": "知识点",
                "icon": "节",
                "title": kp["title"],
                "url": "/chapter/%d/%d" % (ch["id"], i),
                "meta": "第 %d 章" % ch["id"],
                "extra": "%s %s" % (ch["title"], _strip_tags(kp.get("html", ""))[:160]),
            })

    diff_cn = {"easy": "简单", "medium": "中等", "hard": "较难"}
    for q in QUESTIONS:
        items.append({
            "group": "练习题",
            "icon": "题",
            "title": "%s %s" % (q["id"], q["title"]),
            "url": "/problem/%s" % q["id"],
            "meta": diff_cn.get(q["difficulty"], ""),
            "extra": "%s %s" % (q.get("hint") or "",
                                _strip_tags(q.get("description_html", ""))[:220]),
        })
    return items


SEARCH_INDEX = _build_search_index()
_GROUP_ORDER = {"课程章节": 0, "知识点": 1, "练习题": 2}


@app.route("/api/search")
def api_search():
    """站内搜索。不要求登录——搜索是「先看看你这站有什么」的入口。"""
    q = (request.args.get("q") or "").strip()[:60]
    if not q:
        return jsonify({"ok": True, "q": "", "results": []})

    ql = q.lower()
    scored = []
    for it in SEARCH_INDEX:
        title = it["title"].lower()
        pos = title.find(ql)
        if pos >= 0:
            # 开头命中比中间命中更相关；命中位置相同时，标题越短越精准
            score = 100.0 - pos * 2.0 - len(title) * 0.1
            if pos == 0:
                score += 30
        else:
            ep = it["extra"].lower().find(ql)
            if ep < 0:
                continue
            # 正文命中排在标题命中之后，且越靠前越相关
            score = 40.0 - min(ep, 200) * 0.05
        scored.append((_GROUP_ORDER.get(it["group"], 9), -score, it))

    # 按「类型优先、相关度其次」排，前端才能把同类结果连成一组
    scored.sort(key=lambda x: (x[0], x[1]))
    results = [{
        "group": it["group"],
        "icon": it["icon"],
        "title": it["title"],
        "url": it["url"],
        "meta": it["meta"],
    } for _, _, it in scored[:15]]

    return jsonify({"ok": True, "q": q, "results": results})


# ---------------------------------------------------------------- API

@app.route("/api/run", methods=["POST"])
def api_run():
    """自由运行：用自己给的 stdin 跑一遍，不对测试用例。

    不要求登录——让人先试试手感，再决定要不要注册。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求格式不对，需要 JSON"}), 400
    code = payload.get("code", "")
    stdin_data = payload.get("stdin", "")
    if not isinstance(code, str) or not isinstance(stdin_data, str):
        return jsonify({"ok": False, "error": "代码和标准输入需要是文本"}), 400
    if not code.strip():
        return jsonify({"ok": False, "error": "代码是空的"}), 400
    if len(code) > 100_000:
        return jsonify({"ok": False, "error": "代码太长"}), 400

    t_want = metrics.enter()
    if not _judge_sem.acquire(timeout=30):
        metrics.leave()
        return jsonify({"ok": False, "error": "判题机忙，稍后再试"}), 503
    t_got = time.time()
    try:
        result = judge.run_freeform(code, stdin_data)
    finally:
        _judge_sem.release()
        metrics.leave()
    # 排队时长 = 从「想进闸门」到「真进去」的间隔。闸门没饱和时它接近 0，
    # 一旦这条线抬头就说明并发不够了——这是唯一能提前看出容量问题的信号。
    metrics.record(
        kind="run",
        verdict=result.get("status") or "SE",
        queue_ms=int((t_got - t_want) * 1000),
        compile_ms=result.get("compile_ms", 0),
        run_ms=result.get("time_ms", 0),
        # 判题器自己的墙钟。compile+run 之外那块差额就是「建目录与比对」，
        # 不给它，面板上那一段永远是 0。
        judge_ms=result.get("judge_ms", 0),
        # 超时归因。判题器给的，路由只负责搬运——不要在路由里根据
        # time_ms 和 cpu_ms 自己推一遍，那会让面板和结果卡各推一套。
        tle_kind=result.get("tle_kind") or "",
    )
    return jsonify({"ok": True, "result": result})


@app.route("/api/submit", methods=["POST"])
@login_required
def api_submit():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求格式不对，需要 JSON"}), 400
    if payload.get("accountId") and payload["accountId"] != user_storage_id(current_user()):
        return jsonify({"ok": False, "error": "账号已在其他页面切换，请刷新后再提交。代码仍保存在原账号草稿中。"}), 409
    qid = payload.get("qid", "")
    code = payload.get("code", "")
    if not isinstance(qid, str) or not isinstance(code, str):
        return jsonify({"ok": False, "error": "题号和代码需要是文本"}), 400
    q = QMAP.get(qid)
    if not q:
        return jsonify({"ok": False, "error": "题目不存在"}), 404
    if not code.strip():
        return jsonify({"ok": False, "error": "代码是空的"}), 400
    if len(code) > 100_000:
        return jsonify({"ok": False, "error": "代码太长"}), 400

    t_want = metrics.enter()
    if not _judge_sem.acquire(timeout=60):
        metrics.leave()
        return jsonify({"ok": False, "error": "判题机忙，稍后再试"}), 503
    t_got = time.time()
    try:
        result = judge.judge(q, code)
    finally:
        _judge_sem.release()
        metrics.leave()

    metrics.record(
        kind="submit",
        verdict=result.get("status") or "SE",
        queue_ms=int((t_got - t_want) * 1000),
        compile_ms=result.get("compile_ms", 0),
        run_ms=result.get("time_ms", 0),
        judge_ms=result.get("judge_ms", 0),
        cases=result.get("total", 0),
        passed=result.get("passed", 0),
        # 逐用例耗时按顺序存，/ops 页拿它画「每次判题的耗时构成条」
        case_ms=[c.get("time_ms", 0) for c in (result.get("cases") or [])],
        qid=qid,
        tle_kind=result.get("tle_kind") or "",
    )

    _record_attempt(q, code, result)
    return jsonify({"ok": True, "result": result,
                    "progress_recorded": verdicts.owner(result.get("status")) != "system"})


def _record_attempt(q, code, result):
    """记录尝试、通过、错题。全部在锁内做读改写，避免并发丢数据。"""
    with _progress_lock:
        data = load_progress()
        qid = q["id"]
        now = int(time.time())

        solution = data.setdefault("solutions", {}).setdefault(qid, {})
        entry = {"id": secrets.token_hex(8), "code": code,
                 "status": result.get("status") or "SE", "ts": now}
        solution["latest"] = entry
        solution["history"] = (solution.get("history", []) + [entry])[-10:]
        if entry["status"] == "AC":
            solution["accepted"] = entry
        if verdicts.owner(entry["status"]) == "system":
            # 保存代码方便重试，但故障不计学生尝试、错题和学习统计。
            save_progress(data)
            return

        data["attempts"][qid] = data["attempts"].get(qid, 0) + 1

        if result["status"] == "AC":
            first = qid not in data["solved"]
            previous = data["solved"].get(qid, {})
            data["solved"][qid] = {"status": "AC", "ts": previous.get("ts", now),
                                   "attempts": previous.get("attempts", data["attempts"][qid])}
            # 做对了就从错题本移走——错题本的意义是「还没弄懂的」
            data["wrong"].pop(qid, None)
            if first:
                data.setdefault("timeline", []).append(
                    {"type": "solve", "qid": qid, "ts": now})
        else:
            # 已经 AC 过的题不再进错题本：学生已经证明会做了，
            # 后面回来随便改崩一次不该再被记一笔。
            if data["solved"].get(qid, {}).get("status") != "AC":
                data["wrong"][qid] = {
                    "code": code,
                    "status": result["status"],
                    "message": result.get("message", "")[:300],
                    "ts": now,
                    "attempts": data["attempts"][qid],
                }
        _touch_day(data)
        save_progress(data)


@app.route("/api/submissions/<qid>")
@login_required
def api_submissions(qid):
    expected = request.headers.get("X-CMaster-Account")
    if expected and expected != user_storage_id(current_user()):
        return jsonify({"ok": False, "error": "账号已切换，请刷新页面。"}), 409
    if qid not in QMAP:
        abort(404)
    return jsonify({"ok": True, "solution": load_progress().get("solutions", {}).get(qid, {})})


@app.route("/api/answer/<qid>")
@login_required
def api_answer(qid):
    if qid not in ANSWERS:
        abort(404)
    return jsonify({"ok": True, "answer": ANSWERS[qid]})


@app.route("/api/compiler")
def api_compiler():
    """前端拿这个判断环境是否就绪，别让用户对着不能用的编辑器写代码。"""
    info = judge.compiler_info()
    return jsonify({"ok": info.get("ok", False),
                    "version": info.get("version", ""),
                    "hint": info.get("hint", "")})


# ---------------------------------------------------------------- 错误页

@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404,
                           message="这个页面不存在"), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="服务器内部错误"), 500


@app.context_processor
def inject_globals():
    return {"current_user": current_user(),
            "user_storage_id": user_storage_id(current_user()) if current_user() else "guest",
            "site_name": "CMaster",
            "chapters": CHAPTERS,
            "stages": COURSES["stages"],
            "qcounts": {cid: len(v) for cid, v in QS_BY_CHAPTER.items()}}


if __name__ == "__main__":
    port = int(os.environ.get("CMASTER_PORT", "5001"))
    info = judge.compiler_info()
    print("=" * 58)
    print("  CMaster  C 语言学习平台")
    print("=" * 58)
    print("  课程: %d 章 / %d 知识点" %
          (len(CHAPTERS), sum(len(c["knowledge_points"]) for c in CHAPTERS.values())))
    print("  题库: %d 题" % len(QUESTIONS))
    if info.get("ok"):
        print("  编译器: %s" % info["version"].splitlines()[0])
        print("          %s" % info["path"])
    else:
        print("  !! 编译器未就绪: %s" % info.get("hint", "找不到 gcc"))
        print("  !! 课程可以看，但判题会全部失败")
    print("  地址: http://127.0.0.1:%d" % port)
    print("=" * 58)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
