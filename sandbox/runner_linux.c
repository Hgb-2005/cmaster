/* ============================================================================
 * runner_linux.c —— 判题沙箱宿主（Linux）
 *
 * 和 runner_win.c 是同一件事的两个实现，**输出格式严格一致**（report 的
 * key 名、单位、语义都对齐），这样 judge.py 只有一份报告解析逻辑。
 * 平台差异只存在于这个文件内部，不向上泄漏。
 *
 * 为什么不直接上 docker / nsjail / firejail：
 *   这个站要能在没有 Docker 的机器上跑（比如一台只装了 gcc 的跳板机），
 *   而 nsjail 要装 libprotobuf、firejail 要 setuid 且默认策略很宽。
 *   手写 400 行换「零依赖、可读、每个 syscall 都说得清为什么」，
 *   对这个项目的规模是划算的。生产环境该用 nsjail——本文件顶上的
 *   README 里也是这么写的，不装作自己是生产级。
 *
 * Linux 侧比 Windows 侧多了什么（也是 Windows 侧做不到的）：
 *   **能把系统调用一个个掐掉**。Windows 的 Job Object 只能限资源，
 *   挡不住「读 /etc/shadow」「连外网」「ptrace 别的进程」这类行为——
 *   那边的对策是进程权限（普通用户 + 文件 ACL），不是 syscall 过滤。
 *   Linux 有 seccomp-bpf，内核在 syscall 入口做过滤，进程自己改不了。
 *   这就是为什么「容器隔离」在 Linux 上是这个形状：namespace 管看得见什么，
 *   cgroup 管能用多少，seccomp 管能做什么——三件事，三个子系统。
 *   这里做的是第三件 + rlimit 版的第二件。
 *
 * 隔离层次（从外到内）：
 *   1. setpgid + PR_SET_PDEATHSIG   进程拓扑：父死子必死，一次杀全家
 *   2. setrlimit                    资源量：CPU / 内存 / 文件大小 / 进程数 / fd
 *   3. setgid/setuid nobody         权限：从 root 降下去（非 root 时跳过）
 *   4. PR_SET_NO_NEW_PRIVS          提权：setuid 二进制也提不了权
 *   5. seccomp-bpf                  行为：白名单外的危险 syscall 直接 EPERM
 *
 * 为什么 4 必须在 5 之前：
 *   no_new_privs 是 unprivileged seccomp 的前置条件。没设它，装过滤器要
 *   CAP_SYS_ADMIN；设了它，任何进程都能给自己装过滤器，而且装完不可撤销。
 *   这个顺序反了会得到 EACCES，然后过滤器静默没装上——最坏的降级：
 *   看上去跑了，实际一点隔离都没有。所以下面某处失败一律写进 report
 *   的 note，让 /admin 能看见「这台机器上沙箱其实没生效」。
 *
 * 编译：
 *     gcc -O2 -std=gnu11 -Wall -Wextra -o runner_linux runner_linux.c
 *   （用 gnu11 而不是 c11：seccomp 的 BPF 宏要 GNU 扩展；
 *     不链接 libseccomp，避免多一个运行时依赖）
 *
 * 退出码约定与 Windows 版完全相同：0 / 124 / 125 / 126 / 127。
 *
 * ⚠️ 诚实声明：这个文件编写时手边没有 Linux 环境（开发机是 Windows，
 *    WSL 被策略禁用），所以**它没有被真跑过**。补偿办法不是加注释辩解，
 *    而是让代码自己在目标平台上证伪：sandbox.py 提供 selftest()，
 *    在 Linux 上会真编译四个越界程序（死循环 / malloc 炸弹 / system 逃逸 /
 *    输出炸弹）跑一遍，结果直接显示在 /admin 上。第一次部署时点一下
 *    自检，四项全绿才算这个文件成立；红了就按 note 改。
 * ========================================================================== */

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* ------------------------------------------------------------------ 选项 */

