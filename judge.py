# -*- coding: utf-8 -*-
"""
C-Master 判题引擎

职责：编译用户 C 代码 -> 逐个测试用例执行 -> 比对输出 -> 给出裁决。
裁决码：AC 通过 / WA 答案错 / TLE 超时 / RE 运行错误 / CE 编译错误 / SE 系统错误

安全边界（这一节以前写的是「没有系统调用级沙箱」，现在改了，改了什么写在下面）：

  **编译阶段**：干净环境变量 + 独立工作目录 + 20 秒超时。编译 `#include` 里
  藏的东西也拦不住（预处理器能把任意文件读进报错信息），所以编译的输出上限
  和超时都要有；真正的隔离留给执行阶段。

  **执行阶段**：交给 sandbox.py，背后是 sandbox/runner_win.c（Windows，
  Job Object）或 sandbox/runner_linux.c（Linux，setrlimit + seccomp-bpf）。
  具体做了什么：

    Windows（Job Object，内核强制配额，进程自己不配合也绕不过）
      · 内存上限 256MB（单进程 + job 合计两个都设）
      · 进程数上限 1 —— 这就是「禁 shellout」：用户代码一 CreateProcess，
        新进程是第 2 个，内核直接拒绝，system() 返回 -1
      · CPU 时间上限、墙钟上限、输出上限（边读边计数，超了立刻杀，不落盘）
      · KILL_ON_JOB_CLOSE：runner 自己崩了，内核也会把 job 里所有进程清掉，
        不留孤儿
      · 禁弹窗 / 禁读剪贴板 / 禁关机

    Linux（五层，正统的容器隔离缩小版）
      · setpgid + PR_SET_PDEATHSIG（父死子必死）
      · setrlimit：CPU / 地址空间 / 文件大小 / 进程数 / fd 数 / core=0 / 栈
      · 降权到 uid=65534（仅当以 root 运行时）
      · PR_SET_NO_NEW_PRIVS（必须在装 seccomp 之前，否则过滤器静默失效）
      · seccomp-bpf 黑名单：execve/fork/socket/ptrace/mount/setuid… 命中返回
        EPERM，用户程序表现为「调用失败」而不是「被信号打死」

  **降级**：沙箱跑不起来时（本机没编译器编不出 runner、平台不支持）会退回
  普通 subprocess，结果里 sandboxed=False 且 /admin 上计数。但**不会**在
  「沙箱本该可用却初始化失败」时降级——那种情况直接判 SE 拒绝执行，
  因为「沙箱坏了就裸跑用户代码」比一开始没有沙箱更危险，它把降级藏起来了。

  一句话概括：以前是「我知道它不安全」，现在是「我在能隔离的地方都隔离了，
  并且有一条命令能证明它真的在隔离」（python sandbox.py / /admin 自检四项）。
"""

import os
import shutil
import subprocess
import tempfile
import time

import sandbox
import verdicts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 单题允许的最大执行时间（秒），超时即 TLE
TIME_LIMIT = 3.0
# 编译超时（秒）
COMPILE_TIMEOUT = 20
# 单次输出上限（字节），防止 printf 死循环把内存吃光
OUTPUT_LIMIT = 64 * 1024


def _candidates():
    """按优先级列出可能的编译器路径。"""
    exe = "gcc.exe" if os.name == "nt" else "gcc"
    yield os.path.join(BASE_DIR, "runtime", "w64devkit", "bin", exe)
    yield os.path.join(BASE_DIR, "runtime", "mingw64", "bin", exe)
    # 系统 PATH 里的
    yield shutil.which("gcc")
    yield shutil.which("cc")
    # 常见安装位置
    if os.name == "nt":
        for p in (
            r"C:\msys64\mingw64\bin\gcc.exe",
            r"C:\mingw64\bin\gcc.exe",
            r"C:\MinGW\bin\gcc.exe",
            r"C:\Program Files\CodeBlocks\MinGW\bin\gcc.exe",
        ):
            yield p


_compiler_cache = {"path": None, "checked": False}


def find_compiler():
    """返回可用的 gcc 路径；没有则返回 None。结果缓存。"""
    if _compiler_cache["checked"]:
        return _compiler_cache["path"]
    found = None
    for c in _candidates():
        if c and os.path.isfile(c):
            found = c
            break
    _compiler_cache["path"] = found
    _compiler_cache["checked"] = True
    return found


