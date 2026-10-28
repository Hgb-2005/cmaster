/* ============================================================================
 * runner_win.c —— 判题沙箱宿主（Windows）
 *
 * 为什么需要这个程序：
 *   之前 judge.py 用 subprocess.run(timeout=...) 跑用户代码。它能做到的只有
 *   「到点了把直接子进程杀掉」，剩下的都做不到：
 *
 *     · 用户代码 fork 出来的孙进程不会死（Windows 上叫 CreateProcess），
 *       杀父不杀子，跑几轮就在后台攒一堆孤儿进程啃 CPU；
 *     · 没有内存上限，`int *p = malloc(4GB)` 能把整台机器换页换死；
 *     · 没有进程数上限，一个 while(1) CreateProcess 就是 fork 炸弹；
 *     · `system("...")` 能直接执行任意命令，等于把 shell 交出去了；
 *     · 能弹窗、能改剪贴板、能 ExitWindowsEx 关机——判个题把机器关掉。
 *
 *   这些东西 Python 一条都改不了，必须落到操作系统原语上。Windows 给的
 *   原语就是 **Job Object**：一个内核对象，把进程塞进去之后，内存、进程数、
 *   CPU 时间、UI 权限全部由内核按配额执行，进程自己不配合也绕不过去
 *   （这是和「投票制 rlimit」最大的区别：Job 配额是内核强制的）。
 *
 * 设计上刻意做的三个选择：
 *
 *   1. **CREATE_SUSPENDED + AssignProcessToJobObject + ResumeThread**
 *      顺序不能反。先 CreateProcess 再 Assign 的话，中间那个窗口里进程已经
 *      在跑了——它完全有机会在这个窗口里 CreateProcess 出孙进程，孙进程不
 *      属于 job，配额管不到它。挂起创建、塞进 job、再放行，才能保证「这个
 *      进程的整个生命周期都在配额内」。这三行的顺序是这个文件里最要紧的
 *      东西，改的时候别挪。
 *
 *   2. **stdout / stderr 用匿名管道 + 两个读线程，不用文件重定向**
 *      写文件看着简单，但 `while(1) printf("x")` 会在超时之前就把盘写满
 *      （3 秒能写好几个 GB）——出题人赔不起。管道边读边计数，超了立刻
 *      TerminateJobObject，盘上一个字节都不落。代价是要两个线程，值。
 *
 *   3. **Windows 侧不假装做系统调用隔离**
 *      Linux 有 seccomp-bpf，能把 syscall 一个个掐掉；Windows 没有对等物
 *      （FilterDriver 要签名的内核驱动，AppContainer 要改清单文件且挑系统
 *      版本）。所以这里只做**资源隔离**：不越权（job 里提不了权）、不逃逸
 *      （ACTIVE_PROCESS=1 让 system() 直接失败）、不拖死机器、不留孤儿子进程。
 *      真正的 syscall 白名单在 runner_linux.c 里。
 *      这个分层是诚实的，也是能讲清楚的：**容器隔离在这两个平台上的形状
 *      本来就不一样**，Windows 容器用 Job Object + Silo，Linux 用 namespace +
 *      cgroup + seccomp。我们做的是同一件事的缩小版。
 *
 * 退出码约定（runner 自己的，不是用户程序的）：
 *     0    正常跑完（用户程序退出码写在 report 里，可能是任意值）
 *     124  墙钟超时，已 TerminateJobObject
 *     125  输出超限，已 TerminateJobObject
 *     126  沙箱初始化失败（建不了 job / 塞不进 job）——调用方应降级
 *     127  启动失败（可执行文件不存在 / 参数错）
 *
 *   注意 124 会和用户程序自己 `return 124` 撞车。所以**判断是否超时只能看
 *   report 里的 timed_out 字段，不能看退出码**。这是个真实的坑：早先版本
 *   靠退出码判断，结果一道题的参考答案正好 return 124 就被误判成 TLE。
 *
 * 编译（项目自带的 w64devkit 即可，不需要 MSVC）：
 *     gcc -O2 -std=c11 -municode -o runner_win.exe runner_win.c
 * ========================================================================== */

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

/* ------------------------------------------------------------------ 常量 */

#define MAX_PATH_LEN 1024

/* 输出缓冲区上限。比 judge.py 的 OUTPUT_LIMIT 略大一点：
   多出来的那点是留给「截断说明」的余量，保证前端看到的仍是完整一段话
   而不是半句被切掉的中文（中文按 UTF-8 是 3 字节，正好切在字符中间就会出乱码）。*/
#define BUF_OUT (96 * 1024)
#define BUF_ERR (32 * 1024)