#define DEF_MS 3000
#define DEF_MEM (256ull * 1024 * 1024)
#define DEF_PROC 32
#define DEF_OUT (64 * 1024)
#define DEF_ERR (16 * 1024)

/* 判定「超时」时给的容忍，必须和 runner_win.c 里的同名宏一致。
   两边不一致的后果：同一份代码在 Windows 上 AC、在容器里 TLE，
   而这种差异在报告里看不出来，只能靠人去翻两个源码文件对数字。
   Linux 侧本来一点宽限都没有（poll 循环最多睡 20ms，是个天然的小宽限），
   但 20ms 和 150ms 不是一回事，所以这里显式对齐。*/
#define LIMIT_GRACE_MS 150

typedef struct {
    char prog[1024];
    char *args[256];
    int nargs;

    char in_path[1024];
    char out_path[1024];
    char err_path[1024];
    char report_path[1024];
    char cwd[1024];
    char run_as[64];            /* 降权目标用户，空 = 不降权 */

    unsigned long long limit_ms;
    unsigned long long limit_mem;
    unsigned long long limit_proc;
    unsigned long long limit_cpu_ms;
    unsigned long long limit_out;
    unsigned long long limit_err;

    int has_in;
    int no_seccomp;
} opts_t;

/* 全局：输出超限时读循环要能杀掉整个进程组 */
static volatile sig_atomic_t g_over_limit = 0;
static pid_t g_child = -1;

static void die(int code, const char *msg) {
    fprintf(stderr, "[runner] %s\n", msg);
    exit(code);
}

static unsigned long long parse_u64(const char *s) {
    if (!s || !*s) return 0;
    return strtoull(s, NULL, 10);
}

static void copy_str(char *dst, const char *src, size_t cap) {
    if (!src) return;
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

static void parse_args(int argc, char **argv, opts_t *o) {
    memset(o, 0, sizeof(*o));
    o->limit_ms = DEF_MS;
    o->limit_mem = DEF_MEM;
    o->limit_proc = DEF_PROC;
    o->limit_out = DEF_OUT;
    o->limit_err = DEF_ERR;

    int i = 1;
    for (; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) { i++; break; }
        if (strncmp(argv[i], "--limit-ms=", 11) == 0) {
            o->limit_ms = parse_u64(argv[i] + 11);
        } else if (strncmp(argv[i], "--limit-mem=", 12) == 0) {
            o->limit_mem = parse_u64(argv[i] + 12);
        } else if (strncmp(argv[i], "--limit-proc=", 13) == 0) {
            o->limit_proc = parse_u64(argv[i] + 13);
        } else if (strncmp(argv[i], "--limit-cpu-ms=", 15) == 0) {
            o->limit_cpu_ms = parse_u64(argv[i] + 15);
        } else if (strncmp(argv[i], "--limit-out=", 12) == 0) {
            o->limit_out = parse_u64(argv[i] + 12);
        } else if (strncmp(argv[i], "--limit-err=", 12) == 0) {
            o->limit_err = parse_u64(argv[i] + 12);
        } else if (strncmp(argv[i], "--in=", 5) == 0) {
            copy_str(o->in_path, argv[i] + 5, sizeof(o->in_path));
            o->has_in = 1;
        } else if (strncmp(argv[i], "--out=", 6) == 0) {
            copy_str(o->out_path, argv[i] + 6, sizeof(o->out_path));
        } else if (strncmp(argv[i], "--err=", 6) == 0) {
            copy_str(o->err_path, argv[i] + 6, sizeof(o->err_path));
        } else if (strncmp(argv[i], "--report=", 9) == 0) {
            copy_str(o->report_path, argv[i] + 9, sizeof(o->report_path));
        } else if (strncmp(argv[i], "--cwd=", 6) == 0) {
            copy_str(o->cwd, argv[i] + 6, sizeof(o->cwd));
        } else if (strncmp(argv[i], "--as=", 5) == 0) {
            copy_str(o->run_as, argv[i] + 5, sizeof(o->run_as));
        } else if (strcmp(argv[i], "--no-seccomp") == 0) {
            o->no_seccomp = 1;
        } else {
            fprintf(stderr, "[runner] 未知参数: %s\n", argv[i]);
            exit(127);
        }
    }

    if (i >= argc) die(127, "没有指定要运行的程序（用 -- 分隔）");
    copy_str(o->prog, argv[i], sizeof(o->prog));

    o->nargs = 0;
    for (int k = i; k < argc && o->nargs < 254; k++) o->args[o->nargs++] = argv[k];
    o->args[o->nargs] = NULL;

    if (o->limit_ms == 0) o->limit_ms = DEF_MS;
    if (o->limit_cpu_ms == 0) o->limit_cpu_ms = o->limit_ms * 3 / 2 + 1000;
    if (!o->report_path[0]) die(127, "缺少 --report=");
}

