"""判题运行时指标：进程内环形缓冲 + 节流落盘 + 聚合快照。

为什么自己写而不是上 Prometheus / statsd：
    这个站是单机单进程，指标量级是「一天几百次判题」。一个 400 条的环形
    缓冲 + 启动时读回，就足够回答运维真正关心的四个问题——现在忙不忙、
    慢在哪一段、失败率高不高、有没有变差的趋势。
    引 prometheus_client 要多一个 exporter 端口、一套抓取配置和一个反向
    代理规则，而这个项目连 gunicorn 都没有。**在这个规模上，可观测性
    工具的运维成本会超过它观测的对象。**

数据流：
    判题接口 ──record()──> 环形缓冲（内存，权威）
                              │
                              ├─ snapshot() ──> /ops 页面（聚合后）
                              └─ 节流落盘 ──> data/metrics.json（只为重启不丢）

并发模型：
    判题是 Flask 多线程 + 信号量，多个请求会同时 record()。所以所有读改
    写都在一把锁里；读的时候返回**拷贝**，否则调用方在遍历时被另一个线程
    append，会拿到半新半旧的列表——这种 bug 只在并发压测下偶发，最难查。
"""
import json
import os
import threading
import time

import verdicts

# 环形缓冲容量。400 条按「一天 200 次判题」算是两天的窗口，
# 再大对趋势判断没有帮助，只是白占内存。
RUNS_MAX = 400

# 落盘节流：指标不是账本，丢几秒不心疼；每次判题都写盘会让判题接口
# 的尾延迟被磁盘抖动带跑（Windows 上尤其明显）。
FLUSH_INTERVAL = 5.0

# 耗时直方图的分桶边界（毫秒）。边界不是等距的——判题耗时的分布
# 长尾极长（正常 300ms，TLE 能到 3.2s），等距分桶的结果是第一桶
# 塞 90% 的样本、后面全空，读不出任何信息。
BUCKETS = [0, 50, 100, 200, 300, 500, 800, 1200, 2000, 3200]

# 裁决码的顺序和分类只在 verdicts.py 里定义。这里不复制一份列表——
# 早先这里写的是 ["AC","WA","TLE","RE","CE","SE","OK"]，加一个状态时
# 忘了改这边，饼图上就少一个扇区，而且不报错。
_VERDICTS = verdicts.ALL

_lock = threading.Lock()
_runs = []                 # list[dict]，最旧在前
_day_counts = {}           # {"2026-10-27": 12}
_totals = {"all": 0, "by_verdict": {}, "ms_sum": 0}
_inflight = 0
_inflight_peak = 0
_dirty = False
_last_flush = 0.0
_path = None


# ------------------------------------------------------------------ 生命周期

def init(path):
    """绑定落盘路径并读回上次的数据。

    读失败**不能抛**：指标坏了不该让站点起不来。运维视角里这叫
    「监控不能成为单点」——面板挂了不影响业务，业务挂了才是事。
    """
    global _path, _last_flush
    _path = path
    _last_flush = time.time()
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:                                        # noqa: BLE001
        # 文件坏了就当空的：留一条备份供排查，然后从零开始
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        return

    with _lock:
        runs = blob.get("runs")
        if isinstance(runs, list):
            _runs.extend(runs[-RUNS_MAX:])
        days = blob.get("days")
        if isinstance(days, dict):
            _day_counts.update({ k: v for k, v in days.items() if isinstance(v, int) })
        tot = blob.get("totals")
        if isinstance(tot, dict):
            _totals["all"] = int(tot.get("all") or 0)
            _totals["ms_sum"] = int(tot.get("ms_sum") or 0)
            bv = tot.get("by_verdict")
            if isinstance(bv, dict):
                _totals["by_verdict"].update({ k: int(v) for k, v in bv.items() })


