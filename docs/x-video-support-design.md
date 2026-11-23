# X（Twitter）视频解析支持 — 设计说明与验收标准

## 1. 范围与决策

- **范围**：X（Twitter）视频接入与 YouTube 共用的通用 yt-dlp 管道，脑图、断点续传/重试、按平台并发配额、去重、SSE 进度等能力与其他平台一致。
- **匿名可解析**：yt-dlp 内置 X 提取器走游客 token，无需登录即可取元数据与 HLS 纯音频流（`hls-audio-128000`，AAC 128kbps）。实测记录见 [`x_feasibility.md`](x_feasibility.md)。
- **飞书 X 链接双路**：配置了视频收藏时继续转发收藏卡；推文含视频则同时发起解析，纯图文推文只收藏不解析。
- **验收测试推文**：`https://x.com/rchen1386/status/2124465845284008268`，`tweet_id=2124465845284008268`（媒体 ID `2124465754183725056`），作者 `Royce Chen`，时长约 337 秒。`video_id` 必须用推文 status ID，不能用媒体 ID，否则去重与断点恢复会失效。

---

## 2. 架构与详细设计（Design Specification）

### 2.1 核心原则：复用通用 `yt-dlp` 管道
参照 Apple Podcasts 接入提交（`git show 4bc5382` 与 `git show 12fa46c`），但**不需要像播客那样写独立下载器**。X 直接复用 `backend/src/video_split/service/downloader.py` 中与 YouTube 共用的 `extract_metadata` 和 `download_audio` 通用 `yt-dlp` 路径：
- **平台标识**：`"x"`（展示名 `"X"`，标签颜色 `"#000000"`）。
- **代理路由**：X 属于国际平台，**严禁加入 `_DIRECT_PLATFORMS`**，必须与 YouTube 一样走 `_proxy_for_platform("x")`。
- **字幕与转写**：X 无平台原生字幕，跳过字幕抓取直走 Whisper 转写（与播客一致隐藏前端 `subtitle_check` 阶段），但**确认阈值走视频档**（非播客档），且**不属于中文优先平台**（语言判定与翻译链路同 YouTube）。
- **天然对齐能力**：复用通用管道后，脑图（`mindmap.py`）、断点恢复（`retryable_task` / `resume`）、按平台并发配额（`check_task_quota(..., platform="x")`）、重复检测（`check_duplicate`）、音频完整性校验（`audio_integrity.py`）全部自动与 YouTube / B 站一致。

### 2.2 后端设计要点（Backend Touch-points）

1. **`backend/src/video_split/service/downloader.py`**
   - **`detect_platform(url)`**：
     - 匹配域名：`(www\.|mobile\.|m\.)?(x|twitter|fxtwitter|vxtwitter|fixupx|fixvx)\.com`
     - 匹配路径：`/<user>/status/(\d+)`、`/i/status/(\d+)`、`/i/web/status/(\d+)`，允许带 `/video/<n>`、`/photo/<n>` 后缀（归一化时剥掉）
     - 返回 `("x", "<tweet_status_id>")`。注意：**`video_id` 必须使用 URL 中的推文 status ID**（如 `2124465845284008268`），而不是 yt-dlp 返回的底层 media ID（`2124465754183725056`），否则入库前 `check_duplicate` 与回链 URL 会对不上。
   - **`normalize_url(url)`**：
     - 将上述所有 X/Twitter/fx/vx 镜像域名统一改写为 `https://x.com/<user>/status/<id>`（或 `https://x.com/i/status/<id>`），并**剥离 query 与 fragment**（如 `?a=...&s=20`），因为 `yt-dlp` 不识别 `fxtwitter`/`vxtwitter` 域名。
   - **多视频推文处理（Playlist 防崩）**：
     - 当一条推文含多个视频时，`yt-dlp` `extract_info` 返回 `_type == "playlist"` 且顶层无 `duration`，直接 `int(info.get("duration", 0))` 会得 0 或异常。
     - 在 `extract_metadata` 中：若 `info.get("_type") == "playlist"` 或含 `entries`，取第一个非空 entry（`entries[0]`）提取时长、缩略图、上传日期等，但 `meta.video_id` 仍保持推文 status ID。
     - 在 `download_audio` 中：当 `platform == "x"` 时，向 `ydl_opts` 传入 `"playlist_items": "1"`（或 `"noplaylist": True`），确保只下载第 1 个视频的音频。
   - **标题清洗（Title Derivation）**：
     - `yt-dlp` 默认给的 X 标题形如 `"Royce Chen - 卧槽！！OpenAI 联合创始人... "`。
     - 针对 `platform == "x"`：优先取 `info.get("description")`（完整推文正文），剥离末尾 `https://t.co/\S+` 链接，合并多余空白，取首行或首句，截断至约 80 字符；若正文为空再回退到 `info.get("title")`。`uploader` 取 `info.get("uploader") or info.get("channel")`。
   - **回放链接 `generate_playback_url(platform, video_id, start_seconds)`**：
     - `platform == "x"` 时返回 `f"https://x.com/i/status/{video_id}"`（X 网页播放器不支持时间戳跳转参数）。
   - **类型化错误码映射（Typed Error Codes）**：
     - 定义 `XPlatformError(RuntimeError)`（携带 `.code` 属性，与 `XiaoyuzhouError` / `ApplePodcastsError` 同构，并在 `task_runner.py` 中同样捕获并写入 `error_code`）：
       - `x_no_video`：捕获 `yt-dlp` 报错含 `"No video could be found in this tweet"` 或图文推文无音视频流。
       - `x_unavailable`：推文已删除、账号受保护（protected）、NSFW/年龄限制需登录（`"NSFW tweet"` / `"Use --cookies"` / `"status is unavailable"`）。

