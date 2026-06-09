# 检索 / RAG 升级计划

> **用途**：记录 QR 本地知识库检索子系统的**现状基线**与**分阶段升级路线**。  
> **何时读**：改动 `query.py` / `indexer.py` / `hybrid.py` / `retrieval_*.py` / 检索相关配置或评测不达标时，**先读本文再动手**。  
> **状态**：截至 2026-06，阶段 A/B/C 已完成；阶段 D～F **刻意未做**，待触发条件满足再实施。

---

## 0. AI / 开发者升级流程

1. 读本文「当前基线」与「评测门槛」
2. 确认触发条件（§6）是否满足；不满足则**不要**做大工程
3. 选定阶段（§4），按该节的「改动范围」「验收」实施
4. 最小 diff；不破坏 `~/.qr` 已有索引（除明确需要 `--reindex` 的阶段）
5. 跑完整自检（§5）
6. 更新本文「变更记录」表

---

## 1. 当前基线（已完成）

### 1.1 检索管线

```
问题
 ├─ 符号精确匹配（标识符形如 foo / load_stats）     → symbol_index
 ├─ 稳定事实短路（端口/embed/context_tokens 等）   → facts.retrieval_hits
 ├─ 向量检索（Ollama bge-m3 → sqlite-vec / numpy）  → query._search_vec
 ├─ 全文检索（chunks_fts BM25）                     → hybrid.fts_search
 └─ RRF 融合 → parent_expand → 路径加分 → 词面 rerank → 同路径去重
```

### 1.2 已实现优化（2026-06）

| 能力 | 模块 | 配置键 |
|------|------|--------|
| 项目/分类过滤时向量过采样 | `retrieval_boost.vec_fetch_limit` | `retrieval_vec_oversample`（默认 8） |
| 配置题 facts 虚拟命中置顶 | `facts.retrieval_hits` | — |
| 同文件 chunk 上限 | `retrieval_boost.dedupe_by_path` | `retrieval_max_per_path`（默认 2） |
| 路径加分规则可配置 | `retrieval_boost.DEFAULT_BOOST_RULES` | `retrieval_boost_rules`、`retrieval_source_adjust` |
| 词面 rerank 同步 `scores.final` | `reranker.rerank_hits` | `rerank_enabled` |
| 问答注入项目 brief | `project_brief.ask_context_block` | — |
| 符号索引 + 代码感知分块 | `symbol_index`、`chunking` | `code_aware_chunking` |
| 增量索引 | `indexer` | `index_incremental_after_ingest` |

### 1.3 核心文件地图

| 文件 | 职责 |
|------|------|
| `qr/query.py` | `search()` / `prepare_ask()`；RRF 后 rerank；符号/facts 合并 |
| `qr/hybrid.py` | FTS 查询、RRF 融合 |
| `qr/retrieval_boost.py` | 可配置 path boost、过采样、去重 |
| `qr/retrieval_meta.py` | `source_type` 分类、`annotate_hit` 分数结构 |
| `qr/reranker.py` | 词面重叠加分（非 cross-encoder） |
| `qr/facts.py` | 稳定事实存储 + `retrieval_hits` 短路 |
| `qr/indexer.py` / `qr/chunking.py` | 分块、embed、写入 chunks/vec_chunks |
| `qr/symbol_index.py` | 符号表与精确查找 |
| `qr/project_relations.py` | 项目关系图（Suite / links / 推断） |
| `qr/retrieval_relations.py` | 阶段 C：沿 links 1 跳扩展检索 |
| `qr/eval_suite.py` | 内置 RAG 评测用例 |
| `qr/static/js/qr-core.js` | 检索结果卡片、可点击打开、跳行 |

### 1.4 分数含义（Web chip）

- `vec` / `fts`：两路原始分
- `rrf`：排名融合分（非相似度）
- `boost`：路径启发式加分（见 `retrieval_boost_rules`）
- `final`：排序用总分（rrf + boost + lexical；符号命中固定 ≈1.0）

### 1.5 评测基线

```bash
# 仅测检索命中率（不调用生成模型）
python3 -c "
from qr import eval_suite, query
cases = eval_suite.load_cases()
ok = sum(1 for c in cases if eval_suite.retrieval_ok(query.search(c['q'], k=8), c))
print(ok, '/', len(cases))
"
# 目标：内置 9 题 retrieval_ok ≥ 8/9；核心题（port/embed/context_cfg）必须全过
```

