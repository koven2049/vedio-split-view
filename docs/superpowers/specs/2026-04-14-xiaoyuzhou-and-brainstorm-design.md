# 小宇宙播客支持 & 思维导图（Brainstorm）设计文档

## 概述

两个独立但互补的功能：

1. **小宇宙播客接入**：作为与 YouTube / Bilibili 平级的第三平台，支持粘贴小宇宙单集链接进行分析
2. **思维导图（Brainstorm）**：为所有平台的已分析内容提供结构化卡片式的主题总结视图

---

## 功能一：小宇宙播客支持

### 平台检测

`detect_platform` 新增正则：

```
xiaoyuzhoufm\.com/episode/([a-f0-9]{24})
```

返回 `("xiaoyuzhou", episode_id)`。

`normalize_url` 新增对 `xiaoyuzhoufm.com` 的处理（确保 `https://www.` 前缀）。

前端 `detectPlatform` 同步新增 `xiaoyuzhoufm.com` 匹配。

### 元数据提取

新建 `backend/src/video_split/service/xiaoyuzhou.py`。

通过 httpx 请求单集页面 HTML，解析以下字段：

| 来源 | 字段 | 目标 |
|------|------|------|
| `og:title` | 单集标题 | `VideoMeta.title` |
| `og:audio` | 音频直链（m4a） | 返回值中单独携带，不存入 VideoMeta（仅下载阶段使用） |
| `og:image` | 封面图 | `VideoMeta.thumbnail` |
| `og:description` | 描述 | 不单独存储，供 LLM 参考 |
| JSON-LD `timeRequired` | 时长（如 `PT81M`） | `VideoMeta.duration_seconds` |
| JSON-LD `datePublished` | 发布时间 | `VideoMeta.upload_date` |
| JSON-LD `name`（PodcastSeries）| 播客名 | `VideoMeta.uploader`（复用字段，语义为"节目/频道名"） |

解析方式：正则提取 `<meta>` 标签和 `<script type="application/ld+json">` 内容，不引入 BeautifulSoup 依赖。

### 音频下载

小宇宙音频为 m4a 直链，直接用 httpx 下载到临时目录，不走 yt-dlp + ffmpeg。

`downloader.py` 新增 `download_xiaoyuzhou_audio(audio_url, dest_path)` 函数。

### 分析流程

`video_service.py` 中 `run_analysis` 新增 `xiaoyuzhou` 分支：

1. **metadata** — 调用 `xiaoyuzhou.py` 的 `extract_xiaoyuzhou_metadata(url)` 解析页面
2. **跳过字幕检查** — 小宇宙无字幕 API，100% 走 ASR
3. **audio_download** — httpx 直接下载 m4a
4. **transcription** — 复用现有 ASR 流程（OpenAI Whisper / Fun-ASR）
5. **analysis** — 复用现有 LLM 分析流程
6. **persist** — 复用现有存储逻辑，自动打 "小宇宙" 标签

### 前端变更

**AnalyzePage.tsx：**
- Tab 栏新增第三个 Tab「小宇宙」
- URL placeholder：`https://www.xiaoyuzhoufm.com/episode/...`
- URL 验证：`xiaoyuzhoufm.com` 域名匹配
- `detectPlatform` 新增 `xiaoyuzhoufm.com` → `'xiaoyuzhou'`

**播放跳转：**
- 格式：`https://www.xiaoyuzhoufm.com/episode/{id}?t={seconds}`
- `generate_playback_url` 和前端 `getPlaybackUrl` 同步新增

**Library / VideoDetailPage：**
- 平台标签显示为「小宇宙」
- 其余功能（摘要、分段、中英切换、标签管理）完全复用

### 配置

小宇宙无需额外配置。不需要 cookie、不需要认证、不需要代理（国内平台直连）。

### 导出/导入

`data_sync.py` 无需修改。`platform` 字段值为 `"xiaoyuzhou"`，`export` / `import` 自动兼容。`manage.sh export xiaoyuzhou` 自动支持按平台过滤。

---

## 功能二：思维导图（Brainstorm）

### 定位

VideoDetailPage 的新 Tab，与现有「分段列表」并列。展示 LLM 按主题重新组织的结构化卡片，包含章节概述、关键要点和关键引用。

### 生成策略

- **延迟生成**：仅在用户首次点击「思维导图」Tab 时触发
- **持久化**：生成结果存入 DB，后续访问直接读取
- **失败重试**：生成失败时显示错误 + 重试按钮，下次点击继续尝试
- **手动刷新**：已生成的思维导图可通过刷新按钮重新生成（需二次确认，因消耗 token）

### 数据模型

Video 表新增：

```sql
ALTER TABLE videos ADD COLUMN mindmap_json TEXT DEFAULT '';
```

JSON 结构：

