# VideoSplit

将长视频拆分为独立主题片段的自动化分析服务。支持 Bilibili / YouTube。

## 分析流程

```
┌──────────┐     ┌───────────────┐     ┌──────────────┐     ┌────────────┐     ┌──────────┐
│  用户输入  │────▶│  提取视频元数据  │────▶│  获取字幕/转录  │────▶│  LLM 内容分析 │────▶│  保存结果  │
│  视频 URL  │     │  (yt-dlp)      │     │              │     │  (主题分段)  │     │  (SQLite) │
└──────────┘     └───────────────┘     └──────┬───────┘     └────────────┘     └──────────┘
                                              │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                     YouTube 字幕 API    Bilibili 字幕      音频转录
                     (无需认证)          (需 QR 登录)      (无字幕时)
                                                               │
                                                    ┌──────────┴──────────┐
                                                    ▼                     ▼
                                              OpenAI Whisper        Aliyun Fun-ASR
                                              (直传文件)            (OSS → 签名URL)
```

**音频转录细节：**
```
音频文件 ──▶ ffmpeg 按 5min 切片 ──▶ 逐片转录（带缓存） ──▶ 合并时间线 ──▶ 完整文本
                                       │
                                  .transcript.json 缓存
                                  (重试时跳过已完成片段)
```

## 快速开始

```bash
# 0. 确认本机已安装并启动 Podman
podman info

# 1. 初始化（生成配置 + HTTPS 证书）
bash manage.sh init

# 2. 编辑配置（必填 admin.password, llm.api_key, transcription.api_key）
vim config/app.yaml

# 3. 构建并启动
bash manage.sh rebuild
```

打开 `https://localhost:5180`，用 admin 账号登录后创建普通用户。

## manage.sh

```bash
bash manage.sh init                  # 初始化目录、配置、证书
bash manage.sh start                 # 启动
bash manage.sh stop                  # 停止
bash manage.sh restart               # 重启
bash manage.sh rebuild               # 增量构建并重启（最常用）
bash manage.sh rebuild -n            # 无缓存完全重建
bash manage.sh rebuild -p            # 重新拉取基础镜像
bash manage.sh status                # 查看状态 + 健康检查
bash manage.sh clean                 # 清理构建缓存
```

容器生命周期由 `manage.sh` 直接调用 Podman：构建使用 `podman build`，启动使用
`podman run` 创建 `vsplit-backend` / `vsplit-frontend` 两个容器，并放入
`vsplit-net` 网络。脚本不再依赖 `podman compose`，因此不会被 Podman 委托给
Docker Compose provider。

## 部署配置 (config/deploy.cfg)

`manage.sh deploy` 和 `manage.sh deploy-data` 默认读取 `config/deploy.cfg`。首次配置：

```bash
cp config/deploy.cfg.example config/deploy.cfg
vim config/deploy.cfg
```

`config/deploy.cfg` 是本地配置，不进 Git；`config/deploy.cfg.example` 用于提交模板。

```bash
DEPLOY_REMOTE="root@your-server"
DEPLOY_REMOTE_DIR="ai/vedio-split-view"
```

常用方式：

```bash
bash manage.sh deploy -d     # dry run，使用 config/deploy.cfg
bash manage.sh deploy        # 同步代码到远端，不同步 data/config/app.yaml 等敏感和运行时数据
bash manage.sh deploy-data   # 同步导出的 JSON 和缩略图
```

命令行参数仍可覆盖配置，例如 `bash manage.sh deploy root@srv ai/vedio-split-view`。

## 配置说明 (config/app.yaml)

`config/app.yaml` 可以很短。除了下面的必填项，其它参数都有代码默认值，只有需要覆盖默认行为时再写。

必填：

| 节 | 关键字段 | 说明 |
|---|---------|------|
| `app` | `secret_key` | JWT 密钥，生产环境必须换成随机长字符串 |
| `admin` | `password` | 管理员密码，启动时覆盖 DB |
| `llm` | `base_url`, `model`, `api_key` | 视频总结和分段使用的 OpenAI 兼容 LLM |

