# X（Twitter）视频解析：可行性调研

## 结论：可行，**不需要登录**

yt-dlp 内置的 X 提取器走游客 token，匿名就能拿到元数据和音频流。用测试推文实测：

| 检查项 | 结果 |
|---|---|
| 元数据（本机，未登录） | ✅ 约 5 秒。标题、作者 `Royce Chen`、时长 336.9 秒、正文都拿到了 |
| 纯音频流 | ✅ HLS 纯音频有 32k / 64k / 128k 三档，`bestaudio` 自动选 `hls-audio-128000` |
| 实际下载 | ✅ 5.2 MB AAC，ffprobe 测得 336.94 秒，和元数据一致 |
| 生产机 mini（yt-dlp 2026.08.19，走 :7897 代理） | ✅ 同样匿名拿到，自动选中 128k 音频 |
| 字幕 | X 视频没有平台字幕，所以和播客一样走 Whisper 转写 |

## 集成方式（参照 Apple Podcasts 那次提交 `4bc5382`）

X 能复用现有的**通用 yt-dlp 路径**，和 YouTube 走的是同一套 `extract_metadata`、`download_audio`，代理也按国际平台的规则路由，所以不用新写下载器。脑图、断点重试、按平台计的并发配额、去重、SSE 进度这些功能本来就不区分平台，X 自动具备。需要改的地方：

- `detect_platform`：识别 `x.com` / `twitter.com` / `mobile.` 和 fx/vx 镜像的 `/status/<id>`。`video_id` 用**推文 ID**（`2124465845284008268`），不用媒体 ID
- 字幕阶段：X 直接走 Whisper，阶段列表里隐藏 `subtitle_check`（和播客一样处理）
- 平台标签、`generate_playback_url`、导出、`admin` 平台筛选
- 标题：yt-dlp 给的是"作者 - 截断正文…"，可以改成用正文首句
- 一条推文里有多个视频时，默认取第 1 个；没有视频的推文返回明确的错误码
- 前端：平台类型、URL 校验、标签和图标、中英文文案、分析页入口
- 文档：README 平台表、`schemas` 描述、API 文档

## 飞书里的 X 链接

飞书收到 X 链接会**转发给 vedio-favorate**；推文含视频时同时发起解析，纯图文推文只收藏不解析。实现见 [`x-video-support-design.md`](x-video-support-design.md) 与 [`feishu-bot.md`](feishu-bot.md)。

## 风险

- X 的游客接口偶尔会收紧，那时需要 cookies。可以预留 `x_cookies_file` 配置项（和 YouTube 的做法一样），默认不用
- 受保护账号和敏感内容的推文匿名拿不到，返回明确的错误码
