# -*- coding: utf-8 -*-
"""
C-Master 沙箱执行层

职责：把 sandbox/runner_win.c 和 sandbox/runner_linux.c 这两个 C 程序包装成
judge.py 能直接调用的 run()，并且**在沙箱不可用时诚实地降级、在沙箱本该可用
却装不上时拒绝执行**。

为什么不直接在 judge.py 里拼 subprocess 参数：
  · 两个平台的参数有差异（Linux 多 --as= / --no-seccomp），调用方不该知道；
  · 报告是 key=value 文本，解析只有一份，放这里才不会平台分叉；
  · 「沙箱到底有没有生效」这件事需要一个唯一的说法（sandboxed 字段），
    如果每个调用方自己判断，迟早出现一处判真一处判假。

三条设计上的取舍，都是有代价的选择，写在这里免得以后自己忘：

  1. **能装就装，装不上就降级，但降级会喊**
     runner 是从源码现编的（仓库里不放 10 万字节的 exe，也不想让人误用旧版）。
     没有编译器 / 平台不支持 → 退回 subprocess，结果里 sandboxed=False，
     并在 note 里写明原因。这是给「本机跑一下」留的路，不是给生产留的。

  2. **本该可用却初始化失败 → 拒绝执行（fail closed）**
     runner 明明编译出来了、探针也过了，真跑的时候却返回 126（建不了 job /
     setuid 失败 / seccomp 装不上），这时候**不能**退回 subprocess——那等于
     「沙箱坏了就裸跑用户代码」，比一开始没有沙箱更糟，因为它把降级藏起来了。
     所以这里返回 error="sandbox_init_failed"，judge.py 把它记成 SE。
     宁可判不了题，也不要在没有任何隔离的情况下执行不可信代码。

  3. **selftest() 真编译真跑，不是检查版本号**
     runner_linux.c 是在没有 Linux 机器的条件下写的（开发机是 Windows，WSL 被
     策略禁用），所以「它能不能用」这个问题只能由目标机器自己回答：selftest
     编译四个越界程序各跑一遍，看沙箱有没有真的挡住。/admin 上那四个绿点就是
     这个文件的验收单，红了就说明某个假设错了。
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SANDBOX_DIR = os.path.join(BASE_DIR, "sandbox")

# 默认配额。数值和 judge.py 的常量对齐，但**不 import** 它——
# 反过来 judge 要 import 本模块，两边互相 import 会在加载期打死结。
# 判题时限由调用方（judge.run_once）显式传进来，这里的值只在调用方
# 偷懒时兜底，作用相当于安全带而不是方向盘。
DEF_MS = 3000
DEF_MEM = 256 * 1024 * 1024
DEF_PROC = 1
DEF_OUT = 64 * 1024
DEF_ERR = 16 * 1024

# CPU 时间上限比墙钟宽一点：wall clock 里含 sleep / 等 IO 的时间，
# 而 CPU 时间是实打实烧掉的。给 1.5 倍 + 1 秒，是为了让「睡 3 秒」的
# 程序按墙钟判 TLE，而不是被 CPU 配额提前砍掉（两者对用户是同一个结论，
# 但 cpu_ms 这个指标会失真）。
def _cpu_ms_for(wall_ms):
    return int(wall_ms) * 3 // 2 + 1000


# --------------------------------------------------------------- 运行统计
#
# /admin 要回答「今天到底有多少次判题是从沙箱里过去的」。这个计数器和
# metrics.py 不是一回事：metrics 记的是每次判题的耗时分布（有环形缓冲、
# 会落盘），这里记的是**沙箱自身的状态机**（走没走沙箱、有没有降级、
# 有没有初始化失败）。混进 metrics 会把两种语义搅在一起。
_stats_lock = threading.Lock()
_stats = {
    "runs": 0,            # 总执行次数
    "sandboxed": 0,       # 走了沙箱
    "fallback": 0,        # 退化成 subprocess（没有沙箱）
    "timeouts": 0,        # 墙钟超限
    "output_limited": 0,  # 输出超限被砍
    "init_failed": 0,     # 沙箱本该可用却初始化失败（拒绝执行）
}
_stats_since = time.time()


def stats():
    """给 /admin 用的计数器快照。"""
    with _stats_lock:
        d = dict(_stats)
    d["since"] = int(_stats_since)
    d["uptime_s"] = int(time.time() - _stats_since)
    return d


def _bump(key, n=1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n


# --------------------------------------------------------------- 后端探测

def _runner_names():
    """返回 (可执行文件名, 源文件名)。文件名带平台后缀，别指望 Windows 认无后缀的。"""
    if os.name == "nt":
        return "runner_win.exe", "runner_win.c"
    return "runner_linux", "runner_linux.c"


def _gcc():
    """拿编译器。

    延迟 import judge 是**有意**的：judge.py 顶部 import 本模块，
    本模块顶部再 import judge 就是一个死结。放在函数里，调用时 judge
    早就加载完了。

    自己再找一遍 PATH 里的 gcc 作为兜底，是为了让 `python sandbox.py`
    这种单独跑的场合也能工作——那时候 judge 还没被加载，但 import 依然成功。
    """
    try:
        import judge as _j  # noqa: PLC0415
        p = _j.find_compiler()
        if p:
            return p
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("gcc") or shutil.which("cc")


_build = {"done": False, "ok": False, "path": None, "reason": "", "ms": 0}
_build_lock = threading.Lock()


def ensure_runner(force=False):
    """确保 runner 可执行文件存在，必要时从源码编一个。结果缓存。

    返回 dict: {"ok", "path", "reason", "ms"}

    每次都编不现实（gcc 一遍要一秒多，判题路径上不能有这种固定开销），
    所以按 mtime 判新旧：源码改了自动重编，这样不会出现「代码修好了、
    跑的还是老二进制」这种最难查的问题。
    """
    with _build_lock:
        if _build["done"] and not force:
            return {"ok": _build["ok"], "path": _build["path"],
                    "reason": _build["reason"], "ms": _build["ms"]}

        t0 = time.time()
        name, src_name = _runner_names()
        exe = os.path.join(SANDBOX_DIR, name)
        src = os.path.join(SANDBOX_DIR, src_name)

        def done(ok, reason=""):
            _build.update(done=True, ok=ok, path=exe if ok else None,
                          reason=reason, ms=int((time.time() - t0) * 1000))
            return {"ok": ok, "path": exe if ok else None,
                    "reason": reason, "ms": _build["ms"]}

        if not os.path.isfile(src):
            return done(False, "找不到沙箱源码 %s" % src_name)

        if os.path.isfile(exe):
            try:
                fresh = os.path.getmtime(exe) >= os.path.getmtime(src)
            except OSError:
                fresh = False
            if fresh:
                return done(True, "")

        gcc = _gcc()
        if not gcc:
            return done(False, "没有编译器，无法从源码构建沙箱宿主")

        # 两边的标准号不一样，别图省事写成同一个：
        #   Linux 必须 gnu11 —— runner_linux.c 的头注释里写了原因，seccomp 的
        #   BPF_STMT 宏和 usleep/strdup 的声明都要 GNU 扩展；用 -std=c11 严格
        #   模式编，轻则隐式声明警告炸成一片，重则链不上。而镜像里构建时用的
        #   也是 gnu11（见 Dockerfile），两处必须一致，否则「本地能编、镜像编
        #   不过」这种差异只在换机器时才暴露。
        #   Windows 用 c11 —— runner_win.c 全是 Win32 API，没有 GNU 依赖。
        std = "c11" if os.name == "nt" else "gnu11"
        cmd = [gcc, "-O2", "-std=" + std, "-Wall", "-Wextra"]
        if os.name == "nt":
            # runner_win.c 用的是 wmain（宽字符入口），不加 -municode
            # 会链不上 CRT 启动代码，报 undefined reference to WinMain。
            cmd.append("-municode")
        cmd += ["-o", exe, src]

        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return done(False, "编译沙箱宿主超时（120s）")
        except Exception as e:  # noqa: BLE001
            return done(False, "编译沙箱宿主失败：%s" % e)

        if r.returncode != 0 or not os.path.isfile(exe):
            tail = " ".join(((r.stderr or "").strip().splitlines())[-3:])[:300]
            return done(False, "编译沙箱宿主失败：%s" % (tail or "无诊断信息"))
        return done(True, "")


_probe = {"done": False, "ok": False, "reason": ""}


def probe():
    """探针：真的把 runner 跑起来一次，确认这个二进制在这台机器上能执行。

    为什么不只看文件存在：交叉编译出来的二进制、缺 DLL 的 exe、没有执行位的
    文件，`os.path.isfile` 全部会说「有」。等判题时才发现跑不起来，用户看到的是
    「系统错误」，运维看到的是「一切正常」——这是最坏的一种失败。

    探针方式：不带参数跑一次，runner 的参数解析到「没有指定要运行的程序」就会
    die(127)。所以「退出码 127 + stderr 有内容」恰好证明它跑到了自己的 main。
    """
    if _probe["done"]:
        return {"ok": _probe["ok"], "reason": _probe["reason"]}
    b = ensure_runner()
    if not b["ok"]:
        _probe.update(done=True, ok=False, reason=b["reason"])
        return {"ok": False, "reason": b["reason"]}
    try:
        r = subprocess.run([b["path"]], capture_output=True, timeout=10,
                           encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        _probe.update(done=True, ok=False, reason="沙箱宿主无法执行：%s" % e)
        return {"ok": False, "reason": _probe["reason"]}
    if r.returncode != 127:
        _probe.update(done=True, ok=False,
                      reason="沙箱宿主行为异常（期望退出码 127，实际 %s）" % r.returncode)
        return {"ok": False, "reason": _probe["reason"]}
    _probe.update(done=True, ok=True, reason="")
    return {"ok": True, "reason": ""}


def _backend_name():
    if os.name == "nt":
        return "windows-job"
    return "linux-seccomp"


def available():
    """沙箱可用状态。结果缓存，/admin 和每次 run() 都读它。

    返回 dict:
      mode   : "sandbox" | "fallback" | "disabled"
      ok     : mode == "sandbox"
      backend: "windows-job" | "linux-seccomp" | "subprocess"
      reason : 不可用时的人话原因（会直接显示在 /admin 上）
    """
    off = (os.environ.get("CMASTER_SANDBOX", "") or "").strip().lower()
    if off in ("0", "off", "false", "no", "none", "subprocess"):
        return {"mode": "disabled", "ok": False, "backend": "subprocess",
                "runner": None, "build_ms": 0,
                "reason": "被 CMASTER_SANDBOX=%s 显式关闭（仅限本机可信环境）" % off}

    b = ensure_runner()
    if not b["ok"]:
        return {"mode": "fallback", "ok": False, "backend": "subprocess",
                "runner": None, "build_ms": b["ms"], "reason": b["reason"]}

    p = probe()
    if not p["ok"]:
        return {"mode": "fallback", "ok": False, "backend": "subprocess",
                "runner": None, "build_ms": b["ms"], "reason": p["reason"]}

    return {"mode": "sandbox", "ok": True, "backend": _backend_name(),
            "runner": b["path"], "build_ms": b["ms"], "reason": ""}


def limits(time_limit=None):
    """把一组配额整理成 dict。给 run() 用，也给 /admin 展示用。"""
    wall_ms = int(round((time_limit if time_limit else DEF_MS / 1000.0) * 1000))
    if wall_ms <= 0:
        wall_ms = DEF_MS
    return {
        "wall_ms": wall_ms,
        "cpu_ms": _cpu_ms_for(wall_ms),
        "mem": DEF_MEM,
        "proc": DEF_PROC,
        "out": DEF_OUT,
        "err": DEF_ERR,
    }


# --------------------------------------------------------------- 超时归因

# 归因的参照系是**墙钟**，不是 CPU 配额。这一点踩过坑，写在这里省得再踩：
#     CPU 配额是 1.5×墙钟 + 1000ms（见 _cpu_ms_for），所以墙钟永远先到，
#     "cpu >= 配额 * 0.9" 这个判据在默认配置下**永不成立**。第一版就是
#     这么写的，结果一个纯 CPU 死循环落进了「算等参半」档——面板上最该被
#     区分开的两种超时（算得慢 / 在等）合成了一个。删掉那个判据，改成
#     拿 CPU 时间比墙钟：墙钟是实际花掉的时间，CPU 是其中真正在算的部分，
#     两者之比就是「这段时间里有多少是在算」。
_CPU_BOUND = 0.85     # 几乎整段都在算
_IDLE_RATIO = 0.10    # 几乎整段都没算

# 「在等」还要加一道绝对值下限：一次 200ms 的程序里 CPU 只有 15ms 也很正常
# （进程启动、动态链接本身就要几十毫秒），占比 8% 不能说明它在等。
# 但一次 3 秒的超时里 CPU 只有 40ms，那一定是在等。
_IDLE_CPU_MS = 150


def attribute_timeout(res, lim=None):
    """给一次超时归因：算得慢 / 算等参半 / 在等。

    借的是 DMOJ 那套双时钟的思路（它有一个 wall_clock_factor 参数，
    把「CPU 时间是判据、墙钟是兜底」这件事显式写进配置）。**判据我们
    没跟着改**，理由写在下面；借过来的是它的**归因**——一个 TLE 徽章
    对学习者几乎没有信息量，「在等」和「算得慢」的改法却是两回事。

    为什么判据还是墙钟而不是 CPU：
        学生的程序 3 秒内没给出答案就是没给出答案，不管那 3 秒是烧 CPU
        还是睡觉。改成纯 CPU 判据会让 `sleep(10)` 直接通过——对一个教 C
        的站来说那不是公平性提升，是把标准改错了。

    三档（顺序不能反，先判最能证伪的那档）：
        cpu   有「CPU 时间超限」这条 note，或 CPU 占掉了墙钟的 85% 以上。
              主体是「一直在算，就是算不完」：改复杂度和常数。
        wait  墙钟走完了，但 CPU 占比不足 10%。这一档最能帮上忙：
              程序没在算，在等——死等 scanf、死锁、或者调了 sleep。
              它对**运维**比对用户更有用：整道题的 wait 占比都高，
              说明题面的输入说明没写清，或者用例格式和题面不一致。
        wall  其余：算了一部分，也等了一部分。见 TLE_KINDS 里的说法。

    以前是拿 CPU 时间和**CPU 配额**比，配额的推导本身又依赖墙钟，结果
    第一档在默认配置下永远不成立，一个纯死循环落进了「算等参半」档。
    现在比的是 wall_ms——它不是配置推出来的，是实测的。

    返回空串的条件：没有超时，或这次执行在降级路径上（降级拿不到 CPU
    时间，cpu_ms 恒为 0，硬归因会把每一次超时都判成「在等」）。
    """
    if not res.get("timed_out"):
        return ""
    # 降级路径没有可信的 CPU 时间。这里必须返回空串而不是 "wait"：
    # 少一个标签只会让面板上有一格「未归因」，而错一个标签会让
    # 学生照着「你在等」去改一个其实算得很慢的程序。
    if not res.get("sandboxed"):
        return ""

    note = res.get("note") or ""
    if "CPU 时间超限" in note:
        return "cpu"

    cpu = int(res.get("cpu_ms") or 0)
    wall = int(res.get("wall_ms") or 0)

    # 墙钟记到 0 说明报告里这一项不可用（runner 半路死了、报告被截断）。
    # 这时候宁可返回「未归因」：拿 0 当分母会把每一档都判成同一档，
    # 而错标签比没标签更糟——学生会照着错的那句去改程序。
    if wall <= 0:
        return ""

    ratio = cpu / float(wall)
    if ratio >= _CPU_BOUND:
        return "cpu"
    if ratio <= _IDLE_RATIO and cpu <= _IDLE_CPU_MS:
        return "wait"
    return "wall"


# --------------------------------------------------------------- 文本处理

def _decode(raw):
    """把子进程吐的字节解成 str。

    UTF-8 优先——gcc 编出来的程序把源码里的中文字面量原样写进 stdout，
    而源码是 UTF-8，所以绝大多数情况一次就中。

    解不了再试 GBK：Windows 上如果用户代码走了 CRT 的宽字符输出
    （wprintf）或者调了 SetConsoleOutputCP，落盘的就是本地码页。
    这一层不追求「永远正确」，只求「别把一段能看懂的中文变成问号」。

    最后才 errors="replace"——宁可有个别替换字符，也不能因为一个字节
    解码失败就把整段输出丢掉：判题时那段输出是用户唯一的线索。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_capped(path, cap):
    """读一个文件，最多 cap 字节。

    多读 1 字节再切，是为了让调用方能判断「到底有没有被截断」；
    这里不用那个信息，但保持这个习惯——以后要显示「已截断」时不用改这里。
    """
    try:
        with open(path, "rb") as f:
            return f.read(cap + 1)[:cap]
    except OSError:
        return b""


