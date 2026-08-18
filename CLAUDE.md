# Scope

只关注本目录，除非用户显式指定，禁止引用父目录下其他子工程代码。
code-review-graph 速查：入口 get_minimal_context(task)；关系查询 query_graph(pattern=callers_of|callees_of|imports_of|tests_for|children_of|file_summary|importers_of|inheritors_of, target="本工程符号/文件路径", detail_level=minimal)；语义搜索 semantic_search_nodes(query, kind) 限定精确关键词防跨工程。

# API 变更工作流（每次新增/修改/删除 API 后必做）

API 文档是**自动派生**的，不要手写端点清单。真相源 = 路由本身（path + 依赖 + `response_model` + docstring）。

- `GET /api/docs-data`（前端 ApiDocsPage 的数据源）从 `app.openapi()` 实时派生；FastAPI 原生 `/docs`、`/openapi.json` 同源。
- 改动 API 后**按顺序**：
  1. 给新端点写 **docstring**（成为文档 description）+ 尽量声明 **`response_model`**（成为响应结构）。请求体/查询参数的说明写在 Pydantic `Field(description=...)` / `Query(description=...)` 里。
  2. 决定对外可见性：只有 router `tags` 命中 `apidocs.py:_VISIBLE_GROUPS` 白名单的端点才进外部文档。要暴露新组 → 加 tag 映射；要隐藏（如 debug/admin）→ 不加。
  3. 跑 `cd backend && .venv/bin/python -m pytest tests/test_apidocs.py -q`，确认文档契约不漂移。
  4. 让运行实例生效：后端容器**未挂载 src**，改完代码必须 `./manage.sh rebuild`（restart 不重建镜像，看不到新代码）。
- 收窄/扩大外部文档范围：编辑 `apidocs.py:_VISIBLE_GROUPS`（tag → 组名），不要回退到手写列表。


**2026-08-12 三个优化同时部署**


2. **Bilibili view API 去重**  

3. **YouTube cookies 支持文档化**  











# 优化���录（APPEND ONLY，性能 + 可���性增强���

**2026-08-12 ���个优化同时部署**

1. **FFmpeg stream copy (67× ���速)**  
   ���：每��� decode → seek → encode，5700s 音频 40.1s  
   新：`-f segment -c copy` ���次完成���0.6s  
   代价：块长���化到帧���界 ±26ms，19 块累积���移 < 0.5s���可接���）  
   无 re-encode = 无���量损失  

2. **Bilibili view API 去���**  
   旧���metadata 调一次 `/view`���subtitle 再调一次 `/view` + `/player`  
   新：metadata + subtitle 共享一次 `/view`，���调 `/player` 拿字幕  
   -352 风控下耗���减半  

3. **YouTube cookies 支持���档化**  
   代���已支��� `config/app.yaml` ��� `youtube_cookies_file`，但生产未配置  
   无配 cookies → bot-check / 年龄限制视���失败，auto-caption ���可用  
   配置���需放 `config/youtube_cookies.txt` 并在 `app.yaml` 填路径

# 优化���录（APPEND ONLY���性能 + 可靠性���强）

**2026-08-12 三�����优化���������署**

1. **FFmpeg stream copy (67x 提速)**  
   旧：每块 decode → seek ��� encode，5700s 音��� 40.1s  
   新���`-f segment -c copy` 一次完���，0.6s  
   代价：块长量���到帧边��� ±26ms，19 块累积漂移 < 0.5s（���接受）  
   无 re-encode = 无质量损失

2. **Bilibili view API 去���**  
   旧：metadata 调一次 `/view`，subtitle 再调一��� `/view` + `/player`  
   新：metadata + subtitle ���享一次 `/view`，���调 `/player` 拿字���  
   -352 风���下耗时减半

3. **YouTube cookies 支持文档化**  
   代码已���持 `config/app.yaml` 的 `youtube_cookies_file`，���生产未���置  
   ���配 cookies ��� bot-check / 年龄限���视���失败，auto-caption ���可用  
   配置后需��� `config/youtube_cookies.txt` ���在 `app.yaml` 填���径
