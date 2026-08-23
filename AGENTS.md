# vedio-split-view Agent Instructions

> **先读并遵守公共 harness 约束**：[../AGENTS.md](../AGENTS.md) 的「通用约束」章节全部适用。
> 本文件只写本工程差异，与通用约束冲突时以本文件为准。`CLAUDE.md` 是指向本文件的软链。

## 本工程专属

API 文档是**自动派生**的，不要手写端点清单。真相源 = 路由本身（path + 依赖 + `response_model` + docstring）。

- `GET /api/docs-data`（前端 ApiDocsPage 的数据源）从 `app.openapi()` 实时派生；FastAPI 原生 `/docs`、`/openapi.json` 同源。
- 改动 API 后**按顺序**：
  1. 给新端点写 **docstring**（成为文档 description）+ 尽量声明 **`response_model`**（成为响应结构）。请求体/查询参数的说明写在 Pydantic `Field(description=...)` / `Query(description=...)` 里。
  2. 决定对外可见性：只有 router `tags` 命中 `apidocs.py:_VISIBLE_GROUPS` 白名单的端点才进外部文档。要暴露新组 → 加 tag 映射；要隐藏（如 debug/admin）→ 不加。
  3. 跑 `cd backend && .venv/bin/python -m pytest tests/test_apidocs.py -q`，确认文档契约不漂移。
  4. 让运行实例生效：后端容器**未挂载 src**，改完代码必须 `./manage.sh rebuild`（restart 不重建镜像，看不到新代码）。
- 收窄/扩大外部文档范围：编辑 `apidocs.py:_VISIBLE_GROUPS`（tag → 组名），不要回退到手写列表。

# 优化记录（APPEND ONLY，性能 + 可靠性增强）

**2026-08-12 三个优化同时部署**

1. **FFmpeg stream copy（67× 提速）**
   旧：每块 decode → seek → encode，5700s 音频 40.1s
   新：`-f segment -c copy` 一次完成，0.6s
   代价：块长量化到帧边界 ±26ms，19 块累积漂移 < 0.5s（可接受）
   无 re-encode = 无质量损失
   ⚠️ 2026-08-20 起：copy 前必须 ffprobe 探测 codec，AAC 输入输出 `.m4a`（见下条）

2. **Bilibili view API 去重**
   旧：metadata 调一次 `/view`，subtitle 再调一次 `/view` + `/player`
   新：metadata + subtitle 共享一次 `/view`，省调 `/player` 拿字幕
   -352 风控下耗时减半

3. **YouTube cookies 支持文档化**
   代码已支持 `config/app.yaml` 的 `youtube_cookies_file`，但生产未配置
   无配 cookies → bot-check / 年龄限制视频失败，auto-caption 不可用
   配置后需放 `config/youtube_cookies.txt` 并在 `app.yaml` 填路径

**2026-08-20 修复小宇宙长音频切片必败（exit 234）**

1. **根因**：小宇宙音频为 AAC/m4a，`split_audio` 用 `-c copy` 灌进 `.mp3` 容器，mp3 muxer 拒绝 AAC → EINVAL（exit 234）。仅"小宇宙 + 时长 > chunk_duration"触发；YouTube/Bilibili 必经 FFmpegExtractAudio 转码 mp3 所以从未中招。
2. **修复**：切片前 ffprobe 探测 codec 路由——mp3→copy+`.mp3`（保留 67× 加速），aac→copy+`.m4a`（零转码），其它→libmp3lame 转码兜底。chunks glob 按实际扩展名收集。
3. **报错工程化**：ffmpeg/ffprobe 调用去掉 `-v quiet`，失败时附 stderr 尾部 6 行（如 muxer 报错原文），不再抛裸 exit code。
4. **防复发**：切片失败清理 chunks 残桩；resume 前用 ffprobe 校验缓存时长（容差 5s），截断缓存降级为重新下载。
5. **测试**：`test_transcriber_chunking.py` 新增真实 ffmpeg 用例（程序生成 AAC 样本复现线上故障），全量 162 passed。