def compiler_info():
    """给前端用的编译器状态信息。"""
    path = find_compiler()
    if not path:
        return {"ok": False, "path": None, "version": None,
                "hint": "未找到 C 编译器。请把 w64devkit 解压到 runtime/w64devkit/，或安装 MinGW 后加入 PATH。"}
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
        version = (r.stdout or "").splitlines()[0].strip()
    except Exception as e:  # noqa: BLE001
        version = "gcc (版本读取失败: %s)" % e
    return {"ok": True, "path": path, "version": version, "hint": ""}


# 退出码翻译表。判题器只说「返回码 3221225477」对学生毫无意义，
# 但 0xC0000005（访问违例）和 0xC0000094（整数除零）是两种完全不同
# 的 bug：前者是野指针/数组越界，后者是分母没防 0。把话说清楚，
# 用户就不用去搜索引擎查那一串十六进制。
_WIN_EXC = {
    0xC0000005: "访问违例（野指针、数组越界或段错误）",
    0xC00000FD: "栈溢出（递归太深，或函数里开了超大局部数组）",
    0xC0000094: "整数除零（分母忘记判断 0）",
    # 除零在 -O2 下常常不是 0xC0000094：gcc 把「除数可能为 0」的除法编成一个
    # 条件 + ud2（非法指令），跑起来就是 0xC000001D。所以这一条也要认，
    # 否则学生看到「非法指令」会以为是编译器坏了。
    0xC000001D: "非法指令（最常见的原因是整数除零，gcc 用 ud2 实现除零陷阱）",
    0xC0000409: "栈缓冲区溢出（往缓冲区里写超了）",
    0xC0000374: "堆损坏（越界写了 malloc 出来的内存）",
    0xC000013A: "被 Ctrl+C 结束",
    0xC0000139: "找不到入口点（链接出了问题）",
}

# 信号号 → 人话。Linux 侧 runner 把「被信号杀死」编码成 256+信号号，
# 和 shell 的约定一致（$? 也是这个数），所以这里能直接还原出来。
_SIG_TEXT = {
    4: "SIGILL（非法指令）",
    6: "SIGABRT（abort，通常是 assert 失败或堆检测报错）",
    8: "SIGFPE（算术错误，常见是除零）",
    11: "SIGSEGV（段错误：野指针或越界访问）",
    24: "SIGXCPU（CPU 时间超限）",
    25: "SIGXFSZ（写出的文件超限）",
}


def _exit_hint(rc):
    """把退出码翻译成一句人话。认不出来就返回空串，不要瞎猜。"""
    if rc is None:
        return ""
    # -1 / -2 是 sandbox.py 自己用的「超时 / 执行失败」哨兵值，不是真退出码，
    # 它们的原因写在 note 里，这里不重复。
    if rc in (-1, -2):
        return ""
    # Windows 的异常码在 int 里是负数：0xC0000005 存成 int 就是 -1073741819，
    # runner 用 %d 写报告，所以这边先还原成无符号再查表。
    if rc < 0:
        rc &= 0xFFFFFFFF
    # 先查 Windows 异常码，再查信号。顺序不能反：异常码是 0xC0000005 这种
    # 三十多亿的数，它同样满足「>= 256」，先按信号解释就会得出
    # 「信号 3221225221」——一个不存在的信号号，比不翻译还糟。
    # （这个坑我踩过：第一版就是这么写的。）
    if rc >= 0xC0000000:
        reason = _WIN_EXC.get(rc)
        if reason:
            return "程序异常终止：%s（0x%08X）。" % (reason, rc)
        return "程序异常终止（0x%08X）。" % rc
    if rc >= 256:
        sig = rc - 256
        return "程序被信号终止：%s。" % _SIG_TEXT.get(sig, "信号 %d" % sig)
    return ""


def _clean_paths(text, workdir):
    """把消息里的临时目录前缀去掉，只留 main.c。

    两个理由：一是学生的报错里出现
    `C:\\Users\\32546\\AppData\\Local\\Temp\\cmaster_cy7h4qd2\\main.c:2` 这种路径
    等于把服务器的目录结构连同用户名一起送出去；二是它太长，把真正有信息量的
    那半句（`error: expected ';'`）挤到一行外面去了。
    """
    if not text:
        return text or ""
    if not workdir:
        return text
    for p in (workdir + os.sep, workdir + "/", workdir):
        text = text.replace(p, "")
    return text