2. **`service/video_service.py` & `database.py` & `task_runner.py`**
   - `_PLATFORM_TAG_MAP` 与 `database.py::_ensure_platform_tags` 新增 `"x": ("X", "#000000")`。
   - **主流程 `run_analysis`**：`meta.platform == "x"` 时不拉平台字幕，SSE 发送 `detail={"method": "whisper", "reason": "X video (no platform subtitles)"}`，并把 `no_sub_reason` 设为 `X video (no platform subtitles).`；不要再重复发第二条无字幕事件。
   - **断点重试 `resume_analysis`**：复用通用元数据/下载分支，捕获 `XPlatformError` 并把 `e.code` 写进 `error_code`；该函数没有字幕阶段，不需要额外分支。
   - 下载与时长/确认阈值走视频档（与 YouTube / B 站同分支），不得误入播客阈值。
   - `task_runner.py`：在捕获 `(XiaoyuzhouError, ApplePodcastsError)` 处并入 `XPlatformError`，将 `e.code` 透传给前端。

3. **飞书双路协同（`backend/src/video_split/service/feishu.py`）**
   - **现状冲突点**：原先 `pick_supported_url(urls)` 因 `detect_platform` 不认识 X 而返回 `None`，才落入下方的 `pick_x_urls(urls)` 转发 `vedio-favorate` 分支（见 `feishu.py:798-825`）。一旦 `detect_platform` 支持 `"x"`，`pick_supported_url` 会直接命中 X 链接并跳过 `vedio-favorate` 转发。
   - **重构逻辑**：
     1. 消息中若含有 X 链接（`pick_x_urls(urls)` 非空）且 `load_vfav_forward() is not None`：**先执行 `vedio-favorate` 转发**并回复收藏结果卡片（保持既有转发能力不退化；若某条是纯图文推文且无其他视频链接，用户体验与之前完全一致）。
     2. 同时检查是否存在可解析的视频 URL：
        - 若 picked 是非 X 平台（YouTube / B 站 / 小宇宙 / Apple Podcasts），照常直接启动解析。
        - 若 picked 是 `"x"`，先通过轻量探测（`asyncio.to_thread` 调 `yt-dlp` `extract_info(url, download=False)`，带超时控制）判断该推文是否包含视频：
          - **含视频**：继续向下调用 `start_analysis_for_user` 启动解析并回复解析卡片。
          - **无视频（`x_no_video` / 纯图文推文）**：若刚才已触发 `vedio-favorate` 转发，则静默结束（不再弹报错卡骚扰用户）；若未配置 `vedio-favorate`，则正常回复“该推文不包含视频”错误提示卡片。
   - 同步更新 `feishu.py` 帮助卡片文案与 `docs/feishu-bot.md`。

4. **其余后端触点**
   - `schemas.py`：更新 URL 字段 `description` 中的支持平台列表。
   - `api/admin.py`：更新 `platform` 查询参数描述。
   - `README.md`：平台支持矩阵表补充 X（Twitter）。

### 2.3 前端设计要点（Frontend Touch-points）

参照 `git show 4bc5382 -- frontend/` 与 `git show 12fa46c -- frontend/`：
1. **`frontend/src/stores/analysisStore.ts`**：
   - `export type AnalysisPlatform = 'youtube' | 'bilibili' | 'xiaoyuzhou' | 'apple_podcasts' | 'x';`
2. **`frontend/src/lib/utils.ts`**：
   - URL 识别与校验支持 `x.com` / `twitter.com` / `fxtwitter.com` / `vxtwitter.com` / `fixupx.com` / `fixvx.com` 的 `/status/<digits>`。
   - 平台显示名映射增加 `x: 'X'`。
   - 播放跳转链接生成：`platform === 'x'` 返回 `https://x.com/i/status/${videoId}`。
3. **`frontend/src/pages/analyzeHelpers.ts`**：
   - `HIDDEN_STAGES_BY_PLATFORM` 中为 `x` 隐藏 `new Set(['subtitle_check'])`。
   - URL 校验与平台图标/样式映射补齐 `x`。
4. **`frontend/src/pages/AnalyzePage.tsx`**：
   - 顶部支持平台 Tab / 标签栏新增 `X` 入口与示例占位提示。
   - 错误码映射新增 `x_no_video` 与 `x_unavailable`。