/* ------------------------------------------------------------------ 报告 */

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
    const char *note;
} report_t;

static void write_report(const opts_t *o, const report_t *r) {
    if (!o->report_path[0]) return;
    FILE *f = fopen(o->report_path, "wb");
    if (!f) return;
    /* key 名和 runner_win.c 一字不差。这不是巧合，是契约：
       judge.py 里那份解析器不区分平台，改一边不改另一边就会让
       整个平台上的沙箱静默降级。*/
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
    fprintf(f, "limit_ms=%llu\n", o->limit_ms);
    fprintf(f, "limit_mem=%llu\n", o->limit_mem);
    fprintf(f, "limit_proc=%llu\n", o->limit_proc);
    fprintf(f, "limit_cpu_ms=%llu\n", o->limit_cpu_ms);
    if (r->note && *r->note) fprintf(f, "note=%s\n", r->note);
    fclose(f);
}

static void report_fail(const opts_t *o, const char *note) {
    report_t r;
    memset(&r, 0, sizeof(r));
    r.exit_code = -1;
    r.note = note;
    write_report(o, &r);
}

/* ------------------------------------------------------------------ seccomp */

/*
 * 过滤器结构（BPF 程序，内核在 syscall 入口执行）：
 *
 *   [0] 读 arch，不等于 x86_64 → KILL
 *   [1] 读 syscall nr
 *   [2..] 逐个比较黑名单，命中 → ERRNO(EPERM)
 *   [n] ALLOW
 *
 * 为什么是黑名单而不是白名单：
 *   白名单（只放 read/write/exit/mmap/brk…）更安全，但 glibc 的启动路径
 *   会摸到二十几个 syscall（arch_prctl、set_tid_address、rseq、getrandom、
 *   newfstatat…），每个 libc 版本还不一样。白名单写窄了是「程序莫名其妙
 *   起来就死」，写宽了等于没写，而且每换一个 glibc 就要重新调一遍。
 *   判题场景里用户程序是「一个 main + 标准库」，真正危险的行为很集中
 *   （执行别的程序、开网络、调试别人、动内核），黑名单能精确覆盖它们。
 *   nsjail 用白名单是因为它面对的是任意不可信代码；我们面对的是 C 课后题，
 *   威胁模型不同，所以选择不同。这个区别要写清楚，不能含糊过去。
 *
 * ERRNO 而不是 KILL：
 *   返回 EPERM 让程序自己拿到「权限不足」，能打印错误、能正常退出，
 *   判题时表现为 RE 而不是「被信号杀死」——后者在 UI 上很难解释。
 *   只有 arch 不匹配才用 KILL，因为那意味着有人在用 x32 ABI 绕过检查，
 *   这不是「权限问题」而是「攻击」。
 */

#if defined(__x86_64__)

#define SC_DENY(nr) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA))

