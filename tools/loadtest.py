"""造真实判题流量，把 /ops 面板喂满。

为什么需要它：
    /ops 上每个数字都来自**真判题**——真编译、真起进程、真比对输出。
    手工点十几次太慢，而且人点出来的裁决分布是偏的（都是 AC）。
    这个脚本按固定比例混合 AC / WA / CE / TLE 和游乐场运行，
    跑完面板就有形状了：直方图有长尾、环上有几种颜色、表格里有几行慢的。

为什么走 HTTP 而不是直接 import judge：
    1. 只有走 /api/submit 才会经过并发闸门、才会写排队时长和并发水位。
       直接调 judge.judge() 面板上的「并发占用」永远是 0。
    2. 更重要的：import app 会在**这个脚本的进程里**再跑一遍
       metrics.init() 并注册 atexit.flush()。脚本退出时那一刷会把服务
       进程刚写好的 data/metrics.json 覆盖成空文件——面板数据凭空消失，
       而且极难定位。所以这里连题库都从 data/*.json 直接读。

跑法：
    python tools/loadtest.py                    # 24 次，默认打 127.0.0.1:5001
    python tools/loadtest.py -n 60 --jobs 3     # 60 次，3 个并发（能压出排队）
    python tools/loadtest.py --no-tle           # 跳过超时用例（每次要等完时限）
    python tools/loadtest.py --dry-run          # 只打印计划，不发请求

副作用：
    · 会注册（或复用）一个 loadtest 账号，进度写进 data/progress/loadtest.json
    · 想清干净：删掉那个文件即可，指标是环形缓冲，会自动被新记录挤掉
"""
import argparse
import http.cookiejar
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------- 场景

# 通用的「错答案」：能编译、能跑，但输出不对。
# 用 scanf 读一个整数再打印 x+1，这样：
#   · 有输入有输出的题 → 数值不对 → WA
#   · 纯输出题（Hello, C 那种）→ 什么都不打印 → WA
#   · 输入是字符串的题 → scanf 失败 → 什么都不打印 → WA
# 一个程序覆盖三种情况，不用给每道题各写一份错答案。
WRONG_CODE = """#include <stdio.h>
int main(void) {
    int x = 0;
    if (scanf("%d", &x) == 1) {
        printf("%d\\n", x + 1);
    }
    return 0;
}
"""

# 少一个分号。错误信息会落在第 2 行，正好模拟「手滑」而不是「不会」。
CE_CODE = """#include <stdio.h>
int main(void) {
    printf("hi\\n")
    return 0;
}
"""

# 死循环。用来喂 TLE，也用来把 P95 拉起来——这样「最慢的几次」那张表
# 才有话说，否则零零星星的 300ms 看不出长尾。
TLE_CODE = """int main(void) {
    volatile unsigned long i = 0;
    while (1) { i++; }
    return 0;
}
"""

# 场景配比。TLE 单独限制数量：每次都要等完时限（1~3 秒），
# 比例稍高一点整轮就跑很久。
MIX = [
    ("ac", 0.50),
    ("wa", 0.22),
    ("ce", 0.15),
    ("run", 0.09),
    ("tle", 0.04),
]


# ---------------------------------------------------------------- HTTP

class Client(object):
    """一个带 cookie 的最小 HTTP 客户端。

    不用 requests：这个项目一条外部依赖都没有，工具脚本也不该开这个口子。
    urllib 的 CookieJar 足够处理这里唯一的会话 cookie。
    """

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def get(self, path, timeout=20):
        req = urllib.request.Request(self.base + path, method="GET")
        with self.opener.open(req, timeout=timeout) as r:
            return r.geturl(), r.read().decode("utf-8", "replace")

    def get_json(self, path, timeout=20):
        _, body = self.get(path, timeout=timeout)
        return json.loads(body)

    def post_form(self, path, fields, timeout=20):
        body = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.opener.open(req, timeout=timeout) as r:
            return r.geturl(), r.read().decode("utf-8", "replace")

    def post_json(self, path, payload, timeout=120):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with self.opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))


