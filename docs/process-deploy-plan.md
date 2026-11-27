# 部署形态：宿主进程（单进程双监听 + launchd 守护）

服务以宿主进程直接运行，不使用容器：一个 Python 进程同时监听两个端口，由 launchd 守护，`manage.sh` 提供生命周期与部署入口。

## 1. 进程形态

- **入口** `scripts/multi_listen.py`：先 `chdir` 到服务根目录，预检端口、校验数据库文件，再在同一个事件循环里起两个 `uvicorn.Server`，共享同一个 FastAPI 应用。
- **双端口**：`0.0.0.0:5180`（SPA + API，用户入口）与 `0.0.0.0:8080`（API 兼口）。监听端口只由环境变量 `VSLP_PORTS` 决定（默认 `5180,8080`，plist 不设置）；`app.yaml` 的 `app.port` / `app.frontend_port` 只影响 `manage.sh` 的探活地址和 CORS 白名单。
- **单进程的原因**：TaskRunner、SSE 订阅、确认 / 取消事件都是进程内状态。拆成两个进程会造成 SSE 404、确认失灵、孤儿任务恢复互杀。
- **生命周期只跑一次**：两个 Server 都以 `lifespan="off"` 启动，由入口手动进入一次应用 lifespan，`init_db` / 建管理员只执行一次。
- **双退**：两个 Server 的 `handle_exit` 指向同一个函数，任一端口收到 SIGINT / SIGTERM，两个监听一起退出。
- **拒启条件**：任一端口被占用（带 `SO_REUSEADDR` 预检，TIME_WAIT 残留不误报）；配置的数据库文件不存在（首次安装显式 `FIRST_RUN=1` 放行）。
- **CWD 锚定**：`app.yaml` 里的存储 / 日志 / cookies 路径是相对路径，入口与 plist 都把工作目录固定为服务根。

## 2. 前端托管

`backend/src/video_split/main.py::_mount_frontend` 在 `frontend/dist/index.html` 存在且 `app.serve_frontend` 为真时挂载前端（环境变量 `VSLP_FRONTEND_DIST` 可覆盖 dist 位置；dev 检出没有 dist 时不挂载）：

- `/assets/*` 由进程直接出，`Cache-Control: public, max-age=31536000, immutable`。
- 其余路径回退到 `index.html`，`Cache-Control: no-cache, no-store, must-revalidate`（SPA 路由）。
- 回退路由挂在全部 API 路由之后，并跳过 `api/`、`docs`、`redoc`、`openapi.json` 前缀：未知的 `/api/*` 保持 FastAPI 的 404 JSON。
- 缩略图挂载在 `/api/thumbnails`，`Cache-Control: private, max-age=86400`。
- gzip 在进程内开启（`minimum_size=1024`）；`text/event-stream`（SSE 进度流）与 `/api/thumbnails/*` 不压缩。

以上行为由 `backend/tests/test_static_frontend.py` 固化。

## 3. 托管形态

实例身份只读部署章 `config/env`：内容为 `production` 即生产副本，无章即 Test 检出。章由 `deploy` / `deploy-local` 成功后写入，是唯一写入点。

| | 生产（有章） | Test（无章） |
|---|---|---|
| 托管 | 系统 LaunchDaemon `com.koven.vsplit.web`（`UserName=admin`、`RunAtLoad`、`KeepAlive`），plist 在 `deploy/`，装到 `/Library/LaunchDaemons/` | pidfile（`logs/multi_listen.pid`）+ nohup，可随时中断，绝不托管给系统 |
| `start` | 已加载则 no-op；被停用则走免密 `svc-ctl enable` 回托管 | nohup 拉起本目录实例 |
| `stop` | 免密 `svc-ctl disable` 真停，跨重启持久；helper 未装则拒绝，不假成功 | 按 pidfile + 端口监听确认身份后结束进程 |
| `restart` | 结束进程，由 KeepAlive 拉起，免 sudo；双端口健康检查通过才算完成 | stop + start，并确认进程换了 |
| `install-daemon` | 打印需 root 执行的安装命令 | 拒绝（exit 1） |

- 托管判定是**目录级**：label 在 system 域 **且** 监听进程的命令行含本目录的 `scripts/multi_listen.py`。同机的 Test 检出与生产副本共用同一个 label，只看 label 会把 Test 的 start / stop 误判成生产。
- 端口被别目录的实例占用时，start / stop / restart 如实报错，不越目录杀进程。
- `manage.sh status` 的 `Lifecycle` 行四态：`running` / `down` / `stopped (intentional)` / `not installed`。`stopped (intentional)` 是机主有意停用，退出码 0，不是故障。

## 4. 部署

- **同机**（开发目录与生产目录都在本机，常态）：`./manage.sh deploy-local [-d] [目标目录]`，本地 rsync，默认目标 `$HOME/Services/<工程名>`，目标须已存在。
- **跨机**：`config/deploy.cfg` 填 `ssh_target` / `remote_path`，`./manage.sh deploy [-d]`，SSH + deploy-check 防呆。
- 两门都要求 `.rsync-exclude` 在位（随仓库走，缺失即拒绝部署），带章的生产副本拒绝作为部署源。成功后向目标写 `config/env=production`，再到目标目录执行 `./manage.sh rebuild`。
- `.rsync-exclude` 对 `config/` 默认拒绝：只同步 `*.example` 与 `build.cfg`，其余配置、密钥、部署章既不覆盖也不被 `--delete` 删除；`data/`、`logs/` 整体排除。首次部署或改动排除清单后先 `-d` 预览，出现 `data/` `logs/` `config/` 路径即停下修清单。
- `deploy-data` 是数据旁路（exports + thumbnails，append-only），与代码部署门无关。
- 回滚：`git revert` 对应提交后重新部署；数据目录不受部署影响。

## 5. 数据

- 数据零迁移：仍是 `data/video_split.db`（SQLite，默认 DELETE 日志模式，不开 WAL）。入口启动时校验该文件存在，防止工作目录不对时悄悄建一个空库。
- `backup` 保持纯 shell（`cp` + 按份数保留），可由 cron 直接调用；`export` / `import` 走管理 API。
- `status` 打印数据库路径、大小、修改时间和 users / videos / tasks 行数；库文件缺失或 users=0 时报错。

## 6. 验收清单

部署或改托管形态后逐项核对：

1. 非回环地址 `GET :5180/` 返回 SPA；两个端口的 `/health` 都返回 JSON `{"status": "ok"}`；`:8080` 同样可用。
2. `/assets/*` 带 immutable 一年缓存且 `content-encoding: gzip`；`index.html` 与 SPA 回退 no-cache；未知 `/api/*` 返回 404 JSON。
3. SSE 进度流不断流、不被压缩。
4. `/api/thumbnails`、`/api/docs-data`、`/docs`、`/api/hooks/feishu`、`/api/public/notes/*` 可达。
5. `kill -TERM` 进程：Test 形态 10 秒内两个端口关闭；生产形态 launchd 自动拉起新进程（pid 变化）。
6. `manage.sh status` 两个端口 health OK，`Lifecycle` 与预期一致；`restart` 输出的 pid 与重启前不同。
7. B 站、小宇宙、YouTube 或 X 各跑一条真实链接，结果入库。
