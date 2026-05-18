# Scope

只关注本目录，除非用户显式指定，禁止引用父目录下其他子工程代码。
code-review-graph 速查：入口 get_minimal_context(task)；关系查询 query_graph(pattern=callers_of|callees_of|imports_of|tests_for|children_of|file_summary|importers_of|inheritors_of, target="本工程符号/文件路径", detail_level=minimal)；语义搜索 semantic_search_nodes(query, kind) 限定精确关键词防跨工程。
