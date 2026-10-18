# 部署形态迁移方案：Podman 容器 → 宿主进程（v3，评审条件性 APPROVE 的三项已修）

> 状态：v1 经独立评审（2 P0 + 4 P1），本版已吸收全部修订；待评审确认后实施。
> 目标：与其它 com.koven.* 服务一致，LaunchAgent + venv 直跑，消除 podman VM 开销，**功能与对外行为不变**。

## 1. 现状（被替换物）

| 组件 | 现状 | 关键事实 |
|---|---|---|
| 后端 | 容器 vsplit-backend，uvicorn :8080（--host 0.0.0.0），镜内 ffmpeg+deno 2.8.3+py3.13 | 宿主已备齐：py3.13 ✓ ffmpeg 9.0.1 ✓ deno 2.9.5 ✓（EJS≥2.6.6）unzip/curl ✓；uvicorn[standard]、yt-dlp-ejs 均在 pyproject 依赖 |
| 前端 | 容器 vsplit-frontend，nginx :80→宿主 5180：静态+SPA+`/api` 反代（SSE buffering off、600s；未开 gzip；X-Real-IP 后端零消费） | 由 FastAPI 直接承担；uvicorn 直出 SSE 无缓冲，行为等价 |
| 数据 | `config/ data/ logs/` bind mount；`data/` 属主 admin:staff | 路径 CWD 相对；config 有锚定 fallback，storage/logging/cookies 相对 CWD |
| 代理 | 容器 `host.containers.internal:7897`；compose 显式清空 4 个代理 env（防运行时污染，B站/小宇宙禁出海代理的保证） | 改 `127.0.0.1:7897` + plist 显式置空 4 个代理变量，逐字复刻 |
| 守护 | **系统级** LaunchDaemon `system/com.koven.vedio-split-view`（30s 循环跑 `scripts/launchd-start.sh`，podman inspect 判活，永不退出）+ 用户级 boot Agent（sleep30→curl5180→不行则 machine start+manage.sh start） | P0-2：系统 Daemon 在停容器后 30s 内会用旧 manage.sh 拉回容器，**切换第 0 步必须先摘除/致瘫** |
| 端口 | 8080 与 5180 均已发布；外部（aliyun→飞书 webhook/分享链）指向不可从本仓确证 | 保留双端口，但**单进程双监听**（P0-1） |

## 2. 目标架构（v2）

```
~/Services/vedio-split-view/
├── backend/.venv/            # py3.13 venv，pip install -e backend
├── frontend/dist/            # npm ci && npm run build
├── data/ logs/ config/       # 不变
├── scripts/multi_listen.py   # 新入口：chdir 服务根 → 同一 app 起 2 个 uvicorn Server（0.0.0.0:5180 + 0.0.0.0:8080），SIGTERM 双退出
└── manage.sh                 # 命令面不变，实现换 launchctl/venv/npm

LaunchAgent com.koven.vsplit.web   → .venv python scripts/multi_listen.py
```

### 2.1 关键决策（评审修订后）

