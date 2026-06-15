# QR 知识库 · 进化计划（执行跟踪）

> 与 [`RETRIEVAL_UPGRADE_PLAN.md`](./RETRIEVAL_UPGRADE_PLAN.md) 互补：本文记录**产品向**优先级与验收，检索细节见后者。
>
> **自动同步**：`qr evolution sync` 或 `qr update` 结束时按验收规则更新状态（仅 **进行中→已完成**，不自动回退）。
> 状态缓存：`~/.qr/evolution_plan_state.json` · 全量 RAG 验收见 `qr evolution sync --full`。

## 优先级（2026-06 起）

| # | 方向 | 状态 | 验收 |
|---|------|------|------|
| 1 | **阶段 C — 项目关系检索** | 已完成 | 限定 dev/qr 问关联项目能命中；eval 9/9 不退化 |
| 2 | **跨项目问答** | 已完成 | 问「A 和 B 怎么协作」自动扩展关联项目 chunk |
| 3 | **引导语 → 规范 → 行为闭环** | 已完成 | 合并引导语的项目优先 standards-auto；每周 infer 关系 |
| 4 | **project-sports 真实 Cursor 事件** | 已完成 | 在 dev/sports/* 工作区采集到 Cursor 事件 |
| 5 | **project-sports 业务基建** | 已完成 | conda env sports；hebei-policy.md 结构化草案 |

## 刻意不做（维持）

- 移动端 / 远程访问
- 阶段 D～F（HyDE、cross-encoder、多向量）— 见 RETRIEVAL 文档触发条件

## 执行后自检

```bash
conda activate qr && pip install -e ~/QR/dev/qr
python3 -m unittest discover -s tests
qr doctor
qr evolution sync --full
```

## 变更记录

| 日期 | 项 | 摘要 |
|------|-----|------|
| 2026-06-08 | C | retrieval_relations + query.search 关联扩展 |
| 2026-06-08 | 计划 | 创建本文；启动 C + 闭环 + project-sports |
| 2026-06-15 | project-sports 真实 Cursor 事件 | 自动验收通过：体育相关 Cursor 事件 91 条（含 dev/sports/*） |
