# QR 本地知识库 · 十大用法详解

> 面向本机日常开发；数据在 `~/.qr`，项目在 `~/QR/<分类>/<名>`。Web：`http://127.0.0.1:8765`（`qr schedule install` 可常驻）。

---

## 1. 行为时间线：「我上周在搞什么？」

**能做什么**  
把 shell 命令、git 提交、文件改动、Cursor 对话、笔记合成按时间排序的「个人开发日记」。

**推荐操作**  
- 一次性：`qr shell enable` → `qr backfill --days 90` → `qr schedule install`  
- 每天自动：`qr update`（采集 + 索引）  
- Web → **时间线**：按来源筛选；搜索框全文检索；点条目可看详情与**相关事件**  

**细节**  
- Cursor 问话会进时间线，同时进入 **引导语收件箱**（见第 11 节）  
- 时间戳不准时，检查 zsh `EXTENDED_HISTORY`（`qr shell check`）  

---

## 2. 只问自己的代码：RAG 问答

**能做什么**  
在已索引的 `~/QR` 项目里提问，答案带**文件路径来源**，默认离线。

**推荐操作**  
```bash
qr ask "这个项目的入口在哪？" -p dev/qr
qr ask "有哪些 API 路由？" --model qwen2.5:32b    # 要快用 32B
qr query "database locked" -k 8                   # 只检索、不生成
```

**细节**  
- Web **问答**：顶部 **接着干** 卡片汇总活跃项目、Cursor 话题、Git 与 README 待办  
- Web **问答**：选模型、限定分类/项目、可开联网  
- 新项目：`qr index` 后再问；大仓库首次索引较慢  
- 日常增量：`qr ingest` / `qr update` 采集后会自动增量索引；也可 `qr index --incremental`  
- 符号定位：`qr symbol <名称>` 或 `qr query <符号名>`（精确标识符会优先命中定义行）  
- 稳定配置事实：`qr facts sync`（端口、embed 模型等）  
- 检索子系统升级计划（给未来改版用）：`docs/RETRIEVAL_UPGRADE_PLAN.md`

---

## 3. Cursor 直连知识库（MCP）

**能做什么**  
在 Cursor Agent 里调用 `qr_search` / `qr_ask` / `qr_project`，不用复制粘贴仓库上下文。

**推荐操作**  
- 配置 MCP：`qr mcp`（stdio）  
- 工具：`qr_search` / `qr_ask` / `qr_project` / `qr_facts` / `qr_log_decision` / `qr_timeline` / `qr_prompts` / `qr_compliance`  
- 对话示例：「用 qr_project 看 dev/qr 最近两周在改什么，再按我的规范建议目录结构。」  

**细节**  
- 适合**跨文件、跨会话**的任务；与当前打开文件互补  
- 配合 `qr rules --user` 让 AI 长期遵守你的规范  

---

## 4. 决策与笔记：让以后的自己能搜到

**能做什么**  
结构化记录决定、随手笔记，进入时间线与检索。

**推荐操作**  
```bash
qr log "选用 SQLite 因单机部署" --type decision
# 或写入 ~/.qr/notes/*.md，再 qr ingest
```

**细节**  
- 决策模板含：问题 / 选项 / 结论 / 原因  
- 笔记会参与 `qr summary` 与 RAG  
- 完整 **引导语** 导出在 `~/.qr/prompts/<类型>/`（见下）  

---

## 5. 个人规范 → 约束 Cursor 与合规扫描

**能做什么**  
一份「活」规范驱动 Cursor 规则、合规检查、行为修订。

**推荐操作**  
```bash
qr standards --edit
qr rules --user              # 粘贴到 Cursor User Rules
qr rules --all               # 各项目 .cursor/rules
qr compliance                # 谁缺 README、目录是否乱
qr standards-revise --period week
```

**细节**  
- 规范正文：`~/.qr/standards.md`，有版本历史  
- 与 **引导语** 配合：把常用 Cursor 提示沉淀成库内资产  

---

## 6. 单项目体检面板

**能做什么**  
一个命令看清某项目近两周 Git、Cursor 话题、合规、稳定事实。

**推荐操作**  
```bash
qr project dev/qr --days 14
```
Web → **项目** 页选项目查看。

**细节**  
- 接手陌生 repo、或隔月再打开时最有用  
- 样例检索展示 RAG 对该项目的命中质量  

---

## 7. 工作区治理：~/QR 统一收纳

**能做什么**  
把散落桌面/文档里的项目迁到 `~/QR/<分类>/<名>`，索引与问答都按分类管理。

**推荐操作**  
```bash
qr workspace status
qr workspace import            # 发现散落项目（qr import 已弃用）
qr workspace migrate --dry-run
qr workspace new my-app -c dev
qr workspace audit && qr workspace prune --yes
```

**细节**  
- 删除项目必须 `qr workspace delete`（二次确认，清索引）  
- 迁移后务必 `qr index --reindex`  

---

## 8. 后台常驻 + 洞察通知

**能做什么**  
自动采集、索引、周报；Web 洞察页生成摘要与知识图谱。

**推荐操作**  
```bash
qr schedule install
qr digest-notify               # 洞察 + macOS 通知
qr desktop --install           # 桌面图标开 Web
```

