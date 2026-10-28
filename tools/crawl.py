"""全站爬取回归：登录态下把每个页面都抓一遍，检查状态码与模板残留。

只读，不改任何数据。跑法：
    ./runtime/venv/Scripts/python.exe tools/crawl.py
"""
import json
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error

# 允许换目标：`CMASTER_BASE=http://127.0.0.1:5002` 就能拿另一个实例验收。
# 这不是为了「以后可能要」，是为了验现在这条路径：`/admin` 的 403 分支
# 只有在 CMASTER_ADMIN 配了、且当前账号不在名单里时才走得到，而常驻的
# 开发实例恰恰**不配**白名单。换句话说，不换实例这份「预期拒绝」的代码
# 永远只有读过的份，没有跑过的份。
BASE = os.environ.get("CMASTER_BASE", "http://127.0.0.1:5001").rstrip("/")
COOKIE_FILE = os.path.join(tempfile.gettempdir(), "cmck.txt")


def load_cookie(path):
    """从 curl 的 cookie jar 里把 session 取出来。

    `#HttpOnly_` 这个前缀是数据、不是注释——这一点踩过坑，别改回去。
    curl 写 jar 时给 HttpOnly 的 cookie 行加 `#HttpOnly_` 前缀，而
    Flask 的 session cookie 恰好就是 HttpOnly 的，于是先判 `startswith("#")`
    再 continue 的写法会把**唯一有用的那一行**当注释扔掉，整个 jar 解析出
    空字符串。更麻烦的是它平时不露脸：jar 里只要还有别的非 HttpOnly cookie
    （登录页的偏好项之类），请求仍然带着它们过去，看起来「登录态有效」；
    等哪天重登过、jar 里只剩 session 一条，脚本就突然报「7 个受保护页面
    被抓成了登录页」，第一反应会去查登录过期，其实一行都没过期。
    """
    pairs = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 7:
                pairs.append("%s=%s" % (parts[5], parts[6]))
    return "; ".join(pairs)


COOKIE = load_cookie(COOKIE_FILE)


def get(path):
    req = urllib.request.Request(BASE + path)
    if COOKIE:
        req.add_header("Cookie", COOKIE)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, repr(e)