完整 RAG（含生成）：`qr eval rag` 或 `scripts/model_eval.py`（较慢）。

---

## 2. 明确不做（现阶段）

以下能力**已评估、刻意搁置**；无 §6 触发条件时不要启动：

- 本地 cross-encoder 二阶段重排
- HyDE / 多查询扩展
- 每文档多向量（标题 / 摘要 / 正文分 embed）
- 项目关系图谱检索扩展

原因：内置评测已满分；大工程带来延迟、依赖或全量 reindex 成本，边际收益不足。

---

## 3. 配置参考（`~/.qr/config.json`）

```json
{
  "embed_model": "bge-m3",
  "chunk_chars": 1200,
  "chunk_overlap": 150,
  "code_aware_chunking": true,
  "parent_expand_chars": 400,
  "rerank_enabled": true,
  "retrieval_vec_oversample": 8,
  "retrieval_max_per_path": 2
}
```

可选覆盖（一般无需手写，缺省用代码内 `DEFAULT_BOOST_RULES`）：

```json
{
  "retrieval_boost_rules": [],
  "retrieval_source_adjust": []
}
```

规则字段：`boost`、`qr_query`、`question_any`、`path_any`、`path_suffix`、`path_all`、`path_one`、`dynamic: project_ref`。

---

## 4. 分阶段升级路线

### 阶段 C — 项目关系图谱检索扩展 【已完成 · 2026-06-08】

**目标**：限定项目检索时，沿 `project_links` 1 跳扩展关联项目 chunk（`depends` / `supports` / `related`）。

**触发条件**（满足任一）：
- 用户频繁跨项目问「A 和 B 怎么协作」
- 限定单项目时检索 miss 且正确答案在关联项目

**改动范围**：
- 新增 `qr/retrieval_relations.py`（或扩 `query.py`）：`expand_projects(pid) -> list[str]`
- `query.search()`：有 `project` 参数时合并关联项目检索结果并降权（建议 ×0.85）
- Web/MCP 命中展示：`source_type` 或 tag 标「关联项目」
- 配置：`retrieval_relation_expand`（bool）、`retrieval_relation_max_projects`（默认 2）、`retrieval_relation_link_types`

**不改**：索引 schema、embed 模型

**验收**：
-  unittest：mock `project_links` 断言扩展项目 chunk 出现
-  手动：选 `dev/qr` 问依赖方 README 中的内容，能命中关联项目
-  内置 eval 9/9 不退化

**预估**：1～2 人日

---

### 阶段 D — HyDE / 多查询扩展 【优先级：中 · 代码小、延迟大】

**目标**：难检索问法通过假设文档或问句改写拓宽召回。

**触发条件**：
- 口语化/抽象问法 miss 增多
- 用户接受检索延迟 +2～5s

**方案 A — 多查询（推荐先做）**：
- LLM 生成 2 个改写问句（短 prompt，温度 0.2）
- 原问 + 改写各 embed，RRF 合并
- 配置：`query_expand_enabled`（**默认 false**）、`query_expand_n`

**方案 B — HyDE**：
- 生成 80～150 字假设答案再 embed
- 与原文 RRF；配置 `hyde_enabled`（默认 false）

**改动范围**：`query.search()` 前置扩展；`config.py` 默认值；评测对比脚本记录 P50 延迟

**风险**：幻觉改写带偏检索 → 必须默认关闭 + Web 开关

**验收**：
-  关闭时行为与现基线 bit-identical（同一 mock embed）
-  开启时自定义 eval case 口语题命中率上升
-  文档注明延迟代价

**预估**：1～2 人日

---

### 阶段 E — 本地 cross-encoder 二阶段重排 【优先级：中 · 新依赖】

**目标**：对 top-24 候选做 (query, passage) 语义 pairwise 重排，替代/增强词面 rerank。

**触发条件**：
- 多条候选 `final` 接近（差 <0.05）且经常排错
- 阶段 C/D 后评测仍卡在生成质量而非召回

**技术选型**（二选一，写入实现前定稿）：

| 方案 | 依赖 | 延迟 | 备注 |
|------|------|------|------|
| **E1** `sentence-transformers` + `BAAI/bge-reranker-base` | pip 新依赖 | ~200–800ms / 24 对 | 推荐 |
| **E2** Ollama LLM 打分 | 无新依赖 | 数秒～数十秒 | 仅作 fallback |

