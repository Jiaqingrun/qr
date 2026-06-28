# 个人开发 / 存储 / 行为规范

> 这是一份"活文档"。`QR本地知识库` 会用它来生成 Cursor 规则、对照你的实际行为给出偏差提醒。
> 随时编辑：`qr standards --edit`。

## 一、存储与目录规范
- 所有代码项目统一放在 **`~/QR/<分类>/<project-name>`**（分类如 `dev` / `mobile` / `experiments` / `tools` / `archive`），**不要**放在桌面 / 下载 / `~/Projects` 散落目录。
- 新建项目：`qr workspace new <名称> --category dev`。
- 项目命名用小写中划线：`my-project`，避免空格和大小写混用（本机文件系统大小写不敏感）。
- 临时文件放系统临时目录，不留在桌面。
- 大文件（ISO / 安装包 / 视频）不放桌面，归档到外部盘或 NAS。

### 知识库与本机数据（QR）
- **业务项目代码**：仅 `~/QR/<分类>/<项目>`；**知识库程序仓库**（本系统源码）：`~/QR/dev/qr`。
- **运行数据**（数据库 `qr.db`、配置、周期总结、笔记）：仅 **`~/.qr`**，不提交 Git、不放入业务项目目录。
- **新建 / 迁移 / 清理**：`qr workspace new`、`qr workspace migrate`、`qr workspace prune`；删除项目须用 `qr workspace delete`（二次确认）。
- **行为与笔记**：关键进展用 `qr log` 或 `~/.qr/notes/*.md`（由 `qr ingest` 同步）；每周 `qr summary --period week` 对照本规范复盘。
- **索引与问答**：项目纳入知识库后执行 `qr index`；问答与检索基于本地索引，不依赖把数据拷进项目树。
- **运维参考**（细节以 `~/QR/dev/qr/README.md` 为准）：自检 `qr doctor`，备份 `qr backup`，后台任务 `qr schedule status`。

## 二、Python 环境规范

> **适用说明**：本章约束 **AI 实现** 时的环境与可复现性（见 §四·协作者角色）。**设计者**不必手写 Python，但须能执行验收清单。

- 系统 Python（`/usr/bin/python3`）保持纯净，不 pip 安装任何东西。
- 每个需要依赖的项目用独立 conda 环境：`conda create -n <name> python=3.x`。
- 统一用 `/opt/anaconda3` 作为唯一 conda 入口；环境名用小写。
- **QR本地知识库** 专用 conda 环境名固定为 **`qr`**（`conda activate qr`），禁止使用旧名 `kb`。
- 每个项目根目录提供 `requirements.txt` 或 `environment.yml`，可复现。

## 三、Git 与开发规范
- 每个项目第一步 `git init` 并尽早提交，避免无版本控制的"裸代码"。
- 提交信息写清"为什么"，使用动词开头：add / update / fix / refactor。
- 每个项目有 `README.md` 说明用途、运行方式、依赖。
- 密钥、token 绝不写进代码或 dotfile 明文；放入 `~/.config/zsh/secrets.zsh`（权限 600）或项目 `.env`（加入 `.gitignore`）。

## 四、AI 协作规范
- 复杂/多文件任务先让 AI 出方案再动手。

### 协作者角色（设计者 / AI 实现 / 验收）

本机 QR 及 `~/QR` 业务项目的协作默认按 **三角色** 理解；**不得**用「是否会手写编程语言」代替对设计能力或 AI 协作水平的评价。

| 角色 | 承担者 | 职责 | 可观测证据 |
|------|--------|------|------------|
| **设计者** | 本人 | 定义要什么/不要什么、模块与数据流、规范与验收标准；维护 `qr standards`、`PROJECT.md`、进化计划 | 规范版本、决策笔记、`EVOLUTION_PLAN`、对话中的约束与方案选择 |
| **AI 实现** | Cursor Agent 等 | 按设计者约束写代码、改配置、跑命令、补测试与文档 | Git diff、`unittest`、对话中的执行记录 |
| **验收** | 本人（可请 AI 解读结果） | 确认行为符合设计：Web 可用、`qr doctor`、评测基线不退化 | `qr doctor`、Web/CLI 点验、`qr eval rag`、显式确认后再 commit |