- **单进程双监听**（替换 v1 的双进程）：TaskRunner/订阅/取消/确认事件全部是进程内状态（task_runner.py:40-56、tasks.py:73-78、feishu.py:612/657/803），双进程会造成 SSE 404、确认失灵、`_recover_orphan_tasks` 互杀对方活跃任务、双写 RotatingFileHandler 竞态。一个事件循环跑两个 `uvicorn.Server`（共享 app 与 runner），一个 plist，一份日志，orphan-recovery 语义恢复为「进程真重启才判死」。附注：两个 Server 对同一 app 各跑一次 lifespan → startup 执行两次（init_db/ensure_admin_user 幂等且启动期无任务，无害）。
- **SIGTERM 双退**：uvicorn `Server.serve()` 的 `capture_signals()` 会用自己的 `handle_exit` 覆盖先装的自定义 signal handler——所以**不装** `signal.signal`，而是在 `serve()` 之前对两个实例做实例级替换：`s1.handle_exit = s2.handle_exit = lambda *a: setattr 双方 should_exit=True`；无论哪个 handler 被触发都双退。运行时验收：起服务 → `kill -TERM` → N 秒内进程退出、两端口关闭（写进验收清单，不信任文字）。
- **CWD 锚定**：`multi_listen.py` 第一行 `os.chdir(Path(__file__).resolve().parent.parent)`——不依赖 plist WorkingDirectory（仍设，双保险）；storage/logging/cookies 的 CWD 相对路径从此确定。
- **绑定 0.0.0.0**（P1-1）：容器时代 podman 发布默认全网卡，aliyun/Tailscale 入口可能走非回环地址；显式 `--host 0.0.0.0`，验收含非回环 curl。
- **代理 env 显式清空**（P1-4）：plist `EnvironmentVariables` 除 `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` 外，`http_proxy/https_proxy/HTTP_PROXY/HTTPS_PROXY=""`。
- **守护**：新 Agent `RunAtLoad + KeepAlive`（崩溃自愈）；**切换第 0 步**先摘旧守护（见 §2.4）；旧系统 Daemon 的彻底 sudo 卸载列为后续项。
- **SPA 兜底不吞 API 语义**（P1-5）：catch-all 挂在全部 API 路由与 `/assets` mount 之后，且只对非 `/api`、`/docs`、`/redoc`、`/openapi.json` 前缀的 GET 做 index.html 回退；未知 `/api/*` 保持 FastAPI 404 JSON（与今天 nginx→后端行为一致）。
- **podman machine 不动**：multica 等栈仍在 VM 内，只停 vsplit 两容器。

### 2.2 功能不变性清单（验收对照）

1. 非回环地址 `GET :5180/` 返回 SPA；`/api/health` → ok；`:8080` 同样可用；`GET :5180/health` 由今天返回 index.html 变为 JSON（评审 P2-6 确认的无害差异，接受）
2. `/assets/*` Cache-Control immutable 一年；`index.html`/SPA 回退 no-cache（同 nginx.conf）；未知 `/api/*` 返回 404 JSON 而非 HTML
3. SSE 进度流不断流（前端 `BASE='/api'` 同源自消费）
4. `/api/thumbnails`、`/api/docs-data`、`/docs`、`/api/hooks/feishu`、`/api/public/notes/*` 全可达
5. 三平台链路：本轮实测 B站+小宇宙（用户给链）；YouTube 因代理问题列为观察项
6. 数据零迁移：同一 `data/video_split.db`（multi_listen chdir 自检：解析后 db 路径必须命中 `<root>/data/video_split.db`，文件不存在且非首次安装则拒启并打印明确错误）
7. manage.sh 命令面不变；`clean` 重写为进程版（清 venv 缓存/node_modules/dist）；`backup` 保持纯 shell（cron 03:00 在用，**不得**引入 venv/launchctl 前置）
8. cron `manage.sh backup` 与 `export/import` 迁移后零改动可用

### 2.3 代码改动清单

