# VideoSplit

把一条长视频链接变成一份带摘要、主题片段和脑图的结构化笔记。

粘贴 YouTube / Bilibili / 小宇宙 链接，服务会自动获取字幕（或转录音频），再用 LLM 按主题把视频切分成若干片段，每段配一句摘要和起止时间。结果可以在线浏览、导出 Markdown、生成脑图，也能分享到站内广场。

## 功能

- **一键分析**：粘贴链接即可，进度通过实时流推送（下载 → 字幕/转录 → AI 分析）。
- **主题分段**：AI 按内容主题把长视频拆成独立片段，标注每段起止时间与一句话摘要。
- **字幕 + 摘要**：每个片段可显示原文字幕，点击「从此处播放」直接跳到对应位置；同时给出整段精华总结。
- **脑图模式**：AI 把内容按章节重组，提取关键要点和名言金句，一眼看清视频骨架。
- **导出 Markdown**：一键导出全文（摘要 + 各片段 + 逐字稿）或仅精华总结，方便存进 Obsidian / Notion。
- **资源库 + 广场**：分析过的视频进资源库管理，可设为公开分享到广场给其他用户看。
- **标签管理**：给视频打标签，资源库支持搜索。
- **多用户**：管理员账号负责建用户、配额、清理存储；普通用户独立空间。
- **API Token**：可在设置里签发长期 Token，用脚本或外部工具调用分析接口。
- **用量追踪**：记录每次分析的 LLM / ASR Token 消耗，视频删除后统计仍保留。
- **双语界面**：中文 / English 自由切换。

## 支持的平台

| 平台 | 字幕来源 | 备注 |
|------|---------|------|
| YouTube | YouTube 字幕 API | 部分视频需配置 cookies 才能下载 |
| Bilibili | 官方字幕 API | 需在设置里扫码登录 Bilibili 账号 |
| 小宇宙 | 内嵌逐字稿 | 仅支持公开单集 |

当视频没有可用字幕时，服务会下载音频并调用转录模型（OpenAI Whisper 或阿里云 Fun-ASR）生成文本，再进入分析。

## 怎么用

1. 用管理员账号登录后，在「用户管理」创建一个普通用户。
2. 切到普通账号，在「分析」页粘贴视频链接，点「分析」。
3. 等待流水线跑完（有字幕的视频通常很快；需要转录的长视频会久一些）。
4. 在「资源库」或「分析」页点开视频详情：看摘要、浏览片段、生成脑图、导出 Markdown。

## 自部署

依赖：**Podman**（不依赖 Docker）。先装好并启动 Podman，确认 `podman info` 能跑通。

```bash
# 1. 初始化（生成配置模板 + 目录结构，含 config/build.cfg）
bash manage.sh init

# 2. 填配置（必填项见下表）
vim config/app.yaml

# 3. 构建并启动
bash manage.sh rebuild
```

打开 `https://localhost:5180`，用 admin 账号登录即可。

### 常用命令

```bash
bash manage.sh status     # 查看容器状态 + 健康检查 + 数据库行数
bash manage.sh rebuild     # 改完代码后增量构建并重启（最常用）
bash manage.sh restart     # 只重启，不重建镜像
bash manage.sh stop        # 停止
bash manage.sh start       # 启动
```

转录说明：长音频尾部短静音块会并入上一块；Fun-ASR 对单块无语音返回空结果，不再让整条任务失败。失败任务重试会复用已完成 chunk 缓存。YouTube 字幕支持代理失败后直连兜底；Bilibili 多 P 链接按 `p` 参数读取对应字幕。

部署到远端服务器：

```bash
cp config/deploy.cfg.example config/deploy.cfg
vim config/deploy.cfg       # 填 DEPLOY_REMOTE 和 DEPLOY_REMOTE_DIR
bash manage.sh deploy -d    # dry-run 预览
bash manage.sh deploy       # 同步代码（不含 data/、配置、密钥）
```

## 配置（config/app.yaml）

`config/app.yaml` 可以很短，没写的字段都走代码默认值。**必填**：

| 节 | 字段 | 说明 |
|----|------|------|
| `app` | `secret_key` | JWT 密钥，生产请换成随机长字符串 |
| `admin` | `password` | 管理员密码，留空服务拒绝启动 |
| `llm` | `base_url`, `model`, `api_key` | 视频分析用的 OpenAI 兼容 LLM |

**按需**（只在用到时才写）：

| 节 | 何时需要 |
|----|---------|
| `transcription` | 视频无可用字幕、需要音频转录时（Whisper 或 Fun-ASR） |
| `oss` | 使用阿里云 Fun-ASR 时（Whisper 不需要） |
| `network` | 下载 / 字幕需要代理，或 YouTube 需要登录态时 |

构建源（镜像仓库、apt、PyPI）写在 `config/build.cfg`，不写死在 Dockerfile。该文件不含密钥、随代码同步，本地与生产用同一套源；环境变量（如 `REGISTRY=docker.io`）优先级更高。`config/deploy.cfg` 只放 SSH 目标，被 rsync 排除。
| `app` (`port`, `frontend_port`) | 想改默认端口 8080 / 5180 时 |

完整字段和默认值见 `config/app.yaml.example`。

## 技术栈

后端 Python + FastAPI + SQLite，前端 React + Vite + Tailwind，容器化用 Podman。