```json
{
  "chapters": [
    {
      "title": "章节中文标题",
      "title_en": "Chapter English Title",
      "summary": "一句话中文概述",
      "summary_en": "One-line English summary",
      "key_points": [
        { "text": "中文要点", "text_en": "English key point" }
      ],
      "quotes": [
        {
          "text": "中文引用原文",
          "text_en": "English translation of quote",
          "time_ref": "23:15"
        }
      ]
    }
  ],
  "usage": {
    "model": "glm-5.2",
    "prompt_tokens": 3200,
    "completion_tokens": 1800,
    "total_tokens": 5000
  },
  "generated_at": "2026-03-10T15:30:00+08:00"
}
```

### 数据量控制

按内容时长自动控制：

| 内容时长 | 章节数 | 每章要点数 | 关键引用 |
|---------|--------|-----------|---------|
| < 10 分钟 | 2-3 | 2-3 | 1-2 条 |
| 10-30 分钟 | 3-5 | 3-4 | 2-3 条 |
| 30-60 分钟 | 4-6 | 3-5 | 3-5 条 |
| 1-3 小时 | 5-8 | 3-5 | 4-6 条 |

原则：宁少勿多，每个要点必须有实质信息量。通过 LLM prompt 中的指引控制。

### 后端 API

| 端点 | 方法 | 描述 |
|------|------|------|
| `GET /api/videos/{id}/mindmap` | GET | 返回已有 mindmap 数据，或 `{"status": "not_generated"}` |
| `POST /api/videos/{id}/mindmap` | POST | 生成思维导图，返回 SSE 进度流 |
| `POST /api/videos/{id}/mindmap?refresh=true` | POST | 强制重新生成 |

SSE 进度事件：
- `{"stage": "generating", "progress": 10, "message": "分析主题结构..."}`
- `{"stage": "stage1_done", "progress": 60, "message": "提取关键引用..."}`
- `{"stage": "complete", "progress": 100, "data": {...}}`
- `{"stage": "failed", "error": "..."}`

### 两阶段 LLM 调用

新建 `backend/src/video_split/service/brainstorm.py`。

**Stage 1：主题划分 + 要点提炼**

输入：
- 视频标题、平台、总时长
- 所有 Segments 的 `segment_index`、`title`、`summary`、`start_seconds`、`end_seconds`

Prompt 要求 LLM：
- 根据 Segments 内容按主题重新组织（可合并、拆分）
- 每章输出：中英双语标题、中英双语概述、中英双语关键要点
- 按时长范围指引控制章节数和要点数
- 输出严格 JSON 格式

**Stage 2：精确引用提取**

输入：
- Stage 1 的章节列表（作为结构上下文）
- 原始字幕文本（`subtitle_json` 解析后的纯文本，或 `raw_transcript`）

Prompt 要求 LLM：
- 为每个章节从原文中提取 1-2 条最有价值的直接引用
- 引用必须是原文中实际存在的内容（非改写）
- 附带时间参考（从字幕时间戳推断）
- 提供英文翻译
- 输出严格 JSON 格式

**容错：**
- Stage 1 失败 → mindmap_json 保持空，返回 failed 事件
- Stage 1 成功 + Stage 2 失败 → 保存 Stage 1 结果（chapters 无 quotes），前端正常展示
- Token usage 从两阶段的 API response 中累加

### 前端组件

**VideoDetailPage Tab 切换：**

```
[ 分段列表 ]  [ 思维导图 ]
```

**MindmapView.tsx 组件：**

状态流转：
1. `not_generated` → 居中展示"生成思维导图"按钮 + 说明文字（"AI 将按主题重新组织内容，提炼关键要点和引用"）
2. `generating` → 进度指示（Stage 1: 分析主题结构... → Stage 2: 提取关键引用...）
3. `complete` → 结构化卡片 + 右上角 LangToggle + 刷新按钮
4. `failed` → 错误信息 + 重试按钮

**卡片布局：**
- 顶部统计栏：`N 个章节 · M 个要点 · K 条引用`
- 每章一张卡片：
  - 左边框颜色按章节索引循环（蓝、紫、粉、橙、绿...）
  - 章节序号 + 标题（中/英切换）
  - 概述文本
  - 关键要点 bullet list
  - 引用区块（斜体 + 引号样式 + 时间戳可点击跳转播放）
- 底部：生成时间 + model + token 消耗

**LangToggle：** 复用现有组件，控制 title/title_en、summary/summary_en、key_points text/text_en、quotes text/text_en 的切换。

### Usage 累加

思维导图生成的 token 消耗累加到 `user.usage_stats_json`，与视频分析的 token 合并统计，在 Settings 页面可见。

### 数据库迁移

`database.py` 的 `_MIGRATIONS` 新增：
```python
("videos", "mindmap_json", "TEXT DEFAULT ''")
```

### 导出/导入

`data_sync.py` 的 `_video_to_dict` 新增 `mindmap_json` 字段，`import_videos` 同步读取。已导出的旧 JSON 文件没有此字段时，导入后默认为空（用户可在新环境重新生成）。

---

## 不在范围内

- 思维导图的导出为图片/PDF（后续可加）
- 小宇宙付费内容/私密播客的支持
- 小宇宙用户认证/登录
- 思维导图的编辑功能（当前为只读展示）