def _parse_report(path):
    """解析 runner 写的 key=value 报告。文件不存在返回 None。

    不用 JSON 是 runner 那边的决定（见 runner_win.c 里 write_report 的注释）：
    写这个文件时进程可能刚被 TerminateJobObject 打过，路径上不该引入
    任何可能失败的库调用。这边顺着它的格式来，split 一下就够了。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(8192)
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v
    return out


def _num(raw, key, default=0, cast=int):
    try:
        v = raw.get(key)
        return cast(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _base_result():
    """所有路径（沙箱 / 降级 / 失败）返回同一个形状。

    形状统一是这一层最大的价值：judge.py 只写一份「读结果」的代码，
    平台差异和降级差异到这里为止。少一个键，judge 里就会多一个
    `.get(x, 0)`，那种默认值在真出问题时会把故障当成 0。
    """
    return {
        "stdout": "", "stderr": "", "exit_code": None,
        "timed_out": False, "output_limited": False,
        # 超时归因：cpu / wall / wait，空串表示没超时或无法归因。
        # 见 attribute_timeout()。它**不是**裁决码，TLE 仍然只有一个。
        "tle_kind": "",
        "wall_ms": 0, "cpu_ms": 0, "peak_mem": 0,
        "total_procs": 0, "active_procs": 0,
        "sandboxed": False, "backend": "subprocess",
        "note": "", "error": "", "limits": {},
    }


# --------------------------------------------------------------- 沙箱执行

def _run_sandbox(exe, stdin_data, workdir, lim, argv, t0):
    """真正走 runner 的那条路。返回结果 dict；返回 None 表示没跑起来，调用方降级。"""
    avail = available()
    runner = avail["runner"]

    # 收件箱单独放一个目录，**不放在 workdir 里**。
    # workdir 是用户程序的工作目录，它对这个目录有写权限：报告文件如果放在那儿，
    # 恶意代码可以在 runner 写之前抢先建一个同名文件（Windows 上还能用
    # FILE_SHARE_NONE 占住），让 runner 的写入失败或者让读到的是伪造内容。
    # 判题报告是裁决依据，伪造它是唯一真正能骗过判题的攻击面。
    scratch = tempfile.mkdtemp(prefix="cmsb_")
    in_path = os.path.join(scratch, "in.txt")
    out_path = os.path.join(scratch, "out.txt")
    err_path = os.path.join(scratch, "err.txt")
    rep_path = os.path.join(scratch, "report.txt")

    try:
        with open(in_path, "wb") as f:
            f.write((stdin_data or "").encode("utf-8"))

        cmd = [
            runner,
            "--limit-ms=%d" % lim["wall_ms"],
            "--limit-mem=%d" % lim["mem"],
            "--limit-proc=%d" % lim["proc"],
            "--limit-cpu-ms=%d" % lim["cpu_ms"],
            "--limit-out=%d" % lim["out"],
            "--limit-err=%d" % lim["err"],
            "--in=" + in_path,
            "--out=" + out_path,
            "--err=" + err_path,
            "--report=" + rep_path,
            "--cwd=" + (workdir or os.path.dirname(exe) or "."),
        ]
        if os.name != "nt":
            # Linux 侧多两个开关：--as= 指定降权目标（空表示不降权），
            # --no-seccomp 只给排障用。默认就是「降权 + 装过滤器」，
            # 这里显式传 'nobody' 而不是让 runner 自己去查 NSS。
            cmd.append("--as=nobody")
        cmd.append("--")
        cmd.append(exe)
        cmd.extend(argv or [])

        # 外层看门狗。runner 自己带 --limit-ms，正常情况它会先到点。但如果
        # runner 自己挂了（比如 job 建到一半崩了），判题线程就会永远等下去——
        # 而它手上握着 _judge_sem，一个僵尸调用能把整个判题队列卡死。
        # 所以这一层必须留：宁可让一次判题报错，不能让它拖住队列。
        outer = lim["wall_ms"] / 1000.0 + 15
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=outer)
        except subprocess.TimeoutExpired:
            res = _base_result()
            res.update(sandboxed=True, backend=avail["backend"], limits=lim,
                       wall_ms=int((time.time() - t0) * 1000),
                       exit_code=-1, timed_out=True,
                       note="沙箱宿主本身超时未返回（外层 %.0f 秒看门狗），已放弃本次判题" % outer)
            _bump("runs")
            _bump("sandboxed")
            _bump("timeouts")
            return res
        except OSError as e:
            # 二进制从探测到现在被删了 / 权限变了。这不是「沙箱坏了」，是环境变了，
            # 退回降级路径（调用方会重新探测）。
            _build.update(done=False)
            _probe.update(done=False)
            return None

        rep = _parse_report(rep_path)
        out = _decode(_read_capped(out_path, lim["out"] + 8192))
        err = _decode(_read_capped(err_path, lim["err"] + 8192))

        res = _base_result()
        res["limits"] = lim
        res["sandboxed"] = True
        res["backend"] = avail["backend"]
        res["stdout"] = out
        res["stderr"] = err

        if rep is None:
            # 报告没写出来。runner 的所有失败路径都会写 report（包括
            # report_fail），所以「没报告」意味着它连 die() 都没走到：
            # 静态链接问题、被外部杀掉、磁盘满……不管哪种，我们拿不到
            # 「有没有超时」这个关键信息，不能猜。
            res["exit_code"] = r.returncode
            res["error"] = "sandbox_init_failed"
            res["note"] = ("沙箱宿主没有写出报告（退出码 %s）：%s"
                           % (r.returncode, _decode(r.stderr)[:200].strip() or "无输出"))
            _bump("runs")
            _bump("init_failed")
            return res

        res["exit_code"] = _num(rep, "exit_code", -1)
        res["timed_out"] = bool(_num(rep, "timed_out", 0))
        res["output_limited"] = bool(_num(rep, "output_limited", 0))
        res["wall_ms"] = _num(rep, "wall_ms", 0, float)
        res["cpu_ms"] = _num(rep, "cpu_ms", 0)
        res["peak_mem"] = _num(rep, "peak_job_mem", 0)
        res["total_procs"] = _num(rep, "total_procs", 0)
        res["active_procs"] = _num(rep, "active_procs", 0)
        res["note"] = rep.get("note", "")

        _bump("runs")
        _bump("sandboxed")
        if res["timed_out"]:
            _bump("timeouts")
            # 归因只在这一条路径上做：只有这里拿到了 runner 写的报告，
            # 才有可信的 cpu_ms / wall_ms。
            # 上面那个「宿主本身超时未返回」的分支故意不归因——宿主都没
            # 返回，我们手上没有任何计时数据，那时候说「你在等」是编的。
            res["tle_kind"] = attribute_timeout(res, lim)
        if res["output_limited"]:
            _bump("output_limited")

        # 退出码 126 是 runner 约定的「沙箱初始化失败」（见 runner_win.c 顶部）。
        # 注意：**不能**把它当成用户程序的运行时错误——用户程序根本没跑。
        # 这种时候必须拒绝执行，理由见模块顶部第 2 条。
        if r.returncode == 126 or res["note"].startswith("SANDBOX_SETUP_FAILED"):
            res["error"] = "sandbox_init_failed"
            res["sandboxed"] = False
            _bump("init_failed")
        return res
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_fallback(exe, stdin_data, workdir, lim, argv, t0, reason):
    """没有沙箱时的降级路径：就是原来那套 subprocess.run(timeout=...)。

    它做不到什么，写在这里免得有人以为降级是「差不多」：
      · 杀父不杀子（用户代码 fork 出的孙进程会留在机器上）
      · 没有内存上限（malloc 4GB 能把机器换页换死）
      · system() 直接放行（等于把 shell 交出去）
      · 只能限制输出大小，不能阻止它先写满盘

    所以降级的每一次调用都会在 /admin 上计一笔，并且结果里 sandboxed=False。
    这是「本机调试用的后门」，不是「生产可接受的方案」。
    """
    res = _base_result()
    res.update(limits=lim, sandboxed=False, backend="subprocess",
               note=reason or "沙箱不可用，已退化为普通 subprocess（无资源隔离）")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": tempfile.gettempdir(),
        "TMP": tempfile.gettempdir(),
        "PYTHONIOENCODING": "utf-8",
    }
    if os.name != "nt":
        env["HOME"] = tempfile.gettempdir()
    env = {k: v for k, v in env.items() if v}

    try:
        p = subprocess.run([exe] + list(argv or []),
                           input=(stdin_data or "").encode("utf-8"),
                           capture_output=True,
                           timeout=lim["wall_ms"] / 1000.0,
                           cwd=workdir, env=env)
        res["stdout"] = _decode(p.stdout[:lim["out"]])
        res["stderr"] = _decode(p.stderr[:lim["err"]])
        res["exit_code"] = p.returncode
        res["wall_ms"] = int((time.time() - t0) * 1000)
        _bump("runs")
        _bump("fallback")
        return res
    except subprocess.TimeoutExpired as e:
        res["stdout"] = _decode((e.stdout or b"")[:lim["out"]])
        res["stderr"] = "超过 %.1f 秒未结束，已强制终止。" % (lim["wall_ms"] / 1000.0)
        res["exit_code"] = -1
        res["timed_out"] = True
        res["wall_ms"] = int((time.time() - t0) * 1000)
        _bump("runs")
        _bump("fallback")
        _bump("timeouts")
        return res
    except Exception as e:  # noqa: BLE001
        res["stderr"] = "执行失败：%s" % e
        res["exit_code"] = -2
        res["error"] = "exec_failed"
        res["wall_ms"] = int((time.time() - t0) * 1000)
        _bump("runs")
        _bump("fallback")
        return res


def run(exe, stdin_data="", time_limit=None, workdir=None, argv=None):
    """在沙箱里跑一个可执行文件。这是本模块唯一的执行入口。

    返回的 dict 形状见 _base_result()，几个字段的语义值得单独说明：

      exit_code       用户程序的退出码。Linux 上被信号杀掉是 256+信号号
                      （和 shell 的约定一致），所以**不要**用 exit_code != 0
                      之外的方式判断正常与否。
      timed_out       只看这个字段判断超时，**不要**用 exit_code == 124。
                      runner 自己的 124 会和用户程序 `return 124` 撞车，
                      早年按退出码判断，把一道参考答案正好 return 124 的题
                      误判成了 TLE。
      peak_mem        内存峰值（字节）。Windows 取自 job 的 PeakJobMemoryUsed，
                      Linux 取自 ru_maxrss（KB，已乘 1024）。
      sandboxed       这一次**真的**在沙箱里跑的。False 意味着上面那些
                      内存/进程数数字都不存在，别拿去展示。
      error           非空表示这次执行在沙箱层面失败了（"sandbox_init_failed"
                      或 "exec_failed"），调用方应当按系统错误处理，
                      而不是当成用户代码的错。
    """
    lim = limits(time_limit)
    t0 = time.time()
    avail = available()

    if avail["ok"]:
        res = _run_sandbox(exe, stdin_data, workdir, lim, argv, t0)
        if res is not None:
            return res
        # 落到这里说明二进制在探测之后消失了，重新走一遍降级，
        # 下次 available() 会重新探测。

    return _run_fallback(exe, stdin_data, workdir, lim, argv, t0,
                         avail.get("reason") or "沙箱不可用")


# --------------------------------------------------------------- 自检
#
# 这一节是 runner_linux.c 文件头那段「诚实声明」的另一半：代码在那个平台上
# 能不能站得住，由这四项在**目标机器上**回答，而不是由注释辩解。
# 四项各覆盖一类越界，而且都是真编译、真跑、真读报告——不是看返回值猜。
_SELFTEST_CASES = [
    {
        "key": "baseline",
        "name": "对照组：正常程序",
        "desc": "跑一个只打印 ok 的程序。它必须通过——如果对照组都不过，"
                "下面四项的结论全是假的（说明管道或报告本身出了问题）。",
        "src": '#include <stdio.h>\nint main(void){printf("ok\\n");return 0;}\n',
    },
    {
        "key": "loop",
        "name": "死循环",
        "desc": "while(1){} 烧 CPU。期望：到点被强杀，机器不被拖死。",
        "src": "int main(void){for(;;){}return 0;}\n",
    },
    {
        "key": "bomb",
        "name": "内存炸弹",
        "desc": "连续 malloc 64MB 并写满，共 200 次（12.8GB）。期望：进程在"
                "配额附近 malloc 失败自己退出，整机内存不被吃掉。",
        "src": ('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
                'int main(void){for(int i=0;i<200;i++){char*p=malloc(64u<<20);'
                'if(!p){printf("malloc 失败于第 %d 轮\\n",i);return 0;}'
                'memset(p,1,64u<<20);}printf("全部成功——限制没生效\\n");return 0;}\n'),
    },
    {
        "key": "shell",
        "name": "命令逃逸",
        "desc": "调 system() 执行 echo 写文件。期望：调用失败、文件不存在。"
                "Windows 靠 job 的 ActiveProcessLimit=1，Linux 靠 seccomp 拦 execve。",
        "src": ('#include <stdio.h>\n#include <stdlib.h>\n'
                'int main(void){int r=system("echo escaped > escaped.txt");'
                'printf("system() 返回 %d\\n",r);return 0;}\n'),
    },
    {
        "key": "spam",
        "name": "输出炸弹",
        "desc": "死循环 printf。期望：到 64KB 就被砍掉，不会把磁盘或内存写满。",
        "src": ('#include <stdio.h>\nint main(void){for(;;)'
                'printf("0123456789abcdef");return 0;}\n'),
    },
]

_selftest = {"done": False, "running": False, "result": None, "at": 0}
_selftest_lock = threading.Lock()


def _check(key, res, lim, workdir):
    """按每一项的判据给出 (ok, detail)。

    判据要写死成「可以证伪」的形式。比如内存炸弹不能只看「程序退出码 0」——
    配额没生效、把整机内存吃光之后 OOM killer 把它杀了，退出码也是非零，
    但那说明沙箱失败了。所以看的是**峰值内存本身**有没有超过配额。
    """
    if res.get("error"):
        return False, "沙箱层面失败：%s" % (res.get("note") or res["error"])
    if not res.get("sandboxed"):
        return False, "没有在沙箱里跑（%s）" % (res.get("note") or "已降级")

    mem_mb = res["peak_mem"] / (1024.0 * 1024.0)
    cpu = res["cpu_ms"]

    if key == "baseline":
        ok = (not res["timed_out"]) and res["exit_code"] == 0 \
            and res["stdout"].strip() == "ok"
        return ok, ("stdout=%r 退出码=%s 耗时=%dms 峰值内存=%.1fMB"
                    % (res["stdout"].strip()[:20], res["exit_code"],
                       res["wall_ms"], mem_mb))

    if key == "loop":
        ok = res["timed_out"] and res["wall_ms"] <= lim["wall_ms"] + 2500
        return ok, ("timed_out=%s 墙钟=%dms CPU=%dms（上限 %dms）"
                    % (res["timed_out"], res["wall_ms"], cpu, lim["wall_ms"]))

    if key == "bomb":
        # 两道判据缺一不可：峰值不能超配额太多，而且程序自己得走到
        # 「malloc 失败」那条分支——如果它打印了「全部成功」，说明配额没生效。
        under = res["peak_mem"] <= lim["mem"] * 1.25
        failed_gracefully = "全部成功" not in res["stdout"]
        ok = under and failed_gracefully and not res["timed_out"]
        return ok, ("峰值内存=%.1fMB（配额 %.0fMB）程序自述=%r"
                    % (mem_mb, lim["mem"] / 1048576.0,
                       (res["stdout"].strip().splitlines() or [""])[-1][:40]))

    if key == "shell":
        esc = os.path.join(workdir, "escaped.txt")
        leaked = os.path.isfile(esc)
        if leaked:
            try:
                os.remove(esc)
            except OSError:
                pass
        # system() 返回 0 就说明命令真执行了。两者任一成立都算失败。
        ok = (not leaked) and ("返回 0" not in res["stdout"])
        return ok, ("逃逸文件=%s 程序自述=%r 进程数=%d"
                    % ("存在（失败）" if leaked else "不存在",
                       res["stdout"].strip()[:40], res["total_procs"]))

    if key == "spam":
        ok = res["output_limited"] and res["wall_ms"] <= lim["wall_ms"] + 2500
        return ok, ("output_limited=%s 实际写出=%dB（上限 %dB）"
                    % (res["output_limited"], res["out_bytes"], lim["out"]))

    return False, "未知用例"


def selftest(force=False):
    """真编译真跑，验证沙箱是否真的挡住了四种越界。

    这个函数是给 /admin 上的一个按钮用的，也在启动时后台跑一次。
    它要编译 5 个程序（1 个对照组 + 4 个越界），大概 3-6 秒，
    所以绝不能放在请求路径里同步调用。

    返回：
      {"ok": 全绿?, "backend": ..., "sandboxed_count": n, "items": [...],
       "at": 时间戳, "took_ms": ..., "note": 顶层说明}
    """
    with _selftest_lock:
        if _selftest["running"]:
            return _selftest["result"] or {"ok": False, "items": [],
                                           "note": "自检正在运行，稍等"}
        _selftest["running"] = True
    t0 = time.time()
    items = []
    try:
        avail = available()
        lim = limits()
        # 延迟 import：见 _gcc() 的注释
        try:
            import judge as _j  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "items": [], "backend": avail["backend"],
                    "note": "无法加载判题模块，自检跳过：%s" % e}

        if not _j.find_compiler():
            return {"ok": False, "items": [], "backend": avail["backend"],
                    "at": int(time.time()),
                    "note": "没有编译器，自检无法进行（连题目都编不了）"}

        workdir = tempfile.mkdtemp(prefix="cmsel_")
        try:
            for case in _SELFTEST_CASES:
                t1 = time.time()
                ok_c, exe, msg, wd = _j.compile_code(case["src"], workdir=workdir)
                if not ok_c:
                    items.append({
                        "key": case["key"], "name": case["name"], "desc": case["desc"],
                        "status": "fail", "detail": "编译失败：%s" % (msg or "")[:200],
                        "ms": int((time.time() - t1) * 1000),
                    })
                    continue
                res = run(exe, "", lim["wall_ms"] / 1000.0, workdir=wd)
                res["out_bytes"] = len((res.get("stdout") or "").encode("utf-8"))
                ok_i, detail = _check(case["key"], res, lim, wd)
                items.append({
                    "key": case["key"], "name": case["name"], "desc": case["desc"],
                    "status": "pass" if ok_i else "fail", "detail": detail,
                    "ms": int((time.time() - t1) * 1000),
                })
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        good = [i for i in items if i["status"] == "pass"]
        result = {
            "ok": len(items) == len(_SELFTEST_CASES) and len(good) == len(items),
            "backend": avail["backend"],
            "sandboxed": avail["ok"],
            "items": items,
            "at": int(time.time()),
            "took_ms": int((time.time() - t0) * 1000),
            "note": ("" if avail["ok"] else
                     "当前是降级模式（%s）：下面四项的结论只反映 subprocess 的能力，"
                     "不代表沙箱生效。" % (avail.get("reason") or "未知原因")),
        }
    finally:
        with _selftest_lock:
            _selftest.update(done=True, running=False, result=result,
                             at=int(time.time()))
    return result


def last_selftest():
    """上一次自检的结果（没跑过返回 None）。/admin 首屏用它，避免每次都重跑。"""
    return _selftest["result"] if _selftest["done"] else None


def status():
    """/admin 要的一整块：可用性 + 配额 + 计数器 + 上一次自检。"""
    avail = available()
    lim = limits()
    return {
        "available": avail,
        "limits": lim,
        "stats": stats(),
        "selftest": last_selftest(),
        "selftest_running": _selftest["running"],
        "platform": os.name,
    }


if __name__ == "__main__":
    # 手工自检：python sandbox.py
    print("可用性:", available())
    print("配额  :", limits())
    r = selftest()
    print("自检  :", "全部通过" if r["ok"] else "有失败项", "（%dms）" % r["took_ms"])
    for it in r["items"]:
        print("  [%s] %-8s %s" % ("PASS" if it["status"] == "pass" else "FAIL",
                                  it["key"], it["detail"]))
    print("计数器:", stats())