def flush(force=False):
    """把内存里的指标写到盘上。

    节流判据放在锁外先做一次快速判断，再进锁确认——绝大多数调用
    会在这里直接返回，不必去抢锁。判题接口的锁争用要省着用。
    """
    global _dirty, _last_flush
    if not _path:
        return
    now = time.time()
    if not force and (not _dirty or now - _last_flush < FLUSH_INTERVAL):
        return
    with _lock:
        if not force and (not _dirty or time.time() - _last_flush < FLUSH_INTERVAL):
            return
        blob = {
            "version": 1,
            "saved_at": int(now),
            "runs": _runs[-RUNS_MAX:],
            "days": dict(_day_counts),
            "totals": {
                "all": _totals["all"],
                "ms_sum": _totals["ms_sum"],
                "by_verdict": dict(_totals["by_verdict"]),
            },
        }
        _dirty = False
        _last_flush = now
    tmp = _path + ".tmp"
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False, separators=(",", ":"))
        # 原子替换：前端可能正好在另一个线程里读这个文件，
        # 直接覆写会读到半截 JSON
        os.replace(tmp, _path)
    except OSError:
        # 写不进去（只读盘 / 权限）不影响判题，静默掉
        pass


# ------------------------------------------------------------------ 采集

def enter():
    """一个判题开始排队。用来算并发水位和排队时长。"""
    global _inflight, _inflight_peak
    with _lock:
        _inflight += 1
        if _inflight > _inflight_peak:
            _inflight_peak = _inflight
        return time.time()


def leave():
    global _inflight
    with _lock:
        if _inflight > 0:
            _inflight -= 1


def record(kind, verdict, queue_ms, compile_ms, run_ms, cases=None,
           passed=0, case_ms=None, qid="", judge_ms=None, tle_kind=""):
    """记一次判题。

    kind 用来区分「交题」和「游乐场跑一下」：前者有正确性概念，
    后者没有。混在一起算 AC 率会把游乐场全算成失败。

    judge_ms 是判题器自己的墙钟：从开始编译到出裁决。它和
    compile_ms + run_ms 之间那点差额不是噪声，是「建目录与比对」——
    建临时目录、写输入文件、读输出、逐行归一化、清理。实测只有
    1–7ms：起进程的时间在 run_once 里就被计时器圈进去了，算在 run 里。
    早先这里用 queue+compile+run 当总数，那个差额就恒等于排队时长，
    单机不排队时永远是 0%，面板上那条占比永远空一截。
    """
    global _dirty
    now = time.time()
    compile_ms = int(compile_ms)
    run_ms = int(run_ms)
    # 旧调用方（或者判题器没给墙钟时）退回 compile+run：宁可让
    # 「建目录与比对」显示 0，也不能编一个数出来。
    judge_ms = int(judge_ms) if judge_ms else (compile_ms + run_ms)
    total = int(queue_ms) + judge_ms
    row = {
        "ts": round(now, 2),
        "qid": qid or "",
        "kind": kind,
        "verdict": verdict,
        "queue_ms": int(queue_ms),
        "compile_ms": compile_ms,
        "run_ms": run_ms,
        "judge_ms": judge_ms,
        "total_ms": total,
        "cases": int(cases or 0),
        "passed": int(passed or 0),
        # 故障归属。落盘时就把归属写死，而不是等聚合时再按裁决码现查——
        # 这样以后 verdicts.py 里改了某个状态的归属（比如将来把
        # 「题目没配用例」单独拆成一个状态），历史数据的归属不会被
        # 追溯性地改写。指标是账本，账本不能因为字典改版就变数。
        "fault": verdicts.owner(verdict),
        # 超时归因（cpu / wall / wait）。只有 TLE 有值，其余是空串。
        "tle_kind": tle_kind or "",
        # 逐用例耗时只留数值，不留 output——那会把文件撑到几十兆
        "case_ms": [int(x) for x in (case_ms or [])][:40],
    }
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    with _lock:
        _runs.append(row)
        if len(_runs) > RUNS_MAX:
            del _runs[:len(_runs) - RUNS_MAX]
        _day_counts[day] = _day_counts.get(day, 0) + 1
        # 只留最近 90 天的日计数：再老的趋势没人看，还占地方
        if len(_day_counts) > 90:
            for k in sorted(_day_counts)[:-90]:
                _day_counts.pop(k, None)
        _totals["all"] += 1
        _totals["ms_sum"] += total
        bv = _totals["by_verdict"]
        bv[verdict] = bv.get(verdict, 0) + 1
        _dirty = True
        dirty_now = True
    if dirty_now:
        flush()

    return row