static int install_seccomp_filter(int *out_errno) {
    struct sock_filter filter[] = {
        /* 架构必须是原生 x86_64。不查这个，攻击者可以用 x32 ABI
           （syscall 号带 0x40000000 前缀）让下面的比较全部落空。*/
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                 (uint32_t)offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),

        BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                 (uint32_t)offsetof(struct seccomp_data, nr)),

        /* — 执行别的程序 ——————————————————————————————
           system() / exec 家族。判题只需要跑用户编译出来的那一个 elf，
           任何二次 exec 都是越界：能 exec 就能起 sh，能起 sh 就能
           cat /etc/passwd。这是最要紧的一条。*/
        SC_DENY(__NR_execve),
        SC_DENY(__NR_execveat),

        /* — 派生进程 ————————————————————————————————
           禁 fork/vfork/clone。C 基础题不需要多进程；真要写多线程的题，
           在题目配置里单独放行（--no-seccomp + rlimit 兜底）。
           fork 炸弹的破坏力全在后代上，堵住入口比限制数量彻底。*/
        SC_DENY(__NR_fork),
        SC_DENY(__NR_vfork),
        SC_DENY(__NR_clone),
#ifdef __NR_clone3
        SC_DENY(__NR_clone3),
#endif

        /* — 网络 ——————————————————————————————————
           判题不需要联网。socket 一开，用户代码就能外联、能当代理、
           能打内网。整组堵死，连 DNS 一起。*/
        SC_DENY(__NR_socket),
        SC_DENY(__NR_socketpair),
        SC_DENY(__NR_connect),
        SC_DENY(__NR_bind),
        SC_DENY(__NR_listen),
        SC_DENY(__NR_accept),
        SC_DENY(__NR_accept4),
        SC_DENY(__NR_sendto),
        SC_DENY(__NR_recvfrom),
        SC_DENY(__NR_sendmsg),
        SC_DENY(__NR_recvmsg),

        /* — 干涉别的进程 ————————————————————————————
           ptrace 能读写别人的内存（包括宿主 Flask 进程里的 session 密钥）；
           kill 能打死宿主的 worker；process_vm_* 同理。
           注意 tgkill 没禁：abort() 用它给自己发信号，禁了整个 libc
           的错误处理路径会炸，而它只能作用于调用者自己所在的线程组。*/
        SC_DENY(__NR_ptrace),
        SC_DENY(__NR_process_vm_readv),
        SC_DENY(__NR_process_vm_writev),
        SC_DENY(__NR_kill),
        SC_DENY(__NR_tkill),

        /* — 文件系统结构 ————————————————————————————
           挂载、改根、改挂载传播。chroot 单独列出来：它是经典逃逸第
           一步（chroot 到自己的目录再 chdir("..") 出去）。*/
        SC_DENY(__NR_mount),
        SC_DENY(__NR_umount2),
        SC_DENY(__NR_pivot_root),
        SC_DENY(__NR_chroot),
        SC_DENY(__NR_unshare),
        SC_DENY(__NR_setns),
        SC_DENY(__NR_open_tree),
        SC_DENY(__NR_move_mount),
        /* open_by_handle_at 是 CAP_DAC_READ_SEARCH 的历史漏洞点：
           拿到一个 file handle 就能绕过路径权限直接开文件。*/
        SC_DENY(__NR_open_by_handle_at),

        /* — 权限与身份 ——————————————————————————————
           本来 no_new_privs 已经让 setuid 失效，但显式拒绝能让
           「尝试提权」这件事在 strace 里留下清楚的痕迹。*/
        SC_DENY(__NR_setuid),
        SC_DENY(__NR_setgid),
        SC_DENY(__NR_setreuid),
        SC_DENY(__NR_setregid),
        SC_DENY(__NR_setresuid),
        SC_DENY(__NR_setresgid),
        SC_DENY(__NR_setfsuid),
        SC_DENY(__NR_setfsgid),
        SC_DENY(__NR_setgroups),
        SC_DENY(__NR_chown),
        SC_DENY(__NR_fchown),
        SC_DENY(__NR_lchown),
        SC_DENY(__NR_chmod),
        SC_DENY(__NR_fchmod),

        /* — 内核与系统 ——————————————————————————————
           加载模块、改时钟、换盘、重启、装 BPF 程序、碰密钥环。
           一个 C 课后题没有理由触到其中任何一个。*/
        SC_DENY(__NR_init_module),
        SC_DENY(__NR_finit_module),
        SC_DENY(__NR_delete_module),
        SC_DENY(__NR_swapon),
        SC_DENY(__NR_swapoff),
        SC_DENY(__NR_reboot),
        SC_DENY(__NR_acct),
        SC_DENY(__NR_settimeofday),
        SC_DENY(__NR_clock_settime),
        SC_DENY(__NR_adjtimex),
        SC_DENY(__NR_bpf),
        SC_DENY(__NR_keyctl),
        SC_DENY(__NR_add_key),
        SC_DENY(__NR_request_key),
        SC_DENY(__NR_sethostname),
        SC_DENY(__NR_setdomainname),
        SC_DENY(__NR_syslog),
        SC_DENY(__NR_setpgid),
        SC_DENY(__NR_setsid),

        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };

    struct sock_fprog prog;
    prog.len = (unsigned short)(sizeof(filter) / sizeof(filter[0]));
    prog.filter = filter;

    /* no_new_privs 必须是先决条件，见文件头「为什么 4 必须在 5 之前」 */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        *out_errno = errno;
        return -1;
    }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) != 0) {
        *out_errno = errno;
        return -1;
    }
    return 0;
}