def login_or_register(cli, user, password):
    """先试登录，失败再注册。

    两个接口都是 POST /login，用 action 字段区分（见 app.py 的 login_page）。
    成功与否看最终 URL：成功后是 302 到 next，失败是就地渲染登录页（200）。
    """
    url, _ = cli.post_form("/login", {
        "username": user, "password": password, "action": "login", "next": "/",
    })
    if not url.rstrip("/").endswith("/login"):
        return "login"

    url, _ = cli.post_form("/login", {
        "username": user, "password": password, "action": "register", "next": "/",
    })
    if not url.rstrip("/").endswith("/login"):
        return "register"
    raise SystemExit("登录和注册都失败了，检查 --user / --password（密码至少 6 位）")


# ---------------------------------------------------------------- 计划

def load_data():
    with open(os.path.join(DATA, "question_bank.json"), encoding="utf-8") as f:
        questions = json.load(f)
    with open(os.path.join(DATA, "answers.json"), encoding="utf-8") as f:
        answers = json.load(f)
    usable = [q for q in questions if answers.get(q["id"], "").strip()]
    if not usable:
        raise SystemExit("answers.json 里没有可用答案，先跑 tools/gate.py 检查题库")
    return usable, answers


def build_plan(n, rng, allow_tle):
    """生成 n 个场景的清单。

    用 rng.choices 按权重抽，而不是「按比例铺满再取前 n 个」——后者在
    n 远小于 100 时会整段落在同一个场景上（前 50 个名额全是 AC）。
    抽完再打散顺序：不打散的话 AC 会挤在曲线左半边、CE 挤在右半边，
    看起来像「前半天正常后半天崩了」，那是假的。
    """
    kinds = [k for k, _ in MIX]
    weights = [w for _, w in MIX]
    plan = rng.choices(kinds, weights=weights, k=n)
    if not allow_tle:
        # 关掉 TLE 就把那几个名额改成 AC。不改总权重，
        # 其余场景之间的比例保持不变。
        plan = ["ac" if k == "tle" else k for k in plan]
    rng.shuffle(plan)
    return plan


# ---------------------------------------------------------------- 执行

def do_one(cli, kind, qid, answer, stdin_data):
    """跑一个场景，返回 (裁决, 耗时毫秒)。

    耗时是**客户端墙钟**，包含网络与队列，和面板上的 judge_ms 不是一个数；
    它只用来看「这一轮有多慢」，不参与断言。
    """
    t0 = time.time()
    if kind == "ac":
        r = cli.post_json("/api/submit", {"qid": qid, "code": answer})
        verdict = (r.get("result") or {}).get("status", "?")
    elif kind == "wa":
        r = cli.post_json("/api/submit", {"qid": qid, "code": WRONG_CODE})
        verdict = (r.get("result") or {}).get("status", "?")
    elif kind == "ce":
        r = cli.post_json("/api/submit", {"qid": qid, "code": CE_CODE})
        verdict = (r.get("result") or {}).get("status", "?")
    elif kind == "tle":
        r = cli.post_json("/api/submit", {"qid": qid, "code": TLE_CODE})
        verdict = (r.get("result") or {}).get("status", "?")
    elif kind == "run":
        # 游乐场运行不要求登录，走的是另一条计数线（counts.sandbox）。
        # 面板上专门把这一项单列，就是为了让人看见它和「交题」不是一回事。
        r = cli.post_json("/api/run", {"code": answer, "stdin": stdin_data})
        verdict = "OK" if r.get("ok") else "?"
    else:
        raise ValueError(kind)
    return verdict, int((time.time() - t0) * 1000)


