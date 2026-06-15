# QR本地知识库

一套**纯本地、离线**的个人系统：记录并分析你的行为，规范开发/存储/行为，
生成周期性总结，并能基于你的项目内容进行语义检索与问答。全程使用本机
[ollama](https://ollama.com)（`bge-m3` 做向量，`qwen2.5:72b` 日常问答、`deepseek-r1:70b` 深度推理），
数据不出本机。

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

## 安装
```bash
conda activate qr
pip install -e ~/QR/dev/qr
qr init
```

> **旧版 kb 用户**：若仍使用 conda 环境 `kb` 或 `~/.kb`，请改用 `qr` / `~/.qr` 后执行 `qr init` 与 `qr schedule install`。

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

Web 界面布局规范见 [`docs/WEB_UI_LAYOUT.md`](docs/WEB_UI_LAYOUT.md)（全宽自适应、`page-ops` / `page-body`）。
qr desktop --install      # 安装 macOS 桌面应用（QR本地知识库.app）
qr workspace migrate --yes  # 将散落项目迁入 ~/QR
qr workspace new my-app --category dev  # 新建项目
```

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

`qr init` 会在检测到 `~/.kb` 时自动把缺失项迁入 `~/.qr`（不覆盖已有 `qr.db`）。

## 配置
编辑 `~/.qr/config.json` 可调整索引目录、模型名、分块大小、排除目录等。