/* 默认值。调用方一定要显式传，这里的默认只是「忘了传也别裸奔」。*/
#define DEF_MS 3000
#define DEF_MEM (256ull * 1024 * 1024)
#define DEF_PROC 1
#define DEF_OUT (64 * 1024)
#define DEF_ERR (16 * 1024)

/* 判定「超时」时给的容忍。时限是一份契约：题面写 3 秒，就该在 3 秒附近
   砍断，宽限可以有，但不能宽到改变结论（以前是 500ms，一个跑了 3.3 秒的
   程序会被判通过，而报告上写着「3 秒内没结束」）。
   150ms 覆盖两件事：WaitForSingleObject 的定时器粒度（Windows 上约 15.6ms，
   向上取整），以及判超时之前最后一次调度。
   **runner_linux.c 里必须有同名同值的宏**：两边宽限不一致，同一份代码在
   Windows 上 AC、在容器里 TLE，是最难解释的一类差异。*/
#define LIMIT_GRACE_MS 150

/* 全局：读线程发现输出超限时要把整个 job 干掉，它需要拿到 job 句柄。
   传指针也行，但读线程是 CreateThread 起的，用全局少一层转手，
   少一层就少一个「传进去的是栈上临时变量」的可能。*/
static HANDLE g_job = NULL;
static volatile LONG g_over_limit = 0;

/* ------------------------------------------------------------------ 选项 */

typedef struct {
    wchar_t prog[MAX_PATH_LEN];
    wchar_t cmdline[8192];      /* 目标程序的完整命令行（含 argv[0]）*/
    wchar_t in_path[MAX_PATH_LEN];
    wchar_t out_path[MAX_PATH_LEN];
    wchar_t err_path[MAX_PATH_LEN];
    wchar_t report_path[MAX_PATH_LEN];
    wchar_t cwd[MAX_PATH_LEN];

    DWORD limit_ms;
    unsigned long long limit_mem;
    DWORD limit_proc;
    unsigned long long limit_cpu_ms;
    DWORD limit_out;
    DWORD limit_err;

    int has_in;
    int verbose;
} opts_t;

static void die(int code, const wchar_t *msg) {
    /* 错误走 stderr，调用方（Python）会把它接住放进 report 的 note 字段。
       这里不 exit(-1)：负数退出码在 Windows 上会被当成 0xFFFFFFFF，
       Python 侧 subprocess 拿到的是 4294967295，比 126 难认。*/
    fwprintf(stderr, L"[runner] %ls\n", msg);
    exit(code);
}

static int starts_with(const wchar_t *s, const wchar_t *p) {
    size_t n = wcslen(p);
    return wcsncmp(s, p, n) == 0;
}

static const wchar_t *after_eq(const wchar_t *s) {
    const wchar_t *eq = wcschr(s, L'=');
    return eq ? eq + 1 : L"";
}

static unsigned long long parse_u64(const wchar_t *s) {
    /* _wcstoui64 对空串返回 0，正好是我们要的「不限」语义 */
    return s && *s ? _wcstoui64(s, NULL, 10) : 0ull;
}