def main():
    ap = argparse.ArgumentParser(description="给 /ops 面板造真实判题流量")
    ap.add_argument("-n", "--count", type=int, default=24, help="提交次数（默认 24）")
    ap.add_argument("--url", default="http://127.0.0.1:5001", help="站点地址")
    ap.add_argument("--user", default="loadtest", help="用哪个账号交题")
    ap.add_argument("--password", default="loadtest123", help="账号密码")
    ap.add_argument("--jobs", type=int, default=2,
                    help="并发数（默认 2；设到 3 以上才压得出排队时长）")
    ap.add_argument("--seed", type=int, default=7, help="随机种子，同种子同顺序")
    ap.add_argument("--no-tle", action="store_true", help="跳过超时场景（省时间）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    args = ap.parse_args()

    questions, answers = load_data()
    rng = random.Random(args.seed)

    # 题目挑「用例数最少」的那几道：轮子转得快，单位时间能造更多样本，
    # 而且用例少不代表判题流程有差别（编译、起进程、比对，一步不少）。
    questions.sort(key=lambda q: (len(q.get("cases") or []), q["id"]))
    pool = questions[: max(6, len(questions) // 4)]

    plan = build_plan(args.count, rng, not args.no_tle)
    jobs = []
    for i, kind in enumerate(plan):
        q = pool[i % len(pool)]
        cases = q.get("cases") or [{}]
        stdin_data = cases[0].get("stdin", "") or ""
        jobs.append((kind, q["id"], answers.get(q["id"], ""), stdin_data))

    tally = {}
    for kind, _, _, _ in jobs:
        tally[kind] = tally.get(kind, 0) + 1
    print("计划 %d 次：%s" % (len(jobs), ", ".join("%s×%d" % (k, v) for k, v in sorted(tally.items()))))

    if args.dry_run:
        for i, (kind, qid, _, _) in enumerate(jobs, 1):
            print("  %2d  %-4s %s" % (i, kind, qid))
        return 0

    cli = Client(args.url)
    how = login_or_register(cli, args.user, args.password)
    print("账号 %s —— %s成功" % (args.user, "注册" if how == "register" else "登录"))

    # 每个线程一个 Client：CookieJar 和 opener 都不是线程安全的，
    # 共用会在并发时随机丢 cookie，表现为「偶尔 302 到登录页」。
    local = threading.local()

    def client_for_thread():
        if getattr(local, "cli", None) is None:
            local.cli = Client(args.url)
            login_or_register(local.cli, args.user, args.password)
        return local.cli

    lock = threading.Lock()
    results = []
    t_start = time.time()

    def work(idx_job):
        idx, (kind, qid, answer, stdin_data) = idx_job
        try:
            verdict, ms = do_one(client_for_thread(), kind, qid, answer, stdin_data)
        except urllib.error.HTTPError as e:
            verdict, ms = "HTTP%d" % e.code, 0
        except Exception as e:                       # noqa: BLE001
            verdict, ms = "ERR", 0
            with lock:
                print("  %2d  %-4s %s  <- %s" % (idx, kind, qid, e))
        with lock:
            results.append((idx, kind, qid, verdict, ms))
            if verdict in ("ERR",) or verdict.startswith("HTTP"):
                print("  %2d  %-4s %s  -> %s" % (idx, kind, qid, verdict))
        return verdict

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool_exec:
        list(pool_exec.map(work, list(enumerate(jobs, 1))))

    elapsed = time.time() - t_start

    # 结果按提交序号排回来：并发跑完的顺序是乱的，按完成顺序打出来
    # 会看成「裁决是随机的」，而实际上每个位置跑的是什么早就定好了。
    results.sort()
    print("\n%-4s %-5s %-6s %s" % ("#", "场景", "题目", "裁决"))
    got = {}
    for idx, kind, qid, verdict, ms in results:
        print("%-4d %-5s %-6s %-4s %5d ms" % (idx, kind, qid, verdict, ms))
        got[verdict] = got.get(verdict, 0) + 1

    print("\n用时 %.1f 秒 / %d 次（并发 %d），裁决分布：%s"
          % (elapsed, len(results), args.jobs,
             ", ".join("%s×%d" % (k, v) for k, v in sorted(got.items()))))

    # 顺手把面板上会显示的东西打一份出来。省得再开浏览器，
    # 也顺手验证了 /api/ops 返回的结构没问题。
    try:
        snap = cli.get_json("/api/ops")
    except Exception:                                # noqa: BLE001
        snap = None
    if snap and snap.get("ok"):
        s = snap["snapshot"]
        print("\n/ops 现在的样子：")
        print("  窗口 %d 次（容量 %d）· 总次数 %d"
              % (s["window"]["runs"], s["window"]["capacity"], s["counts"]["total"]))
        print("  P50 %d ms · P90 %d ms · P95 %d ms · P99 %d ms"
              % (s["latency"]["p50"], s["latency"]["p90"],
                 s["latency"]["p95"], s["latency"]["p99"]))
        # 保留一位小数。这里原先用 %d，0.9% 会被截成 0%，
        # 看上去像「那一段没采到数据」——而它其实一直在采，只是小。
        print("  编译 %.1f%% · 运行 %.1f%% · 建目录与比对 %.1f%%"
              % (s["phases"]["compile_pct"], s["phases"]["run_pct"],
                 s["phases"]["other_pct"]))
        print("  健康：%s —— %s" % (s["health"]["label"], s["health"]["text"]))
    else:
        print("\n（拿不到 /api/ops 快照，手动打开页面看吧）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