**细节**  
- launchd：`tracker` / `cursor` / `auto` / `weekly` / `web`  
- 洞察页可跑 RAG 评测、合规、导出 Obsidian  

---

## 9. 导出与备份

**能做什么**  
把笔记/总结/对话导出 Markdown；备份整个知识库库文件。

**推荐操作**  
```bash
qr export-obsidian
qr backup
qr backup --list
qr backup --verify ~/.qr/backups/qr-时间戳.db
qr backup --restore ~/.qr/backups/qr-时间戳.db   # 恢复前自动另存 qr-pre-restore-*.db
qr index-health
qr index-health --cleanup    # 清理源文件已消失的向量索引
```

**细节**  
- 引导语 Markdown 在 `~/.qr/prompts/`，`qr ingest` 会同步进笔记事件  
- `qr doctor` 会提示索引失效路径与备份状态；Web **运维** 页也可一键检查/清理  

---

## 10. 模型与 RAG 质量评测

**能做什么**  
对比四款 Ollama 模型的命中率与速度；单独测检索基线。

**推荐操作**  
```bash
qr eval rag                    # 几秒，只看检索
qr eval compare-four           # 全量四模型（较久）
open ~/.qr/logs/model_compare_latest.html
```

**细节**  
- 报告分 **RAG 基线**（与模型无关）和 **模型生成** 两栏  
- 改索引后先 `qr index` 再评测  

---

## 11. 引导语（新）：Cursor 问话 → 分类 → 合并复用

**能做什么**  
- **自动**：`qr ingest` 后把 Cursor 用户问话收入 **收件箱**，并按规则打上类型（功能开发 / 排错 / 理解代码…）  
- **合并**：多段勾选合成一条 **完整引导语**（标记「合并合成」）  
- **手动**：自己写一条标准提示（标记「手动创建」）  
- **类型**：内置 9 类；可 Web/CLI **新建类型**；手改分类后标记「手动分类」  

**识别方式（Web → 引导语）**  

| 徽章 | 含义 |
|------|------|
| 自动采集 | 来自 Cursor 时间线 |
| 自动分类 | 规则匹配的类型 |
| 手动分类 | 你改过类型 |
| 合并合成 | 多段问话合并后的完整引导语 |
| 手动创建 | 非 Cursor、手写的引导语 |
| 待合并 | 仍在收件箱、未入库 |

**推荐操作**  
```bash
qr prompts sync                # 同步 Cursor → 收件箱
qr prompts list                # 看收件箱
qr prompts merge 1,2,3 --title "重构模块 X" --type "重构优化"
qr prompts add "代码审查清单" "请按以下维度审查…" --type "规范治理"
qr prompts types
```

**细节**  
- 保存后导出 `~/.qr/prompts/<类型slug>/0001-标题.md`，参与笔记索引，可用 `qr ask` 检索  
- 配置项（`~/.qr/config.json`）：`prompt_guides_auto_sync`、`prompt_guides_dir`  
- **侧栏对话标题前缀**（`前缀-主题`）：仅 **`执行-`** 自动进收件箱；**`参考-`** 只留时间线；无前缀或未知前缀暂不进，待改标题后再同步（见 `standards/STANDARDS.md`）  
- 规范要求：高价值 **执行类** Cursor 对话应合并为引导语，避免重复劳动  

---

## 12. 项目域：体检 / 简介 / 变更简报

**三个概念（勿混用）**

| 名称 | CLI / API | 用途 |
|------|-----------|------|
| **体检面板** | `qr project dev/qr`、`/api/project` | Git、Cursor、合规、事实、样例检索 |
| **项目简介** | `/api/project/brief` | README/PROJECT.md 用途、功能点、完成度 |
| **变更简报** | `qr changelog dev/qr`、`/api/changelog/{id}` | 近 N 天 Git/Cursor/文件活动 Markdown |

Web **项目**页可点「变更简报」；**洞察**页有「今日总览」（接着干 + 洞察摘要 + 提醒）。  
规范**沿革**请用 `/api/standards/history`（旧路径 `/api/standards/changelog` 仍可用）。

---

## 13. 项目简报与主动提醒

**能做什么**  
- 单项目 **变更简报**（Git 提交、Cursor 话题、文件改动、合规、稳定事实）  
- **Cursor 会话自动摘要**写入 `~/.qr/notes/`，进入时间线与检索  
- **主动提醒**：项目休眠、散落目录、合规待改进、RAG 命中率下降（`digest-notify` 推送）  

**推荐操作**  
```bash
qr changelog dev/qr --days 7
qr digest-notify              # 洞察 + 简报摘要 + macOS 通知
```

**细节**  
- 简报保存：`~/.qr/logs/changelog-<项目>-<日期>.md`  
- 提醒列表：`~/.qr/logs/alerts-latest.json`  
- 时间线 Web 页支持关键词搜索（`GET /api/events?q=`）  

---

## 最小上手路径（约 15 分钟）

1. `qr init` → `qr ingest` → `qr index`  
2. `qr schedule install`  
3. 浏览器打开 Web → 试 **问答** + **引导语 → 同步 Cursor**  
4. `qr rules --user` 粘贴规范（可选）  

有问题先：`qr doctor` · `qr status`