#else
/* 非 x86_64（arm64 等）上 syscall 号不同，下面这套常量不能照搬。
   与其编一个「看起来装了过滤器、实际对不上号」的东西，不如明确不装，
   靠 rlimit + 降权兜底，并在 report 里写清楚。*/
static int install_seccomp_filter(int *out_errno) {
    *out_errno = ENOSYS;
    return -1;
}
#endif

/* ------------------------------------------------------------------ 子进程 */

static void child_setup_and_exec(const opts_t *o,
                                 int in_fd, int out_fd, int err_fd,
                                 int *err_pipe) {
    /* 1. 独立进程组：父进程 kill(-pid) 一次带走整组，
          不用递归找孙进程。这比 /proc 遍历可靠。*/
    setpgid(0, 0);

    /* 2. 父死子死。注意 PDEATHSIG 的经典竞态：如果父进程在 fork 之后、
          prctl 之前就死了，这个信号永远不会发（父进程已经不存在，
          内核发信号时找不到目标）。所以设完之后要再查一次 getppid。*/
    prctl(PR_SET_PDEATHSIG, SIGKILL);
    if (getppid() == 1) _exit(126);

    /* 3. 重定向标准流 */
    if (in_fd >= 0) { dup2(in_fd, 0); close(in_fd); }
    dup2(out_fd, 1);
    dup2(err_fd, 2);
    if (out_fd > 2) close(out_fd);
    if (err_fd > 2) close(err_fd);

    /* 4. 关掉 3 号以上的所有 fd：宿主的 socket、日志句柄、数据文件
          不能漏进用户进程。这是「最小特权」里最容易忘的一条——
          一个继承来的 fd 就能让用户代码读写 progress 文件。*/
    for (int fd = 3; fd < 1024; fd++) close(fd);

    if (o->cwd[0]) {
        if (chdir(o->cwd) != 0) { *err_pipe = errno; _exit(126); }
    }

    /* 5. rlimit。顺序无所谓，但每一项都要设，因为子进程继承的是
          宿主（Flask）的限制——宿主通常是不限的。*/
    struct rlimit rl;
    /* RLIMIT_CPU 是秒。soft 比墙钟略大：墙钟超时由父进程负责，
       这里是 CPU 时间的兜底（纯计算死循环可以比墙钟更早掐断）。
       超了内核发 SIGXCPU，默认动作是终止进程。*/
    unsigned long long cpu_s = (o->limit_cpu_ms + 999) / 1000;
    if (cpu_s == 0) cpu_s = 1;
    rl.rlim_cur = (rlim_t)cpu_s;
    rl.rlim_max = (rlim_t)(cpu_s + 1);
    setrlimit(RLIMIT_CPU, &rl);

    if (o->limit_mem) {
        /* RLIMIT_AS 限的是虚拟地址空间，比 RSS 严——mmap 一片大区域
           即使不写也会失败。判题要的正是这个：不让它有钱包地。
           代价是 glibc 的 arena 预留会占一部分，所以真正可用的堆比
           限额小几十 MB。这个差额要记住，别把限额卡在题目刚好需要的位置上。*/
        rl.rlim_cur = rl.rlim_max = (rlim_t)o->limit_mem;
        setrlimit(RLIMIT_AS, &rl);
    }

    /* RLIMIT_FSIZE：写文件上限。防 `fopen("a","w")` 死循环把盘写满——
       管道有输出限流，文件没有，这条是它唯一的拦法。超了内核发 SIGXFSZ。*/
    rl.rlim_cur = rl.rlim_max = 8ull * 1024 * 1024;
    setrlimit(RLIMIT_FSIZE, &rl);

    if (o->limit_proc) {
        rl.rlim_cur = rl.rlim_max = (rlim_t)o->limit_proc;
        setrlimit(RLIMIT_NPROC, &rl);
    }

    rl.rlim_cur = rl.rlim_max = 64;
    setrlimit(RLIMIT_NOFILE, &rl);

    /* 不产 core：一个段错误的程序能 dump 出几百 MB，
       几道题就把磁盘塞满，而且 core 里可能带着宿主的堆内存。*/
    rl.rlim_cur = rl.rlim_max = 0;
    setrlimit(RLIMIT_CORE, &rl);

    rl.rlim_cur = rl.rlim_max = 16ull * 1024 * 1024;
    setrlimit(RLIMIT_STACK, &rl);

    /* 6. 降权。只有以 root 跑的时候才做得了；普通用户跑不动 setuid，
          跳过即可（此时进程本来就没有特权，等于已经降到位）。
          先 setgid 再 setuid——反过来的话 setgid 会因为没有特权失败，
          留下一个「uid 对了、gid 还是 root」的半吊子状态。
          目标固定用 65534（nobody/nogroup）：不查 NSS 是有意的，
          容器里 NSS 走 LDAP 超时会卡好几秒，判题路径上不该有这种
          不确定性。*/
    if (o->run_as[0] && geteuid() == 0) {
        if (setgid(65534) != 0 || setuid(65534) != 0) {
            *err_pipe = errno;
            _exit(126);
        }
    }

    /* 7. no_new_privs + seccomp */
    if (!o->no_seccomp) {
        int se = 0;
        if (install_seccomp_filter(&se) != 0) {
            *err_pipe = se ? se : EPERM;
            _exit(126);
        }
    }

    execv(o->prog, o->args);
    *err_pipe = errno;
    _exit(127);
}

