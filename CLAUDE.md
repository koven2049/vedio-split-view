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
