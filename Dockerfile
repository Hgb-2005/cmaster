# ============================================================================
# CMaster —— 容器镜像
#
# 这个文件的职责很单一：把「一个能判题的 C 语言学习站」打包成可复现的一层，
# 并且把 README 里那句「别裸暴露公网」变成具体的参数，而不是一句提醒。
#
# 为什么基础镜像选 slim 而不是 alpine：
#   alpine 用 musl libc，glibc 用 glibc。用户写的 C 代码里 #include <pthread.h>
#   、strerror_r 的返回值类型、甚至 malloc 的行为都不一样——在 alpine 上
#   判成 WA 的代码，学生拿回自己的 Windows 上跑却是对的。判题机最不能有的
#   就是「和用户的机器不一样」这种偏差。runner_linux.c 还要 linux/audit.h
#   和 linux/seccomp.h，musl 上没有现成的，得额外装包。
#   代价是镜像从 ~50MB 变成 ~200MB，这个交换我认为划算。
# ============================================================================
FROM python:3.12-slim-bookworm

# PYTHONUNBUFFERED：容器里 stdout 默认是全缓冲，`docker logs` 看不到实时输出，
#   判题日志会攒到 4KB 才吐一次。这一条是排查「卡住了还是没卡住」的前提。
# PYTHONDONTWRITEBYTECODE：根文件系统是只读的（见 compose），写 __pycache__
#   会刷一屏 Permission denied；而 .pyc 对这个体量的启动速度没有意义。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CMASTER_PORT=5001 \
    CMASTER_CONCURRENCY=3

# gcc 不是「为了方便」装进来的，是功能依赖：没有它整个站一题都判不了。
# libc6-dev 必须显式写——slim 镜像里 --no-install-recommends 会把 gcc 的
# 推荐依赖（其中就有头文件）一起省掉，结果是 gcc 在、stdio.h 不在。
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：改代码不会让 pip 重装，docker build 的缓存才有意义。
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# ---------------------------------------------------------------------------
# 构建期就把沙箱宿主编出来，这一步是本文件里最重要的三行。
#
# 三个理由，从次要到主要：
#   1. 省掉首次判题时那 1 秒编译开销（运行期 ensure_runner() 会按 mtime
#      判断「已是最新」直接跳过）。
#   2. 运行期根文件系统是只读的，/app/sandbox 写不进去。不预编译的话，
#      运行时会走到「没有可执行文件 → 尝试编译 → 失败 → 降级 subprocess」，
#      而那条降级路径恰恰是我们最不想在生产里踩的。
#   3. 最重要：编译不过应该在 `docker build` 这一步炸掉，而不是变成
#      运行期的一次静默降级。runner_linux.c 里有 seccomp 的 BPF 宏，
#      换编译器/换 libc 就可能是「编得过但行为不同」或者「根本编不过」，
#      让构建失败是最便宜的发现方式。
#
# 标准号必须和 sandbox.py::ensure_runner() 里的一致（gnu11），
# 否则会出现「镜像能编、蹦到别的机器上 ensure_runner 编不过」的分裂。
# -Wall -Wextra 不加 -Werror：警告留着看，但不让一条无害的警告阻断构建。
# ---------------------------------------------------------------------------
RUN gcc -O2 -std=gnu11 -Wall -Wextra \
        -o sandbox/runner_linux sandbox/runner_linux.c \
 && ls -l sandbox/runner_linux

# 把 data/ 另存一份到 seed/，给 entrypoint 做「只补缺失、从不覆盖」的补种。
#
# 先把容易说错的那半句说清楚：**命名卷第一次挂载时，Docker 会把镜像里
# 该路径的内容复制进空卷**。所以按 compose 现在的写法，data/ 本来不会丢，
# 这一步不是「救命」，是兜底。
#
# 那 seed 到底给谁准备？给运维实际部署时会顺手做的两件事：
#   - 把挂载改成 bind mount（./data:/app/data），好直接在宿主机翻进度
#     文件。**bind mount 一个字节都不复制**，宿主机的空目录会原样盖住
#     镜像内容——页面能开，一道题都没有；
#   - 在 k8s 上挂 emptyDir，或者 docker run 时自己 -v 了个空目录。
#
# 多这几百 KB 换「换一种挂载方式也能开箱可用」，比让人对着一个空题库
# 排查半小时划算。这条注释宁可写长，是因为「为什么要有这行」如果写成
# 一个不成立的理由，下一个人会把它删掉。
RUN cp -a data /app/seed

# ---------------------------------------------------------------------------
# 非 root 运行。这一条不是形式主义：
#   容器默认是 root，而容器里跑的是**用户提交的 C 代码**。沙箱已经把
#   内存/进程/syscall 都限住了，但「万一还是逃出来」之后拿到的身份，
#   决定了它能不能碰宿主机的挂载卷、能不能改 /app 里的代码。
#   降成普通用户之后，最坏情况也只是容器内的一个无名用户。
#
# uid 10001 而不是 1000：1000 在不少宿主机上已经是第一个普通用户，
# 容器里再抢同一个号，在共享卷上会出现「文件属主看起来是自己、
# 实际写不进去」的迷惑现象。
#
# 附带后果（写在代码里，不藏）：runner_linux.c 的 setuid(nobody) 那一层
#   在非 root 时会自动跳过（见源码里「非 root 时跳过」那句）。这不是
#   少了一层防护，而是这一层已经在镜像里做过了——容器进程本来就是普通
#   用户，不需要在子进程里再降一次。分层要能说清楚，不能算两遍。
# ---------------------------------------------------------------------------
RUN groupadd -g 10001 cmaster \
 && useradd -u 10001 -g cmaster -M -s /usr/sbin/nologin cmaster \
 && mkdir -p /app/data \
 && chown -R cmaster:cmaster /app/data

# 不用 COPY --chmod：那个语法依赖 BuildKit，而「构建能不能成功」不该
# 取决于宿主机的 docker 版本。一行 RUN chmod 在任何 builder 上都一样。
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER 10001:10001

EXPOSE 5001

# ---------------------------------------------------------------------------
# 健康检查只用 /healthz（存活），**刻意不用 /readyz**。
#
# 这是整套部署里最容易做错的一处：Docker 的 HEALTHCHECK 在 swarm 里会
# 直接驱动「重建容器」这个动作，autoheal 之类的工具也一样。如果把
# readiness 塞进来，那么「gcc 没装」这种**重启治不好**的问题会让容器
# 被反复重建，从一个故障变成一串故障，而且日志被重启冲掉，更难查。
#
# 分工是：
#   /healthz  进程还活着吗  → 交给 docker（要不要重启）
#   /readyz   能不能真的判题 → 交给反向代理 / k8s readinessProbe / 监控
# 后者用 `docker compose exec cmaster curl -f localhost:5001/readyz` 或者
# 挂在网关的 /readyz 上，见 README「健康检查」一节。
#
# slim 镜像里没有 curl，所以用 python 标准库发这个请求——多装一个 curl
# 只为了探针不值得，而且 libcurl 会带进一串依赖。
# ---------------------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=2).status == 200 else 1)"]

# exec 形式 + 让 python 接 SIGTERM：shell 形式会让 PID 1 变成 sh，
# docker stop 时 SIGTERM 打在 sh 上，python 收不到，10 秒后被 SIGKILL——
# 正在跑的判题线程直接消失，用户看到的是「提交了但没结果」。
STOPSIGNAL SIGTERM

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py"]
