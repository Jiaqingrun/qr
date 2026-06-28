# QR本地知识库

一套**纯本地、离线**的个人系统：记录并分析你的行为，规范开发/存储/行为，
生成周期性总结，并能基于你的项目内容进行语义检索与问答。全程使用本机
[ollama](https://ollama.com)（`qwen3-embedding:8b` 做向量，`qwen2.5:32b` 日常问答、`deepseek-r1:32b` 深度推理），
数据不出本机。（纯AI生成，无任何手写代码。问答模型可自行下载适合本机配置的量级，推荐Qwen2.5）
规范会自动限制AI行为，已实现多项目分级规范管理，项目之间只有关联但互不影响。

## 许可与外部组件

- **本仓库源码**： [MIT](LICENSE)
- **Python 依赖**：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)（均为 MIT / BSD / Apache-2.0 等宽松协议，无 GPL 传染链）
- **安装范围**：仅需 `pip install -e .` 时 `pyproject.toml` 声明的依赖
- **Ollama**：需用户在本机单独安装（[Ollama 为 MIT](https://github.com/ollama/ollama/blob/main/LICENSE)），**不随本仓库分发**
- **大模型权重**：由用户自行 `ollama pull`；各模型许可以 [Ollama 模型页](https://ollama.com/library) 及上游官方为准
- **联网搜索（可选）**：`qr ask --web` 使用百度/必应抓取或百度千帆 API，受对应服务条款约束

## 功能点
- [x] 行为采集与时间线（shell / git / file / cursor / notes）
- [x] 向量检索、Hybrid 问答与符号定位
- [x] 周期总结、规范对照与沿革
- [x] Web 控制台（8765）与 launchd 后台任务
- [x] 工作区治理（migrate / audit / prune）与 MCP
- [x] 稳定事实、项目 brief 与「接着干」入口
- [ ] 移动端 / 远程访问（当前刻意不做）

## 能力
- **行为采集**：Shell（带 epoch 历史）、Git、文件变更、Cursor 对话；**时间线 note** 仅 `qr log` 手动记录（`~/.qr/notes/*.md` 不再写入时间线）。
- **语义检索/问答**：对 `~/QR` 工作区建立向量索引，支持按分类/项目筛选，`qr ask` 用本地大模型回答。检索升级路线见 [`docs/RETRIEVAL_UPGRADE_PLAN.md`](docs/RETRIEVAL_UPGRADE_PLAN.md)。
- **周期性总结**：按天/周/月生成 Markdown 行为总结，并对照个人规范指出偏差。
- **治理**：个人规范、项目 `.cursor/rules` / `AGENTS.md`、工作区迁移/审计/删除。
- **自动化**：`launchd` 应用追踪、Cursor 同步、定时 ingest+索引、Web 常驻。
- **边界透明**：时间线标注估算时间；`qr doctor` 检查权限与时间戳覆盖率。

## 产品定位

QR 不是单纯的本地 RAG 问答工具，而是一套 **Personal AI Ops（个人 AI 运维）** 系统，将感知、记忆、治理与闭环四段串在同一条数据链上：

| 层次 | 能力 | 数据落点 |
|------|------|----------|
| **感知** | Shell / Git / 文件 / Cursor / 屏幕采样 | `~/.qr/qr.db` → `events` |
| **记忆** | 向量索引 + FTS + 符号索引 + 稳定事实 | `chunks` / `vec_chunks` / `facts.json` |
| **治理** | 个人规范、项目规范、合规扫描、工作区收纳 | `standards.md` / `.cursor/rules` |
| **闭环** | 引导语沉淀、决策笔记、周期总结、AI 水平评测 | `prompt_guides` / `assessments/` |

**目标用户**：在 macOS 上用 Cursor 做多项目开发的个人开发者（设计者定义约束与验收，AI 负责实现）。

**非目标**：团队协作、SaaS 多租户、跨设备同步、移动端（见功能点中「刻意不做」项）。

协作模型见 `standards/STANDARDS.md` §四（设计者 / AI 实现 / 验收）；评测量表见 [`docs/AI_SKILL_ASSESSMENT.md`](docs/AI_SKILL_ASSESSMENT.md)。

## 架构概览

**技术栈**：Python 3.12+ · Typer CLI · FastAPI Web · SQLite（WAL）· sqlite-vec · Ollama · launchd · MCP stdio · 单页 Web（原生 JS）。

**模块结构**（约 90+ Python 文件）：

```
采集 collectors/  →  qr.db  →  索引 indexer/  →  检索 query/ hybrid/
                                      ↓
              治理 governance/ workspace/  ←→  CLI · Web · MCP
```

**检索管线**（阶段 A/B/C 已完成，细节见 [`docs/RETRIEVAL_UPGRADE_PLAN.md`](docs/RETRIEVAL_UPGRADE_PLAN.md)）：

```
问题
 ├─ 符号精确匹配 → symbol_index
 ├─ 稳定事实短路 → facts.retrieval_hits
 ├─ 向量检索（Ollama embed → sqlite-vec）
 ├─ 全文检索（chunks_fts BM25）
 └─ RRF 融合 → parent_expand → 路径加分 → 词面 rerank → 同路径去重
```

阶段 D～F（HyDE、cross-encoder、多向量）在内置评测达标前**刻意不做**；升级触发条件见检索计划 §6。

**MCP 工具**（Cursor Agent）：`qr_search` / `qr_ask` / `qr_project` / `qr_facts` / `qr_log_decision` / `qr_timeline` / `qr_prompts` / `qr_compliance`。

**Web 视图**：问答、项目、关系图、检索、使用统计、总结、时间线、引导语、规范、沿革、洞察、运维等（布局见 [`docs/WEB_UI_LAYOUT.md`](docs/WEB_UI_LAYOUT.md)）。

## 差异化能力

| 能力 | 说明 |
|------|------|
| Cursor 对话 → 引导语 → 检索 | 问话进收件箱，合并后入库并可被 `qr ask` 检索 |
| 规范 → Rules → 合规 | 全局 + 项目双层规范，生成 Cursor 规则并扫描 README/目录 |
| 行为时间线 + RAG | Shell/Git/Cursor/文件/笔记与向量索引共用项目维度 |
| 评测门禁 | `qr eval rag`（core 9 题）+ extended；改检索须过门禁 |
| 项目关系与跨项目检索 | 关系图 + 沿 links 扩展 chunk（阶段 C） |
| macOS 自动化 | launchd：tracker / cursor / auto / weekly / web 等 |

完整用法见 [`docs/USE_CASES.md`](docs/USE_CASES.md)；已知短板与修复路线见 [`docs/短板修复.md`](docs/短板修复.md)。

## 工程质量

| 方面 | 现状 |
|------|------|
| 文档 | 进化计划、检索升级、短板修复、评测量表、用例详解分工明确 |
| 测试 | `tests/` 覆盖检索、规范、合规、Web API、会话检查点等 |
| 运维 | `qr doctor` 分模块告警；`index-health`；`qr backup` 轮转；敏感信息 `qr cursor sanitize` |
| 依赖 | 运行时依赖少（见 `pyproject.toml`），单机 SQLite，无 Redis/ES |
| 隐私 | 默认离线；数据在 `~/.qr`；可选 `qr ask --web` 联网 |

**已知局限**（单机定位下的取舍，非遗漏）：

- **平台**：深度绑定 macOS（launchd、pyobjc、屏幕采样）；Linux/Windows 需单独适配。
- **存储**：SQLite 多进程并发写偶发 `database is locked`（WAL + 退避已缓解）。
- **前端**：单 HTML/JS 文件，无 SPA 构建链，长期维护需模块化拆分。
- **检索上限**：无 cross-encoder / HyDE；大库或强跨语言场景可能触顶（见检索计划触发条件）。
- **闭环使用率**：引导语合并、决策笔记机制已齐，实际使用比例依赖个人习惯（见短板修复 M2）。

## 同业对比

对比维度：本地/隐私 · 代码 RAG · 行为时间线 · IDE 集成 · 规范治理 · 多项目 · 内置评测。

| 产品 | 类型 | 本地 | 代码 RAG | 时间线 | IDE | 治理 | 多项目 | 评测 | 平台 |
|------|------|------|----------|--------|-----|------|--------|------|------|
| **QR** | Personal AI Ops | ●●●●● | ●●●● | ●●●●● | ●●●●● MCP | ●●●●● | ●●●●● | ●●●● | macOS |
| Cursor @codebase | IDE 内置 | ●●●● | ●●●● | ●● | ●●●●● | ● | ●●● | — | 跨平台 |
| Continue.dev | IDE 插件 | ●●●● | ●●● | ● | ●●●● | — | ●● | — | 跨平台 |
| Sourcegraph Cody | 企业代码 AI | ●● | ●●●●● | ● | ●●●● | — | ●●●●● | ●● | 云/自托管 |
| PrivateGPT / GPT4All | 本地 RAG | ●●●●● | ●●● | — | — | — | ●● | — | 跨平台 |
| Obsidian + AI 插件 | PKM | ●●●●● | ●● | ●● | ●● | ●● | ●●● | — | 跨平台 |
| Mem0 / Zep | 记忆中间件 | ●●~●●● | ●● | ●●● | API | — | ●●● | ●● | 库/云 |
| Pieces | 开发者记忆 | ●●● | ●●● | ●●●● | ●●● | — | ●●● | — | 跨平台 |
| Rewind.ai | 屏幕回溯 | ●●●● | — | ●●●●● | ● | — | ● | — | macOS |

（● 为相对评级，非绝对分数。）

**简要对照**：

- **vs Cursor @codebase**：Cursor 零配置、与编辑上下文一体；QR 补 **跨会话行为记忆**、可配置检索、MCP 工具化与评测门禁。二者互补。
- **vs Continue / Cody**：QR 不做补全；在 **个人规范闭环、时间线、引导语资产化** 上更深。Cody 强于企业多仓与权限。
- **vs PrivateGPT / 自搭 LlamaIndex**：框架更灵活、跨平台；QR 是 **一体化 opinionated 产品**，省去自建向量库与管道，绑定 Ollama + SQLite + launchd。
- **vs Obsidian**：笔记与插件生态 Obsidian 更强；QR 在 **代码仓索引、Git/Cursor 采集、合规与工作区治理** 上更深。
- **vs Mem0 / Zep**：记忆 API 可嵌入任意 Agent；QR 是 **端到端应用**，采集、规范、UI 一体，不做对外平台化。
- **vs Pieces / Rewind**：Pieces 偏 snippet；Rewind 偏全屏录像。QR 为 **结构化开发事件**（可检索、可关联项目），不录屏。

**品类定位**：公开市场少见同时提供 Cursor MCP、多源开发时间线、双层规范治理、引导语闭环与 RAG 评测门禁的产品。QR 面向 **macOS + Cursor 重度个人用户**，不是 Obsidian 或 ChatGPT 的直接替代，而是开发工作流中的 **记忆与治理层**。

## 安装
```bash
conda activate qr
pip install -e ~/QR/dev/qr
qr init
```


## 常用命令
```bash
qr status                 # 查看状态
qr ingest                 # 采集所有行为数据（增量）
qr index                  # 索引 ~/QR 等项目内容
qr index --incremental    # 仅索引上次采集后变更的文件
qr index --since-days 3   # 仅索引近 3 天修改过的文件
qr symbol loadStats       # 按符号名查找定义位置（函数/类）
qr query "关键词"          # 语义检索片段
qr ask "我之前怎么实现X的？"  # 本地大模型流式回答（加 --no-stream 可等完整后再渲染 Markdown）
qr log "今天决定用方案A"     # 随手记笔记
qr summary --period week  # 生成本周行为总结
qr standards --edit       # 编辑个人规范
qr prompts sync           # 同步 Cursor 问话 → 引导语收件箱
qr rules --target ~/QR/dev/my-app       # 为某项目生成 Cursor 规则/AGENTS.md
qr doctor                 # 检查权限、时间戳、后台任务等待完善项
qr backup                 # 备份 ~/.qr/qr.db（自动轮转，保留最近 10 份）
qr backup --list         # 列出备份并校验
qr backup --restore PATH  # 从备份恢复（恢复前自动另存当前库）
qr index-health          # 索引健康检查（失效路径、孤儿文档）
qr index-health --cleanup  # 清理源文件已消失的索引
qr changelog dev/qr      # 生成项目变更简报（Git / Cursor / 文件）
qr project dev/qr        # 项目体检面板（Git / Cursor / 合规）
qr shell enable           # 启用 zsh 带时间戳历史
qr permissions setup      # 扩大采集范围并打开系统隐私设置
qr schedule install       # 安装后台任务（追踪/同步/收录/Web）
qr web --install          # 安装 Web 后台服务 + 每 45s 健康巡检（异常自动重启）
qr web-watch --once       # 手动探测 Web 并在必要时拉起
qr desktop --install      # 安装 macOS 桌面应用（QR本地知识库.app）
qr workspace migrate --yes  # 将散落项目迁入 ~/QR
qr workspace new my-app --category dev  # 新建项目
```

Web 界面布局规范见 [`docs/WEB_UI_LAYOUT.md`](docs/WEB_UI_LAYOUT.md)（全宽自适应、`page-ops` / `page-body`）。

## 工作区（~/QR）

本机代码统一放在 `~/QR/<分类>/<项目名>`，默认分类：`dev` / `mobile` / `experiments` / `tools` / `archive`。

```bash
qr workspace status       # 查看待迁移项目
qr workspace migrate --yes
qr index --reindex        # 迁移后重建索引（project 变为 dev/qr 形式）
```

## 数据位置
- 工作区：`~/QR/<分类>/<项目>`
- 知识库代码：`~/QR/dev/qr`
- 运行数据：`~/.qr`（`qr.db`、`config.json`、`standards.md`、`facts.json`、`summaries/`、`notes/`、`backups/`）
- 路径与职责的**规范条文**见 `standards/STANDARDS.md`（生效副本：`~/.qr/standards.md`）


## 配置
编辑 `~/.qr/config.json` 可调整索引目录、模型名、分块大小、排除目录等。

## 运维与数据库

- **WAL 模式**：`qr.db` 默认 `PRAGMA journal_mode=WAL`；读写并发时若见 `database is locked`，`db.session()` / `set_state` 会自动退避重试。
- **写事务**：Web 屏蔽/删除等短写用 `write_session()`（`BEGIN IMMEDIATE` + `busy_timeout`）。
- **减轻锁竞争**：长期运行后可 `qr doctor --fix`；避免多进程同时 `init_db` 重建 vec 表。
- **迁移包**：`qr export-bundle` / `qr import-bundle --dry-run`（仅 `~/.qr` 数据，不含 `~/QR` 源码）。
- **冒烟**：`qr web --restart` 后 `python scripts/web_smoke.py`。