def _normalize(text):
    """输出比对前的归一化：统一换行、去掉每行行尾空白、去掉首尾空行。

    竞赛判题里这件事必须做——用户输出末尾多一个换行不该判错。
    """
    if text is None:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _clean_env(extra_path=None):
    """给子进程一个干净、最小化的环境，避免用户代码摸到宿主的密钥。

    extra_path：编译器所在目录。gcc 要调用同目录的 cc1 / as / ld，
    不把该目录塞进 PATH 会报 "cannot execute cc1"。
    """
    path = os.environ.get("PATH", "")
    if extra_path:
        path = extra_path + os.pathsep + path
    env = {
        "PATH": path,
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": tempfile.gettempdir(),
        "TMP": tempfile.gettempdir(),
        "PYTHONIOENCODING": "utf-8",
    }
    if os.name != "nt":
        env["HOME"] = tempfile.gettempdir()
    return {k: v for k, v in env.items() if v}


def compile_code(code, workdir=None):
    """编译一段 C 代码。

    返回 (ok, exe_path, message, workdir)
    workdir 由调用方决定是否清理——判题时需要保留到执行结束。
    """
    gcc = find_compiler()
    if not gcc:
        return False, None, "未找到 C 编译器，无法编译。请在 runtime/w64devkit/ 放置编译器。", workdir

    workdir = workdir or tempfile.mkdtemp(prefix="cmaster_")
    src = os.path.join(workdir, "main.c")
    exe = os.path.join(workdir, "main.exe" if os.name == "nt" else "main")

    with open(src, "w", encoding="utf-8") as f:
        f.write(code)

    cmd = [gcc, "-std=c11", "-O2", "-Wall", "-Wextra", "-fno-asm",
           "-o", exe, src, "-lm"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=COMPILE_TIMEOUT,
                           env=_clean_env(os.path.dirname(gcc)), cwd=workdir,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, None, "编译超时（代码过大或编译器异常）。", workdir
    except Exception as e:  # noqa: BLE001
        return False, None, "启动编译器失败：%s" % e, workdir

    # 编译失败时把 gcc 原始诊断给用户，这是最有价值的信息；但路径前缀要去掉
    # （见 _clean_paths 的注释），否则报错的第一行全是我们自己的临时目录。
    msg = _clean_paths((r.stderr or "").strip(), workdir)
    if r.returncode != 0 or not os.path.isfile(exe):
        return False, None, msg or "编译失败（无诊断信息）。", workdir
    return True, exe, msg, workdir


def run_once_ex(exe, stdin_data="", time_limit=TIME_LIMIT, workdir=None):
    """执行一次可执行文件，返回沙箱的完整结果 dict。

    为什么不自己拼 subprocess 参数了：执行这件事已经不该由判题器管。
    judge.py 的职责是「编译 -> 执行 -> 比对 -> 裁决」，执行只是其中一步；
    把隔离策略写进判题器里，就等于判题器永远只能在同一个安全级别上跑。

    返回的 dict 里，除了 stdout/stderr/exit_code 这些老字段，还多了几个
    只有在沙箱里才有意义的：cpu_ms（真实 CPU 时间，不含等待）、peak_mem
    （内存峰值）、timed_out / output_limited（两个不同的「被砍掉」）、
    sandboxed（这一次到底有没有隔离）、note（降级或异常原因）。
    """
    return sandbox.run(exe, stdin_data or "", time_limit, workdir=workdir)


def run_once(exe, stdin_data="", time_limit=TIME_LIMIT, workdir=None):
    """老接口，返回 (stdout, stderr, rc, elapsed, timed_out)。

    留着只是为了不把调用点一起改。**新代码请用 run_once_ex()**：
    这个包装丢掉的 cpu_ms / peak_mem / sandboxed 恰恰是「沙箱有没有生效」
    的唯一证据，只拿五元组的话，降级和不降级在调用方看来长得一模一样。
    """
    r = run_once_ex(exe, stdin_data, time_limit, workdir)
    rc = r["exit_code"]
    if rc is None:
        rc = -2
    if r.get("error") and rc == -1:
        rc = -2
    return r["stdout"], r["stderr"], rc, (r.get("wall_ms") or 0) / 1000.0, bool(r["timed_out"])


def _stamp(res):
    """给结果补上裁决码的语义字段。

    字段定义只在 verdicts.py 里，这里不重新映射——否则加一个状态要改两处，
    而漏改的那一处不会报错，只会让面板上出现一个没有名字的扇区。

    executed 用 cases 的长度兜底：正常路径自己会填，编译失败/沙箱失败
    那两条路径没有用例可跑，0 就是事实。
    """
    st = res.get("status") or "SE"
    res.setdefault("owner", verdicts.owner(st))
    res.setdefault("verdict_code", verdicts.code(st))
    res.setdefault("verdict_name", verdicts.name(st))
    res.setdefault("tle_kind", "")
    res.setdefault("executed", len(res.get("cases") or []))
    return res


def judge(question, code):
    """对一道题执行完整判题。

    question 需要包含 cases: [{"input": str, "output": str, "name": str?}]

    这是个薄包装：真正的逻辑在 _judge_impl 里，包装只做一件事——补上
    裁决码的语义字段。之所以不让 _judge_impl 的每个 return 各写一遍，
    是因为它有四条返回路径（无用例 / 编译失败 / 沙箱失败 / 正常裁决），
    而「少补一处」这种错不会报错，只会让某一类裁决在前端变成一个
    没有名字的状态。
    """
    return _stamp(_judge_impl(question, code))


# 这一行以上的东西都在 _judge_impl 之前定义，因为 Python 的函数体在调用时
# 才求值名字，所以 judge 可以引用后面定义的 _judge_impl。放在 judge 之后
# 定义是为了让文件的阅读顺序和调用顺序一致：读者先看到「对外是什么」，
# 再看到「里面怎么算」。
def _judge_impl(question, code):
    cases = question.get("cases") or []
    if not cases:
        return {"status": "SE", "passed": 0, "total": 0,
                "message": "这道题没有配置测试用例。", "cases": [],
                "compile_ms": 0, "judge_ms": 0}

    # 编译耗时单列出来给 /ops 用。它经常占整个判题时间的一半以上，
    # 但用户在页面上只看到一个总数——「慢」到底慢在编译还是慢在跑，
    # 是两件完全不同的事（前者该优化调用方式，后者该查题目算法）。
    t_begin = time.time()
    ok, exe, msg, workdir = compile_code(code)
    compile_ms = int((time.time() - t_begin) * 1000)
    if not ok:
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
        return {"status": "CE", "passed": 0, "total": len(cases),
                "message": msg, "cases": [], "compile_msg": msg,
                "compile_ms": compile_ms,
                "judge_ms": int((time.time() - t_begin) * 1000)}

    results = []
    passed = 0
    final = "AC"
    total_time = 0.0
    sandboxed = False
    cpu_ms_total = 0
    peak_mem = 0
    # 短路：RE / TLE 之后不再跑剩下的用例。见下面 break 处的注释。
    short_circuit = False
    tle_kind = ""

    try:
        for i, case in enumerate(cases, 1):
            expected = case.get("output", "")
            res = run_once_ex(exe, case.get("input", ""), TIME_LIMIT, workdir=workdir)

            # 沙箱本该可用却初始化失败：用户程序**根本没有跑**。
            # 这时候不能接着跑下一个用例，更不能退回 subprocess 硬跑——
            # 那等于「隔离坏了就裸奔」。直接判 SE 并说明原因，让人去修。
            # 判成 RE 是错的：用户代码可能完全正确，红字会指向错误的方向。
            if res.get("error") == "sandbox_init_failed":
                shutil.rmtree(workdir, ignore_errors=True)
                return {
                    "status": "SE", "passed": 0, "total": len(cases), "cases": [],
                    "message": "沙箱初始化失败，已拒绝执行（不会退化为无隔离运行）：%s"
                               % (res.get("note") or "未知原因"),
                    "compile_ms": compile_ms,
                    "judge_ms": int((time.time() - t_begin) * 1000),
                    "sandboxed": False,
                }

            elapsed = (res.get("wall_ms") or 0) / 1000.0
            total_time += elapsed
            sandboxed = bool(res.get("sandboxed"))
            cpu_ms_total += int(res.get("cpu_ms") or 0)
            peak_mem = max(peak_mem, int(res.get("peak_mem") or 0))

            timed_out = bool(res.get("timed_out"))
            rc = res.get("exit_code")
            out = res.get("stdout") or ""
            err = res.get("stderr") or ""
            kind = res.get("tle_kind") or ""

            if timed_out:
                status = "TLE"
                if not tle_kind:
                    tle_kind = kind
            elif rc not in (0, None):
                status = "RE"
            elif _normalize(out) == _normalize(expected):
                status = "AC"
                passed += 1
            else:
                status = "WA"

            # 输出超限单独说一句：它现在被归到 RE（程序没正常结束），
            # 但原因和「段错误」差得远，用户在 stderr 里看不到任何线索。
            if res.get("output_limited"):
                err = ("输出超过 %d KB 被强制截断（大概率是死循环里 printf）。\n"
                       % (OUTPUT_LIMIT // 1024)) + err

            # 判 TLE / RE 时把「为什么」写进去。stderr 常常是空的
            # （用户程序一句都没打印就死了），光一个 RE 红色徽章
            # 对学习者没有任何信息量。
            if status in ("RE", "TLE"):
                hint = _exit_hint(rc)
                if status == "TLE":
                    # 归因文案从 verdicts.TLE_KINDS 来，不在这里现写。
                    # 「算得慢 / 在等」这两种超时的改法完全相反：前者要换
                    # 算法，后者要查输入读取。统一说一句「运行超时」等于
                    # 把最有用的那条信息扔了。
                    kd = verdicts.tle_kind(kind)
                    hint = ("超时（%s）：%.0f 秒内没结束，CPU 时间 %d ms。\n%s\n"
                            % (kd["name"], TIME_LIMIT,
                               int(res.get("cpu_ms") or 0), kd["note"])) + hint
                if hint:
                    err = hint + ("\n" + err if err else "")

            results.append({
                "idx": i,
                "name": case.get("name") or ("用例 %d" % i),
                "status": status,
                "time_ms": int(elapsed * 1000),
                # 下面两个是「沙箱真的在算账」的证据：cpu_ms 不含等待时间，
                # peak_mem 是内核记的峰值而不是猜的。降级模式下它们是 0，
                # 前端看到 0 就知道这次没有隔离，别拿它当「程序很省内存」。
                "cpu_ms": int(res.get("cpu_ms") or 0),
                "peak_mem": int(res.get("peak_mem") or 0),
                # 只有 TLE 的用例才有意义，其余是空串。
                "tle_kind": kind,
                "input": case.get("input", ""),
                "expected": expected,
                "actual": out,
                "stderr": _clean_paths(err, workdir) if status in ("RE", "TLE") else "",
            })

            # 首个非 AC 就是最终裁决
            if status != "AC" and final == "AC":
                final = status

            # 首败即停：RE / TLE 之后不再跑剩下的用例。
            #
            # 这条是从 DMOJ 借的（它的 feedback 就是在第一个非 AC 处收敛）。
            # 原来这里写的是「继续跑完，方便用户一次看全」，那个理由对
            # **WA** 成立——「通过 3/5」告诉学生离对还差多远，而 WA 很快。
            # 对 RE / TLE 不成立：同一个 bug 会在每个用例上重复触发，
            # 用户看到的是一模一样的五行红字，代价是每个用例再烧一次时限。
            # 一道 5 用例的题、程序是死循环时，原来要 5×3.2 ≈ 16 秒才出
            # 结果，现在 3.2 秒——省下的不只是等待，还有那把判题信号量
            # 被无效占用的时间（并发 3 的时候，一个人提交死循环就能把
            # 队伍堵到别人以为站点挂了）。
            if status in ("RE", "TLE"):
                short_circuit = i < len(cases)
                break
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    msg = _verdict_text(final, passed, len(cases), len(results))
    return {
        "status": final,
        "passed": passed,
        "total": len(cases),
        "executed": len(results),
        "short_circuit": short_circuit,
        # 只有 TLE 有意义；其余裁决是空串。verdict_code / name / owner
        # 由外层的 _stamp 统一补，这里不写。
        "tle_kind": tle_kind,
        # time_ms 只含跑用例的时间（历史字段，前端进度显示在用），
        # judge_ms 是从开始编译到出裁决的墙钟时间。两个都留着：
        # 前者回答「程序快不快」，后者回答「用户等了多久」。
        "time_ms": int(total_time * 1000),
        # cpu_ms 是各用例真实 CPU 时间之和：它和 time_ms 的差额就是「等 IO /
        # 等调度」的部分。TLE 的题看这一对数字就能分清是「算法慢」还是「卡住」。
        "cpu_ms": cpu_ms_total,
        "peak_mem": peak_mem,
        "sandboxed": sandboxed,
        "compile_ms": compile_ms,
        "judge_ms": int((time.time() - t_begin) * 1000),
        "cases": results,
        "message": msg,
    }


def _verdict_text(status, passed, total, executed=None):
    """把裁决翻译成一句话。

    executed 是**实际跑过的**用例数，和 total 不一定相等（见 judge() 里的
    短路）。这两个数必须都说出来：只写「通过 0/5」而实际只跑了 1 个，
    读的人会以为程序在 5 个用例上都错了——那是把一句偷懒的话说成了
    一句错的话。
    """
    tail = ""
    if executed is not None and executed < total:
        tail = "（后 %d 个用例未执行：同一个错误会重复触发，不再空烧时限）" % (
            total - executed)
    return {
        "AC": "全部 %d 个用例通过。" % total,
        "WA": "通过 %d/%d，输出与预期不符。" % (passed, total) + tail,
        "TLE": "通过 %d/%d，有用例运行超时。" % (passed, total) + tail,
        "RE": "通过 %d/%d，程序异常退出（看 stderr）。" % (passed, total) + tail,
        "CE": "编译失败。",
        "SE": "系统错误。",
    }.get(status, "未知状态。")


def run_freeform(code, stdin_data=""):
    """游乐场模式：编译 + 运行一次，不比对答案。

    返回里带一个 status，取值 CE / TLE / RE / OK，让前端和判题结果
    共用同一套渲染逻辑（OK 而不是 AC——这里没有"正确"可言）。
    """
    t_begin = time.time()
    ok, exe, msg, workdir = compile_code(code)
    compile_ms = int((time.time() - t_begin) * 1000)
    if not ok:
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
        return _stamp({"ok": False, "status": "CE", "stage": "compile", "compile_msg": msg,
                       "message": "编译失败，看下面的编译器输出。",
                       "stdout": "", "stderr": "", "exit_code": None,
                       "time_ms": 0, "compile_ms": compile_ms,
                       "judge_ms": int((time.time() - t_begin) * 1000)})
    try:
        res = run_once_ex(exe, stdin_data, TIME_LIMIT, workdir=workdir)
        out = res.get("stdout") or ""
        err = res.get("stderr") or ""
        rc = res.get("exit_code")
        elapsed = (res.get("wall_ms") or 0) / 1000.0
        timed_out = bool(res.get("timed_out"))

        if res.get("error") == "sandbox_init_failed":
            # 同上：沙箱没装成就拒绝执行，别把「我们自己的问题」显示成
            # 用户代码的运行时错误。
            status, message = "SE", "沙箱初始化失败，已拒绝执行：%s" % (res.get("note") or "未知原因")
            rc = None
        elif timed_out:
            # 游乐场里更需要这句归因：这里没有测试用例，学生只能靠自己
            # 判断「为什么 3 秒还没出来」。说「超时」等于什么都没说。
            kd = verdicts.tle_kind(res.get("tle_kind") or "")
            status = "TLE"
            message = ("超时（%s）：%.0f 秒内没结束。%s"
                       % (kd["name"], TIME_LIMIT, kd["note"]))
        elif rc != 0:
            status, message = "RE", "程序异常退出，返回码 %s。" % rc
        else:
            status, message = "OK", ""
        if res.get("output_limited") and status in ("OK", "RE"):
            status = "RE"
            message = "输出超过 %d KB 被强制截断，程序没跑完。" % (OUTPUT_LIMIT // 1024)
        return _stamp({
            "ok": status == "OK",
            "status": status,
            "message": message,
            "stage": "run",
            "compile_msg": msg,
            "stdout": out,
            "stderr": err,
            "exit_code": rc,
            "time_ms": int(elapsed * 1000),
            # 这两个字段是给「为什么慢/为什么挂」用的：cpu_ms 是内核记的
            # 真实 CPU 时间（不含等待），peak_mem 是内存峰值。
            "cpu_ms": int(res.get("cpu_ms") or 0),
            "peak_mem": int(res.get("peak_mem") or 0),
            "sandboxed": bool(res.get("sandboxed")),
            "sandbox_note": res.get("note") or "",
            "compile_ms": compile_ms,
            "judge_ms": int((time.time() - t_begin) * 1000),
            "timed_out": timed_out,
            "tle_kind": res.get("tle_kind") or "",
        })
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    # 自测：python judge.py
    print("compiler:", compiler_info())
    demo = {"cases": [{"input": "3 4\n", "output": "7"}]}
    print(judge(demo, '#include <stdio.h>\nint main(){int a,b;scanf("%d %d",&a,&b);printf("%d",a+b);return 0;}'))
