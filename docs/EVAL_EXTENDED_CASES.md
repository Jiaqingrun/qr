# RAG 扩展评测题集（extended）

> **M4-1** · 与 **core 门禁 9 题** 分离；extended **不阻断发布**，用于口语/叙事问法与周报观察。  
> 命令：`qr eval rag --extended` · 月报附录：`qr eval monthly --save`

## 分栏说明

| 分栏 | tier | 题量 | 用途 |
|------|------|------|------|
| **core** | `core` / `hard` / `trap` / `negative` | 9 | 进化计划 / doctor 门禁（须 9/9 且无泄漏） |
| **extended** | `extended` | 6+ | 口语、跨文件叙事、决策/规范检索；周报展示 |

自定义题写入 `~/.qr/eval_cases.json`，`tier: extended` 即归入扩展栏。

## 内置 extended 题（期望命中路径）

### oral · 口语问法

| id | 问法 | expect_paths | 说明 |
|----|------|--------------|------|
| `oral_port` | 知识库网页默认几号端口来着？ | config.json, config.py, web.py, /.qr/ | 口语问法；期望 web_port **8765** |
| `oral_data_dir` | 运行数据放哪个隐藏目录？就本机知识库那个。 | config.py, /.qr/, QR_HOME, standards | 期望 **~/.qr** / QR_HOME |
| `oral_conda` | 这个知识库项目规定用哪个 conda 环境名？ | STANDARDS, standards.md, README, AGENTS | 期望环境名 **qr** |

### narrative · 跨文件叙事

| id | 问法 | expect_paths | 说明 |
|----|------|--------------|------|
| `narrative_schedule` | 装了 schedule 之后，每周大概会自动跑哪些维护任务？ | cli.py, schedule, standards, EVOLUTION | schedule / weekly / update |
| `narrative_retrieval_plan` | 检索升级计划里什么时候才考虑上 HyDE 或多查询？ | RETRIEVAL_UPGRADE, RETRIEVAL, query.py | 触发条件见 RETRIEVAL_UPGRADE_PLAN |

### decision · 决策/规范检索

| id | 问法 | expect_paths | 说明 |
|----|------|--------------|------|
| `decision_milestone` | 里程碑结束至少要记一条什么类型的日志？ | STANDARDS, standards.md, USE_CASES, decision | 期望 `qr log --type decision` |

## 命中判定

与 core 相同：Top-6 检索结果路径中须包含 `expect_paths` 任一子串；不得命中 `eval_suite.py` / `model_eval.py` 等考题泄漏标记。

## 维护

- 源码题集：`qr/eval_suite.py` → `EXTENDED_BUILTIN_CASES`
- 新增题后执行 `qr index` 确保相关文档已索引
- 对照 `qr eval rag --extended` 与月报 extended 段落观察趋势