# ------------------------------------------------------------------ 聚合

def _pct(sorted_vals, p):
    """线性插值分位数。

    不做插值的话，40 个样本的 P95 会退化成「最大值」，
    而最大值恰恰是被一个 TLE 拉爆的那个——用它做告警阈值会天天误报。
    """
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _histogram(values):
    """按 BUCKETS 分桶。最后一个桶是开区间（>= 最后一个边界）。"""
    counts = [0] * (len(BUCKETS))
    for v in values:
        placed = False
        for i in range(len(BUCKETS) - 1, -1, -1):
            if v >= BUCKETS[i]:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[0] += 1
    labels = []
    for i in range(len(BUCKETS)):
        if i == len(BUCKETS) - 1:
            labels.append("≥%d" % BUCKETS[i])
        else:
            labels.append("%d–%d" % (BUCKETS[i], BUCKETS[i + 1]))
    return {"labels": labels, "edges": BUCKETS, "counts": counts}


def snapshot(series_n=60, concurrency=3):
    """给 /ops 页面用的聚合结果。

    只传「算好的数」和「能直接画的数据」，不把原始记录扔给前端：
    400 条记录里每条的 case_ms 加起来是几万个数字，前端算分位数既慢
    又容易和 Python 侧算出不一样的答案（两边各写一份算法必然漂移）。
    """
    with _lock:
        runs = [dict(r) for r in _runs]
        days = dict(_day_counts)
        totals = {
            "all": _totals["all"],
            "ms_sum": _totals["ms_sum"],
            "by_verdict": dict(_totals["by_verdict"]),
        }
        inflight = _inflight
        peak = _inflight_peak

    submits = [r for r in runs if r.get("kind") == "submit"]
    sandbox = [r for r in runs if r.get("kind") == "run"]

    totals_ms = sorted(r["total_ms"] for r in runs)
    compiles = sorted(r["compile_ms"] for r in runs)
    queues = sorted(r["queue_ms"] for r in runs)

    # 三段耗时占比：编译 / 运行 / 其余（建临时目录 + 文件 IO + 输出比对）
    # 「其余」这一项单列是因为它是账上唯一对不上的地方——不单列出来，
    # 三段加起来就凑不满墙钟，差额没人认领。
    #
    # 分母是判题墙钟（judge_ms），不是 total_ms。total_ms 里还含着排队时长，
    # 那是另一张卡的事；把它算进分母会让三段永远凑不满 100%，条形图右边
    # 一直空一截，看上去像渲染 bug。排队时长为 0 时两者相等，所以这个
    # 区别只在并发打满时才显出来——而恰恰是那时候最需要这张图准。
    sum_compile = sum(r["compile_ms"] for r in runs)
    sum_run = sum(r["run_ms"] for r in runs)
    sum_all = sum(r["total_ms"] for r in runs)
    sum_queue = sum(r["queue_ms"] for r in runs)
    # 老记录没有 judge_ms 字段（落盘文件是跨版本读的），退回 total - queue，
    # 等价于当时的 compile+run，算出来 other=0，和它当初显示的完全一致。
    sum_judge = sum(int(r.get("judge_ms") or (r["total_ms"] - r["queue_ms"]))
                    for r in runs)
    other = max(0, sum_judge - sum_compile - sum_run)

    def rate(num, den):
        return round(num * 100.0 / den, 1) if den else 0.0

    ac = totals["by_verdict"].get("AC", 0)
    ce = totals["by_verdict"].get("CE", 0)
    tle = totals["by_verdict"].get("TLE", 0)
    re_cnt = totals["by_verdict"].get("RE", 0)
    wa_cnt = totals["by_verdict"].get("WA", 0)
    se = totals["by_verdict"].get("SE", 0)

    # 「有正确性语义的判题」= 全部请求减去 owner 为 none 的那些（游乐场 OK）。
    #
    # 为什么分母要单独算：这个站有两种请求——交题（有对错）和游乐场
    # 跑一下（只想知道能不能编译、会不会崩）。把后者放进 AC 率的分母，
    # 数学上没错，**语义上是错的**：一个人在游乐场里试了 20 次
    # Hello World，AC 率就会被稀释掉一半，而「这批题学生做得怎么样」
    # 和「他试了几次 Hello World」根本不是一个问题。
    # 于是按 verdicts.py 的 owner 字段现算，不写死 "OK"——以后再加
    # 一个无正确性语义的状态，这里自动跟上。
    ok_none = sum(n for v, n in totals["by_verdict"].items()
                  if verdicts.owner(v) == "none")
    scored = max(0, totals["all"] - ok_none)

    # 窗口内的系统故障。老记录（这个字段加进来之前落盘的）没有 fault，
    # 按裁决码现查——那时系统故障只有 SE 一类，反查结果和当初一致，
    # 不会把历史数据改写出一个不存在的结论。
    win_system = sum(
        1 for r in runs
        if (r.get("fault") or verdicts.owner(r.get("verdict") or "")) == "system")

    tle_counts = {"cpu": 0, "wall": 0, "wait": 0, "unknown": 0}
    for r in runs:
        if r.get("verdict") != "TLE":
            continue
        k = r.get("tle_kind") or ""
        if k not in ("cpu", "wall", "wait"):
            k = "unknown"
        tle_counts[k] += 1
    tle_total = sum(tle_counts.values())

    # 最近 N 条的时序，给折线图用。顺序是时间正序（旧 → 新）。
    recent = runs[-series_n:]
    series = {
        "labels": [time.strftime("%H:%M", time.localtime(r["ts"])) for r in recent],
        "total": [r["total_ms"] for r in recent],
        "compile": [r["compile_ms"] for r in recent],
        "run": [r["run_ms"] for r in recent],
        "verdict": [r["verdict"] for r in recent],
    }

    # 最慢的 6 次，用来回答「是什么把 P95 拉上去的」
    slowest = sorted(runs, key=lambda r: r["total_ms"], reverse=True)[:6]

    return {
        "now": int(time.time()),
        "saved_label": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "window": {"runs": len(runs), "capacity": RUNS_MAX, "series": len(recent)},
        "queue": {
            "inflight": inflight,
            "peak": peak,
            "capacity": concurrency,
            "saturated": inflight >= concurrency,
        },
        "counts": {
            "total": totals["all"],
            "window_total": len(runs),
            "submit": len(submits),
            "sandbox": len(sandbox),
            "ac": ac,
            "ce": ce,
            "tle": tle,
            "re": re_cnt,
            "wa": totals["by_verdict"].get("WA", 0),
            "se": totals["by_verdict"].get("SE", 0),
            "session_peak": peak,
        },
        "rates": {
            # 下面三个率的分母都是 scored（有正确性语义的判题），不是
            # totals["all"]。差别只在有人用过游乐场时才显出来——而恰恰
            # 是那时候，用错分母会让整个面板读起来像「学生变菜了」。
            # denom 单独给出来，是为了让页面上那行小字能写出真实分母，
            # 而不是让人去猜「这个百分比是按什么算的」。
            "denom": scored,
            "ac": rate(ac, scored),
            "ce": rate(ce, scored),
            "tle": rate(tle, scored),
            "re": rate(re_cnt, scored),
        },
        "reliability": {
            "total": totals["all"],
            "system": se,
            # 「判题可用性」= 1 - 系统错误率。它和 AC 率是**两种东西**：
            # AC 率是产品指标（这批题学生做得怎么样），可用性是运维指标
            # （这台机器把活干成了没有）。混在一起看，一个又难又没人做对的
            # 题库会让人以为判题机坏了——而真正坏掉的时候，那个数字反而
            # 只是「更难了一点」，因为 SE 在分母里占的份额小。
            "availability": round(100.0 - rate(se, totals["all"]), 1),
            "system_rate": rate(se, totals["all"]),
            # 用户侧的失败率：分母也是 scored。把游乐场算进来，会让
            # 「用户错误率」跟着「有人在瞎玩」一起动——那不是用户错误。
            "user_fail": wa_cnt + tle + re_cnt + ce,
            "user_fail_rate": rate(wa_cnt + tle + re_cnt + ce, scored),
            "scored": scored,
            # 窗口内（最近 400 条）单独算一份：累计值会被上周的一次故障
            # 永久拖住，而运维要判断的是「现在好不好」。
            "window_total": len(runs),
            "window_system": win_system,
            "window_rate": rate(win_system, len(runs)),
        },
        "tle": {
            # 超时归因。三个数加起来等于窗口内的 TLE 数（unknown 是降级
            # 路径上拿不到 CPU 时间的那些）。分这三档不是为了好看：
            # 「超时占 15%」这句话追不下去，而「其中 9 次是在等输入」
            # 直接指向一个可以改的东西——题面的输入格式没写清。
            "total": tle_total,
            "cpu": tle_counts["cpu"],
            "wall": tle_counts["wall"],
            "wait": tle_counts["wait"],
            "unknown": tle_counts["unknown"],
            "cpu_pct": rate(tle_counts["cpu"], tle_total),
            "wall_pct": rate(tle_counts["wall"], tle_total),
            "wait_pct": rate(tle_counts["wait"], tle_total),
            "unknown_pct": rate(tle_counts["unknown"], tle_total),
            # 四个归因的名字也从 verdicts.py 取。前端拿到 labels 之后
            # 只需要按 key 查——不然刷新时新出现的归因（比如第一次出现
            # wall）会没有名字可用，只能在前端再抄一份中文。
            "labels": {k: verdicts.tle_kind(k)["name"]
                       for k in ("cpu", "wall", "wait", "unknown")},
        },
        "latency": {
            "avg": int(sum_all / len(runs)) if runs else 0,
            "p50": int(_pct(totals_ms, 0.50)),
            "p90": int(_pct(totals_ms, 0.90)),
            "p95": int(_pct(totals_ms, 0.95)),
            "p99": int(_pct(totals_ms, 0.99)),
            "max": totals_ms[-1] if totals_ms else 0,
            "compile_avg": int(sum_compile / len(runs)) if runs else 0,
            "compile_p95": int(_pct(compiles, 0.95)),
            "queue_avg": int(sum_queue / len(runs)) if runs else 0,
            "queue_max": queues[-1] if queues else 0,
        },
        "phases": {
            # total 是判题墙钟之和（不含排队）。页面上那句「累计 X 秒」
            # 就是它，和下面三段的百分比同一个分母，加得起来。
            #
            # 保留一位小数：这个站一天才几百次判题，窗口里经常只有十几秒，
            # 取整会让「编译 0s / 运行 0s / 其余 0s」三行并排出现，
            # 看着像没采到数据。
            "compile": round(sum_compile / 1000.0, 1) if sum_judge else 0,
            "run": round(sum_run / 1000.0, 1) if sum_judge else 0,
            "other": round(other / 1000.0, 1) if sum_judge else 0,
            "total": round(sum_judge / 1000.0, 1) if sum_judge else 0,
            "compile_pct": rate(sum_compile, sum_judge),
            "run_pct": rate(sum_run, sum_judge),
            "other_pct": rate(other, sum_judge),
        },
        "histogram": _histogram([r["total_ms"] for r in runs]),
        "verdicts": {
            "labels": [v for v in _VERDICTS if totals["by_verdict"].get(v)],
            "counts": [totals["by_verdict"].get(v, 0) for v in _VERDICTS if totals["by_verdict"].get(v)],
        },
        "series": series,
        "days": [{"date": k, "count": days[k]} for k in sorted(days)[-14:]],
        "slowest": [
            {
                "ts": int(r["ts"]),
                "label": time.strftime("%m-%d %H:%M", time.localtime(r["ts"])),
                "qid": r.get("qid") or "—",
                "verdict": r["verdict"],
                "total_ms": r["total_ms"],
                "compile_ms": r["compile_ms"],
                "run_ms": r["run_ms"],
                # 「其余」= 总耗时 - 编译 - 运行 = 排队 + 起进程 + 读输出 + 比对。
                # 不给出这一列，表格里三列数字就对不上，而读表的人第一件
                # 事就是拿总耗时去减——减完对不上会以为是数据错了。
                "other_ms": max(0, r["total_ms"] - r["compile_ms"] - r["run_ms"]),
                "queue_ms": r["queue_ms"],
                "cases": r["cases"],
                "passed": r["passed"],
                "case_ms": r.get("case_ms") or [],
            }
            for r in slowest
        ],
        "health": _health(totals, totals_ms, inflight, concurrency),
    }