5. **`frontend/src/i18n/locales/zh.ts` & `en.ts`**：
   - 新增平台文案、`x_no_video`（“该推文不包含视频” / "No video found in this tweet"）、`x_unavailable`（“该推文不可访问（已删除、受保护或受限）” / "This tweet is unavailable (deleted, protected, or restricted)"）。
   - 遵守 `AGENTS.md`「UI 内容与验收」规则：精炼不啰嗦，不加多余解释性注脚。

---

## 3. 代码评审标准（Review & Acceptance Criteria）

无论是派 Review 子 agent 还是主会话自检，必须逐条核对以下 Checklist：

### 3.1 功能与边界条件（Blocking Criteria）
- **ID 一致性**：`detect_platform("https://x.com/rchen1386/status/2124465845284008268?a=1&s=20")` 必须返回 `("x", "2124465845284008268")`，且 `extract_metadata` 产出的 `VideoMeta.video_id` 也是 `"2124465845284008268"`（绝不能变成底层 media ID `2124465754183725056`，否则 `check_duplicate` 去重与断点恢复会失效）。
- **镜像域名归一化**：`fxtwitter.com`、`vxtwitter.com`、`twitter.com`、`mobile.x.com` 传入 `normalize_url` 后均转为干净的 `https://x.com/.../status/<id>`，再交给 `yt-dlp`。
- **代理正确性**：`_DIRECT_PLATFORMS` 绝对不能包含 `"x"`；`proxy_enabled=True` 时 X 必须走代理。
- **多视频推文不崩**：`yt-dlp` 返回 `_type: "playlist"` + `entries: [...]` 时，`extract_metadata` 正确取 `entries[0]`，下载带 `playlist_items: "1"`。
- **断点续传/重试路径覆盖**：`run_analysis` 与 `resume_analysis` 都要正确处理 `"x"`（后者没有字幕阶段，但要透传 `XPlatformError` 的 `error_code`）。
- **飞书双路不回归**：
  - 配置了 `vfav` + 带视频 X 推文 → 既调用 `forward_to_vfav` 回复收藏卡，又启动 `start_analysis_for_user`。
  - 配置了 `vfav` + 纯图文 X 推文 → 只调用 `forward_to_vfav` 回复收藏卡，**不**创建失败的解析任务。
  - 未配置 `vfav` + 带视频 X 推文 → 直接启动解析。
  - 原有 YouTube / B 站 / 小宇宙 / Apple Podcasts 飞书解析测试 100% 通过。
- **OpenAPI 文档契约**：`cd backend && .venv/bin/python -m pytest tests/test_apidocs.py -q` 必须通过（遵循 `AGENTS.md` API 文档自动派生约束）。
- **文档合规**：遵循 `AGENTS.md`「文档即现状」与「配置值只在配置源维护」，文档中不得出现“本次新增/v2 修改”字样，不得抄录配置当前值或密钥。

### 3.2 验证闭环命令（Verification Gate）
1. **后端单测与 Lint**：
   ```bash
   cd backend && .venv/bin/python -m pytest -q
   cd backend && .venv/bin/python -m ruff check src tests
   # venv 未装 ruff 时用 uvx/pipx 取出与 uv.lock 一致的版本
   ```
2. **前端类型检查与构建**：
   ```bash
   cd frontend && npm run check && npm run build
   ```
3. **真实推文端到端冒烟测试**：
   - 使用 `https://x.com/rchen1386/status/2124465845284008268?a=F25JJuNRRsuJxjooklI&s=20` 验证元数据提取、音频下载、转写、分段分析、脑图生成与断点/并发配额逻辑。
4. **生产部署与线上探活**（合并 push 后）：
   ```bash
   git push
   ./manage.sh deploy
   ```
   随后在生产环境验证 `GET /health` 及一个只读端点正常返回。

---

## 4. 参考技能

实现或收尾时可按需调用以下 Skill：
- **`code-review`**（`/Users/mingjian/.agents/skills/code-review/SKILL.md`）：做独立 pass 评审与假阳性过滤，对照本文第 3 节标准逐项核查。
- **`code-simplifier`**（`/Users/mingjian/.agents/skills/code-simplifier/SKILL.md`）：合并前精简新增分支判断，确保改动最小化。
- **`ui-simplify`**（`/Users/mingjian/.agents/skills/ui-simplify/SKILL.md`）：前端改动交付前自查，确保没有多余注脚或啰嗦导航。
- **`my-proxy`**（`/Users/mingjian/.agents/skills/my-proxy/SKILL.md`）：本机若在无代理环境变量的 shell 中跑真实 X 推文冒烟测试且直连超时，按该规约临时使用 `http://127.0.0.1:7897`。
- **`manage-sh`**（`/Users/mingjian/.agents/skills/manage-sh/SKILL.md`）：执行 `./manage.sh rebuild` / `./manage.sh deploy` 部署到生产机 Mac mini 时遵循生命周期与部署盖章规约。