- **设计者身份**：QR 本地知识库由本人亲手设计；实现层以 AI 协作为主（见 `dev/qr/README.md`），**不改变设计主权**。
- **学历与语法**：未受过系统编程教育 **不** 等同于「无设计能力」；**禁止**在 AI 评测、复盘、对话中将「看不懂源码」直接判为低档位。
- **§二 Python / §三 Git**：主要约束 **AI 实现**；设计者以 **验收清单** 把关，不要求逐行手写。
- **设计者验收清单（最小）**：① `qr doctor` 无新增严重项；② 相关功能在 Web 或 CLI 点验通过；③ 里程碑 `qr log --type decision` 记录「问题 / 选项 / 结论 / 原因」。
- **AI 评测读法**：六维与 L 阶梯评的是 **AI 协作与 Personal AI Ops**；`工具链` 高分可来自「设计并落地 qr」；**不得**仅用 Python 熟练度替代设计或协作档位。

- **全局规范**（本文件）与 **项目规范**（各仓库 `PROJECT.md`）**严格分层、绝对不混写**：
  - **全局只写**：`~/QR` / `~/.qr`、conda/Git、通用 AI 协作、引导语前缀、QR Web **共用**布局习惯（第六章）。
  - **项目只写**：用途、技术栈、本项目目录、测试命令、业务边界、禁止改动的范围、本项目 MCP `project` 参数等。
  - **禁止**：把全局条文复制进 `PROJECT.md`；把某业务项目规则写进本文件；在任一文件中用「## 一、」～「## 六、」式全局章节标题写项目内容。
  - **叠加方式**：`qr rules` 生成 `00-personal-standards.mdc` + `10-project.mdc`（两层同时加载）；`AGENTS.md` 仅作汇总阅读，**手改无效**，以 `.mdc` 为准。冲突时**项目细则优先**。
- 全局可从全部对话摘要修订：`qr standards-revise --from-conversations`；项目可从本项目对话修订：`qr project-standards-revise <项目>`。
- **定时修订**：`qr schedule install` 后，`com.qr.weekly` 每周执行 `qr update --summary week` 时会自动修订全局规范（及近期有 Cursor 活动的最多 2 个项目）；间隔与开关见 `~/.qr/config.json` 中 `standards_auto_*`；手动：`qr standards-auto --force`。
- 重要的 AI 对话结论，用 `qr log` 或 `~/.qr/notes/*.md` 沉淀，便于时间线与总结引用。
- **引导语（Cursor 提示资产）**：
  - Cursor 中的有效问话由知识库 **自动采集** 到引导语收件箱（`qr ingest` / `qr prompts sync`）；Web → **引导语** 页可查看。
  - **Cursor 侧栏对话标题前缀**（格式一律为 `前缀-说明`，连字符后为主题）：
    - **`执行-`**：要落地改代码/配置/运维等项目行为 → **必须** 进入引导语收件箱。
    - **`参考-`**：资料查阅、问题查询、分析讨论 → **只** 进时间线等记录，**不** 进入引导语。
    - **未加前缀** 或 **非上述已知前缀**：尚未确认用途 → 引导语 **暂不引用**，待改标题后再判定。
    - 今后可能增加其他前缀；新增用途在本节登记后，同步逻辑再跟进。
  - 同一任务的多轮追问，应 **合并** 为一条完整引导语（标记「合并合成」），避免碎片重复劳动。
  - 可复用的提示模板 **手动新建** 或 **指定/新增类型**（内置类型 + 自定义类型名）；改分类视为「手动分类」。
  - 完整引导语导出在 `~/.qr/prompts/<类型>/`，纳入笔记索引，可用 `qr ask` 检索；详见 `docs/USE_CASES.md` 第 11 节。

## 五、行为与复盘规范
- 每天用 `qr log` 记录关键进展或决定。
- 每周查看 `qr summary --period week` 的总结，对照本规范修正习惯。
- 阶段性把散落项目迁入 `~/QR` 或归档：`qr workspace migrate` / `qr workspace prune`。