**改动范围**：
- 新模块 `qr/cross_rerank.py`
- `config`：`cross_rerank_enabled`、`cross_rerank_model`、`cross_rerank_top_n`
- `reranker.py`：词面 rerank 保留为 fallback
- `requirements.txt` / conda 说明更新

**验收**：
-  关闭时与现基线一致
-  开启后 eval 生成 pass 率不降、检索 ok 不降
-  `qr doctor` 检查 reranker 模型是否可用

**预估**：2～3 人日（含依赖与打包说明）

---

### 阶段 F — 每文档多向量 【优先级：低 · 架构级】

**目标**：除 chunk 向量外，为每文档增加 `title` / `summary` 向量，改善「泛问法只命中 README 标题」类场景。

**触发条件**：
- chunk 级检索对 README/PROJECT.md 类文档明显偏弱
- 愿意全量 `qr index --reindex` 且接受索引时间 ×1.5～2

**schema 草案**（实现时择一）：

```sql
-- 方案 F1：扩展 chunks
ALTER TABLE chunks ADD COLUMN kind TEXT DEFAULT 'body';  -- title|summary|body

-- 方案 F2：独立表
CREATE TABLE doc_vectors (
  doc_id INTEGER, kind TEXT, embedding BLOB, PRIMARY KEY(doc_id, kind)
);
```

**改动范围**：`db.py` 迁移、`indexer.py` 抽标题摘要、`query._search_vec` 多路查询合并、`index_health`、全量 reindex 文档

**验收**：
-  迁移脚本可重复执行
-  增量索引写入 doc 级向量
-  自定义 case：「某某项目是干什么的」优先命中 README 首段

**预估**：4～7 人日；**风险最高**，最后做

---

### 阶段 G — 小步维护（随时可做）

无需触发条件，随评测失败或用户反馈插入：

| 项 | 说明 |
|----|------|
| boost 规则沉淀 | 将新 fail case 的 `expect_paths` 转为 `retrieval_boost_rules` 条目，而非堆 `if` |
| FTS 短语/AND | `hybrid._fts_query` 支持引号短语、可选 AND 模式 |
| embed 查询缓存 | 相同 question hash 缓存 qvec（`~/.qr/cache/embed/`） |
| 意图路由 | 配置类 → facts；标识符 → symbol；概念 → 向量为主 |
| 负例降权 | 跨项目名不匹配时 transcript 额外减分 |

---

## 5. 升级后自检清单

```bash
conda activate qr
pip install -e ~/QR/dev/qr
python3 -m unittest discover -s tests
qr doctor

# 检索评测
python3 -c "from qr import eval_suite, query; ..."  # 见 §1.5

# 若动 Web
qr web --restart
# 浏览器：检索页 hybrid 分数、点击打开、符号跳行；问答引用可点
```

新增阶段必须补 `tests/test_new_features.py` 或 `tests/test_retrieval_*.py` 中用例。

---

## 6. 触发条件速查

| 阶段 | 何时启动 |
|------|----------|
| C 关系图谱 | 跨项目协作问法增多；单项目 miss 且答案在关联库 |
| D HyDE/多查询 | 口语/抽象问法 miss；用户接受更慢检索 |
| E cross-encoder | 召回够但排序经常错；C/D 不够 |
| F 多向量 | README/标题类泛问法弱；可接受全量 reindex |
| G 小步 | eval 单题失败；或明确 bug |

**默认策略**：评测 9/9 且用户无抱怨 → **保持现状**，只做 G。

---

## 7. 推荐实施顺序

```
C（关系图谱） → D（多查询，默认关） → E（cross-encoder） → F（多向量）
         ↑
    G 小步维护贯穿全程
```

---

## 8. 变更记录

| 日期 | 阶段 | 摘要 |
|------|------|------|
| 2026-06-08 | C | `retrieval_relations` + `query.search` 关联 1 跳扩展；跨项目口语问法 |
| 2026-06 | A/B | 过采样、facts 短路、去重、boost 配置化、问答 brief、符号跳行、workspace import |
| 2026-06 | 计划 | 创建本文；D～F 搁置 |

---

## 9. 相关文档

- 用法：`docs/USE_CASES.md` §2 RAG 问答
- 自检：`docs/CODE_AUDIT_CHECKLIST.md`
- 项目约定：`.cursor/rules/10-project.mdc`
