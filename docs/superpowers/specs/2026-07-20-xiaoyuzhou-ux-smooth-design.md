# 小宇宙体验顺滑化重构 — Design

> date: 2026-07-20
> status: draft
> owner: koven2049
> related: docs/superpowers/specs/2026-04-14-xiaoyuzhou-and-brainstorm-design.md

## 1. 目标

把小宇宙（Xiaoyuzhou）播客分析流程从「能跑」提升到「顺滑」。聚焦 7 个体感卡点，顺带让 YouTube / Bilibili 的下载进度也受益。

### 可观察的成功

- 小宇宙单集下载时，前端进度条 15%→55% 之间持续渐进，显示「百分比 + 已下载/总字节」，不再卡死。
- 粘贴非单集页（`/podcast/`、`/user/`）或格式错链接，前端立即给出针对性文案，不到后端才报错。
- 下载/抓取失败时，错误原因可读（CDN 过期 / 付费私密 / 页面改版），并指引下一步（重试能拿新链接）。
- 1h+ 播客的确认弹窗是播客语境文案，阈值比视频更宽。
- 小宇宙 Tab 有一行支持范围说明。
- bilibili + 小宇宙无视全局 proxy，强制直连。
- 小宇宙任务不展示永远 skipped 的「字幕检查」阶段。

## 2. 范围

### In Scope

1. 下载进度接通（三平台：小宇宙、YouTube、Bilibili），asyncio.Queue 桥接 + 节流 yield。
2. 前端小宇宙链接校验加严（`/episode/<24hex>`），区分三类错误。
3. 小宇宙错误类型化（`XiaoyuzhouError` + 4 个 code），前端按 code 映射文案。
4. 小宇宙过长前置确认（播客语境文案 + 独立阈值）。
5. 小宇宙 Tab 空状态文案。
6. proxy 按平台跳过：bilibili + 小宇宙强制直连（无视 `proxy_enabled`）。
7. 前端按平台隐藏 `subtitle_check` 阶段（仅小宇宙）。

### Out of Scope

- 不改后端流水线骨架（stage 顺序、SSE 协议、ProgressEvent 字段结构）。
- 不改转写、分析、存储、导出、思维导图等下游。
- 不给 YouTube/Bilibili 加播客语境文案（仅下载进度顺带受益）。
- 不做小宇宙登录/凭证支持（仍只支持公开单集）。
- 不重构 `download_audio` / `download_xiaoyuzhou_audio` 签名。

## 3. 架构与组件

### 3.1 下载进度桥接（后端核心）

新增 `video_service._relay_download_progress(stage, base_pct, span_pct, download_coro_factory, *, message_template)`：

- 入参：stage 名（`audio_download`）、区间起点 base（15）、跨度 span（40）、一个产出 download task 的 factory、消息模板。
- 内部建 `asyncio.Queue`，把 factory 注入的 `progress_callback` 做成 `lambda ratio: q.put_nowait(ratio)`（注意线程安全：yt_dlp 的 progress_hook 在工作线程里调用，需 `loop.call_soon_threadsafe(q.put_nowait, ratio)`；小宇宙 httpx 的 callback 在同 loop，直接 put）。
- download 跑成 `asyncio.Task`，主循环 `while not task.done()`：`await asyncio.wait_for(q.get(), timeout=0.3)`，节流（≥1% 增量 或 ≥0.5s 间隔才 yield），映射 `progress = base + ratio*span`，`yield ProgressEvent(stage, progress, message=message_template(ratio, downloaded, total), detail={"ratio","downloaded_bytes","total_bytes"})`。
- task 完成后 `await task` 传播异常，最后 `yield` 一次 `progress=base+span` 收尾（避免末尾跳变）。

`_download_audio_wrapper` 改为返回 `(coro, callback_slot)` 或直接由 `_relay_download_progress` 内部构造 yt_dlp 时的 hook。具体：`download_audio` 已有 `progress_callback` 形参，wrapper 把它透传进去即可（当前 wrapper 行 499-511 没透传，是 bug）。

调用点改动：
- `video_service.py:345-350`（run_analysis 小宇宙）→ 走 `_relay_download_progress("audio_download", 15, 40, lambda cb: download_xiaoyuzhou_audio(audio_url, task_dir, progress_callback=cb))`。
- `video_service.py:352-356`（run_analysis YT/Bilibili）→ 走同一 helper，factory 调 `_download_audio_wrapper(..., progress_callback=cb)`（wrapper 需新增透传）。
- `video_service.py:560-563`（resume 小宇宙）、YT/Bilibili resume 分支 → 同样接通。

