# AI 使用水平 · 完整版评测

生成时间：{{generated_at}}

> 模板：`~/.qr/templates/ai_assess_full.md` · 量表见 `docs/AI_SKILL_ASSESSMENT.md`  
> **禁止**用「看不懂源码」解释低档位；六维评的是 AI 协作与 Personal AI Ops。

## 1. 执行摘要

| 项 | 结论 |
|----|------|
| 综合档位（L） | |
| 六维均值 | /10 |
| 最强项 | |
| 最弱项 | |
| 相对上次 Δ | |

## 2. 协作者角色 · 分项证据

| 角色 | 承担者 | 本周期可观测证据 | 评分备注（1–10 或达标/未达标） |
|------|--------|------------------|-------------------------------|
| **设计者** | 本人 | 决策笔记数、规范版本、进化计划验收、`focus_project`、模块划分与约束清晰度 | |
| **AI 实现** | Cursor 等 | Git diff、unittest、对话执行记录、多文件落地 | **不**等同于「设计者编程水平」 |
| **验收** | 本人 | `qr doctor`、`qr ship-check`、`qr eval rag` core 9/9、Web/CLI 点验 | |

### 设计者证据（从 `qr ai-assess` / 库中摘录）

- 近 30 天决策 / Cursor 对话：{{decisions_30d}} / {{cursor_events_30d}}（{{decision_to_cursor_pct}}%）
- 决策者验收（ship-check）次数：{{ship_check_count}}
- 引导语 / 片段 / 已合并：{{prompt_guides}} / {{prompt_fragments}} / {{prompt_guides_merged}}
- 本周主攻项目：{{focus_project}}

### 反面示例（评测时避免）

- ❌ 「看不懂 Python 源码 → 工具链 4 分」
- ✅ 「决策笔记周均 <1、混开 workspace → 复盘/多项目扣分」

## 3. 硬数据画像

（粘贴 `qr ai-assess --save` 与 `qr eval monthly` 摘要，或引用 `~/.qr/assessments/`）

## 4. QR 六维分项

| 维度 | 分数 | 证据摘要 |
|------|------|----------|
| 提示工程 | | |
| 工具链 | | |
| 元认知 | | |
| 复盘习惯 | | |
| 多项目协作 | | |
| 领域应用 | | |

## 5. 多框架对照（重大评测时填）

| 框架 | 估算 | 备注 |
|------|------|------|
| AISA 五维 | | |
| L 阶梯 | | |
| AILit / PRL | | |

## 6. 优势 / 短板 / 90 天建议

### 优势

-

### 短板

-

### 90 天行动项

- [ ] 

## 7. 附录

- 数据时间：{{generated_at}}
- `qr doctor` 状态：（手填或粘贴）
- 检索 core：{{rag_core_summary}}
- extended（观察）：{{rag_extended_summary}}