def _health(totals, totals_ms, inflight, concurrency):
    """把这堆数字翻译成一句人话 + 一个等级。

    面板最容易犯的错是「数字很多但没人知道该不该管」。所以这里显式
    给三个档：ok / warn / bad，前端只负责上色。判据都写在这，别散到
    前端去——否则以后加个告警规则要改两个地方。
    """
    total = totals["all"]
    if total < 5:
        return {"level": "idle", "label": "样本还少",
                "text": "再交几次题，指标才有参考价值。样本少于 5 次时不判断健康度。"}

    p95 = _pct(totals_ms, 0.95)
    se = totals["by_verdict"].get("SE", 0)
    se_rate = se * 100.0 / total
    # CE / TLE 的分母用 scored，和页面上那三张卡同一个口径。
    # 面板最怕「同一个数在两处不一样」——健康判据说「编译失败 60%」，
    # 而下面卡片写 45%，读的人第一反应是数据坏了，不是分母不同。
    ok_none = sum(n for v, n in totals["by_verdict"].items()
                  if verdicts.owner(v) == "none")
    scored = max(1, total - ok_none)
    ce_rate = totals["by_verdict"].get("CE", 0) * 100.0 / scored
    tle_rate = totals["by_verdict"].get("TLE", 0) * 100.0 / scored

    # 系统错误排在最前面。理由：其余几条都是「这台机器现在忙/慢/题不好」，
    # 只有这一条是「这台机器在骗人」——用户代码可能是对的，报出来的
    # 却是红的。而且它是唯一一条**不看流量、只看代码**的结论：并发满了
    # 等一会儿就好，SE 只会一直错下去。
    #
    # 阈值 5% 而不是 0：单机的判题流程里有几处「环境变了」的分支
    # （二进制被删、临时目录不可写），偶发一两次值得记一笔但不该把
    # 整页刷成红色——一个永远红着的面板等于没有面板。
    if se_rate >= 5:
        return {"level": "bad", "label": "判题机自身故障",
                "text": "系统错误占 %.0f%%（%d/%d），这些不是用户代码的问题。"
                        "先去 /admin 看沙箱可用性和数据目录。" % (se_rate, se, total)}
    if se > 0 and se_rate > 0:
        return {"level": "warn", "label": "偶发系统错误",
                "text": "有 %d 次判题以系统错误结束（%.1f%%）。数量不大，"
                        "但每一条都该能指向一个具体原因。" % (se, se_rate)}
    if inflight >= concurrency:
        return {"level": "warn", "label": "队列已满",
                "text": "并发闸门已占满（%d/%d），新提交会排队等锁。" % (inflight, concurrency)}
    if p95 > 2000:
        return {"level": "bad", "label": "尾延迟过高",
                "text": "P95 = %d ms，超过 2 秒。先看下面「最慢的几次」里是不是有 TLE 在拖。" % p95}
    if ce_rate > 40:
        return {"level": "warn", "label": "编译失败偏多",
                "text": "编译失败占 %.0f%%，多半是题目描述或初始模板的问题，不是用户写错。" % ce_rate}
    if tle_rate > 15:
        return {"level": "warn", "label": "超时偏多",
                "text": "超时占 %.0f%%。可能是时限设得太紧，也可能是题目本身有死循环陷阱。" % tle_rate}
    return {"level": "ok", "label": "运行正常",
            "text": "P95 = %d ms，并发 %d/%d，没有异常聚集。" % (p95, inflight, concurrency)}