def main():
    # 脚本在 tools/ 下，要借用项目根的 app 模块算 URL 清单，避免手写
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app

    # /ops 要登录才给看（见 ops_page 的注释），crawl 全程带 cookie，所以能抓到。
    # /admin 也列进来：它是维护者页、渲染最重（沙箱自检 + 指标 + 运行环境），
    # 恰恰是最容易在某个字段上 KeyError 而 500 的一页。不爬它，就等于承认
    # 「这一页坏了没人知道」——/ops 就是这么坏过一轮的。
    paths = ["/", "/problems", "/playground", "/wrongbook", "/profile",
             "/analytics", "/map", "/ops", "/admin", "/memory", "/training",
             "/login", "/favicon.ico"]
    for cid, ch in app.CHAPTERS.items():
        paths.append("/chapter/%d" % cid)
        for kid in range(1, len(ch["knowledge_points"]) + 1):
            paths.append("/chapter/%d/%d" % (cid, kid))
    for qid in app.QMAP:
        paths.append("/problem/%s" % qid)
    paths.append("/no-such-page")  # 404 页

    bad, residual, empty, denied, guarded = [], [], [], [], []

    # 需要登录才能看的页面。它们的判据不是「状态码 200」——未登录时
    # Flask 会 302 到 /login，urllib 跟过去也是 200，于是「全站 0 异常」
    # 变成一句空话：实际抓到的是十几份一模一样的登录页。
    # 这个坑真踩过：/ops 在登录态下 500，而爬取报的是全绿。
    PROTECTED = ("/wrongbook", "/profile", "/analytics", "/map", "/ops",
                 "/memory", "/training", "/admin")
    LOGIN_MARK = 'name="password"'

    def is_login_page(body):
        return LOGIN_MARK in body and "/login" in body

    def find_residual(body):
        """找模板没渲染干净的痕迹。

        不能只搜 '{{'：C 的数组初始化 `int a[2][2] = {{1,2},{3,4}};`
        渲染出来长得一模一样。判据是「花括号里是 Jinja 表达式」——
        即含字母、且不含 HTML 标签。`{{1,2}}` 全是数字和逗号，放过。
        """
        if "{%" in body or "jinja2.exceptions" in body:
            return True, "block-tag"
        for m in re.finditer(r"\{\{", body):
            tail = body[m.start():m.start() + 120]
            end = tail.find("}}")
            if end < 0:
                continue
            inner = tail[2:end]
            if "<" in inner:
                continue
            if re.search(r"[A-Za-z_]", inner):
                return True, tail[:80]
        return False, ""

    for p in paths:
        code, body = get(p)
        want = 404 if p == "/no-such-page" else 200
        if code != want:
            # 403 出现在受保护页面上是**预期的拒绝**，不是故障：配了
            # CMASTER_ADMIN 时，验收账号不一定在名单里（本地通常干脆不配）。
            # 把它算进「异常状态码」，运维会把「加了白名单」读成「站点坏了」，
            # 而这两件事的处置方向正好相反。单独记一类，数出来给人看。
            if code == 403 and p in PROTECTED:
                guarded.append(p)
                continue
            bad.append((p, code, body[:160].replace("\n", " ")))
            continue
        # 登录态失效是**前置条件不满足**，不是某一页的错。单独记一类，
        # 否则它会伪装成 134 个「正常页面」，而真正的问题藏在后面。
        if p in PROTECTED and is_login_page(body):
            denied.append(p)
            continue
        if code == 200:
            hit, why = find_residual(body)
            if hit:
                residual.append((p, why))
        if code == 200 and p not in ("/favicon.ico",) and len(body) < 400:
            empty.append((p, len(body)))

    if denied:
        print("登录态无效：%d 个受保护页面被抓成了登录页" % len(denied))
        print("  %s" % " ".join(denied[:8]))
        print("  先登录并把 cookie 落到 %s，再跑这个脚本。" % COOKIE_FILE)
        print("  （未登录时的「全绿」没有任何意义——它只证明了登录页能打开。）")
        return 3

    print("页面总数 %d" % len(paths))
    print("异常状态码 %d" % len(bad))
    for p, c, b in bad[:12]:
        print("  [%s] %s -> %s" % (c, p, b))
    print("预期拒绝（CMASTER_ADMIN 生效）%d" % len(guarded))
    for p in guarded[:6]:
        print("  %s 403" % p)
    print("模板残留 %d" % len(residual))
    for p, b in residual[:8]:
        print("  %s ... %s" % (p, b.replace("\n", " ")))
    print("疑似空页 %d" % len(empty))
    for p, n in empty[:8]:
        print("  %s (%d bytes)" % (p, n))

    # 静态资源：路径按 templates/ 里实际 url_for 的写法填，别靠猜
    static = [
        "/static/css/style.css",
        "/static/css/motion.css",
        "/static/css/chart.css",
        "/static/css/analytics.css",
        "/static/css/graph.css",
        "/static/css/map.css",
        "/static/css/ops.css",
        "/static/css/memory.css",
        "/static/css/training.css",
        "/static/js/app.js",
        "/static/js/editor.js",
        "/static/js/ux.js",
        "/static/js/lib/tween.js",
        "/static/js/lib/chart.js",
        "/static/js/analytics.js",
        "/static/js/lib/graph.js",
        "/static/js/map.js",
        "/static/js/ops.js",
        "/static/js/lib/memview.js",
        "/static/js/memory.js",
        "/static/js/training.js",
        "/static/favicon.svg",
        "/favicon.ico",
        "/static/vendor/codemirror/lib/codemirror.min.js",
        "/static/vendor/codemirror/lib/codemirror.min.css",
        "/static/vendor/codemirror/theme/material-darker.min.css",
        "/static/vendor/codemirror/mode/clike/clike.min.js",
        "/static/vendor/codemirror/addon/edit/closebrackets.min.js",
        "/static/vendor/codemirror/addon/selection/active-line.min.js",
    ]
    print("\n静态资源：")
    static_bad = 0
    for s in static:
        code, body = get(s)
        ok = code == 200 and len(body) > 60  # app.js 本身就只有几行，别设太高
        if not ok:
            static_bad += 1
        print("  %s %s %d (%d bytes)" % ("OK " if ok else "BAD", s, code, len(body)))

    # 接口契约：页面全绿**不代表接口没坏**。
    # 这一节是补一个真实的盲区——/api/verdicts 刚加进 app.py 的那一轮，
    # 页面 133 个全 200，而这个接口因为模板变量拼错是 500；没有任何页面
    # 会暴露它，只有真去 GET 一次才知道。契约被谁读：判题器、面板、前端，
    # 三方都读的地方，坏一天也没人报错，只会悄悄少一条状态。
    contracts = [
        ("/healthz", None),                    # 纯文本 ok，只验状态码
        ("/readyz", ("ok", "checks")),         # JSON：两条以上就绪检查
        ("/api/verdicts", ("ok", "verdicts", "tle_kinds")),
        ("/api/ops", ("ok", "snapshot")),      # 要登录态，正好验证 cookie 有效
    ]
    print("\n接口契约：")
    contract_bad = 0
    for path, keys in contracts:
        code, body = get(path)
        ok = code == 200
        detail = ""
        if ok and keys:
            try:
                data = json.loads(body)
            except ValueError:
                ok, detail = False, "返回的不是 JSON"
            else:
                miss = [k for k in keys if k not in data]
                if miss:
                    ok, detail = False, "缺字段 " + ",".join(miss)
        elif not ok:
            detail = "状态码 %d" % code
        if not ok:
            contract_bad += 1
        print("  %s %s %d %s" % ("OK " if ok else "BAD", path, code, detail))

    return 0 if (not bad and not residual and not empty
                 and not static_bad and not contract_bad) else 1


if __name__ == "__main__":
    sys.exit(main())
