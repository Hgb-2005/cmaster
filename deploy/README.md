# CMaster 完整在线版部署

本目录提供保留注册登录、学习记录、笔记、训练和真实 GCC 判题的部署方案。
它需要一台 Linux 服务器；把源码放到 GitHub 或 GitHub Pages 不会启动后端。
尚未开通服务器和域名时，没有可替换作品集入口的公网地址。

## 准备资源

个人展示阶段建议从 **Ubuntu 24.04 LTS、x86_64、2 核 CPU、4 GB 内存、至少 20 GB 磁盘**开始。
这是本方案的起步配置建议，不是并发容量承诺。判题一次处理一个提交，其余请求短暂等待；JSON 存储必须保持单 Web worker。

需要自己注册云平台账号、确认费用后创建服务器，并准备解析到服务器公网 IP 的域名或子域名。
只需将该域名的 A 记录指向服务器；已有 IPv6 记录时，也要指向可达的同一服务器。
允许入站 TCP 80、443；SSH 端口按云平台的管理方式设置，尽量只允许自己的地址访问。
不开放 5001、8080、Docker API 或判题服务端口。

不要把云平台密码、访问密钥、SSH 私钥、网站数据备份发到聊天或提交到 GitHub。
账号、服务器购买及付费确认由本人完成；后续部署通过已登录终端或安全认证操作。

## 安装与启动

以下命令在 **Linux 服务器**执行，不在 Windows 本机的 PowerShell 执行。
先按 [Docker 官方 Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/)安装 Docker Engine 和 Compose 插件。
确认 `docker version` 与 `docker compose version` 能正常返回。

```bash
git clone https://github.com/Hgb-2005/cmaster.git
cd cmaster
cp deploy/.env.example deploy/.env
nano deploy/.env
```

把 `SITE_HOST=cmaster.example.com` 改为自己的真实域名，只写域名，例如 `learn.your-domain.com`，不带协议和路径。
DNS 生效后构建 **三个镜像**，再启动三个常驻服务：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yml --profile build build
docker compose --env-file deploy/.env -f deploy/compose.yml up -d gateway web broker
docker compose --env-file deploy/.env -f deploy/compose.yml ps
```

不要漏掉 `--profile build build`：其中包含临时判题使用的镜像。首次启动由 Caddy 申请 HTTPS 证书，需要域名已正确解析且 80/443 可达。

浏览器打开 `https://你的域名/`。如果申请证书失败，先查看日志和 DNS：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yml logs --tail=100 gateway web broker
```

## 上线验收

1. 首页、课程、题库能正常加载，`/healthz` 返回 200。
2. `/readyz` 返回 200，`isolated_judge` 和 `persistent_data` 都为 `true`。
3. 注册自己的账号，运行游乐场的 Hello World；在线版运行代码需先登录。
4. 提交 `c001` 的正确答案得到 AC，故意写错得到 WA，编译错误得到 CE，死循环得到 TLE。
5. 阅读一节课、保存笔记、完成训练，确认个人中心和记录导出可用。
6. 执行下方重建命令，确认原账号可以登录，原 Cookie、通过记录、阅读和笔记仍保留。

```bash
docker compose --env-file deploy/.env -f deploy/compose.yml up -d --force-recreate web broker
```

验收成功后，才把个人作品集 `cmaster.url` 改成真实的 HTTPS 地址。不要使用 localhost、127.0.0.1 或本机局域网地址作为公开入口。

## 数据、备份与更新

`cmaster-online_learning-data` 持久卷保存账号口令哈希、Session 密钥和学习记录。
课程题库随镜像发布，重建镜像保留上述数据。本机已有记录不会自动上传；如需迁移，先备份并单独安排。

下面在暂停写入后生成一次私密备份，路径是 `deploy/backups/learning-data.tar.gz`。它已被 Git 和 Docker 构建忽略。
重新备份时请先将上一份文件另存到带日期的安全位置，避免覆盖。

```bash
mkdir -p deploy/backups
chmod 700 deploy/backups
docker compose --env-file deploy/.env -f deploy/compose.yml stop web
(umask 077; docker compose --env-file deploy/.env -f deploy/compose.yml run --rm --no-deps -T web tar czf - -C /var/lib/cmaster . > deploy/backups/learning-data.tar.gz)
docker compose --env-file deploy/.env -f deploy/compose.yml start web
```

将备份另存到私密位置。恢复时先停止 Web 并备份现有数据，再把确认过的备份挂载到 `/backup:ro`，通过维护容器解压到 `/var/lib/cmaster`，保持文件 UID/GID 为 `10001:10001`，然后启动 Web。
不应把备份放在 `static/`、公开仓库或公开网盘。

更新代码前先备份。更新顺序：

```bash
git pull --ff-only
docker compose --env-file deploy/.env -f deploy/compose.yml --profile build build
docker compose --env-file deploy/.env -f deploy/compose.yml up -d gateway web broker
```

不要使用 `docker compose down -v`：`-v` 会删除学习数据和证书卷。普通重启使用 `restart`。

## 部署结构与边界

- Caddy 接收公网请求并提供 HTTPS；Web 只在内部网络提供服务。
- Web 不安装 GCC，不挂 Docker socket，只写独立的学习数据卷。
- 判题 broker 通过内部 Unix socket 接收固定请求，是唯一可访问 Docker daemon 的服务，无网络、无公开端口、无学习数据挂载。
- 每次提交创建一个独立容器，包含 GCC 和公开判题引擎，不包含账号文件、网站密钥、题库和参考答案。
- 判题容器无网络、无宿主目录挂载，非 root、只读根文件系统；限制 512 MB 内存、1 CPU、64 个进程和临时空间。
- 用例限制 3 秒、256 MB 地址空间、每路输出 64 KiB。整个容器另有总超时和清理。容器失联返回系统错误，不会回退到网站宿主直接执行。
- `/admin`、`/ops` 及其维护 API 在线上关闭；使用服务器日志维护。
- 当前运行层没有采集精确 CPU 时间和内存峰值，相关值为 0，不用于性能结论。仅支持单机、小规模展示；扩大并发前需要换数据库、任务队列并重新做容量和隔离评估。

## 自动验收

GitHub Actions 的 **Online deployment verification** 使用 Ubuntu 和真实 Docker 构建全部镜像，运行：

- Python 单元回归。
- GCC 的 AC、WA、CE、RE、TLE、输出超限和全部 60 道参考答案。
- 编译及运行阶段无法读取网站账号，容器不可写应用目录、不可连外网，任务结束没有残留容器。
- 真实 HTTP 注册、登录、提交 AC 和阅读进度，再重建 Web/broker 验证账号、Session 和学习记录持久化。

`compose.test.yml` 和 `smoke_web.py` 只用于一次性的 CI 环境，不要对真实用户数据运行。
CI 通过表示这组功能在测试环境通过；仍需在自己的服务器完成 DNS、证书、访问和数据备份验收。