消息模板：`f"下载音频 {pct:.0f}% · {downloaded_mb:.1f} / {total_mb:.1f} MB"`，total 未知时退化 `f"下载音频 {downloaded_mb:.1f} MB"`。

### 3.2 前端进度消费

- `analysisStore.ts` `ProgressData` 类型加可选 `detail.ratio / downloaded_bytes / total_bytes`。`processEvent` 把 detail 存进 slot.progress。
- `AnalysisSlotCard`（AnalyzePage.tsx 行 53+）：`audio_download` 阶段，若 `detail` 有字节信息，进度条下方显示 `{downloaded_mb} / {total_mb} MB`；纯百分比 fallback。
- 不新增 stage，不改 STAGES 数组结构。

### 3.3 前端链接校验加严

`AnalyzePage.validatePlatformMatch`（行 404-429）小宇宙分支：
- 正则 `xiaoyuzhoufm\.com/episode/[0-9a-f]{24}`（与后端 `detect_platform` 一致）。
- 不匹配但含 `xiaoyuzhoufm.com/(podcast|user|category)/` → `analyze.xiaoyuzhouNotEpisode`。
- 含 `xiaoyuzhoufm.com` 但其余不匹配 → `analyze.xiaoyuzhouBadFormat`。
- i18n `zh/en` 各加两条。

### 3.4 小宇宙错误类型化

`xiaoyuzhou.py` 新增：

```python
class XiaoyuzhouError(RuntimeError):
    def __init__(self, code: Literal["cdn_expired","paid_private","page_changed","not_episode"], message: str): ...
```

判定：
- 页面 HTML 有效（拿到 og:title 等）但无 `og:audio` 且无 JSON-LD `contentUrl` → `cdn_expired`（音频链接缺失/过期，重试会重新抓页面）。
- 页面含付费/私密特征（启发式：HTML 含「付费」「登录后可」「VIP」且无音频链接 / duration 为 0）→ `paid_private`。
- og 元数据 + JSON-LD 全 miss（页面结构变）→ `page_changed`。
- URL 不含 `/episode/` → `not_episode`（理论上前端已拦，后端兜底）。

`task_runner.py` 捕获 `XiaoyuzhouError`，在 failed 事件 payload 写 `error_code`；前端 i18n 按 code 出文案，重试按钮始终可见（cdn_expired 文案明示「重试会获取最新链接」）。

### 3.5 过长播客前置确认

`video_service.run_analysis` 小宇宙分支（行 226-241 附近 confirm 逻辑）：
- 新增 `settings.limits.podcast_confirm_threshold_seconds`（默认 7200，比视频更宽）。
- 小宇宙用 podcast 阈值；confirm 事件 `detail.platform="xiaoyuzhou"`。
- 前端 confirm 弹窗按 `detail.platform` 选文案：小宇宙用 `analyze.confirmLongPodcast`（「这期播客 {duration}，转写耗时较长，确认开始？」）。

### 3.6 Tab 空状态文案

`AnalyzePage.tsx` 小宇宙 Tab 下方（行 458-477 区块）加一行灰字提示：`analyze.xiaoyuzhouTabHint`（「支持公开单集；付费/私密内容暂不支持」）。i18n zh/en。

### 3.7 proxy 按平台跳过

新增 `downloader._should_use_proxy(platform) -> bool`：`platform in {"bilibili","xiaoyuzhou"}` → False，否则按 `settings.network.proxy_enabled`。

替换点：
- `downloader.py:242-245`（extract_video_metadata yt-dlp fallback）
- `downloader.py:309-311`（thumbnail httpx）
- `downloader.py:417-420`（download_audio yt-dlp）
- `xiaoyuzhou.py:127-129`、`200-202`（metadata + audio httpx）

bilibili 的 `_fetch_bilibili_metadata_via_api` 若内部也设 proxy，同样替换（实现时核实）。

### 3.8 subtitle_check 按平台隐藏

`AnalysisSlotCard` stage 圆点渲染（行 174-193）：`slot.platform === 'xiaoyuzhou'` 时 `STAGES.filter(s => s !== 'subtitle_check')`。后端仍发 `subtitle_check` 事件（不改流水线），前端纯展示过滤。

