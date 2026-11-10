# plan.md — 里程碑拆解（交付层）

```
作用：把 spec.md 拆成可独立验证的里程碑，不是 todo，是交付单元。
写入时机：spec.md 冻结后生成；里程碑完成后删除其详情节（总览表行保留、
状态改 done）；作废/拆分不删行，状态改 dropped 并备注原因。
读取时机：开始新里程碑时 AI 读一次，了解依赖和验证条件。
```

---

## 拆解原则

1. **每个里程碑必须有可执行的验证命令**（lint / type / test / build / 自定义脚本）
2. 一个里程碑 ≤ **半个上下文窗口**的工作量（约 30K token），超出就再拆
3. 里程碑之间是**串行**的——上一个不绿，下一个不开始
4. 里程碑标题用动词开头（"添加 X"、"重构 Y"），不要写"完成第二阶段"这种空标题

## 状态机（跨 session / 跨模型交接用）

- 取值：`todo` / `doing` / `done` / `blocked` / `dropped`；同一时刻 `doing` 最多一个。
- 完成判据只认**验证命令退出码**，不认自我判断。
- 作废/拆分**不删行**：状态改 `dropped`+备注原因，再新增行。
- 完成一步 → 立即改状态 → `git commit` → 再下一步。

---

## Milestone 1 — <标题>

**目标**：<一句话>

**依赖**：无 / Milestone N

**实现要点**：
- 文件/模块层面的关键改动（不到行级，行级在 `implement.md` 里）
- 关键设计决策（为什么这样做，权衡是什么）

**测试设计**（先于实现）：
- [ ] 写新 case 前先查本工程已有测试（索引/搜索，记录候选 nodeid）；命中时优先扩展已有 case/场景矩阵，不平行堆新文件
- [ ] `test_X_when_Y_then_Z` — 验证主路径
- [ ] `test_X_when_invalid_input` — 验证边界
- [ ] `test_X_when_dependency_fails` — 验证失败路径

**验证命令**：
```bash
./manage.sh check && <针对性测试，如 ./.venv/bin/pytest tests/test_X.py>
```

**完成标准（DoD）**：走 `milestone-dod` skill 的 5 步流水线（TDD → 验证链 →
code review → simplify → 归档），本文件不复述。

**状态**：`todo` / `doing` / `done` / `blocked` / `dropped`（见顶部状态机）

---

## Milestone 2 — <标题>

（同上结构）

---

## Milestone N — <标题>

（同上结构）

---

## 里程碑总览

| # | 标题 | 状态 | 验证命令 |
|---|------|------|---------|
| 1 | … | todo | … |
| 2 | … | todo | … |