| 文件 | 改动 |
|---|---|
| `backend/src/video_split/main.py` | `_mount_frontend(app)`：dist 存在且 `app.serve_frontend`（新配置项，默认 True）时挂 `/assets`（StaticFiles 子类设 immutable 头）+ SPA 兜底路由（no-cache，排除 API/docs 前缀） |
| `backend/src/video_split/config.py` | `AppSettings.serve_frontend: bool = True` |
| `scripts/multi_listen.py`（新） | chdir 锚定 + db 路径自检（**db 文件缺失一律拒启**；首次安装显式 `FIRST_RUN=1` 放行）+ 端口占用预检 + 双 `uvicorn.Server`（5180/8080，0.0.0.0）共享 app + 实例级 `handle_exit` 双退 |
| `manage.sh` | 命令面不变；**start=`launchctl bootstrap`（已 loaded 则 kickstart，幂等）；stop=`launchctl bootout`（KeepAlive 下 kill 只会被立刻重拉，stop 必须是 bootout）**；rebuild=venv `pip install -e .` + `source nvm` 后 `npm ci && npm run build`；status=pgrep+curl+数据体检（保留 `_report_data_health`）；clean 重写为进程版（**先确认服务已停**，清 venv 缓存/node_modules/dist，不碰 data/logs/config）；install-launchd=装新 Agent（免 sudo）；deploy/export/import/backup 原样，文尾提示语同步 |
| `scripts/com.koven.vsplit.web.plist`（新） | LaunchAgent：venv python multi_listen.py；WorkingDirectory+PATH+清空代理 env；RunAtLoad+KeepAlive；日志 `logs/process.log` |
| `backend/tests/test_static_frontend.py`（新） | SPA 回退、/assets 缓存头、未知 /api/* 404 JSON、dist 缺失 no-op |
| `README.md`、`AGENTS.md` | 部署描述进程化；`rebuild` 语义更新（不再有容器重建） |

### 2.4 切换步骤（~/Services，评审修订后顺序）

0. **摘除旧守护**（P0-2，停容器前！）：
   a. 用户域 boot Agent：`launchctl bootout gui/$UID/com.koven.vedio-split-view && rm ~/Library/LaunchAgents/com.koven.vedio-split-view.plist`
   b. 系统域 Daemon（需 sudo，不可交互时）——**先杀进程再换文件**（bash 已把 while-true 循环读进内存，只改文件对运行中的进程无效）：
      ① `pkill -f launchd-start.sh`（进程属主 admin，同 UID 可杀；其 plist 无 KeepAlive，launchd 不会重拉；trap 自清 lockdir）
      ② 将 `~/Services/vedio-split-view/scripts/launchd-start.sh` 内容替换为 `#!/bin/bash\nexit 0`（防下次重启复活）
      ③ 验证：`ps` 无该进程 + `podman ps` 清空后静置 60s 无容器复活
      完整 `sudo launchctl bootout system/... && sudo rm /Library/LaunchDaemons/...` 列为后续清理项
1. **回滚保险**（P1-2）：`cp manage.sh manage.sh.container.bak; cp compose.yaml compose.yaml.container.bak`（~/Services 无 .git，git checkout 不可用）；镜像仍在，回滚=恢复 bak+旧 start
2. 旧栈下线：`./manage.sh stop`（容器版）+ `podman stop vsplit-backend vsplit-frontend`；**不动 machine**
3. rsync 新代码（排除清单不变）
4. `config/app.yaml`：`http(s)_proxy` → `http://127.0.0.1:7897`（唯一配置改动）
5. `backend`: `python3 -m venv .venv && .venv/bin/pip install -e .`；`frontend`: `npm ci && npm run build`
6. `./manage.sh install-launchd && ./manage.sh start`
7. 验收 §2.2 清单 1-4（含非回环 curl）→ 两条链接全链路 E2E：
   - `https://www.bilibili.com/video/BV1NLh16UEvs/...`
   - `https://www.xiaoyuzhoufm.com/episode/6acb15cee742e36efcc1261e`
8. SIGTERM 运行时验收：`kill -TERM` 服务 pid → 10s 内进程退出、5180/8080 均关（不信任方案文字，实测为准）
9. 回滚预案：进程版 stop（=bootout）→ `mv *.container.bak` 还原 → 旧 `manage.sh start` 拉容器（数据未动，分钟级）

### 2.5 风险与对策（增量）

| 风险 | 对策 |
|---|---|
| ffmpeg 9.0.1 主版本跨度（容器为 Debian 老版） | 代码只用稳定 CLI 面；B站/小宇宙 E2E 必测转码路径兜底 |
| nvm node 不在非交互 PATH | rebuild 内显式 source nvm / 写死路径 |
| 时区：容器 UTC vs 宿主 CST | DB 时间戳全 tz-aware UTC，无数据影响；跨切换期看日志注意 8h 平移 |
| aliyun 反代指向不确定 | 双端口单进程兜底；后续可上 aliyun 查反代配置确证后下线多余监听 |
| 双监听单进程：一端口被占启动失败 | multi_listen 启动前 probe 两端口，占用即报错退出（KeepAlive 不盲目重试刷日志——launchd 默认退避） |

## 3. 明确不做

不改业务逻辑；不迁数据；不停 podman machine；不引入 systemd/supervisor/nginx 等新依赖；本轮不测 YouTube（代理问题，观察项）。