## 4. 数据流

下载进度（小宇宙为例）：
```
httpx stream chunk
  → progress_callback(downloaded/total)
  → asyncio.Queue.put_nowait(ratio)   [同 loop]
relay loop
  → 节流 → ProgressEvent(stage=audio_download, progress=15+ratio*40, detail={ratio,bytes})
  → yield → task_runner broadcast → SSE
前端 analysisStore.processEvent
  → slot.progress.detail = {...}
AnalysisSlotCard
  → 渲染百分比 + MB 文案
```

## 5. 错误处理

- 下载异常分两类：`XiaoyuzhouError`（已知 code，前端友好文案）vs 其他 `Exception`（通用「下载失败，请重试」）。两者都让 task 转 failed，但 detail 带 code。
- relay task 取消：`cancel_event` 触发时，relay 取消 download task 并退出。
- queue 满 / callback 抛错：put_nowait 不阻塞，最坏丢弃中间进度帧，不影响正确性。

## 6. 测试设计

后端 pytest（`test/test_xiaoyuzhou_ux.py` 新增）：
- `test_download_progress_relay_progressive` — mock download 流式 yield 多 chunk，断言 helper 产出渐进 ProgressEvent（首 15、末 55、中间单调）。
- `test_download_progress_throttle` — 高频 callback，断言节流后 yield 数 < callback 数。
- `test_download_progress_threadsafe_callback` — 模拟线程内 callback（yt_dlp 场景），断言不抛错。
- `test_xiaoyuzhou_error_cdn_expired` — HTML 有效但无 og:audio → code=cdn_expired。
- `test_xiaoyuzhou_error_paid_private` — 付费特征 → code=paid_private。
- `test_xiaoyuzhou_error_page_changed` — og 全 miss → code=page_changed。
- `test_proxy_bypass_bilibili` — proxy_enabled=True，bilibili client/ydl_opts proxy 为空。
- `test_proxy_bypass_xiaoyuzhou` — 同上，小宇宙 httpx proxy=None。
- `test_proxy_applied_youtube` — youtube 仍走 proxy。
- `test_podcast_confirm_threshold` — 小宇宙 1.5h 不触发 confirm，2.5h 触发。

前端测试：
- `validatePlatformMatch` — 4 case：合法 episode、`/podcast/` 页、格式错、非小宇宙链接。
- `AnalysisSlotCard` — 小宇宙 platform 不渲染 subtitle_check 圆点；youtube 仍渲染。

## 7. 验证链

```bash
# 后端
cd backend && .venv/bin/python -m pytest tests/ -q

# 前端
cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

后端容器未挂载 src，验证通过后 `./manage.sh rebuild` 让运行实例生效。

## 8. 实现并行度

三平台下载进度接通、错误类型化、proxy 跳过、前端校验/UI 改动，彼此文件边界清晰。实现阶段派并行 subagent：

- Agent A：后端进度 relay helper + 三平台调用点接通（video_service.py + downloader.py wrapper）。
- Agent B：后端 xiaoyuzhou 错误类型化（xiaoyuzhou.py + task_runner.py）+ podcast confirm。
- Agent C：后端 proxy 平台跳过（downloader.py + xiaoyuzhou.py，新增 `_should_use_proxy`）。
- Agent D：前端（AnalyzePage 校验 + Tab hint + subtitle_check 隐藏 + 进度 MB 渲染 + i18n）。

**文件归属（避免合并冲突）**：
- `video_service.py` — A（relay helper + 调用点）、B（podcast confirm 分支）。A/B 改不同函数，并行后主 session 合并。
- `downloader.py` — A（`_download_audio_wrapper` 透传 callback）、C（`_should_use_proxy` + 4 处替换）。**C 串在 A 后**，避免同文件并发写。
- `xiaoyuzhou.py` — B（错误类型化）、C（proxy 跳过 2 处）。**C 串在 B 后**。
- 前端 — D 独占，与后端零冲突。

**执行顺序**：A、B、D 并行（三波）→ C 串行接 A/B 之后 → 主 session 合并 + 跑完整验证链 + rebuild。

## 9. 未决问题

无。

## 10. 变更历史

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-07-20 | 初稿 | 小宇宙体验顺滑化 |