/* ------------------------------------------------------------------ 主流程 */

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

/* 读循环：两个管道一起 poll，谁有数据读谁。单线程就够——
   因为 poll 不阻塞在某个管道上，一个线程能同时盯两个。*/
typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    unsigned long long total;
    unsigned long long limit;
} sink_t;

static void drain(int fd, sink_t *s, int *open_flag) {
    char chunk[8192];
    ssize_t n = read(fd, chunk, sizeof(chunk));
    if (n > 0) {
        s->total += (unsigned long long)n;
        if (s->len < s->cap) {
            size_t room = s->cap - s->len;
            size_t take = (size_t)n < room ? (size_t)n : room;
            memcpy(s->buf + s->len, chunk, take);
            s->len += take;
        }
        if (s->limit && s->total > s->limit && !g_over_limit) {
            /* 超限后继续读但不再留——不读的话管道满了子进程会阻塞在
               write 上，永远不退出，反而比不限制更糟。*/
            g_over_limit = 1;
            if (g_child > 0) kill(-g_child, SIGKILL);
        }
    } else if (n == 0) {
        *open_flag = 0;
    } else if (errno != EINTR && errno != EAGAIN) {
        *open_flag = 0;
    }
}

int main(int argc, char **argv) {
    opts_t o;
    parse_args(argc, argv, &o);

    int in_fd = -1;
    if (o.has_in && o.in_path[0]) {
        in_fd = open(o.in_path, O_RDONLY);
    }
    if (in_fd < 0) in_fd = open("/dev/null", O_RDONLY);

    int out_p[2], err_p[2], err_pipe[2];
    if (pipe(out_p) != 0 || pipe(err_p) != 0 || pipe(err_pipe) != 0) {
        report_fail(&o, "pipe() 失败");
        die(126, "创建管道失败");
    }

    double t0 = now_ms();

    pid_t pid = fork();
    if (pid < 0) {
        report_fail(&o, "fork() 失败");
        die(126, "fork 失败");
    }

    if (pid == 0) {
        /* 子进程 */
        close(out_p[0]);
        close(err_p[0]);
        close(err_pipe[0]);
        child_setup_and_exec(&o, in_fd, out_p[1], err_p[1], &err_pipe[1]);
        _exit(127);   /* 不会到这里 */
    }

    /* 父进程 */
    g_child = pid;
    close(out_p[1]);
    close(err_p[1]);
    if (in_fd >= 0) close(in_fd);

    sink_t so, se;
    memset(&so, 0, sizeof(so));
    memset(&se, 0, sizeof(se));
    so.cap = o.limit_out ? (size_t)o.limit_out + 4096 : (size_t)DEF_OUT;
    se.cap = o.limit_err ? (size_t)o.limit_err + 4096 : (size_t)DEF_ERR;
    so.limit = o.limit_out;
    se.limit = o.limit_err;
    so.buf = malloc(so.cap);
    se.buf = malloc(se.cap);

    int out_open = 1, err_open = 1;
    int timed_out = 0, setup_failed = 0, setup_errno = 0;

    struct pollfd fds[3];
    int nfds_before = 0;
    (void)nfds_before;

    for (;;) {
        double elapsed = now_ms() - t0;
        if (elapsed >= (double)(o.limit_ms + LIMIT_GRACE_MS)) { timed_out = 1; break; }
        if (!out_open && !err_open) break;

        int n = 0;
        if (out_open) { fds[n].fd = out_p[0]; fds[n].events = POLLIN; n++; }
        if (err_open) { fds[n].fd = err_p[0]; fds[n].events = POLLIN; n++; }
        fds[n].fd = err_pipe[0]; fds[n].events = POLLIN; n++;

        /* 轮询间隔 20ms：够快（判题耗时的分辨率本来就在毫秒级），
           又不至于空转烧 CPU。用「剩余时间」当超时上限，避免睡过头。
           剩余时间按**含宽限的**上限算，否则宽限那 150ms 会被拆成
           一串 1ms 的忙轮询——既烧 CPU，又让 wall_ms 比实际晚一点。*/
        double remain = (double)(o.limit_ms + LIMIT_GRACE_MS) - elapsed;
        int wait_ms = remain < 20 ? (int)remain + 1 : 20;
        if (wait_ms < 1) wait_ms = 1;

        int pr = poll(fds, (nfds_t)n, wait_ms);
        if (pr < 0 && errno != EINTR) break;

        int idx = 0;
        if (out_open) { drain(fds[idx].fd, &so, &out_open); idx++; }
        if (err_open) { drain(fds[idx].fd, &se, &err_open); idx++; }

        /* 子进程在 seccomp 之前就失败时，会通过 err_pipe 把 errno 送过来。
           这条通道是必需的：如果只靠 stderr 里的文字，Python 侧就得猜
           「这行报错是沙箱没装成，还是用户代码自己打印的」——猜错就会
           在真出问题的时候当成正常结果。*/
        if (fds[idx].revents & POLLIN) {
            char c = 0;
            if (read(fds[idx].fd, &c, 1) == 1) { setup_failed = 1; setup_errno = c; }
        }
    }

    /* 墙钟在这里定格：这一刻是「进程结束」或「被判超时」的真实时刻。
       后面还有 kill + wait4 的收尸时间，那是我们自己的开销，不该记到
       用户程序头上——300ms 的时限里它能多记出几十毫秒，而归因正是拿
       wall 当分母的，分母错了，「算得慢」会被算成「在等」。
       Windows 侧同一个位置打了点（runner_win.c 的 t_end）。*/
    double wall_ms = now_ms() - t0;

    if (timed_out || g_over_limit) {
        kill(-pid, SIGKILL);
        kill(pid, SIGKILL);
    }

    int status = 0;
    struct rusage ru;
    memset(&ru, 0, sizeof(ru));
    wait4(pid, &status, 0, &ru);


    /* 收尾：把管道里剩下的读干净（子进程已死，只会读到 EOF）。
       不读的话 report 里的字节数是半截的，而「输出被截断」和
       「输出就这么多」在判题里是两回事。*/
    while (out_open) drain(out_p[0], &so, &out_open);
    while (err_open) drain(err_p[0], &se, &err_open);

    if (o.out_path[0]) {
        FILE *f = fopen(o.out_path, "wb");
        if (f) { if (so.len) fwrite(so.buf, 1, so.len, f); fclose(f); }
    }
    if (o.err_path[0]) {
        FILE *f = fopen(o.err_path, "wb");
        if (f) { if (se.len) fwrite(se.buf, 1, se.len, f); fclose(f); }
    }

    /* 退出码语义：
       · 正常退出 → WEXITSTATUS
       · 被信号杀 → 256 + 信号号（和 shell 的约定一致，且和正常退出码不撞）
       · 被 SIGXCPU 杀 → 记为超时（这是 CPU 时间超限，不是程序 bug）
       · 被 SIGXFSZ 杀 → 记为输出超限（文件写超了）*/
    int exit_code;
    int killed_by_cpu = 0;
    if (WIFSIGNALED(status)) {
        int sig = WTERMSIG(status);
        exit_code = 256 + sig;
        if (sig == SIGXCPU) { killed_by_cpu = 1; timed_out = 1; }
        if (sig == SIGXFSZ) { g_over_limit = 1; }
    } else {
        exit_code = WEXITSTATUS(status);
    }

    char note[256];
    note[0] = '\0';
    if (setup_failed) {
        /* 这个是**必须**暴露的降级：沙箱没装上，用户程序根本没跑。
           判题器看到 note 里带 SANDBOX_SETUP_FAILED 就不该把它当成
           用户代码的错，而应该按 SE 处理并提示运维。*/
        snprintf(note, sizeof(note),
                 "SANDBOX_SETUP_FAILED errno=%d(%s)", setup_errno,
                 strerror(setup_errno));
    } else if (killed_by_cpu) {
        snprintf(note, sizeof(note), "CPU 时间超限（RLIMIT_CPU）");
    }

    report_t r;
    memset(&r, 0, sizeof(r));
    r.exit_code = exit_code;
    r.timed_out = timed_out;
    r.output_limited = g_over_limit ? 1 : 0;
    r.out_bytes = so.total;
    r.err_bytes = se.total;
    r.wall_ms = wall_ms;
    /* rusage 是内核记的真账：用户态 + 内核态 CPU 时间，
       和 Windows 侧的 TotalUserTime + TotalKernelTime 语义一致。*/
    r.cpu_ms = (unsigned long long)((ru.ru_utime.tv_sec + ru.ru_stime.tv_sec) * 1000
                                    + (ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1000);
    /* ru_maxrss 在 Linux 上是 KB（macOS 上是字节，这里只支持 Linux） */
    r.peak_job_mem = (unsigned long long)ru.ru_maxrss * 1024ull;
    r.peak_proc_mem = r.peak_job_mem;
    r.total_procs = 1;      /* fork 被 seccomp 挡了，成功执行就是 1 */
    r.active_procs = 0;
    r.note = note[0] ? note : NULL;
    write_report(&o, &r);

    free(so.buf);
    free(se.buf);

    if (setup_failed) return 126;
    if (timed_out) return 124;
    if (g_over_limit) return 125;
    return 0;
}