### 本机使用统计
- QR 统计本机使用信息（`app_usage` 屏幕采样、`qr usage`、Web 使用页、`qr ai-assess`、周期总结中的应用时长段落）时，**必须过滤一切游戏相关应用**，不计入 Top 应用、活跃总时长、AI 评测中的屏幕活跃指标。
- **游戏**指游玩与商业启动器（Steam / Epic / Battle.net / GOG / Paradox Launcher / EVE 客户端等），**不含**在 `~/QR` 内自研项目的调试前台（如 `华夏重工`）；后者列入 `usage_include_apps` 白名单。
- 原始采样仍可写入 `app_usage` 表；**展示与汇总时排除**（默认 `usage_exclude_games: true`）。可在 `~/.qr/config.json` 用 `usage_exclude_apps` / `usage_exclude_bundles` 追加排除项，`usage_include_apps` 追加白名单。
- **暂停采集**：`tracker_pause_until`（`qr track --pause 2h` / Web 运维页）；暂停期间**不写入** `app_usage`。按 bundle/App 不采样用 `tracker_exclude_bundles` / `tracker_exclude_apps`（与展示过滤独立）。

### AI 使用水平评测（规则摘要）
- **原则**：以 `~/.qr/qr.db` 行为证据为主（Cursor 归档、屏幕采样、eval、facts），辅以多框架对照；完整量表见 **`~/QR/dev/qr/docs/AI_SKILL_ASSESSMENT.md`**。
- **角色声明**：评测须对照 §四「协作者角色」；**禁止**以是否会手写代码否定设计者档位或 QR 设计成就。
- **主量表**：**QR 六维**（提示工程、工具链、元认知、复盘习惯、多项目协作、领域应用），各 1–10 分；综合分 = 六维均值。**档位**：7.6–8.5 为 L4+，8.6+ 逼近 L5。
- **对照框架**（重大评测时）：AISA 五维（0–100）、个人 L1–L6、AILit 四域、Prompt 认证（Foundation→Lead）、PRL 提示就绪度。
- **硬指标**：近月 Cursor 时长与切入、按项目对话分布、决策笔记数、引导语/片段、RAG 基线（`qr eval rag`：命中率/泄漏/均耗时）、规范版本与 facts。
- **节奏**：
  - **每日**：`qr ai-assess --save` → `~/.qr/assessments/`；洞察页勾选「每日 AI 水平评测」。
  - **每月**：`qr eval monthly --save` → `~/.qr/eval_monthly/`；对照 `docs/RETRIEVAL_UPGRADE_PLAN.md` 触发条件。
  - **按需**：对话级完整评测（六维 + 多框架 + 90 天行动项）；里程碑可导出 Word/PDF 归档。
- **里程碑**：每个主项目阶段结束至少 **1 条** `qr log --type decision`；避免「对话上千、决策个位数」。

### 进化计划自动同步
- 跟踪文档：`~/QR/dev/qr/docs/EVOLUTION_PLAN.md`（产品向优先级与验收）。
- **自动**：`qr update` 结束时若 `evolution_auto_sync=true`（默认），按规则检测并更新状态（仅 **进行中→已完成**）。
- **手动**：`qr evolution sync`（快检）· `qr evolution sync --full`（含 RAG 9/9）· `qr evolution status`。
- 状态缓存：`~/.qr/evolution_plan_state.json`。

## 六、界面与视觉规范（全局）
- **QR 知识库 Web**（`http://127.0.0.1:8765`）与各业务项目前台，在信息架构上保持一致：侧栏导航、卡片分区、时间线列表（首行标题 + 可点击路径）、标签色与来源色区分。
- **QR Web 布局（2026-06）**：主内容区**全宽自适应**；每标签页 `view--fill` 纵向填满；`page-ops` + `page-body`，间距 16px、外边距 20px；列表+详情用 `split-shell`。细则见 `docs/WEB_UI_LAYOUT.md`。
- 深色主题为默认参考；主色使用 CSS 变量（如 `--lime` / `--muted` / `--rose`），避免硬编码散落颜色。
- 交互：可点击项用 `.tl-link` / `btn` 体系；危险操作用 `--rose` 并二次确认；加载与空状态需有明确文案，避免空白页。
- 新页面或改版先对照本节与 WEB_UI_LAYOUT，再写项目内 UI；**项目特有**的视觉（品牌色、插画）写在各项目 `PROJECT.md`，不写入本节。
