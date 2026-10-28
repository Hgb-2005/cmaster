# -*- coding: utf-8 -*-
"""TLE 归因探针：给三种超时各造一个程序，把 runner 报的真实 cpu_ms / wall_ms
打出来，**并断言归因落在预期档**。

为什么要单独一个脚本而不是靠 /api/run 手点：
    /api/run 只回裁决码和一句人话，看不到 cpu_ms 和 wall_ms 的原始数字。
    归因阈值（贴到多少算「烧满」）如果靠感觉定，改完第一次遇到真超时就
    会发现分档全错，而那时判断对错的依据也已经被改掉了。先有数字，再定阈值。

为什么最后要 sys.exit：
    第一版只有 print。归因逻辑要是退回死判据（比如又去比 CPU 配额），
    这个脚本照样四行数字、照样退出 0——和 `/api/verdicts` 自 500 而页面
    全绿是同一个毛病：**能看的数字不是能挡的闸门**。现在它自己判对错。

用法：python tools/probe_tle.py      # 对不上退出码 1
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import judge      # noqa: E402
import sandbox    # noqa: E402
import verdicts  # noqa: E402

# 第四个字段是**预期归因档**，不是「希望它大概是」：这四项是手工核对过
# 的物理事实（烧满 CPU 的必须落在 cpu，只 sleep 的必须落在 wait），
# 换机器换时序都不该变。变不了才是判据，会变的是观测量。
CASES = [
    ("cpu-spin", "纯 CPU 死循环：应判「算得慢」", "cpu",
     '#include <stdio.h>\nint main(void){volatile long i=0;while(1){i++;}return 0;}\n'),
    ("sleep", "睡眠 9 秒：应判「在等」（Windows 用 Sleep，POSIX 用 sleep）", "wait",
     '#include <stdio.h>\n'
     '#ifdef _WIN32\n#include <windows.h>\n#define NAP(x) Sleep(x)\n'
     '#else\n#include <unistd.h>\n#define NAP(x) sleep((x)/1000)\n#endif\n'
     'int main(void){NAP(9000);printf("done\\n");return 0;}\n'),
    ("both", "先算 1.5 秒再睡 9 秒：应判「算等参半」", "wall",
     '#include <stdio.h>\n#include <time.h>\n'
     '#ifdef _WIN32\n#include <windows.h>\n#define NAP(x) Sleep(x)\n'
     '#else\n#include <unistd.h>\n#define NAP(x) sleep((x)/1000)\n#endif\n'
     'int main(void){clock_t t0=clock();volatile long i=0;\n'
     'while((double)(clock()-t0)/CLOCKS_PER_SEC < 1.5){i++;}\n'
     'NAP(9000);printf("%ld\\n",(long)i);return 0;}\n'),
    # 对照组的预期是空串：它没超时，**不该被归因**。这一条挡的是
    # 「把正常跑完的程序也打上超时标签」这类越界。
    ("quick", "对照：正常跑完的小程序（不该被归因）", "",
     '#include <stdio.h>\nint main(void){printf("ok\\n");return 0;}\n'),
]

# 多线程烧 CPU 这一项只在 Windows 上加。它挡的是一个 Windows 特有的记账 bug：
# job 的 CPU 配额触发时，内核用 STATUS_QUOTA_EXCEEDED（0xC0000044）结束进程，
# 而 timed_out 若不认这个码，四线程程序就会以 exit=-1073741756 落进「运行时
# 错误」——面板上写「你的程序崩了」，真相是「你的程序太慢」。这个 bug 在修复
# 前实测稳定复现。Linux 侧对应的是 RLIMIT_CPU → SIGXCPU，那条路径本来就写
# note 并判超时，没有这个缺口。
#
# 为什么不在 Linux 上也加：judge.compile_code 的编译命令没带 -pthread，pthread
# 版本在 Linux 上编不过，届时「编译失败」会被算成用例失败——那是在报工具链的
# 错，不是报判题器的错。工具链缺什么就说什么，不拿它冒充结论。
if os.name == "nt":
    CASES.append((
        "mt", "四线程烧 CPU：job 配额会在墙钟之前到，仍应判「算得慢」", "cpu",
        '#include <windows.h>\n'
        'static DWORD WINAPI burn(LPVOID p){volatile unsigned long long x=0;(void)p;'
        'for(;;)x++;return 0;}\n'
        'int main(void){HANDLE t[4];int i;'
        'for(i=0;i<4;i++)t[i]=CreateThread(NULL,0,burn,NULL,0,NULL);'
        'for(i=0;i<4;i++)WaitForSingleObject(t[i],INFINITE);return 0;}\n'))


def main():
    lim = sandbox.limits()
    print("配额: wall=%dms cpu=%dms" % (lim["wall_ms"], lim["cpu_ms"]))
    print("阈值: ratio>=%.2f -> cpu, ratio<=%.2f 且 cpu<=%dms -> wait, 其余 wall"
          % (sandbox._CPU_BOUND, sandbox._IDLE_RATIO, sandbox._IDLE_CPU_MS))
    print("")
    rows, bad = [], []
    for key, desc, expect, src in CASES:
        ok, exe, msg, wd = judge.compile_code(src)
        if not ok:
            # 编译失败曾经只是 continue——四项里少一项，看起来仍然「跑完了」。
            # 少一行输出比错一行输出更危险：没有人会去数还剩几行。
            print("[%s] 编译失败: %s" % (key, msg[:200]))
            bad.append((key, expect, "<编译失败>"))
            continue
        try:
            r = judge.run_once_ex(exe, "", workdir=wd)
        finally:
            import shutil
            shutil.rmtree(wd, ignore_errors=True)
        cpu = r.get("cpu_ms") or 0
        wall = r.get("wall_ms") or 0
        ratio = (cpu / float(wall)) if wall else 0.0
        got = r.get("tle_kind") or ""
        print("[%-9s] %s" % (key, desc))
        print("   timed_out=%s sandboxed=%s exit=%s"
              % (r.get("timed_out"), r.get("sandboxed"), r.get("exit_code")))
        print("   wall=%dms cpu=%dms ratio=%.3f -> tle_kind=%r"
              % (wall, cpu, ratio, got))
        # 顺带把学生实际看到的那句话打出来。上一版打的是 r["note"]，
        # 而 run_once_ex 根本不产这个键，于是永远显示「note=」——看起来像
        # 归因没生效。档名和人话得成对验证：改了 TLE_KINDS 却漏了某一个
        # kind，面板上会出现一个只有色块、没有解释的档。
        #
        # 没超时的用例**不能**照搬 "" 档的文案：那一档的 note 是
        # 「没有可信的 CPU 时间（降级路径）」，说的是「超时了但归不了因」，
        # 套在一次正常跑完的程序上会让人以为 runner 降级了。
        if r.get("timed_out"):
            kk = verdicts.tle_kind(got)
            print("   %s：%s" % (kk["name"], kk["note"]))
        else:
            print("   没超时，不归因（这一档不该出现在面板上）")
        print("")
        verdict = "OK" if got == expect else "不符预期（应为 %r）" % (expect,)
        print("   判定: %s" % verdict)
        print("")
        if got != expect:
            bad.append((key, expect, got or "<未归因>"))
        rows.append(dict(key=key, wall=wall, cpu=cpu, ratio=round(ratio, 3),
                         kind=got, expect=expect,
                         timed_out=bool(r.get("timed_out"))))
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    print("")
    if bad:
        print("归因不符预期 %d / %d：" % (len(bad), len(CASES)))
        for key, expect, got in bad:
            print("  %-9s 应为 %r，实际 %r" % (key, expect, got))
        print("  先别调阈值——档错了通常是判据的参照系错了（比的是配置还是实测）。")
        return 1
    print("归因 %d / %d 全部符合预期" % (len(rows), len(CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