static void parse_args(int argc, wchar_t **argv, opts_t *o) {
    memset(o, 0, sizeof(*o));
    o->limit_ms = DEF_MS;
    o->limit_mem = DEF_MEM;
    o->limit_proc = DEF_PROC;
    o->limit_cpu_ms = 0;          /* 0 = 由 limit_ms 推一个，见下 */
    o->limit_out = DEF_OUT;
    o->limit_err = DEF_ERR;

    int i = 1;
    for (; i < argc; i++) {
        if (wcscmp(argv[i], L"--") == 0) { i++; break; }
        if (starts_with(argv[i], L"--limit-ms=")) {
            o->limit_ms = (DWORD)parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--limit-mem=")) {
            o->limit_mem = parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--limit-proc=")) {
            o->limit_proc = (DWORD)parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--limit-cpu-ms=")) {
            o->limit_cpu_ms = parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--limit-out=")) {
            o->limit_out = (DWORD)parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--limit-err=")) {
            o->limit_err = (DWORD)parse_u64(after_eq(argv[i]));
        } else if (starts_with(argv[i], L"--in=")) {
            wcsncpy(o->in_path, after_eq(argv[i]), MAX_PATH_LEN - 1);
            o->has_in = 1;
        } else if (starts_with(argv[i], L"--out=")) {
            wcsncpy(o->out_path, after_eq(argv[i]), MAX_PATH_LEN - 1);
        } else if (starts_with(argv[i], L"--err=")) {
            wcsncpy(o->err_path, after_eq(argv[i]), MAX_PATH_LEN - 1);
        } else if (starts_with(argv[i], L"--report=")) {
            wcsncpy(o->report_path, after_eq(argv[i]), MAX_PATH_LEN - 1);
        } else if (starts_with(argv[i], L"--cwd=")) {
            wcsncpy(o->cwd, after_eq(argv[i]), MAX_PATH_LEN - 1);
        } else if (wcscmp(argv[i], L"--verbose") == 0) {
            o->verbose = 1;
        } else {
            /* 不认识的参数直接报错而不是忽略：调用方拼错一个 --limit-mem
               就会静默变成「内存不限」，这种「保护悄悄失效」最难查。*/
            fwprintf(stderr, L"[runner] 未知参数: %ls\n", argv[i]);
            exit(127);
        }
    }

    if (i >= argc) die(127, L"没有指定要运行的程序（用 -- 分隔）");

    wcsncpy(o->prog, argv[i], MAX_PATH_LEN - 1);

    /* 拼命令行：CreateProcessW 要一整条字符串，第一个 token 必须是程序名。
       Windows 的引号规则和 POSIX 不一样（argv[0] 也参与解析），所以这里
       给每个 token 都加引号再转义内部引号，是 argc/argv -> cmdline 的
       标准做法。不这么做的话，路径里有空格就会在空格处断开。*/
    {
        size_t used = 0;
        /* _snwprintf 在截断时返回 -1（不是返回实际写入长度）。
           直接 used += _snwprintf(...) 的话，一次截断就让 used 变成 SIZE_MAX，
           下一轮 cap - used 会溢出成一个巨大的数，然后把命令行写成一段乱码。
           所以每步都要判负。这个坑在 _CRT_SECURE_NO_WARNINGS 关掉的时候
           编译器不会提醒。*/
        int n = _snwprintf(o->cmdline, 8191, L"\"%ls\"", argv[i]);
        if (n < 0) { o->cmdline[8191] = L'\0'; }
        else { used = (size_t)n; }

        for (int k = i + 1; k < argc && used < 8000; k++) {
            n = _snwprintf(o->cmdline + used, 8191 - used, L" \"%ls\"", argv[k]);
            if (n < 0) { o->cmdline[8191] = L'\0'; break; }
            used += (size_t)n;
        }
        o->cmdline[used > 8191 ? 8191 : used] = L'\0';
    }

    if (o->limit_ms == 0) o->limit_ms = DEF_MS;
    /* CPU 时间上限默认给墙钟的 1.5 倍 + 1 秒：一个正常程序不会超过墙钟，
       但如果用户代码是「睡 3 秒」型的，wall 到了 CPU 还早，这时不该被
       CPU 配额误杀。反过来纯计算型的，CPU 配额先到，能比墙钟更早掐断
       一个死循环。两者错开是故意的，不是冗余。*/
    if (o->limit_cpu_ms == 0) {
        o->limit_cpu_ms = (unsigned long long)o->limit_ms * 3 / 2 + 1000;
    }
    if (!o->report_path[0]) die(127, L"缺少 --report=，调用方需要它判断超时");
}

/* ------------------------------------------------------------------ 管道 */

typedef struct {
    HANDLE pipe;
    char *buf;
    DWORD cap;
    DWORD len;
    unsigned long long total;
    DWORD limit;
    int is_out;
} reader_t;

