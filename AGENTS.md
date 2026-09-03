# vedio-split-view Agent Instructions

> **先读并遵守公共 harness 约束**：[../AGENTS.md](../AGENTS.md) 的「通用约束」章节全部适用。
> 本文件只写本工程差异，与通用约束冲突时以本文件为准。`CLAUDE.md` 是指向本文件的软链。
> harness AGENTS.md 中的 `.workflow/` 路径从本目录看是 `../.workflow/`；本目录若有同名 `.workflow/` 文件则优先用本目录的。

## 本工程专属

- **API 文档自动派生**，不要手写端点清单。真相源 = 路由（path + `response_model` + docstring）；`GET /api/docs-data` 与 `/docs` 都来自 `app.openapi()`。对外可见性由 `apidocs.py:_VISIBLE_GROUPS`（tag → 组名）白名单控制。改 API 后跑 `cd backend && .venv/bin/python -m pytest tests/test_apidocs.py -q`。
- 后端容器**不挂载 src**：改代码必须 `./manage.sh rebuild`（`restart` 看不到新代码）。
- 运行时不要给容器 `--dns`：自定义网络上它只会被 aardvark 收成唯一上游，公网解析单点失败即 `EAI_AGAIN`。`podman_dns` 只用于 `rebuild`。开机自启用 `./manage.sh install-launchd`（系统 LaunchDaemon，需 sudo 一次）。
- 抓取路径不要回退：YouTube 用 Deno + `web_embedded`/`android`（不要锁 `js_runtimes=node`）；B 站音频走 playurl，扫码/指纹禁止出海代理；AAC 切片输出 `.m4a`，禁止 `-c copy` 进 `.mp3`。
