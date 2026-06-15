# AI 使用水平评测规则

> 本文件为 **完整量表与对照框架**；全局规范摘要见 `standards/STANDARDS.md` §五。  
> 执行：`qr ai-assess --save`（日）· `qr eval monthly --save`（月）· 洞察页每日计划勾选。

## 1. 评测原则

1. **行为证据优先**：以 `~/.qr/qr.db`、屏幕采样、facts、eval 结果为主，不以自评问卷为主。
2. **多框架对照**：至少覆盖 QR 六维 + AISA 五维 + L 阶梯；重大评测可对照 AILit / PRL。
3. **纵向可比**：日快照 `~/.qr/assessments/`、月报 `~/.qr/eval_monthly/`；对照上月与 `docs/RETRIEVAL_UPGRADE_PLAN.md` 触发条件。
4. **不替代认证**：本评测供个人复盘与规划；对外证明须附 eval 快照与时间线。

## 2. QR 六维工程化雷达（主量表）

各维 **1–10 分**，参照个人开发者群体；**10 ≈ 该维顶尖实践**。

| 维度 | 考察什么 | 高分证据（可观测） | 常见扣分 |
|------|----------|-------------------|----------|
| **提示工程** | 任务拆解、约束、迭代、可复用 prompt | 引导语精炼、前缀规范、多轮纠偏、验收标准明确 | 模糊目标（「继续优化」）、碎片未合并 |
| **工具链** | Personal AI Ops：采集/RAG/治理/评测/MCP | 自研或深度定制 qr 类系统、eval 达标、launchd | 仅聊天无归档、无评测 |
| **元认知** | 对 AI 能力边界与升级时机的判断 | 写清「不做什么」、触发条件、指标追问 | 无限堆功能、不读 eval 结果 |
| **复盘习惯** | log、总结、决策、评测节奏 | 日/月评测、决策笔记、周期 summary | 对话多决策少、无快照序列 |
| **多项目协作** | 工作区隔离、MCP project、PROJECT 分层 | 每仓独立 Cursor 根、facts 分项目 | 混开 workspace、跨项目检索污染 |
| **领域应用** | AI 在业务上的落地深度 | sports MVP、scribe 长篇、边缘部署等 | 只有规划无代码/无文档闭环 |

**综合分** = 六维算术均值（保留一位小数）。  
**档位**：≤6.0 进阶前 · 6.1–7.5 进阶 · 7.6–8.5 L4+ · 8.6–9.2 L4+ 逼近 L5 · ≥9.3 L5 单项触达。

## 3. 个人 AI 工程化 L1–L6

| 级别 | 特征 | 判定 |
|------|------|------|
| L1 | 偶尔问答 | 无规则、无归档 |
| L2 | Tab/Chat 熟练 | 少量 User Rules |
| L3 | Agent 多文件 | README/规范有，无 MCP/RAG |
| **L4** | MCP、RAG、规范闭环、行为采集 | **主档位参考** |
| L5 | 评测驱动、多项目可复制、稳定 SLO | 工具链或多项目单项触达 |
| L6 | 对外平台化、大规模协作 | 非个人当前目标 |

## 4. AISA 五维（对照用，估算 0–100）

权重：Prompting 23% · Critical 22% · Technical 20% · Workflow 25% · Safety 10%。

| 1–10 档 | 行为描述 |
|---------|----------|
| 1–2 Novice | 无意识地使用，无结构 |
| 3–4 Developing | 知道技能但 inconsistently |
| 5–6 Competent | 可重复的技巧 |
| 7–8 Proficient | 能解释为何这样做 |
| 9–10 Expert | 原则内化，重塑工作方式 |

**Proficient 参考线**：综合约 **75–89**；**Competent** 约 55–74。

## 5. 外部框架简表（重大评测时对照）

| 框架 | 用途 |
|------|------|
| **AILit 四域** | Engaging / Creating / Managing / Designing AI |
| **Frontiers 三域** | Functional / Critical / Rhetorical |
| **Prompt 认证** | Foundation → Practitioner → Lead |
| **PRL 9 级** | 提示资产工程成熟度（本机参考 6–7） |

## 6. 硬指标采集清单

评测报告须尽量包含：

| 类别 | 指标 | 来源 |
|------|------|------|
| 强度 | 近月/周 Cursor 前台时长、切入次数 | `app_usage` / `qr ai-assess` |
| 归档 | Cursor 事件总数、按 `project` 分布 | `events` |
| 沉淀 | 决策笔记、引导语/片段、chunks | `qr.db` |
| 治理 | 规范版本数、facts 条数 | `standards_versions` / `facts.json` |
| 检索 | `qr eval rag` 或月报：命中率、泄漏、均耗时 | `eval_suite` |
| 节奏 | 日/月计划勾选、`assessments/` 文件数 | `daily_plan.json` |

## 7. 评测节奏（与规范绑定）

| 频率 | 命令 | 产出 |
|------|------|------|
| **每日** | `qr ai-assess --save` | `~/.qr/assessments/YYYY-MM-DD.md` |
| **每月** | `qr eval monthly --save` | `~/.qr/eval_monthly/YYYY-MM.md` |
| **按需** | 本对话级完整评测 | 六维表 + 多框架 + 90 天行动项；可导出 Word/PDF |
| **洞察页** | 勾选每日计划 | 与命令互补，不替代 `--save` |

## 8. 报告结构（完整版）

1. 执行摘要（等级、综合分、最强/最弱项）  
2. 相对上次 Δ（若有）  
3. 硬数据画像  
4. 六维分项 + 证据  
5. 多框架对照表  
6. 优势 / 短板 / 90 天建议  
7. 附录：数据时间与 `qr doctor` 状态  

## 9. 与检索升级的关系

内置检索 **连续 2 月低于 9/9** 或延迟明显恶化时，对照 `docs/RETRIEVAL_UPGRADE_PLAN.md` 考虑阶段 C+；否则优先体验、资产化与多项目落地。
