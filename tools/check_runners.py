# -*- coding: utf-8 -*-
"""两个 runner 的契约面必须一致——这里把「一致」写成能判对错的断言。

为什么需要这个脚本：sandbox/runner_win.c 和 sandbox/runner_linux.c 是同一份
契约的两份实现。上一轮核对「报告键 16 个逐一相同」是手工 diff 出来的，结论
只活在那一次会话里；下一次改任一侧，没有任何东西会提醒另一侧。这和
probe_tle.py 第一版只 print 是同一个毛病：**能看的结论不是能挡的闸门**。

这个脚本**不**验证「能编译」。本机没有 Linux 工具链（无 docker、WSL 被策略
禁用、无 clang），runner_linux.c 拿不到编译证据——那就明说没做，而不是用一条
静态检查冒充编译通过。静态能验证的是另外四件事，而它们恰恰是出错也不报错的
那几件：

    1. 时限宽限 LIMIT_GRACE_MS —— 两侧不等，同一份代码会 Windows AC、容器 TLE
    2. CPU 配额公式 —— 两侧不等，两侧的 CPU 超限会在不同时刻触发
    3. 报告键 —— 少一个键是 KeyError，多一个键是静默忽略，两种都不报编译错
    4. 墙钟定格在收尸之前 —— 顺序错了 wall_ms 会多记几十到两百毫秒，而归因
       正是拿 wall 当分母的

第 4 条是这个脚本存在的真正理由：它不违反任何语法，编译器一个字都不会说，
但 300ms 时限下它能直接把「算得慢」判成「在等」。

用法：python tools/check_runners.py      # 有任何一条不成立就退出 1
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN = os.path.join(ROOT, "sandbox", "runner_win.c")
LIN = os.path.join(ROOT, "sandbox", "runner_linux.c")


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def strip_comments(src):
    """去掉 C 注释，保留字符串字面量。

    这一步不是洁癖：`TerminateJobObject` 在文件头的说明注释里就出现过一次，
    直接 rfind / re.search 抓到的是**注释里的位置**，于是「墙钟是否早于收尸」
    会拿注释去比，结论随机。第一次跑就是这么误判的。
    去注释只影响比对基准，不影响源文件。
    """
    out = []
    i, n = 0, len(src)
    in_str = in_chr = in_line = in_block = False
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_line:
            if c == "\n":
                in_line = False
                out.append(c)
            i += 1
            continue
        if in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            # 块注释里的换行要留下，否则行号结构会变（本脚本只用下标，但留着更省心）
            if c == "\n":
                out.append(c)
            i += 1
            continue
        if in_str or in_chr:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(src[i + 1])
                i += 2
                continue
            if (in_str and c == '"') or (in_chr and c == "'"):
                in_str = in_chr = False
            i += 1
            continue
        if c == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if c == '"':
            in_str = True
        elif c == "'":
            in_chr = True
        out.append(c)
        i += 1
    return "".join(out)


def first(txt, pattern):
    """返回第一个匹配的起止下标；找不到返回 None。"""
    m = re.search(pattern, txt)
    return m.start() if m else None


def last_before(txt, pattern, pos):
    """返回 pos 之前最后一次出现的下标；没有返回 -1。"""
    return txt.rfind(pattern, 0, pos)


def keys_of(txt):
    """报告输出的键名。两侧都是 fprintf(f, "key=...\\n", ...) 的形式。"""
    return set(re.findall(r'fprintf\(\s*f\s*,\s*"([a-z][a-z0-9_]*)=', txt))


def num_after(txt, pattern):
    m = re.search(pattern, txt)
    return int(m.group(1)) if m else None


def main():
    # 结构比对一律在**去注释**的文本上做：注释里提到函数名，会把「位置」比错。
    w, l = strip_comments(read(WIN)), strip_comments(read(LIN))
    rows = []   # (检查项, 结果, 说明)

    # -- 1. 宽限 ------------------------------------------------------------
    gw = num_after(w, r"#define\s+LIMIT_GRACE_MS\s+(\d+)")
    gl = num_after(l, r"#define\s+LIMIT_GRACE_MS\s+(\d+)")
    if gw is None or gl is None:
        rows.append(("时限宽限", False, "有一侧没有 #define LIMIT_GRACE_MS（win=%r linux=%r）"
                     % (gw, gl)))
    elif gw != gl:
        # 不等就是「同一份代码两个结论」：写 150 的一侧放行、写 0 的一侧判超时。
        rows.append(("时限宽限", False, "两侧不等：win=%d linux=%d。"
                     "同一份代码会在两侧得到不同的判题结果" % (gw, gl)))
    else:
        rows.append(("时限宽限", True, "LIMIT_GRACE_MS=%d（两侧相同）" % gw))

    # 宽限必须真的用在时限上，不能只是定义了
    uw = "limit_ms + LIMIT_GRACE_MS" in w
    ul = "limit_ms + LIMIT_GRACE_MS" in l
    rows.append(("宽限已用于时限", uw and ul,
                 "两侧都按 limit_ms + LIMIT_GRACE_MS 判超时" if (uw and ul) else
                 "定义了但没用在判超时上：win=%s linux=%s" % (uw, ul)))

    # -- 2. CPU 配额公式 ----------------------------------------------------
    fw = "3 / 2 + 1000" in w
    fl = "3 / 2 + 1000" in l
    rows.append(("CPU 配额公式", fw and fl,
                 "两侧都是 limit_ms*3/2+1000" if (fw and fl) else
                 "两侧公式不一致：win=%s linux=%s（CPU 超限会在不同时刻触发）" % (fw, fl)))

    # -- 3. 报告键 ----------------------------------------------------------
    kw, kl = keys_of(w), keys_of(l)
    if kw == kl:
        rows.append(("报告键", True, "%d 个键两侧完全相同" % len(kw)))
    else:
        only_w = sorted(kw - kl)
        only_l = sorted(kl - kw)
        rows.append(("报告键", False, "只有 win 有 %r；只有 linux 有 %r" % (only_w, only_l)))

    # -- 4. 墙钟定格在收尸之前 ----------------------------------------------
    # win: t_end（QueryPerformanceCounter）必须早于 TerminateJobObject 和
    #      读线程 join；linux: wall_ms 必须早于 kill 和 wait4。
    # 这条是四条里唯一「错了不报错、只让数字偏大」的，所以单独查。
    # 锚点必须是**超时那一次**收尸：输出超限的那条路径也调
    # TerminateJobObject(g_job, 125)，位置更靠前，用它当锚会把「早于收尸」
    # 判成不成立。裁定超时用的是 124。
    iw_end = first(w, r"QueryPerformanceCounter\(\s*&t_end\s*\)")
    iw_reap = first(w, r"TerminateJobObject\(\s*g_job\s*,\s*124\s*\)")
    iw_join = first(w, r"WaitForSingleObject\(\s*th_out")
    ok_w = (iw_end is not None and iw_reap is not None
            and iw_end < iw_reap and (iw_join is None or iw_end < iw_join))

    il_end = first(l, r"double\s+wall_ms\s*=\s*now_ms\(\)\s*-\s*t0")
    il_kill = first(l, r"kill\(\s*-pid")
    il_wait = first(l, r"wait4\(")
    ok_l = (il_end is not None and il_kill is not None and il_wait is not None
            and il_end < il_kill and il_end < il_wait)

    rows.append(("墙钟在收尸前定格", ok_w and ok_l,
                 "两侧都在进程结束/判超时那一刻打点，收尸时间不计入 wall"
                 if (ok_w and ok_l) else
                 "有一侧把收尸时间算进了 wall_ms：win=%s linux=%s" % (ok_w, ok_l)))

    # -- 5. CPU 超限的 note 两侧都要有 ---------------------------------------
    # sandbox.py 的归因先看 note 里有没有「CPU 时间超限」，再看比值。只在一侧
    # 写这个串，那一侧的超限就会掉进比值判据——四线程时比值可能 >1 侥幸还能对，
    # 但也可能被别的档吃掉。这个串是 Python 与 runner 之间唯一的语义接口，
    # 它比报告键更隐蔽：键少一个会 KeyError，note 少一个什么都不报。
    nw = "CPU 时间超限" in w
    nl = "CPU 时间超限" in l
    rows.append(("CPU 超限 note", nw and nl,
                 "两侧被 CPU 配额/RLIMIT_CPU 杀掉时都写「CPU 时间超限」，"
                 "sandbox.py 靠这个串走 cpu 档" if (nw and nl) else
                 "有一侧不写这个 note，那一侧的超限会掉进比值判据：win=%s linux=%s"
                 % (nw, nl)))

    # -- 6. 墙钟起点：win 必须在 CreateProcess 之后 -------------------------
    # CreateProcess + 建 job + 建管道实测 150~200ms，算进 wall 会让 300ms 时限
    # 下的归因反号。linux 侧起点在 fork 之前（见下方已知差异），这条只查 win。
    iw_t0 = first(w, r"QueryPerformanceCounter\(\s*&t0\s*\)")
    iw_cp_last = w.rfind("CreateProcessW(")
    iw_resume = first(w, r"ResumeThread\(")
    ok_t0 = (iw_t0 is not None and iw_cp_last != -1 and iw_resume is not None
             and iw_cp_last < iw_t0 < iw_resume)
    rows.append(("win 起点在 CreateProcess 之后", ok_t0,
                 "t0 打在 ResumeThread 之前，启动开销不计入 wall" if ok_t0 else
                 "t0 的位置不对（应在最后一次 CreateProcessW 之后、ResumeThread 之前）"))

    # -- 已知差异 ------------------------------------------------------------
    # 有理由的单边实现，不算错，但要印出来：下次有人想「顺手补齐对称」时，
    # 会先看到这里写着为什么不对称。
    notes = []
    if "double t0 = now_ms();" in l and (first(l, r"double\s+t0\s*=\s*now_ms\(\)") or 0) < (first(l, r"fork\(") or 0):
        notes.append(
            "墙钟起点 Linux 在 fork 之前、Windows 在 ResumeThread 之前：两侧排除的\n"
            "    都是本机**大**的那份开销。Windows 的 CreateProcess+建job+建管道是\n"
            "    150~200ms（实测），必须排除；Linux 的 fork 只是拷页表，与前者不是一个\n"
            "    量级，把起点挪到 fork 之后要在父子两支都改，收益不抵改动。")

    # -- 输出 ---------------------------------------------------------------
    print("runner 契约面：")
    bad = 0
    for name, ok, detail in rows:
        print("  %-4s %-28s %s" % ("OK" if ok else "FAIL", name, detail))
        if not ok:
            bad += 1

    if notes:
        print("\n已知差异（有理由，不算错）：")
        for n in notes:
            print("  · " + n)

    if bad:
        print("\n%d / %d 项不成立。两侧是同一份契约的两份实现，" % (bad, len(rows)))
        print("改一侧就要改另一侧；确实不能对称的，写进本脚本的「已知差异」并说明理由。")
        return 1

    print("\n%d / %d 项成立；报告键 %d 个两侧逐一相同。" % (len(rows), len(rows), len(kw)))
    print("注意：这里验证的是静态契约，**不是**编译通过。runner_linux.c 在本机")
    print("没有工具链（无 docker / WSL 被策略禁用 / 无 clang），编译证据要上 Linux 才有。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