static DWORD WINAPI reader_main(LPVOID param) {
    reader_t *r = (reader_t *)param;
    char chunk[8192];
    DWORD got = 0;

    for (;;) {
        /* ReadFile 在「子进程还活着但没输出」时会阻塞，这正是我们要的：
           线程挂着不占 CPU，子进程一死管道关闭（写端引用计数归零），
           ReadFile 立刻返回 ERROR_BROKEN_PIPE。不需要轮询。*/
        BOOL ok = ReadFile(r->pipe, chunk, sizeof(chunk), &got, NULL);
        if (!ok || got == 0) break;

        r->total += got;
        if (r->len < r->cap) {
            DWORD room = r->cap - r->len;
            DWORD n = got < room ? got : room;
            memcpy(r->buf + r->len, chunk, n);
            r->len += n;
        }
        /* 超限后继续读但不留（必须继续读，否则管道填满，子进程 write 阻塞，
           它就一直不退出，反而比不限制更糟——这叫「用限流把自己锁死」）。
           同时只让第一个发现的线程动手终止，避免两个线程同时
           TerminateJobObject 造成退出码互相覆盖。*/
        if (r->limit && r->total > r->limit) {
            if (InterlockedExchange(&g_over_limit, 1) == 0 && g_job) {
                TerminateJobObject(g_job, 125);
            }
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ 报告 */

/* 报告的字段全塞进一个结构体。
 *
 * 为什么不用长参数表——因为我自己在这个文件上数错了三次逗号：加一个字段
 * 之后有六处调用要跟着改，其中五处全是 `-1, 0, 0, 0, ...` 这样的占位，
 * 少一个多一个编译器能报出来（万幸），但「某个 0 落到错误的位置上」编译器
 * 报不出来——比如 peak_proc 的 0 落进 total_procs，两者都是无符号整数，
 * 编译通过、运行正常、字段恒为 0。这种错只能靠数，而人会数错。
 *
 * 结构体 + memset 之后，加字段只改一处，调用方永远不用动。
 * 参数超过五六个就该换结构体，这是那条老规矩，我自己又验证了一遍。*/
typedef struct {
    int exit_code;
    int timed_out;
    int output_limited;
    unsigned long long out_bytes;
    unsigned long long err_bytes;
    double wall_ms;
    unsigned long long cpu_ms;
    unsigned long long peak_job_mem;
    unsigned long long peak_proc_mem;
    unsigned int total_procs;
    unsigned int active_procs;
    const wchar_t *note;
} report_t;

static void write_report(const opts_t *o, const report_t *r) {
    if (!o->report_path[0]) return;

    FILE *f = _wfopen(o->report_path, L"wb");
    if (!f) return;

    /* 一行一个 key=value，不用 JSON：写这个文件的时候进程可能已经被
       TerminateJobObject 打过，路径上不该再引入任何可能失败的库调用。
       纯 fprintf 是最不容易出事的形态，Python 那边 split 一下就好。*/
    fprintf(f, "exit_code=%d\n", r->exit_code);
    fprintf(f, "timed_out=%d\n", r->timed_out ? 1 : 0);
    fprintf(f, "output_limited=%d\n", r->output_limited ? 1 : 0);
    fprintf(f, "out_bytes=%llu\n", r->out_bytes);
    fprintf(f, "err_bytes=%llu\n", r->err_bytes);
    fprintf(f, "wall_ms=%.1f\n", r->wall_ms);
    fprintf(f, "cpu_ms=%llu\n", r->cpu_ms);
    fprintf(f, "peak_job_mem=%llu\n", r->peak_job_mem);
    fprintf(f, "peak_proc_mem=%llu\n", r->peak_proc_mem);
    fprintf(f, "total_procs=%u\n", r->total_procs);
    fprintf(f, "active_procs=%u\n", r->active_procs);
    fprintf(f, "limit_ms=%lu\n", (unsigned long)o->limit_ms);
    fprintf(f, "limit_mem=%llu\n", o->limit_mem);
    fprintf(f, "limit_proc=%lu\n", (unsigned long)o->limit_proc);
    fprintf(f, "limit_cpu_ms=%llu\n", o->limit_cpu_ms);

    if (r->note && *r->note) {
        /* 宽字符 → UTF-8 再写。直接 %ls 走的是 C locale 的转换，
           中文会变成 '?'，而 note 里恰恰会写中文错误信息。*/
        char utf8[512];
        int n = WideCharToMultiByte(CP_UTF8, 0, r->note, -1, utf8,
                                   (int)sizeof(utf8) - 1, NULL, NULL);
        if (n > 0) {
            utf8[n] = '\0';
            fprintf(f, "note=%s\n", utf8);
        }
    }
    fclose(f);
}

/* 初始化失败时的统一出口。失败路径全都长一样：写个只有 note 的报告，
   然后 die(126/127)。抽出来是为了保证六处失败点的报告格式一致——
   否则 Python 侧读到的 report 会缺字段，而缺字段的 report 比没有更危险，
   它会让人以为「跑完了，一切正常」。*/
static void report_fail(const opts_t *o, const wchar_t *note) {
    report_t r;
    memset(&r, 0, sizeof(r));
    r.exit_code = -1;
    r.note = note;
    write_report(o, &r);
}

/* ------------------------------------------------------------------ 主流程 */

int wmain(int argc, wchar_t **argv) {
    opts_t o;
    parse_args(argc, argv, &o);

    if (o.verbose) {
        fwprintf(stderr, L"[runner] prog=%ls\n", o.prog);
        fwprintf(stderr, L"[runner] cmdline=%ls\n", o.cmdline);
    }

    /* -- 1. 建 job ----------------------------------------------------- */
    g_job = CreateJobObjectW(NULL, NULL);
    if (!g_job) {
        report_fail(&o,
                     L"CreateJobObject 失败");
        die(126, L"CreateJobObject 失败（沙箱不可用，调用方应降级）");
    }

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION li;
    memset(&li, 0, sizeof(li));

    /* KILL_ON_JOB_CLOSE 是整套东西的保命绳：只要 job 句柄一关（本进程退出、
       崩溃、被 taskkill），内核立刻干掉 job 里所有进程。没有它，runner 自己
       挂了就会留下一堆没人管的用户程序——这正是原来 subprocess 方案的老毛病。*/
    li.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

    if (o.limit_mem) {
        /* 两个都设：ProcessMemoryLimit 管单个进程，JobMemoryLimit 管整个 job
           的合计。只设后者的话，一个进程就能把整个配额吃掉一半以上再引发
           更多进程；只设前者的话 N 个进程各吃一份，合计仍然超。
           判题场景两者都设才有意义——「单题内存 256MB，且总共 256MB」。*/
        li.ProcessMemoryLimit = (SIZE_T)o.limit_mem;
        li.JobMemoryLimit = (SIZE_T)o.limit_mem;
        li.BasicLimitInformation.LimitFlags |=
            JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY;
    }

    if (o.limit_proc) {
        /* ACTIVE_PROCESS = 1 意味着 job 里同时只能活 1 个进程。
           用户代码只要 CreateProcess / system()，那个新进程就是第 2 个，
           内核直接拒绝——**这就是「禁 shellout」在 Windows 上的实现方式**，
           不需要 hook API、不需要改代码，一行配额。*/
        li.BasicLimitInformation.ActiveProcessLimit = o.limit_proc;
        li.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    }

    if (o.limit_cpu_ms) {
        /* 单位是 100 纳秒，所以 *10000。超了这个量，整个 job 被终止，
           这是比墙钟更早的一道闸——纯 CPU 死循环在这里就被掐掉。*/
        li.BasicLimitInformation.PerJobUserTimeLimit.QuadPart =
            (LONGLONG)o.limit_cpu_ms * 10000LL;
        li.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_TIME;
    }

    /* 优先级压到 Below Normal：判题是后台活，不该和用户的前台程序抢 CPU。
       配额管的是「最多用多少」，优先级管的是「争抢时谁先让」。*/
    li.BasicLimitInformation.PriorityClass = BELOW_NORMAL_PRIORITY_CLASS;
    li.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PRIORITY_CLASS;

    /* 未处理异常直接结束进程，不要弹「程序已停止工作」对话框——
       那个框会一直挂着，把判题卡死。*/
    li.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;

    if (!SetInformationJobObject(g_job, JobObjectExtendedLimitInformation,
                                 &li, sizeof(li))) {
        report_fail(&o,
                     L"SetInformationJobObject(Extended) 失败");
        CloseHandle(g_job);
        die(126, L"设置 job 配额失败（沙箱不可用，调用方应降级）");
    }

    /* UI 限制：这一组只在一个地方有用——禁止用户程序碰桌面。
       · HANDLES          摸不到其他进程的 USER 句柄（不能操作别人的窗口）
       · READCLIPBOARD    读不到剪贴板（作业答案可能就在剪贴板里）
       · WRITECLIPBOARD   写不了剪贴板（污染用户环境）
       · SYSTEMPARAMETERS 改不了系统设置
       · DISPLAYSETTINGS  改不了分辨率
       · EXITWINDOWS      关不了机（判个题把机器关了，这条最要紧）
       · GLOBALATOMS      不污染全局原子表
       这组设置是「防骚扰」，不是安全边界，但它成本极低（一次调用），
       而且能挡掉一类很尴尬的事故。*/
    JOBOBJECT_BASIC_UI_RESTRICTIONS ui;
    ui.UIRestrictionsClass =
        JOB_OBJECT_UILIMIT_HANDLES |
        JOB_OBJECT_UILIMIT_READCLIPBOARD |
        JOB_OBJECT_UILIMIT_WRITECLIPBOARD |
        JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS |
        JOB_OBJECT_UILIMIT_DISPLAYSETTINGS |
        JOB_OBJECT_UILIMIT_EXITWINDOWS |
        JOB_OBJECT_UILIMIT_GLOBALATOMS;
    /* 失败不致命：某些受限会话（服务、容器）不支持 UI 限制，
       但资源配额仍然生效。这里只是「能挡就挡」。*/
    SetInformationJobObject(g_job, JobObjectBasicUIRestrictions, &ui, sizeof(ui));

    /* -- 2. 准备输入输出 ----------------------------------------------- */
    HANDLE h_in = INVALID_HANDLE_VALUE, h_out_r = NULL, h_out_w = NULL;
    HANDLE h_err_r = NULL, h_err_w = NULL;

    /* 这三个句柄都要交给子进程，所以**全部**必须可继承。
       这一行最早漏了：CreateFileW 的 lpSecurityAttributes 传 NULL 时
       句柄是不可继承的，而 StartProcess 用 bInheritHandles=TRUE 只会
       复制「可继承」的那部分——输入文件句柄被静默丢掉，子进程的 stdin
       是个无效句柄。表现是 scanf 一个都读不到、变量保持未初始化，
       用户看到的是「我在本机跑得好好的，到你这就乱码」。
       这种 bug 不报错、不崩、只是答案错，最难查，所以三个句柄共用一份 sa。*/
    SECURITY_ATTRIBUTES sa;
    memset(&sa, 0, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    if (o.has_in && o.in_path[0]) {
        h_in = CreateFileW(o.in_path, GENERIC_READ, FILE_SHARE_READ, &sa,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        /* 输入文件打不开不是错误：很多题没有输入，走空 stdin 就好。
           把 h_in 留给 NUL 设备，程序读到立刻 EOF。*/
        if (h_in == INVALID_HANDLE_VALUE) {
            h_in = CreateFileW(L"NUL", GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                               OPEN_EXISTING, 0, NULL);
        }
    } else {
        h_in = CreateFileW(L"NUL", GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                           OPEN_EXISTING, 0, NULL);
    }

    if (!CreatePipe(&h_out_r, &h_out_w, &sa, 0) ||
        !CreatePipe(&h_err_r, &h_err_w, &sa, 0)) {
        report_fail(&o, L"CreatePipe 失败");
        CloseHandle(g_job);
        die(126, L"创建管道失败");
    }
    /* 读端不能被继承：否则子进程持着读端，父进程永远等不到 EOF，
       这条题会在 case 结束时挂住。这是管道编程的经典死锁，
       两个 SetHandleInformation 就是在防它。*/
    SetHandleInformation(h_out_r, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(h_err_r, HANDLE_FLAG_INHERIT, 0);

    /* -- 3. 挂起创建 --------------------------------------------------- */
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    memset(&pi, 0, sizeof(pi));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;      /* 控制台程序也要藏窗口，否则每次判题闪一下黑框 */
    si.hStdInput = h_in;
    si.hStdOutput = h_out_w;
    si.hStdError = h_err_w;

    DWORD flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB;
    /* CREATE_BREAKAWAY_FROM_JOB：如果本 runner 恰好被某个父 job 管着
       （CI、Windows 服务、某些终端模拟器会这么做），子进程默认会继承那个
       父 job 的配额——父 job 可能根本没有限制，于是我们的配额形同虚设；
       更糟的是父 job 的 ACTIVE_PROCESS 可能已经是满的，创建直接失败。
       断开后再自己建 job，才是「我说了算」。断不了（父 job 设了
       BREAKAWAY_OK 才允许）就退回不带这个标志，能跑总比跑不了强。*/
    LARGE_INTEGER t0, t_end, freq;
    QueryPerformanceFrequency(&freq);

    if (!CreateProcessW(o.prog, o.cmdline, NULL, NULL, TRUE, flags, NULL,
                        o.cwd[0] ? o.cwd : NULL, &si, &pi)) {
        DWORD err = GetLastError();
        if (err == ERROR_ACCESS_DENIED) {
            flags &= ~CREATE_BREAKAWAY_FROM_JOB;
            if (!CreateProcessW(o.prog, o.cmdline, NULL, NULL, TRUE, flags, NULL,
                                o.cwd[0] ? o.cwd : NULL, &si, &pi)) {
                report_fail(&o,
                             L"CreateProcess 失败（不可 breakaway 且重试失败）");
                CloseHandle(g_job);
                die(127, L"启动目标程序失败");
            }
        } else {
            wchar_t note[256];
            _snwprintf(note, 256, L"CreateProcess 失败，GetLastError=%lu",
                       (unsigned long)err);
            report_fail(&o, note);
            CloseHandle(g_job);
            die(127, L"启动目标程序失败");
        }
    }

    /* 立刻关掉父进程手里的写端：不关的话子进程死了管道也不算关闭
       （父进程还持着写端），读线程永远等不到 EOF。 */
    CloseHandle(h_out_w);
    CloseHandle(h_err_w);
    h_out_w = h_err_w = NULL;

    /* -- 4. 塞进 job，再放行 ------------------------------------------- */
    if (!AssignProcessToJobObject(g_job, pi.hProcess)) {
        DWORD err = GetLastError();
        /* 失败常见原因：当前进程已在不支持嵌套的 job 里（Win7 之前），
           或者权限不足。这时候**不能放行**——没有配额的进程就是裸奔。
           杀掉、报 126、让 Python 侧降级到 subprocess 并如实标注
           「本次无系统级隔离」，比假装限制成功诚实得多。*/
        TerminateProcess(pi.hProcess, 126);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        wchar_t note[256];
        _snwprintf(note, 256, L"AssignProcessToJobObject 失败，GetLastError=%lu",
                   (unsigned long)err);
        report_fail(&o, note);
        CloseHandle(g_job);
        die(126, L"进程无法加入 job（沙箱不可用，调用方应降级）");
    }
    /* 墙钟从这里开始记：这一刻才是「用户的程序开始跑」。
       早先版本在 CreateProcess 之前打点，于是 wall_ms 里混进了我们自己的
       启动开销（CreateProcess + 建 job + 建管道，实测 150~200ms）。这个
       偏差在 3 秒的时限上只是难看（判 TLE 的程序显示跑了 3.2 秒），
       在 300ms 的时限上是**会改结论**的：200ms 的启动开销占了墙钟四成，
       「算得慢」会被算成「在等」。度量口径错一个词，归因就全错。*/
    QueryPerformanceCounter(&t0);
    ResumeThread(pi.hThread);

    /* -- 5. 起读线程 --------------------------------------------------- */
    reader_t rd_out, rd_err;
    memset(&rd_out, 0, sizeof(rd_out));
    memset(&rd_err, 0, sizeof(rd_err));

    rd_out.pipe = h_out_r;
    rd_out.buf = (char *)malloc(BUF_OUT);
    rd_out.cap = BUF_OUT;
    rd_out.limit = o.limit_out;
    rd_out.is_out = 1;

    rd_err.pipe = h_err_r;
    rd_err.buf = (char *)malloc(BUF_ERR);
    rd_err.cap = BUF_ERR;
    rd_err.limit = o.limit_err;
    rd_err.is_out = 0;

    HANDLE th_out = CreateThread(NULL, 0, reader_main, &rd_out, 0, NULL);
    HANDLE th_err = CreateThread(NULL, 0, reader_main, &rd_err, 0, NULL);

    /* -- 6. 等 --------------------------------------------------------- */
    /* 时限 + LIMIT_GRACE_MS。这个宏的定义处写了为什么以前是 500、为什么
       现在必须是 150，以及为什么 Linux 侧要跟着改。*/
    DWORD wait_ms = o.limit_ms + LIMIT_GRACE_MS;
    DWORD wr = WaitForSingleObject(pi.hProcess, wait_ms);

    /* 墙钟就在这一刻打点：这一刻是「进程结束」或「被判超时」的真实时刻。
       早先版本是在 join 完读线程、收完尸之后才打点，于是 wall_ms 里混进了
       200ms 左右的收尸时间——报告上写 3679ms，可用的时限是 3000ms，
       看起来像判题器自己不守时。测出来的四个用例都是 3.6~3.7 秒，
       差值稳定得可疑，就是这个原因。*/
    QueryPerformanceCounter(&t_end);

    int timed_out = 0;
    if (wr == WAIT_TIMEOUT) {
        timed_out = 1;
        TerminateJobObject(g_job, 124);
        /* 收尸要等：Terminate 之后立刻 GetExitCodeProcess 可能还是
           STILL_ACTIVE。等 2 秒是够用的，等不到也继续往下走——
           报告比精确的退出码重要。*/
        WaitForSingleObject(pi.hProcess, 2000);
    }

    /* 读线程要 join，否则 report 里的字节数是半截的。
       子进程已死 → 管道关闭 → ReadFile 立刻返回 → 线程自然退出。*/
    if (th_out) { WaitForSingleObject(th_out, 3000); CloseHandle(th_out); }
    if (th_err) { WaitForSingleObject(th_err, 3000); CloseHandle(th_err); }

    double wall_ms = (double)(t_end.QuadPart - t0.QuadPart) * 1000.0 / (double)freq.QuadPart;

    DWORD user_exit = 0;
    GetExitCodeProcess(pi.hProcess, &user_exit);

    /* job 的 CPU 配额触发时，内核用 STATUS_QUOTA_EXCEEDED（0xC0000044）结束
       进程。这**不是**「用户的程序崩了」，是一次超时——只是掐它的是 CPU 配额
       而不是墙钟。不认这一条的后果是实测出来的：四线程烧 CPU 的程序退出码
       是 -1073741756，timed_out 还是 0，判题器把它记成运行时错误，面板上写
       「你的程序崩了」，而真相是「你的程序太慢」。这两句话的改法完全不同。

       顺带记一笔这个配额有多不可靠：单线程烧 CPU 时，500ms 的配额跑到
       cpu_ms=5078 都没被掐（墙钟先到）；四线程时又会在墙钟之前掐。所以它
       只能当「额外的早退信号」，不能当判超时的唯一手段——判超时仍然靠墙钟，
       这一段只是把「已在别处发生的超时」如实记成超时。*/
    int cpu_quota_hit = 0;
    if (!timed_out && (unsigned long)user_exit == 0xC0000044ul) {
        timed_out = 1;
        cpu_quota_hit = 1;
    }

    /* -- 7. 真实用量 --------------------------------------------------- */
    /* 沙箱不该只是「限制」，还应该是「度量」。Job 对象会自己累计峰值内存
       和 CPU 时间，这些数是内核记的，比任何在 Python 侧估算的都准。
       拿到它们之后 /admin 上就能显示「这道题真吃了 87MB / 上限 256MB」，
       而不只是「限制是 256MB」——后者是配置，前者才是事实。*/
    unsigned long long cpu_ms = 0, peak_job = 0, peak_proc = 0;
    unsigned int total_procs = 0, active_procs = 0;

    /* 两次查询，不是一次。这两个数不在同一个结构里，这一点很容易写错：
       内存峰值在 ExtendedLimitInformation，CPU 时间和进程计数在
       BasicAccountingInformation。早先版本想从 ExtendedLimit 里读
       TotalUserTime，编译能过（结构里确实有个 LARGE_INTEGER），但读出来的
       是 PerProcessUserTimeLimit——一个配置值，不是用量。
       这种 bug 不会报错，只会让面板上永远显示 0，或者显示一个上限。*/
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION used;
    memset(&used, 0, sizeof(used));
    if (QueryInformationJobObject(g_job, JobObjectExtendedLimitInformation,
                                  &used, sizeof(used), NULL)) {
        peak_job = (unsigned long long)used.PeakJobMemoryUsed;
        peak_proc = (unsigned long long)used.PeakProcessMemoryUsed;
    }

    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION acct;
    memset(&acct, 0, sizeof(acct));
    if (QueryInformationJobObject(g_job, JobObjectBasicAccountingInformation,
                                  &acct, sizeof(acct), NULL)) {
        /* 100 纳秒 → 毫秒 */
        cpu_ms = (unsigned long long)
            ((acct.TotalUserTime.QuadPart + acct.TotalKernelTime.QuadPart) / 10000LL);
        /* TotalProcesses 是「这个 job 里累计创建过多少进程」。
           它比峰值有用：正常 C 题恒等于 1，出现 2 就说明程序尝试过
           CreateProcess（被配额挡住了，但尝试本身值得记一笔）。*/
        total_procs = (unsigned int)acct.TotalProcesses;
        active_procs = (unsigned int)acct.ActiveProcesses;
    }

    /* -- 8. 落盘 ------------------------------------------------------- */
    int out_limited = (InterlockedCompareExchange(&g_over_limit, 0, 0) != 0);

    if (o.out_path[0]) {
        FILE *f = _wfopen(o.out_path, L"wb");
        if (f) {
            if (rd_out.len) fwrite(rd_out.buf, 1, rd_out.len, f);
            fclose(f);
        }
    }
    if (o.err_path[0]) {
        FILE *f = _wfopen(o.err_path, L"wb");
        if (f) {
            if (rd_err.len) fwrite(rd_err.buf, 1, rd_err.len, f);
            fclose(f);
        }
    }

    report_t rep;
    memset(&rep, 0, sizeof(rep));
    rep.exit_code = (int)user_exit;
    rep.timed_out = timed_out;
    rep.output_limited = out_limited;
    rep.out_bytes = rd_out.total;
    rep.err_bytes = rd_err.total;
    rep.wall_ms = wall_ms;
    rep.cpu_ms = cpu_ms;
    rep.peak_job_mem = peak_job;
    rep.peak_proc_mem = peak_proc;
    rep.total_procs = total_procs;
    rep.active_procs = active_procs;
    /* note 走中文串而不是让 Python 去猜退出码：sandbox.py 的归因先看 note
       里有没有「CPU 时间超限」，再看比值。Linux 侧被 RLIMIT_CPU 杀掉时写的
       是「CPU 时间超限（RLIMIT_CPU）」，这里跟上，两侧走同一条判据。*/
    rep.note = cpu_quota_hit ? L"CPU 时间超限（job 配额）" : NULL;
    write_report(&o, &rep);

    free(rd_out.buf);
    free(rd_err.buf);
    CloseHandle(h_out_r);
    CloseHandle(h_err_r);
    if (h_in != INVALID_HANDLE_VALUE) CloseHandle(h_in);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    /* 先 KillOnClose 已经生效，再关 job 句柄。
       关句柄时内核会顺手清掉 job 里残留的进程——双保险。*/
    CloseHandle(g_job);

    if (timed_out) return 124;
    if (out_limited) return 125;
    return 0;
}