按需填写：

| 节 | 关键字段 | 说明 |
|---|---------|------|
| `transcription` | `base_url`, `model`, `api_key` | 只有视频无可用字幕、需要音频转录时才用 |
| `oss` | `endpoint`, `access_key_id/secret`, `bucket_name` | 只有使用阿里云 Fun-ASR 时才需要；Whisper 不需要 |
| `app` | `port`, `frontend_port` | 只有要改默认端口 `8080` / `5180` 时才写 |
| `network` | `proxy_enabled`, `http_proxy`, `youtube_cookies_file` | 只有下载/字幕需要代理或 YouTube 登录态时才写 |
| `storage` | `db_path`, `temp_dir`, `max_pending_tasks_per_user` | 只有要改数据库、临时目录或配额时才写 |
| `video` | `max_duration_seconds`, `confirm_threshold_seconds` | 只有要改视频时长限制时才写 |
| `logging` | `level`, `dir` | 只有要改日志级别或目录时才写 |

## Debug API

通过 `http://localhost:{port}/docs` 查看 Swagger UI。以下为调试常用接口：

```bash
# 登录获取 token
TOKEN=$(curl -s http://localhost:4305/api/auth/login \
  -X POST -H "Content-Type: application/json" \
  -d '{"username":"test","password":"password"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 仅下载音频（SSE 流，实时进度）
curl -N http://localhost:4305/api/debug/download \
  -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"url":"https://www.bilibili.com/video/BV1xxxxxx/"}'

# 列出当前任务
curl http://localhost:4305/api/debug/tasks -H "Authorization: Bearer $TOKEN"

# 查看某任务的音频分片
curl http://localhost:4305/api/debug/tasks/3/chunks -H "Authorization: Bearer $TOKEN"

# 转录指定任务（全部分片）
curl http://localhost:4305/api/debug/transcribe \
  -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"task_id": 3}'

# 转录指定任务的单个分片
curl http://localhost:4305/api/debug/transcribe \
  -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"task_id": 3, "chunk_index": 0}'

# 直接转录本地文件（不占配额，用于测试 ASR 连通性）
curl http://localhost:4305/api/debug/test-asr \
  -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"file_path": "/app/data/tmp/3/chunks/chunk_000.mp3"}'

# 清理某个任务（删除文件 + DB 记录）
curl -X DELETE http://localhost:4305/api/debug/tasks/3 -H "Authorization: Bearer $TOKEN"
```

## 本地测试脚本

```bash
# 端到端 ASR 测试（需先通过 debug/download 下载音频）
# 编辑 test_transcribe.py 中的 test_file 路径，然后：
cd vedio-split-view
python3 test_transcribe.py
# 结果输出到 test_transcription_result.txt
```

## 日志

应用日志写入 `logs/app.log`（自动轮转，50MB/文件，保留 5 份）。

```bash
# 实时查看
tail -f logs/app.log

# 筛选外部调用
grep '\[metadata\]\|\[download\]\|\[whisper\]\|\[funasr\]\|\[llm\]\|\[oss\]' logs/app.log
```

## 目录结构

```
vedio-split-view/
├── manage.sh              # 容器管理脚本
├── compose.yaml           # Podman Compose 定义
├── config/
│   ├── app.yaml           # 运行时配置（gitignore）
│   ├── app.yaml.example   # 配置模板
│   └── certs/             # （已移除，Cloudflare 终止 HTTPS）
├── backend/               # Python FastAPI
├── frontend/              # React + Vite + Tailwind
├── data/                  # SQLite DB + 临时文件（gitignore）
├── logs/                  # 应用日志（gitignore）
└── test_transcribe.py     # ASR 端到端测试脚本
```

## Changelog

- `2026-05-27` — Bilibili 元数据/字幕获取改为直接调用 `api.bilibili.com` 官方 API，绕过 `www.bilibili.com` 网页端的 412 风控拦截。
