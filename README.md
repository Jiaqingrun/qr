# QR本地知识库

一套**纯本地、离线**的个人系统：记录并分析你的行为，规范开发/存储/行为，
生成周期性总结，并能基于你的项目内容进行语义检索与问答。全程使用本机
[ollama](https://ollama.com)（`bge-m3` 做向量，`qwen2.5:72b` 日常问答、`deepseek-r1:70b` 深度推理），
数据不出本机。

## 能力
- **行为采集**：Shell（带 epoch 历史）、Git、文件变更、Cursor 对话、笔记（`qr log` 与 `~/.qr/notes/*.md`）。
- **语义检索/问答**：对 `~/QR` 工作区建立向量索引，支持按分类/项目筛选，`qr ask` 用本地大模型回答。
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

> **勿再使用** `conda activate kb`：那是旧版环境名。若仍并存 `kb` 与 `qr`，请只用 `qr`，确认无误后可 `conda remove -n kb`。运行 `qr doctor` 可检查 launchd 是否仍指向旧路径。

## 常用命令
```bash
qr status                 # 查看状态
qr ingest                 # 采集所有行为数据（增量）
qr index                  # 索引 ~/QR 等项目内容
qr query "关键词"          # 语义检索片段
qr ask "我之前怎么实现X的？"  # 本地大模型基于项目内容回答
qr log "今天决定用方案A"     # 随手记笔记
qr summary --period week  # 生成本周行为总结
qr standards --edit       # 编辑个人规范
qr prompts sync           # 同步 Cursor 问话 → 引导语收件箱
qr rules --target ~/QR/dev/my-app       # 为某项目生成 Cursor 规则/AGENTS.md
qr doctor                 # 检查权限、时间戳、后台任务等待完善项
qr backup                 # 备份 ~/.qr/qr.db
qr shell enable           # 启用 zsh 带时间戳历史
qr permissions setup      # 扩大采集范围并打开系统隐私设置
qr schedule install       # 安装后台任务（追踪/同步/收录/Web）
qr web --install          # 安装 Web 后台服务 + 每 45s 健康巡检（异常自动重启）
qr web-watch --once       # 手动探测 Web 并在必要时拉起
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

首次运行 `qr init` 会自动将旧目录 `~/.kb` 中的数据迁移到 `~/.qr`。

## 从旧版迁移
1. 将仓库目录重命名：`mv ~/Projects/kb ~/Projects/qr`（若尚未重命名）
2. 将 conda 环境重命名为 `qr`（或新建环境后 `pip install -e ~/Projects/qr`）
3. 运行 `qr init` 完成 `~/.kb` → `~/.qr` 数据迁移
4. 重新安装定时任务：`qr schedule uninstall` 后 `qr schedule install`（launchd 标签已改为 `com.qr.*`）

## 配置
编辑 `~/.qr/config.json` 可调整索引目录、模型名、分块大小、排除目录等。
