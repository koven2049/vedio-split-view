# 性能冲刺简报：vedio-split-view（2026-11-03）

## 职责

负责 VideoSplit 打开和使用的速度：先测基线，每一步用测量证明，赢下来的用棘轮锁住。用户看得见的变化和上线由人决定。

## 核心操作与「能用」的定义（用户 2026-11-03 选定）

| 操作 | 页面 | 起点 | 终点（真的能用） | 判定表达式 |
|---|---|---|---|---|
| 打开视频详情 | `/video/1` | 导航开始 | 摘要 + 全部 20 个分段卡片都已渲染、能点 | `document.body.innerText.includes('片段 19')` |
| 打开资源库 | `/library` | 导航开始 | 首屏 20 张视频卡片都已渲染（搜索框同时可用） | `(document.body.innerText.match(/测速视频 \d+：/g)\|\|[]).length >= 20` |
| 打开 API 文档 | `/api-docs` | 导航开始 | 端点列表可见 | `document.body.innerText.includes('/api/videos/analyze')` |
| API 响应耗时 | `/api/videos`、`/api/videos/{id}`、`/ids`、`/meta`、`/api/docs-data` | 发请求 | 收完响应体 | 回环直连，`perf/bench/api.py` |

骨架、spinner 都不算能用：判定条件都要求真实数据已经出现在页面上。

## 不能动的东西

- 视觉：页面外观不变；这轮 UI 改动只限用户点名的 Token 区和公开文档页，前后截图在报告里
- 功能：所有接口响应逐字节一致（时间戳字段除外，两个测速库造数时刻不同），见 `/tmp` 黄金比对脚本口径
- SSE 进度流不能被缓冲（gzip 必须跳过 `text/event-stream`，有测试固化）
- 无 SEO / 第三方统计

## 目标

- 主指标：三个页面「打开到能用」p75 各降一半（用户没给目标，按闪电默认）
- API：列表接口 p75 降一半
- 口径：Fast 4G（20ms RTT、4 Mbps 下行，对所有请求生效），CPU 不限速，冷缓存，每组 10 次，A/B 交替配对

## 测试数据

线上（ts-macmini-m4，只读查询）：57 个视频，字幕 JSON 平均 58 KB、最大 178 KB，逐字稿最大 93 KB，最多 20 个分段，缩略图平均约 70 KB。
测速库 `perf/bench/seed.py`：60 个视频，按**最大值**造（每行约 270 KB 大文本、20 个分段、138 KB 缩略图），让列表和详情接口的负载效应能显现出来。

## 上线与回滚

- 放行条件（用户 2026-11-03）：护栏全绿、配对测量确实变快 → 合并 push、本机 rebuild、部署 ts-macmini-m4，并回报线上验证
- 部署：`./manage.sh deploy`（rsync + 远端 rebuild；`.rsync-exclude` 排除 data/ logs/ config/）
- 回滚：`git revert <commit>` 后重新 `./manage.sh deploy`；本轮没有数据库结构变更，回滚不涉及数据
